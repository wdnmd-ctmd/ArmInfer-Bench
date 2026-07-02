#!/usr/bin/env python3
"""scripts/test_assemble_results.py — P3③ 冒烟测试(防回归).

喂合成样本 JSON(15 speed + 4 perplexity,数字让 G1/G7/spot-check 都通过)给
assemble_results.py,断言产出非空 + 合法:
  - 3 份 comparison MD 非空 + 含 G1 section
  - decision-table.md 非空 + 含 Q8_0 headline / G5 / PMU section
  - manifest.json 合法 + P3① 列出的文件都真实存在
  - docs/data/dashboard.json 合法(P1 自包含)+ P2 headlines 3 项 + 15 speed + 4 ppl
  - P2 headlines verdict 与行为一致(Q8_0=kai_wins, Q4_0=tie, Q4_K_M=noop)

跑法:
  python scripts/test_assemble_results.py

自包含:不依赖 results/ 里的真实数据(清空 results/ 也能跑)。合成样本基于
schema(AGENTS.md 26 字段 + T2 探针 4 字段 + source 3 字段 + nm_count/cmake_state 3 字段)。
"""

import json
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).parent
REPO = HERE.parent
SCRIPT = HERE / "assemble_results.py"

VARIANTS = ["naive", "norepack", "repack", "kleidiai_only", "kleidiai"]
QUANTS = ["q4_k_m", "q4_0", "q8_0"]
PPL_SET = [("naive", "q4_k_m"), ("naive", "q4_0"), ("naive", "q8_0"), ("kleidiai", "q4_0")]

# 基准数字(让 G1 通过):
#   Q4_K_M: kleidiai_only ≈ norepack(no-op,差 <3%)
#   Q4_0/Q8_0: kleidiai_only 显著 > norepack(>5%,真接管)
#   Q4_0: kai vs repack 差 <5%(tie);Q8_0: kai > repack >5%(kai_wins)
BASE_DECODE = {
    "q4_k_m": {"naive": 20.0, "norepack": 25.0, "repack": 30.0, "kleidiai_only": 25.5, "kleidiai": 30.2},
    "q4_0":   {"naive": 18.0, "norepack": 22.0, "repack": 32.0, "kleidiai_only": 33.0, "kleidiai": 33.5},
    "q8_0":   {"naive": 15.0, "norepack": 19.0, "repack": 26.0, "kleidiai_only": 30.0, "kleidiai": 30.5},
}
BASE_PREFILL = {
    "q4_k_m": {"naive": 100.0, "norepack": 120.0, "repack": 140.0, "kleidiai_only": 121.0, "kleidiai": 141.0},
    "q4_0":   {"naive": 95.0,  "norepack": 115.0, "repack": 150.0, "kleidiai_only": 155.0, "kleidiai": 156.0},
    "q8_0":   {"naive": 80.0,  "norepack": 100.0, "repack": 130.0, "kleidiai_only": 145.0, "kleidiai": 146.0},
}
BASE_MEM = {
    "q4_k_m": {"naive": 1500, "norepack": 1600, "repack": 2700, "kleidiai_only": 1650, "kleidiai": 2750},
    "q4_0":   {"naive": 1450, "norepack": 1550, "repack": 2650, "kleidiai_only": 1853, "kleidiai": 2700},
    "q8_0":   {"naive": 1800, "norepack": 1900, "repack": 3100, "kleidiai_only": 2000, "kleidiai": 3150},
}
MODEL_SIZE_MB = {"q4_k_m": 987, "q4_0": 922, "q8_0": 1810}
PPL_BASE = {"q4_k_m": 11.5, "q4_0": 12.0, "q8_0": 10.5}


def make_speed_record(variant, quant, ts):
    """合成一份 speed JSON(符合 schema)."""
    decode = BASE_DECODE[quant][variant]
    prefill = BASE_PREFILL[quant][variant]
    mem = BASE_MEM[quant][variant]
    # G1: Q4_K_M 上 kleidiai_active=False(no-op);Q4_0/Q8_0 上 kleidiai_only/kleidiai=True
    kai_active = (quant in ("q4_0", "q8_0")) and variant in ("kleidiai_only", "kleidiai")
    kai_compiled = variant in ("kleidiai_only", "kleidiai")
    repack_active = variant in ("repack", "kleidiai")
    if not kai_active and quant == "q4_k_m":
        kai_src = "no_runtime_takeover_kquant_noop"
    elif kai_active:
        kai_src = "verbose_log_primary_kernel"
    else:
        kai_src = "verbose_log_no_kernel"
    return {
        "variant": variant,
        "quant": quant,
        "model": "Qwen2.5-1.5B-Instruct-GGUF",
        "model_revision": "91cad51170dc346986eccefdc2dd33a9da36ead9",
        "model_sha256": "deadbeef" * 8,
        "model_size_mb": MODEL_SIZE_MB[quant],
        "bench_args": "-t 4 -p 512 -n 128 -r 5",
        "pp_n": 512,
        "tg_n": 128,
        "reps": 5,
        "prefill_tok_s": prefill,
        "prefill_stddev": prefill * 0.01,
        "decode_tok_s": decode,
        "decode_stddev": decode * 0.02,
        "ttft_ms": 512 / prefill * 1000,
        "ttft_formula": "pp_n / prefill_tok_s * 1000",
        "peak_mem_mb": mem,
        "peak_mem_source": "time_v_maxrss",
        "n_threads": 4,
        "cpu_model": "Neoverse-N2 (implementer=0x41 part=0xd49)",
        "cpu_features": "fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm lrcpc dcpop asimddp i8mm sve2 svebf16 svei8mm",
        "compiler": "gcc (Ubuntu 13.2.0) 13.2.0",
        "llama_commit": "fabde3bf5136940eb03821aa2490e2360093965b",
        "runner_os": "Ubuntu 24.04 LTS",
        "timestamp": ts,
        # T2 探针(4 字段 + source 3 字段 + nm_count/cmake_state 3 字段)
        "kleidiai_compiled": kai_compiled,
        "kleidiai_active": kai_active,
        "kleidiai_tensors_offloaded": None,
        "repack_active": repack_active,
        "kleidiai_active_source": kai_src,
        "kleidiai_tensors_offloaded_source": "unavailable_in_build_log",
        "repack_active_source": "verbose_log_repack_tensor" if repack_active else "cmake_inferred_off",
        "kleidiai_compiled_nm_count": 447 if kai_compiled else 0,
        "repack_cmake_state": "ON" if repack_active else "OFF",
        "kleidiai_cmake_state": "ON" if kai_compiled else "OFF",
    }


def make_ppl_record(variant, quant, ts):
    """合成一份 perplexity JSON(G7: 4 份 n_chunks/n_ctx/wikitext_sha256 一致;
    spot-check: kleidiai-Q4_0 vs naive-Q4_0 diff <2%)."""
    ppl = PPL_BASE[quant]
    if variant == "kleidiai" and quant == "q4_0":
        ppl = PPL_BASE[quant] * 1.005  # diff 0.5% < 2% spot-check PASS
    return {
        "variant": variant,
        "quant": quant,
        "model": "Qwen2.5-1.5B-Instruct-GGUF",
        "model_revision": "91cad51170dc346986eccefdc2dd33a9da36ead9",
        "model_sha256": "deadbeef" * 8,
        "model_size_mb": MODEL_SIZE_MB[quant],
        "perplexity": ppl,
        "perplexity_stddev": 0.65,
        "perplexity_formula": "llama-perplexity Final estimate PPL = X +/- Y",
        "n_chunks": 8,
        "n_ctx": 512,
        "chunks_tokens": 4096,
        "wikitext_sha256": "abc123def456" * 4,  # G7 一致
        "llama_commit": "fabde3bf5136940eb03821aa2490e2360093965b",
        "timestamp": ts,
    }


def run_test():
    ts = "20260702T999999Z"  # test timestamp(不会与真实 CI 产出冲突)
    passed = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_results = pathlib.Path(tmp) / "results"
        tmp_docs = pathlib.Path(tmp) / "docs"
        tmp_results.mkdir()
        tmp_docs.mkdir()

        # 写 15 份 speed JSON + 4 份 ppl JSON
        for V in VARIANTS:
            for Q in QUANTS:
                rec = make_speed_record(V, Q, ts)
                fpath = tmp_results / f"{ts}-{V}-{Q}.json"
                fpath.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        for V, Q in PPL_SET:
            rec = make_ppl_record(V, Q, ts)
            fpath = tmp_results / f"{ts}-perplexity-{V}-{Q}.json"
            fpath.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

        # 写 pmu_probe.log(模拟 T3b 实测:PMU 设备在但 SPE 不在 + perf stat 被拦)
        pmu_log = pathlib.Path(tmp) / "pmu_probe.log"
        pmu_log.write_text(
            "/sys/bus/event_source/devices: armv8_pmuv3_0\n"
            "arm_spe: (no arm_spe)\n"
            "perf stat: FAILED (<not supported>)\n",
            encoding="utf-8",
        )

        # subprocess 调用 assemble_results.py(端到端,真实 CLI 接口)
        cmd = [
            sys.executable, str(SCRIPT), ts,
            "--results-dir", str(tmp_results),
            "--docs-dir", str(tmp_docs),
            "--pmu-log", str(pmu_log),
        ]
        print(f"=== Running: {' '.join(cmd)} ===")
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO))
        print("=== STDOUT ===")
        print(result.stdout)
        if result.stderr:
            print("=== STDERR ===")
            print(result.stderr)
        assert result.returncode == 0, f"assemble_results.py exited {result.returncode}"

        # 1. 3 份 comparison MD 非空 + 合法
        for Q in QUANTS:
            f = tmp_results / f"{ts}-comparison-{Q}.md"
            assert f.exists(), f"missing {f.name}"
            text = f.read_text(encoding="utf-8")
            assert len(text) > 100, f"{f.name} too short ({len(text)} bytes)"
            assert "同机对照表" in text, f"{f.name} missing header"
            assert "G1 行为交叉验证" in text, f"{f.name} missing G1 section"
        print("✓ 3 comparison MD files non-empty + valid")
        passed += 1

        # 2. decision-table.md 非空 + 含关键 section
        dt = tmp_results / f"{ts}-decision-table.md"
        assert dt.exists(), "missing decision-table.md"
        dt_text = dt.read_text(encoding="utf-8")
        assert len(dt_text) > 200, "decision-table.md too short"
        assert "选哪个量化" in dt_text
        assert "Q8_0 KleidiAI vs repack" in dt_text
        assert "G5 内存 tie-break" in dt_text
        assert "PMU 探针实测结论" in dt_text
        print("✓ decision-table.md non-empty + valid (Q8_0 headline + G5 + PMU sections present)")
        passed += 1

        # 3. manifest.json 合法 + P3① 列出的文件都真实存在
        mf = tmp_results / "manifest.json"
        assert mf.exists(), "missing manifest.json"
        manifest = json.loads(mf.read_text(encoding="utf-8"))
        assert manifest["latest_timestamp"] == ts
        assert ts in manifest["runs"]
        run = manifest["runs"][ts]
        listed = run["speed_files"] + run["perplexity_files"] + run["comparisons"] + [run["decision_table"]]
        for fname in listed:
            assert (tmp_results / fname).exists(), f"manifest P3① fail: {fname} not found in {tmp_results}"
        print(f"✓ manifest.json valid + P3① assert passed ({len(listed)} listed files all exist on disk)")
        passed += 1

        # 4. docs/data/dashboard.json(P1 自包含)
        dj = tmp_docs / "data" / "dashboard.json"
        assert dj.exists(), "missing docs/data/dashboard.json"
        dashboard = json.loads(dj.read_text(encoding="utf-8"))
        assert dashboard["latest_timestamp"] == ts
        assert ts in dashboard["runs"]
        run = dashboard["runs"][ts]
        # P2: headlines 3 项
        assert "headlines" in run, "dashboard missing headlines"
        for Q in QUANTS:
            assert Q in run["headlines"], f"missing headlines[{Q}]"
            h = run["headlines"][Q]
            assert h["verdict"] in ("noop", "tie", "kai_wins", "repack_wins", "missing"), \
                f"bad verdict {h['verdict']} for {Q}"
            assert "narrative" in h and len(h["narrative"]) > 0, f"empty narrative for {Q}"
        # speed_records 15 项 + perplexity_records 4 项
        assert len(run["speed_records"]) == 15, \
            f"expected 15 speed_records, got {len(run['speed_records'])}"
        assert len(run["perplexity_records"]) == 4, \
            f"expected 4 perplexity_records, got {len(run['perplexity_records'])}"
        # decision_table_md + comparisons_md 3 份
        assert "decision_table_md" in run and len(run["decision_table_md"]) > 100
        assert len(run["comparisons_md"]) == 3
        print("✓ docs/data/dashboard.json valid (P1 self-contained + P2 headlines 3 + 15 speed + 4 ppl)")
        passed += 1

        # 5. P2 单一计算验证:headlines verdict 与行为一致
        h_q8 = dashboard["runs"][ts]["headlines"]["q8_0"]
        assert h_q8["verdict"] == "kai_wins", \
            f"Q8_0 should be kai_wins (30.0 vs 26.0 = +15.4%), got {h_q8['verdict']}"
        h_q4 = dashboard["runs"][ts]["headlines"]["q4_0"]
        assert h_q4["verdict"] == "tie", \
            f"Q4_0 should be tie (33.0 vs 32.0 = +3.1% <5%), got {h_q4['verdict']}"
        h_qkm = dashboard["runs"][ts]["headlines"]["q4_k_m"]
        assert h_qkm["verdict"] == "noop", \
            f"Q4_K_M should be noop (kai_active=False), got {h_qkm['verdict']}"
        print(f"✓ P2 headlines single-source computation matches behavior "
              f"(Q8_0=kai_wins +{h_q8['decode_diff_pct']:.1f}%, "
              f"Q4_0=tie, Q4_K_M=noop)")
        passed += 1

        print(f"\n=== ALL {passed} SMOKE TEST ASSERTIONS PASSED ===")
        return 0


if __name__ == "__main__":
    sys.exit(run_test())
