#!/usr/bin/env bash
# scripts/run_bench.sh
#
# T4: 一键基准 — 本地 Arm64 Linux 等价 CI 全流程。
#
# 流程:fetch llama.cpp → build 5 档 → 下 3 模型 + wikitext → 15 speed bench +
#       4 perplexity + PMU 探针 → Phase 1+2 解析(bench_logs → 37 字段 JSON / ppl_logs → 16 字段 JSON)
#       → assemble_results.py(Phase 3+4: comparison + decision table + manifest + dashboard)
#
# 严格遵守 NF4 同机对照:本地单机天然满足,五档连续跑,speedup 分母 = 本 job naive。
# 参数与 CI 完全对齐(-t 4 -p 512 -n 128 -r 5),环境变量可覆盖。不引入第二套口径。
#
# 用法:
#   bash scripts/run_bench.sh                     # 跑全流程(默认 5 档 × 3 量化)
#   bash scripts/run_bench.sh -t 4 -p 512 -n 128 -r 5   # 显式 bench 参数(与 CI 对齐)
#   VARIANTS="naive kleidiai" bash scripts/run_bench.sh   # 只跑部分档(调试用,会破坏 NF4 对照)
#   THREADS=8 REPS=10 bash scripts/run_bench.sh   # env 覆盖默认参数
#
# 非 aarch64 退出(Windows/x86 开发机不能跑,靠 CI 或评委 Arm64 复现)。

set -euo pipefail

# ============================================================================
# 0a. 命令行参数解析(-t/-p/-n/-r,与 CI env 对齐;CLI 覆盖 env,env 覆盖默认)
# ============================================================================
usage() {
    cat <<EOF
用法:
  bash scripts/run_bench.sh                          # 默认全流程(5 档 × 3 量化)
  bash scripts/run_bench.sh -t 4 -p 512 -n 128 -r 5  # 显式 bench 参数(与 CI 对齐)
  VARIANTS="naive kleidiai" bash scripts/run_bench.sh  # 只跑部分档(调试,破坏 NF4)
  THREADS=8 REPS=10 bash scripts/run_bench.sh          # env 覆盖默认

参数:
  -t THREADS  线程数(默认 4,env THREADS)
  -p PP       prefill tokens(默认 512,env PP)
  -n TG       decode tokens(默认 128,env TG)
  -r REPS     repeats(默认 5,env REPS)
  -h          显示此帮助

非 aarch64 退出(Windows/x86 开发机不能跑,靠 CI 或评委 Arm64 复现)。
EOF
}

while getopts ":t:p:n:r:h" opt; do
    case "$opt" in
        t) THREADS="$OPTARG" ;;
        p) PP="$OPTARG" ;;
        n) TG="$OPTARG" ;;
        r) REPS="$OPTARG" ;;
        h) usage; exit 0 ;;
        \?) echo "::error::unknown option: -$OPTARG" >&2; usage >&2; exit 2 ;;
        :)  echo "::error::option -$OPTARG requires an argument" >&2; usage >&2; exit 2 ;;
    esac
done

# ============================================================================
# 0. 入口检测 + 环境变量默认值(与 CI env 完全对齐)
# ============================================================================

if [[ "$(uname -m)" != "aarch64" ]]; then
    echo "::error::run_bench.sh requires aarch64 Linux (CI uses ubuntu-24.04-arm)" >&2
    echo "   current arch: $(uname -m)" >&2
    echo "   Windows/x86 开发机不能跑实机;靠 CI 或评委 Arm64 复现。" >&2
    exit 2
fi

# Bench 参数(与 bench.yml env 完全一致)
THREADS="${THREADS:-4}"
PP="${PP:-512}"
TG="${TG:-128}"
REPS="${REPS:-5}"

# llama.cpp pin
LLAMA_COMMIT="${LLAMA_COMMIT:-fabde3bf5136940eb03821aa2490e2360093965b}"

# GGUF pin
GGUF_REPO="${GGUF_REPO:-Qwen/Qwen2.5-1.5B-Instruct-GGUF}"
GGUF_REV="${GGUF_REV:-91cad51170dc346986eccefdc2dd33a9da36ead9}"
GGUF_FILE_Q4_K_M="${GGUF_FILE_Q4_K_M:-qwen2.5-1.5b-instruct-q4_k_m.gguf}"
GGUF_SHA256_Q4_K_M="${GGUF_SHA256_Q4_K_M:-6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e}"
GGUF_FILE_Q4_0="${GGUF_FILE_Q4_0:-qwen2.5-1.5b-instruct-q4_0.gguf}"
GGUF_FILE_Q8_0="${GGUF_FILE_Q8_0:-qwen2.5-1.5b-instruct-q8_0.gguf}"
# Q4_0/Q8_0 sha256 首次下载时计算并记录进 JSON(CI 行为一致)

# Wikitext-2 perplexity 数据集
WIKITEXT_URL="${WIKITEXT_URL:-https://huggingface.co/datasets/ggml-org/ci/resolve/main/wikitext-2-raw-v1.zip}"
WIKITEXT_FILE="${WIKITEXT_FILE:-wikitext-2-raw/wiki.test.raw}"
# wikitext_sha256 首次下载时计算

# Perplexity 参数
PPL_CHUNKS="${PPL_CHUNKS:-8}"
PPL_CTX="${PPL_CTX:-512}"
PPL_BATCH="${PPL_BATCH:-512}"

# 档位 + 量化(默认全矩阵;调试可缩 VARIANTS 但会破坏 NF4 对照)
VARIANTS="${VARIANTS:-naive norepack repack kleidiai_only kleidiai}"
QUANTS="${QUANTS:-q4_k_m q4_0 q8_0}"

# 模型缓存目录(避免重下)
MODEL_CACHE="${MODEL_CACHE:-$HOME/.cache/arm-infer-bench/models}"

# 时间戳(UTC,与 CI 一致)
TS=$(date -u +%Y%m%dT%H%M%SZ)

# 仓库根
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "================================================================"
echo " ArmInfer-Bench 一键基准 (run_bench.sh)"
echo " timestamp : $TS"
echo " threads   : $THREADS"
echo " pp/tg/reps: $PP / $TG / $REPS"
echo " variants  : $VARIANTS"
echo " quants    : $QUANTS"
echo " llama pin : $LLAMA_COMMIT"
echo " repo root : $REPO_ROOT"
echo "================================================================"

# ============================================================================
# 1. Fetch llama.cpp at pinned commit
# ============================================================================
echo "::group::Step 1: fetch llama.cpp"
bash scripts/fetch_llamacpp.sh "$LLAMA_COMMIT" third_party/llama.cpp
echo "::endgroup::"

# ============================================================================
# 2. Build 5 variants (serial, same machine — NF4 天然满足)
# ============================================================================
echo "::group::Step 2: build 5 variants"
for V in $VARIANTS; do
    echo "=== building variant: $V ==="
    bash scripts/build_variant.sh "$V" third_party/llama.cpp "third_party/llama.cpp/build-$V"
done
echo "::endgroup::"

# ============================================================================
# 3. Download GGUFs + wikitext + sha256 verify (缓存到 MODEL_CACHE)
# ============================================================================
echo "::group::Step 3: download GGUFs + wikitext"
mkdir -p models "$MODEL_CACHE"

# Helper: download + cache + symlink into models/
download_gguf() {
    local file="$1"
    local sha_var="$2"
    local cached="$MODEL_CACHE/$file"
    local link="models/$file"
    if [[ -f "$cached" ]]; then
        echo "cache hit: $cached"
    else
        local url="https://huggingface.co/${GGUF_REPO}/resolve/${GGUF_REV}/${file}"
        echo "downloading: $url"
        curl -fL --retry 5 --retry-delay 3 --connect-timeout 30 -o "$cached" "$url"
    fi
    ln -sf "$cached" "$link"
    local actual
    actual="$(sha256sum "$link" | awk '{print $1}')"
    echo "$file sha256: $actual"
    if [[ -n "${!sha_var:-}" ]]; then
        if [[ "$actual" != "${!sha_var}" ]]; then
            echo "::error::$file sha256 mismatch: expected ${!sha_var}, got $actual" >&2
            exit 1
        fi
    else
        # Q4_0/Q8_0: 首次下载记录 sha256(CI 行为一致)
        echo "${file}_sha256=$actual"
    fi
}

# Compute sha256 for Q4_0 / Q8_0 (CI computes on first download)
GGUF_SHA256_Q4_K_M="${GGUF_SHA256_Q4_K_M}" download_gguf "$GGUF_FILE_Q4_K_M" GGUF_SHA256_Q4_K_M
download_gguf "$GGUF_FILE_Q4_0" GGUF_SHA256_Q4_0
GGUF_SHA256_Q4_0="$(sha256sum "models/$GGUF_FILE_Q4_0" | awk '{print $1}')"
echo "exported: GGUF_SHA256_Q4_0=$GGUF_SHA256_Q4_0"
download_gguf "$GGUF_FILE_Q8_0" GGUF_SHA256_Q8_0
GGUF_SHA256_Q8_0="$(sha256sum "models/$GGUF_FILE_Q8_0" | awk '{print $1}')"
echo "exported: GGUF_SHA256_Q8_0=$GGUF_SHA256_Q8_0"

# Wikitext-2 test set
if [[ ! -f "$WIKITEXT_FILE" ]]; then
    echo "downloading wikitext-2 from $WIKITEXT_URL"
    curl -fL --retry 5 --retry-delay 3 --connect-timeout 30 -o "wikitext-2-raw-v1.zip" "$WIKITEXT_URL"
    unzip -o "wikitext-2-raw-v1.zip"
    rm -f "wikitext-2-raw-v1.zip"
fi
WIKITEXT_SHA256="$(sha256sum "$WIKITEXT_FILE" | awk '{print $1}')"
echo "wikitext sha256: $WIKITEXT_SHA256"
echo "::endgroup::"

# ============================================================================
# 4. Run 15 benchmarks (5 variants × 3 quants, -v for runtime probes — G1)
# ============================================================================
echo "::group::Step 4: 15 speed benchmarks"
mkdir -p bench_logs
for V in $VARIANTS; do
    for Q in $QUANTS; do
        case "$Q" in
            q4_k_m) MODEL="models/${GGUF_FILE_Q4_K_M}" ;;
            q4_0)   MODEL="models/${GGUF_FILE_Q4_0}" ;;
            q8_0)   MODEL="models/${GGUF_FILE_Q8_0}" ;;
        esac
        BENCH_BIN="third_party/llama.cpp/build-$V/bin/llama-bench"
        TAG="${V}__${Q}"
        echo "--- bench $TAG ---"
        # G1: raise log level + -v to surface repack/kleidiai DEBUG runtime evidence.
        # stderr holds BOTH time -v stats (printed at exit) AND llama-bench -v verbose log.
        set +e
        /usr/bin/time -v env GGML_LOG_LEVEL=DEBUG GGML_DEBUG=1 \
            "$BENCH_BIN" \
            -t "$THREADS" -p "$PP" -n "$TG" -r "$REPS" \
            -m "$MODEL" -o json -v \
            > "bench_logs/bench_${TAG}.json" 2> "bench_logs/all_${TAG}.stderr"
        RC=$?
        set -e
        if [ $RC -ne 0 ]; then
            echo "::error::llama-bench $TAG exited $RC" >&2
            echo "=== stderr ==="; cat "bench_logs/all_${TAG}.stderr" >&2
            exit $RC
        fi
        echo "  ok (rc=0)"
    done
done
echo "::endgroup::"

# ============================================================================
# 5. Run perplexity (naive × 3 quants + kleidiai-Q4_0 spot-check — G3/G4/G7)
# ============================================================================
echo "::group::Step 5: perplexity"
mkdir -p ppl_logs
PPL_SET="naive__q4_k_m naive__q4_0 naive__q8_0 kleidiai__q4_0"
for TAG in $PPL_SET; do
    V="${TAG%%__*}"; Q="${TAG##*__}"
    case "$Q" in
        q4_k_m) MODEL="models/${GGUF_FILE_Q4_K_M}" ;;
        q4_0)   MODEL="models/${GGUF_FILE_Q4_0}" ;;
        q8_0)   MODEL="models/${GGUF_FILE_Q8_0}" ;;
    esac
    PPL_BIN="third_party/llama.cpp/build-$V/bin/llama-perplexity"
    echo "--- perplexity $TAG ---"
    set +e
    /usr/bin/time -v "$PPL_BIN" \
        -m "$MODEL" -f "$WIKITEXT_FILE" \
        -c "$PPL_CTX" -b "$PPL_BATCH" -t "$THREADS" \
        --chunks "$PPL_CHUNKS" \
        > "ppl_logs/ppl_${TAG}.log" 2>&1
    RC=$?
    set -e
    if [ $RC -ne 0 ]; then
        echo "::error::llama-perplexity $TAG exited $RC" >&2
        cat "ppl_logs/ppl_${TAG}.log" >&2
        exit $RC
    fi
    echo "  ok (rc=0)"
done
echo "::endgroup::"

# ============================================================================
# 6. PMU availability probe (T3b Performix feasibility — non-blocking)
# ============================================================================
echo "::group::Step 6: PMU probe (non-blocking)"
{
    echo "=== /proc/bus/event_source/devices ==="
    ls /proc/bus/event_source/devices/ 2>/dev/null || echo "(none)"
    echo "=== /sys/bus/event_source/devices ==="
    ls /sys/bus/event_source/devices/ 2>/dev/null || echo "(none)"
    echo "=== arm_spe (Statistical Profiling Extension) ==="
    ls /sys/bus/event_source/devices/arm_spe* 2>/dev/null || echo "(no arm_spe)"
    echo "=== perf stat test (cycles + instructions) ==="
    perf stat -e cycles,instructions ls / >/dev/null 2>&1 && echo "perf_stat: OK" || echo "perf_stat: FAILED"
    echo "=== perf list hardware ==="
    perf list hw 2>/dev/null | head -20 || echo "(perf not available)"
} | tee pmu_probe.log
echo "pmu_probe.log written (non-blocking, conclusion parsed by assemble_results.py)"
echo "::endgroup::"

# ============================================================================
# 7. Phase 1+2: parse bench_logs + ppl_logs → 37/16-field JSONs
#    (与 bench.yml inline Python 同源;Phase 3+4 交给 assemble_results.py)
# ============================================================================
echo "::group::Step 7: Phase 1+2 parse (bench_logs → JSON)"
export TS GGUF_REV GGUF_FILE_Q4_K_M GGUF_FILE_Q4_0 GGUF_FILE_Q8_0
export GGUF_SHA256_Q4_K_M GGUF_SHA256_Q4_0 GGUF_SHA256_Q8_0
export BENCH_THREADS="$THREADS" BENCH_PP="$PP" BENCH_TG="$TG" BENCH_REPS="$REPS"
export PPL_CHUNKS PPL_CTX WIKITEXT_SHA256 WIKITEXT_FILE

python3 - <<'PYEOF'
import json, os, re, subprocess, datetime, pathlib, sys

def sh(cmd):
    return subprocess.check_output(cmd, shell=True, text=True).strip()

# --- CPU info + friendly name ---
cpuinfo = pathlib.Path('/proc/cpuinfo').read_text()
feats = impl = part = ''
for line in cpuinfo.splitlines():
    if line.startswith('Features'):         feats = line.split(':',1)[1].strip()
    elif line.startswith('CPU implementer'): impl = line.split(':',1)[1].strip().split()[-1]
    elif line.startswith('CPU part'):       part = line.split(':',1)[1].strip().split()[-1]
name_map = {('0x41','0xd49'): 'Neoverse-N2'}
cpu_name = name_map.get((impl, part), 'unknown')
cpu_model = f"{cpu_name} (implementer={impl} part={part})"

compiler = sh("cc --version | head -1")
runner_os = ""
for line in pathlib.Path('/etc/os-release').read_text().splitlines():
    if line.startswith('PRETTY_NAME='):
        runner_os = line.split('=',1)[1].strip().strip('"')
llama_commit = sh("git -C third_party/llama.cpp rev-parse HEAD")

pp_n = int(os.environ['BENCH_PP']); tg_n = int(os.environ['BENCH_TG'])
reps = int(os.environ['BENCH_REPS']); n_threads = int(os.environ['BENCH_THREADS'])
ttft_formula = "ttft_ms = pp_n / prefill_tok_s * 1000"

variants = ["naive","norepack","repack","kleidiai_only","kleidiai"]
quants   = ["q4_k_m","q4_0","q8_0"]
gguf_file = {"q4_k_m": os.environ['GGUF_FILE_Q4_K_M'],
             "q4_0":   os.environ['GGUF_FILE_Q4_0'],
             "q8_0":   os.environ['GGUF_FILE_Q8_0']}
gguf_sha  = {"q4_k_m": os.environ['GGUF_SHA256_Q4_K_M'],
             "q4_0":   os.environ.get('GGUF_SHA256_Q4_0',''),
             "q8_0":   os.environ.get('GGUF_SHA256_Q8_0','')}

ts = os.environ['TS']
pathlib.Path('results').mkdir(exist_ok=True)
records = {}

# ===== Phase 1: Assemble 15 speed JSONs =====
for V in variants:
    for Q in quants:
        tag = f"{V}__{Q}"
        raw_path = pathlib.Path(f"bench_logs/bench_{tag}.json")
        if not raw_path.exists():
            print(f"::error::bench log missing for {tag} — speed JSON is the lifeline, cannot proceed")
            sys.exit(1)
        raw = raw_path.read_text().strip()
        if not raw:
            print(f"::error::empty llama-bench output for {tag}"); sys.exit(1)
        rows = json.loads(raw)
        prefill_avg = prefill_sd = decode_avg = decode_sd = None
        for r in rows:
            avg_ts = r.get('avg_ts'); stddev_ts = r.get('stddev_ts')
            n_p = r.get('n_prompt',0) or 0; n_g = r.get('n_gen',0) or 0
            if avg_ts is None: continue
            if n_p > 0 and prefill_avg is None:
                prefill_avg = float(avg_ts); prefill_sd = float(stddev_ts or 0)
            elif n_g > 0 and decode_avg is None:
                decode_avg = float(avg_ts); decode_sd = float(stddev_ts or 0)
        if prefill_avg is None or decode_avg is None:
            print(f"::error::could not parse avg_ts for {tag}"); print(rows); sys.exit(1)

        stderr_txt = pathlib.Path(f"bench_logs/all_{tag}.stderr").read_text(errors='replace')
        m = re.search(r'Maximum resident set size \(kbytes\)\s*:\s*(\d+)', stderr_txt)
        if not m:
            print(f"::error::no peak RSS for {tag}"); sys.exit(1)
        peak_mem_mb = round(int(m.group(1))/1024.0, 2)

        for nm,avg,sd in (("prefill",prefill_avg,prefill_sd),("decode",decode_avg,decode_sd)):
            if avg>0 and sd/avg>0.10:
                print(f"::warning::{tag} {nm} stddev/avg={sd/avg*100:.2f}% >10%")

        # ===== Activation probes (G1) =====
        probe_build = pathlib.Path(f"third_party/llama.cpp/build-{V}/probe_build.txt").read_text(errors='replace')
        def probe_val(key):
            mt = re.search(rf'^{key}=(.*)$', probe_build, re.M)
            return mt.group(1).strip() if mt else ''
        nm_count = int(probe_val('kleidiai_compiled_nm_count') or 0)
        kleidiai_compiled = nm_count > 0
        repack_cmake = probe_val('repack_cmake_state')
        kleidiai_cmake = probe_val('kleidiai_cmake_state')

        repack_msgs = re.findall(r'repack tensor', stderr_txt, re.I)
        kleidiai_lines = [l for l in stderr_txt.splitlines() if re.search(r'kleidiai', l, re.I)]

        kleidiai_primary_kernel = any(re.search(r'kleidiai:\s*primary\s+(q4|q8)\s+kernel', l, re.I) for l in kleidiai_lines)
        kleidiai_no_compatible  = any(re.search(r'kleidiai:\s*no compatible\s+(q4|q8)\s+kernels', l, re.I) for l in kleidiai_lines)
        kleidiai_supported_quant = Q in ("q4_0", "q8_0")

        if not kleidiai_compiled:
            kleidiai_active = False; kleidiai_active_source = "no_kleidiai_in_build"
        elif not kleidiai_supported_quant:
            kleidiai_active = False; kleidiai_active_source = "no_runtime_takeover_kquant_noop"
        elif kleidiai_no_compatible:
            kleidiai_active = False; kleidiai_active_source = "no_compatible_kernel_for_cpu"
        elif kleidiai_primary_kernel:
            kleidiai_active = True; kleidiai_active_source = "verbose_log_primary_kernel"
        else:
            kleidiai_active = False; kleidiai_active_source = "inconclusive_no_primary_kernel_log"

        if repack_msgs:
            repack_active = True; repack_active_source = "verbose_log"
        elif repack_cmake == "ON":
            repack_active = True; repack_active_source = "cmake_inferred"
        else:
            repack_active = False; repack_active_source = "cmake_off"

        offloaded = None
        offloaded_source = "unavailable_in_build_log"

        model_size_mb = round(pathlib.Path('models', gguf_file[Q]).stat().st_size/(1024*1024), 2)
        ttft_ms = round(pp_n/prefill_avg*1000.0, 3)

        result = {
            "variant": V, "quant": Q.upper(),
            "model": "Qwen2.5-1.5B-Instruct",
            "model_revision": os.environ['GGUF_REV'],
            "model_sha256": gguf_sha[Q],
            "model_size_mb": model_size_mb,
            "bench_args": f"-t {n_threads} -p {pp_n} -n {tg_n} -r {reps}",
            "pp_n": pp_n, "tg_n": tg_n, "reps": reps,
            "prefill_tok_s": prefill_avg, "prefill_stddev": prefill_sd,
            "decode_tok_s": decode_avg, "decode_stddev": decode_sd,
            "ttft_ms": ttft_ms, "ttft_formula": ttft_formula,
            "peak_mem_mb": peak_mem_mb, "peak_mem_source": "time_v_maxrss",
            "n_threads": n_threads, "cpu_model": cpu_model, "cpu_features": feats,
            "compiler": compiler, "llama_commit": llama_commit, "runner_os": runner_os,
            "timestamp": ts,
            "kleidiai_compiled": kleidiai_compiled,
            "kleidiai_active": kleidiai_active,
            "kleidiai_tensors_offloaded": offloaded,
            "kleidiai_tensors_offloaded_source": offloaded_source,
            "repack_active": repack_active,
            "kleidiai_active_source": kleidiai_active_source,
            "repack_active_source": repack_active_source,
            "kleidiai_compiled_nm_count": nm_count,
            "repack_cmake_state": repack_cmake,
            "kleidiai_cmake_state": kleidiai_cmake,
        }
        out = pathlib.Path('results')/f"{ts}-{V}-{Q}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding='utf-8')
        records[(V,Q)] = result
        print(f"=== {tag}: pp={prefill_avg:.3f} tg={decode_avg:.3f} mem={peak_mem_mb:.1f} k_act={kleidiai_active} r_act={repack_active} ===")

# ===== G1 consistency assertion =====
for Q in quants:
    k = records.get(("kleidiai_only", Q)); n = records.get(("norepack", Q))
    if not (k and n and n['prefill_tok_s'] > 0):
        continue
    pp_ratio = k['prefill_tok_s'] / n['prefill_tok_s']
    tg_ratio = k['decode_tok_s']  / n['decode_tok_s']
    if k['kleidiai_active']:
        if abs(pp_ratio - 1.0) < 0.05 and abs(tg_ratio - 1.0) < 0.05:
            print(f"::error::G1 CONSISTENCY FAIL: kleidiai_only-{Q} kleidiai_active=True but speedup~=1 (pp={pp_ratio:.3f}, tg={tg_ratio:.3f})")
            sys.exit(1)
    else:
        if Q in ("q4_0", "q8_0") and (pp_ratio > 1.10 or tg_ratio > 1.10):
            print(f"::error::G1 CONSISTENCY FAIL: kleidiai_only-{Q} kleidiai_active=False but speedup>>1 (pp={pp_ratio:.3f}, tg={tg_ratio:.3f})")
            sys.exit(1)
print("=== G1 consistency check PASSED ===")

# ===== Phase 2: Parse perplexity logs =====
ppl_records = {}
ppl_set = [("naive","q4_k_m"), ("naive","q4_0"), ("naive","q8_0"), ("kleidiai","q4_0")]
ppl_chunks = int(os.environ.get('PPL_CHUNKS','8'))
ppl_ctx = int(os.environ.get('PPL_CTX','512'))
wiki_sha = os.environ.get('WIKITEXT_SHA256','')
for V, Q in ppl_set:
    tag = f"{V}__{Q}"
    log_path = pathlib.Path(f"ppl_logs/ppl_{tag}.log")
    if not log_path.exists():
        print(f"::warning::perplexity log missing for {tag} (step may have timed out) — PPL = N/A")
        continue
    log = log_path.read_text(errors='replace')
    m = re.search(r'Final estimate: PPL = ([\d.]+) \+/- ([\d.]+)', log)
    if not m:
        print(f"::warning::could not parse PPL for {tag} — PPL = N/A")
        continue
    ppl = float(m.group(1)); ppl_sd = float(m.group(2))
    ppl_records[(V,Q)] = {"perplexity": ppl, "perplexity_stddev": ppl_sd}
    ppl_json = {
        "variant": V, "quant": Q.upper(),
        "model": "Qwen2.5-1.5B-Instruct", "model_revision": os.environ['GGUF_REV'],
        "model_sha256": gguf_sha[Q],
        "model_size_mb": round(pathlib.Path('models', gguf_file[Q]).stat().st_size/(1024*1024), 2),
        "perplexity": ppl, "perplexity_stddev": ppl_sd,
        "perplexity_formula": f"Final estimate: PPL = X +/- Y (wikitext-2 test, --chunks {ppl_chunks} -c {ppl_ctx})",
        "n_chunks": ppl_chunks, "n_ctx": ppl_ctx,
        "chunks_tokens": ppl_chunks * ppl_ctx,
        "wikitext_sha256": wiki_sha,
        "llama_commit": llama_commit, "timestamp": ts,
    }
    pathlib.Path(f'results/{ts}-perplexity-{V}-{Q}.json').write_text(
        json.dumps(ppl_json, ensure_ascii=False, indent=2)+"\n", encoding='utf-8')
    print(f"=== perplexity {tag}: PPL={ppl:.4f} +/- {ppl_sd:.5f} ===")

print(f"=== Phase 1+2 DONE: {len(records)} speed JSONs + {len(ppl_records)} perplexity JSONs written to results/ ===")
PYEOF
echo "::endgroup::"

# ============================================================================
# 8. Phase 3+4: assemble_results.py (comparison + decision table + manifest + dashboard)
# ============================================================================
echo "::group::Step 8: assemble results (comparison + decision table + manifest + dashboard)"
python3 scripts/assemble_results.py "$TS" --pmu-log pmu_probe.log
echo "::endgroup::"

echo "================================================================"
echo " DONE: run_bench.sh complete"
echo " timestamp : $TS"
echo " results   : results/${TS}-*.json (15 speed + 4 PPL)"
echo "             results/${TS}-comparison-*.md (3)"
echo "             results/${TS}-decision-table.md"
echo "             results/manifest.json"
echo "             docs/data/dashboard.json"
echo "================================================================"
