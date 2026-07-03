#!/usr/bin/env python3
# scripts/bench_server.py
#
# T4b serving bench load generator — pure stdlib (M2: zero pip install).
#
# Spawns llama-server, warms up, runs 4 concurrency levels (1/2/4/6), produces
# 1 serving JSON. Strictly stdlib: http.client + ThreadPoolExecutor + manual SSE
# parsing. Same dependency level as assemble_results.py.
#
# Constraints honored:
#   M2  pure stdlib (no aiohttp/asyncio; clean arm64 runner runs directly)
#   M3  VmHWM read BEFORE killing server (proc/<pid>/status vanishes after kill)
#   Sa  S1 throughput soft-warning (peak(c>=2) > c=1), no hard assert / no crash
#   S1  wall-clock throughput = total_tokens / (max(t_end) - min(t_post))
#   S4  warm-up: 1 c=1 request, max_tokens=8, discarded
#   S5  TTFT p50/mean/max (no p99/p95; n=16 too small); max labeled "样本 N=16 的最大值"
#   S6  8 in-repo prompts; ctx=4096 >= 6x(512+128)+margin; server_args recorded
#   S7  completion_tokens prefer final chunk usage.completion_tokens, else chunk count
#   Sc  each prompt tokenize <= 512 (via /tokenize); >512 -> ::error:: + skip
#
# Usage:
#   python3 scripts/bench_server.py \
#     --variant naive --quant q4_0 \
#     --model models/<gguf> --server-bin third_party/llama.cpp/build-naive-server/bin/llama-server \
#     --concurrency 1,2,4,6 --max-tokens 128 --n-requests 16 \
#     --prompts scripts/serving_prompts.json \
#     --port 8080 --threads 4 \
#     --output results/<ts>-serving-naive-q4_0.json

import argparse
import hashlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone


# === Helpers ===

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_port_listening(host, port, timeout=0.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_health(host, port, timeout_s=60):
    """Poll /health until 200 (ready) or timeout. Returns True if ready."""
    deadline = time.monotonic() + timeout_s
    last_err = ""
    while time.monotonic() < deadline:
        try:
            conn = http.client.HTTPConnection(host, port, timeout=5)
            conn.request("GET", "/health")
            resp = conn.getresponse()
            resp.read()  # drain
            conn.close()
            if resp.status == 200:
                return True
            last_err = "HTTP {}".format(resp.status)
        except Exception as e:
            last_err = str(e)
        time.sleep(0.5)
    print("::warning::server /health not ready within {}s (last: {})".format(timeout_s, last_err))
    return False


def tokenize_prompt(host, port, content, timeout=10):
    """Sc: call /tokenize endpoint, return token count."""
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        body = json.dumps({"content": content}).encode("utf-8")
        conn.request("POST", "/tokenize", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = resp.read().decode("utf-8")
        conn.close()
        if resp.status != 200:
            return None, "HTTP {}".format(resp.status)
        obj = json.loads(data)
        tokens = obj.get("tokens")
        if not isinstance(tokens, list):
            return None, "no tokens field"
        return len(tokens), "ok"
    except Exception as e:
        return None, str(e)


def send_completion_streaming(host, port, prompt, max_tokens, timeout=120):
    """
    M2: pure-stdlib streaming POST to /completion.
    Returns dict with t_post, t_first_token, t_end, completion_tokens,
    token_count_source, error.
    """
    result = {
        "t_post": None,
        "t_first_token": None,
        "t_end": None,
        "completion_tokens": None,
        "token_count_source": None,
        "error": None,
    }
    body = json.dumps({
        "prompt": prompt,
        "n_predict": max_tokens,
        "stream": True,
        "temperature": 0,  # deterministic
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    t_post = time.monotonic()
    result["t_post"] = t_post

    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("POST", "/completion", body=body, headers=headers)
        resp = conn.getresponse()
        if resp.status != 200:
            result["error"] = "HTTP {} {}".format(resp.status, resp.read().decode("utf-8", "replace")[:200])
            result["t_end"] = time.monotonic()
            conn.close()
            return result

        first_token_seen = False
        usage_tokens = None
        chunk_count = 0

        # M2: manual SSE parse via readline on response.
        while True:
            line = resp.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            if not line.startswith(b"data:"):
                continue
            # strip "data: " or "data:"
            payload = line[5:].lstrip()
            if payload == b"[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except Exception:
                continue
            content = chunk.get("content")
            if content:
                chunk_count += 1
                if not first_token_seen:
                    result["t_first_token"] = time.monotonic()
                    first_token_seen = True
            # S7: usage.completion_tokens is authoritative (final chunk).
            usage = chunk.get("usage")
            if isinstance(usage, dict):
                ct = usage.get("completion_tokens")
                if isinstance(ct, int):
                    usage_tokens = ct

        result["t_end"] = time.monotonic()
        conn.close()

        # S7: prefer usage.completion_tokens; fallback to chunk count.
        if usage_tokens is not None:
            result["completion_tokens"] = usage_tokens
            result["token_count_source"] = "usage_field"
        else:
            result["completion_tokens"] = chunk_count
            result["token_count_source"] = "chunk_count"
    except Exception as e:
        result["t_end"] = time.monotonic()
        result["error"] = str(e)
    return result


def percentile(sorted_vals, p):
    """Simple percentile (p in 0-100). sorted_vals must be sorted ascending."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    # p50 = median
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def run_concurrency_level(host, port, prompts, c, n_requests, max_tokens):
    """
    Run n_requests at concurrency c. Returns measurement dict.
    S1: wall_clock_s = max(t_end) - min(t_post); throughput = total_tokens / wall_clock_s.
    S5: ttft p50/mean/max from per-request (t_first_token - t_post) in ms.
    """
    # Each task picks prompt[i % n_prompts].
    n_prompts = len(prompts)

    def task(i):
        p = prompts[i % n_prompts]
        return send_completion_streaming(host, port, p, max_tokens)

    results = []
    with ThreadPoolExecutor(max_workers=c) as pool:
        futures = [pool.submit(task, i) for i in range(n_requests)]
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                results.append({
                    "t_post": None, "t_first_token": None, "t_end": None,
                    "completion_tokens": None, "token_count_source": None,
                    "error": "task exception: {}".format(e),
                })

    # Sort by t_post for stable analysis (filter out fully failed).
    valid = [r for r in results if r["t_post"] is not None and r["t_end"] is not None]
    if not valid:
        return {
            "concurrency": c,
            "n_requests": n_requests,
            "n_valid": 0,
            "total_tokens": 0,
            "wall_clock_s": None,
            "throughput_tok_s": None,
            "ttft_p50_ms": None,
            "ttft_mean_ms": None,
            "ttft_max_ms": None,
            "ttft_max_label": "样本 N={} 的最大值".format(n_requests),
            "token_count_source": None,
            "warmup_done": True,
            "errors": [r.get("error") for r in results],
        }

    t_start_min = min(r["t_post"] for r in valid)
    t_end_max = max(r["t_end"] for r in valid)
    wall_clock_s = t_end_max - t_start_min
    total_tokens = sum((r["completion_tokens"] or 0) for r in valid)
    throughput = total_tokens / wall_clock_s if wall_clock_s > 0 else None

    # S5: TTFT samples (ms). Only requests with t_first_token.
    ttfts_ms = []
    for r in valid:
        if r["t_first_token"] is not None and r["t_post"] is not None:
            ttfts_ms.append((r["t_first_token"] - r["t_post"]) * 1000.0)
    ttfts_ms.sort()
    if ttfts_ms:
        ttft_p50 = percentile(ttfts_ms, 50)
        ttft_mean = sum(ttfts_ms) / len(ttfts_ms)
        ttft_max = ttfts_ms[-1]
    else:
        ttft_p50 = ttft_mean = ttft_max = None

    # token_count_source: majority among valid (or "mixed").
    sources = [r["token_count_source"] for r in valid if r["token_count_source"]]
    if sources:
        if len(set(sources)) == 1:
            tc_source = sources[0]
        else:
            tc_source = "mixed"
    else:
        tc_source = None

    n_errors = sum(1 for r in results if r.get("error"))

    return {
        "concurrency": c,
        "n_requests": n_requests,
        "n_valid": len(valid),
        "n_errors": n_errors,
        "total_tokens": total_tokens,
        "wall_clock_s": wall_clock_s,
        "throughput_tok_s": throughput,
        "ttft_p50_ms": ttft_p50,
        "ttft_mean_ms": ttft_mean,
        "ttft_max_ms": ttft_max,
        "ttft_max_label": "样本 N={} 的最大值".format(n_requests),
        "token_count_source": tc_source,
        "warmup_done": True,
    }


def read_vmhwm_kb(pid):
    """
    M3: read VmHWM (peak RSS, KB) from /proc/<pid>/status.
    MUST be called BEFORE killing the process (proc/<pid> vanishes after kill).
    Returns (vmhwm_kb_int_or_None, source_str).
    """
    try:
        with open("/proc/{}/status".format(pid), "r") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    parts = line.split()
                    # "VmHWM:\t12345 kB"
                    if len(parts) >= 2:
                        return int(parts[1]), "proc_vmhwm"
        return None, "unavailable"
    except Exception:
        return None, "unavailable"


def kill_server(proc, timeout_s=5):
    """M3: kill server gracefully. SIGINT -> wait -> SIGKILL if alive."""
    if proc is None:
        return
    if proc.poll() is not None:
        return  # already exited
    try:
        proc.send_signal(signal.SIGINT)
    except Exception:
        pass
    try:
        proc.wait(timeout=0.5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
        proc.wait(timeout=timeout_s)
    except Exception:
        pass


# === SIGTERM emergency write (step-timeout protection) ===
#
# CI #28667394594 root cause: serving step hit 15min timeout-minutes, GitHub Actions
# sent SIGTERM, Python's finally block did NOT execute (SIGTERM default-terminates the
# interpreter without unwinding), so 0 serving JSONs were written → dashboard got
# 0 serving_records. This handler is the double-insurance: on SIGTERM it performs an
# emergency write_output() before os._exit(1) so the merge step always finds a JSON
# (status=sigterm_killed / current status), even when the step is hard-cancelled.
#
# Python 3.5+ delivers signals to the main thread between bytecodes; if the main thread
# is blocked in a C extension (e.g. socket.read), the signal interrupts it and the
# handler runs. So this is reliable even during http.client readline().

_emergency_state = {
    'enabled': False,
}


def _sigterm_handler(signum, frame):
    """Emergency JSON write on SIGTERM (step timeout). Then os._exit(1)."""
    if not _emergency_state.get('enabled'):
        # Either not started yet, or finally already wrote. Safe exit.
        os._exit(1)
    print("::warning::SIGTERM received (likely step timeout) — emergency JSON write", flush=True)
    try:
        # Try to read VmHWM if server was started and we haven't yet.
        pid = _emergency_state.get('server_pid')
        if pid and _emergency_state.get('peak_mem_mb') is None:
            vmhwm_kb, src = read_vmhwm_kb(pid)
            if vmhwm_kb is not None:
                _emergency_state['peak_mem_mb'] = round(vmhwm_kb / 1024.0, 2)
                _emergency_state['peak_mem_source'] = src
        if _emergency_state.get('status') in (None, 'crashed'):
            _emergency_state['status'] = 'sigterm_killed'
        write_output(
            _emergency_state['args'],
            _emergency_state['server_args'],
            _emergency_state['prompt_set_sha256'],
            _emergency_state['n_prompts'],
            _emergency_state['prompt_token_counts'],
            _emergency_state['measurements'],
            _emergency_state['peak_mem_mb'],
            _emergency_state['peak_mem_source'],
            _emergency_state['ctx_size'],
            _emergency_state['max_concurrency'],
            _emergency_state['model_size_mb'],
            _emergency_state['model_sha256'],
            _emergency_state['status'],
        )
        print("::warning::emergency JSON write done (status={})".format(
            _emergency_state['status']), flush=True)
    except Exception as e:
        print("::error::emergency write failed: {}".format(e), flush=True)
    os._exit(1)


# === Main ===

def main():
    ap = argparse.ArgumentParser(description="T4b serving bench (pure stdlib, M2)")
    ap.add_argument("--variant", required=True, choices=["naive", "kleidiai"])
    ap.add_argument("--quant", required=True, choices=["q4_0", "q8_0"])
    ap.add_argument("--model", required=True)
    ap.add_argument("--server-bin", required=True)
    ap.add_argument("--concurrency", default="1,2,4,6")
    ap.add_argument("--max-tokens", type=int, default=128)
    ap.add_argument("--n-requests", type=int, default=16)
    ap.add_argument("--prompts", default="scripts/serving_prompts.json")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--ctx-size", type=int, default=4096)
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument("--output", required=True)
    ap.add_argument("--llama-commit", default="")
    args = ap.parse_args()

    concurrency_levels = [int(x) for x in args.concurrency.split(",")]
    max_concurrency = max(concurrency_levels)

    # S6: ctx-size sanity (should be >= max_c * (max_prompt + max_tokens) + margin).
    # 6 * (512 + 128) + 256 = 4096. We default ctx_size=4096.
    ctx_size = max(args.ctx_size, max_concurrency * (512 + args.max_tokens) + 256)
    if ctx_size != args.ctx_size:
        print("::warning::ctx-size bumped from {} to {} to fit max_concurrency budget".format(
            args.ctx_size, ctx_size))

    # 1. Load prompts + sha256.
    if not os.path.exists(args.prompts):
        print("::error::prompts file not found: {}".format(args.prompts))
        sys.exit(2)
    prompt_set_sha256 = sha256_file(args.prompts)
    with open(args.prompts) as f:
        prompts_doc = json.load(f)
    prompt_objs = prompts_doc["prompts"]
    prompts = [p["prompt"] for p in prompt_objs]
    n_prompts = len(prompts)

    # Model file info.
    model_size_mb = round(os.path.getsize(args.model) / (1024 * 1024), 2) if os.path.exists(args.model) else None
    model_sha256 = None  # not hashing GGUF here (assemble_results.py / fetch already did); leave null

    server_args = {
        "binary": args.server_bin,
        "host": args.host,
        "port": args.port,
        "threads": args.threads,
        "ctx_size": ctx_size,
        "parallel": args.parallel,
        "cont_batching": True,
        "max_tokens": args.max_tokens,
        "n_requests": args.n_requests,
    }

    # State variables (declared before try so except/finally can access them).
    measurements = []
    peak_mem_mb = None
    peak_mem_source = "unavailable"
    prompt_token_counts = []
    proc = None
    server_pid = None
    status = "crashed"  # default; set to "ok"/"server_unhealthy"/etc. on known paths

    # Register SIGTERM handler for step-timeout emergency write (double insurance).
    # _emergency_state holds write_output args; 'measurements' is a list reference so
    # the handler sees current contents even if SIGTERM arrives mid-concurrency-test.
    signal.signal(signal.SIGTERM, _sigterm_handler)
    _emergency_state.update({
        'enabled': True,
        'args': args,
        'server_args': server_args,
        'prompt_set_sha256': prompt_set_sha256,
        'n_prompts': n_prompts,
        'prompt_token_counts': prompt_token_counts,  # list ref, auto-updates
        'measurements': measurements,                # list ref, auto-updates
        'peak_mem_mb': None,
        'peak_mem_source': 'unavailable',
        'ctx_size': ctx_size,
        'max_concurrency': max_concurrency,
        'model_size_mb': model_size_mb,
        'model_sha256': model_sha256,
        'status': 'crashed',
        'server_pid': None,
    })

    try:
        # Port conflict guard.
        if is_port_listening(args.host, args.port):
            print("::warning::port {} occupied, attempting pkill -f llama-server".format(args.port))
            subprocess.run(["pkill", "-f", "llama-server"], check=False)
            time.sleep(1.0)
            if is_port_listening(args.host, args.port):
                raise RuntimeError("port {} still occupied after pkill".format(args.port))

        # 3. Start server (inside try so FileNotFoundError/PermissionError is caught).
        if not os.path.exists(args.server_bin):
            raise FileNotFoundError("server binary not found: {}".format(args.server_bin))
        server_cmd = [
            args.server_bin,
            "--model", args.model,
            "--host", args.host,
            "--port", str(args.port),
            "--threads", str(args.threads),
            "--ctx-size", str(ctx_size),
            "--parallel", str(args.parallel),
            "--cont-batching",
        ]
        print("::group::llama-server ({}/{})".format(args.variant, args.quant))
        print("cmd: " + " ".join(server_cmd))
        proc = subprocess.Popen(
            server_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        server_pid = proc.pid
        # Keep emergency state in sync so SIGTERM handler can read VmHWM if step
        # timeout fires while we're mid-bench.
        _emergency_state['server_pid'] = server_pid

        # 4. Wait for /health.
        if not wait_for_health(args.host, args.port, timeout_s=60):
            print("::error::server failed to become healthy; aborting {}".format(args.output))
            # M3: read VmHWM before kill (proc/<pid> vanishes after kill).
            vmhwm_kb, peak_mem_source = read_vmhwm_kb(server_pid)
            if vmhwm_kb is not None:
                peak_mem_mb = round(vmhwm_kb / 1024.0, 2)
            status = "server_unhealthy"
            # Mirror to emergency state (sys.exit raises SystemExit → finally runs; but
            # if SIGTERM lands in this narrow window, handler needs the right values).
            _emergency_state['peak_mem_mb'] = peak_mem_mb
            _emergency_state['peak_mem_source'] = peak_mem_source
            _emergency_state['status'] = status
            sys.exit(1)  # triggers finally (kill + drain + write_output)

        # Sc: tokenize each prompt, verify <= 512.
        print("::group::Sc prompt token budget check")
        over_budget = []
        for i, p in enumerate(prompts):
            n, msg = tokenize_prompt(args.host, args.port, p)
            prompt_token_counts.append(n)
            print("  prompt[{}] ({}) tokens={} {}".format(
                i, prompt_objs[i].get("id", "?"), n, msg))
            if n is not None and n > 512:
                over_budget.append((i, n))
        print("::endgroup::")
        if over_budget:
            for i, n in over_budget:
                print("::error::Sc: prompt[{}] tokenize={} > 512; would crowd max_tokens={}".format(
                    i, n, args.max_tokens))
            vmhwm_kb, peak_mem_source = read_vmhwm_kb(server_pid)
            if vmhwm_kb is not None:
                peak_mem_mb = round(vmhwm_kb / 1024.0, 2)
            status = "sc_prompt_over_512"
            _emergency_state['peak_mem_mb'] = peak_mem_mb
            _emergency_state['peak_mem_source'] = peak_mem_source
            _emergency_state['status'] = status
            sys.exit(1)  # triggers finally (kill + drain + write_output)

        # 5. Warm-up (S4): 1 c=1 request, max_tokens=8, discard.
        print("::group::warmup (S4)")
        warmup_result = send_completion_streaming(
            args.host, args.port, prompts[0], max_tokens=8, timeout=120)
        if warmup_result.get("error"):
            print("::warning::warmup request errored: {}".format(warmup_result["error"]))
        else:
            print("warmup done (tokens={}, source={})".format(
                warmup_result["completion_tokens"],
                warmup_result["token_count_source"]))
        print("::endgroup::")

        # 6. Run each concurrency level.
        c1_throughput = None
        peak_throughput_c_ge_2 = None
        for c in concurrency_levels:
            print("::group::concurrency c={}".format(c))
            m = run_concurrency_level(
                args.host, args.port, prompts, c, args.n_requests, args.max_tokens)
            measurements.append(m)
            print("  c={}: n_valid={}/{}, total_tokens={}, wall_clock={:.2f}s, throughput={:.2f} tok/s".format(
                c, m["n_valid"], m["n_requests"], m["total_tokens"],
                m["wall_clock_s"] or 0.0, m["throughput_tok_s"] or 0.0))
            print("  ttft p50={:.1f}ms mean={:.1f}ms max={:.1f}ms ({})".format(
                m["ttft_p50_ms"] or 0.0, m["ttft_mean_ms"] or 0.0,
                m["ttft_max_ms"] or 0.0, m["ttft_max_label"]))
            if m.get("n_errors"):
                print("  ::warning::{} requests errored at c={}".format(m["n_errors"], c))
            print("::endgroup::")

            if c == 1:
                c1_throughput = m["throughput_tok_s"]
            elif c >= 2:
                if peak_throughput_c_ge_2 is None or (m["throughput_tok_s"] or 0) > (peak_throughput_c_ge_2 or 0):
                    peak_throughput_c_ge_2 = m["throughput_tok_s"]

        # Sa: S1 self-check (soft warning, no crash).
        if c1_throughput is not None and peak_throughput_c_ge_2 is not None:
            if (peak_throughput_c_ge_2 or 0) <= (c1_throughput or 0):
                print("::warning::S1 串行化可疑: 并发峰值吞吐(c>=2)={:.2f} <= c=1 吞吐={:.2f}; slot 可能未批起来".format(
                    peak_throughput_c_ge_2 or 0.0, c1_throughput or 0.0))
            else:
                print("Sa S1 自测通过: 并发峰值吞吐(c>=2)={:.2f} > c=1 吞吐={:.2f} (batching 生效)".format(
                    peak_throughput_c_ge_2 or 0.0, c1_throughput or 0.0))

        # M3: read VmHWM BEFORE killing server (proc/<pid> vanishes after kill).
        vmhwm_kb, peak_mem_source = read_vmhwm_kb(server_pid)
        if vmhwm_kb is not None:
            peak_mem_mb = round(vmhwm_kb / 1024.0, 2)
            print("M3 VmHWM: {} kB = {} MB (source={})".format(vmhwm_kb, peak_mem_mb, peak_mem_source))
        else:
            print("::warning::VmHWM read failed (source={}); peak_mem_mb=null".format(peak_mem_source))
        # Mirror to emergency state so SIGTERM handler sees the same peak_mem.
        _emergency_state['peak_mem_mb'] = peak_mem_mb
        _emergency_state['peak_mem_source'] = peak_mem_source

        status = "ok"
        _emergency_state['status'] = 'ok'

    except SystemExit:
        # sys.exit(1) from known failure paths (status already set). Let finally run.
        raise
    except Exception as e:
        import traceback
        print("::error::bench_server crashed: {}".format(e))
        traceback.print_exc()
        # Try to read VmHWM if server was started.
        if server_pid is not None and peak_mem_mb is None:
            vmhwm_kb, peak_mem_source = read_vmhwm_kb(server_pid)
            if vmhwm_kb is not None:
                peak_mem_mb = round(vmhwm_kb / 1024.0, 2)
        # Mirror to emergency state (status stays "crashed").
        _emergency_state['peak_mem_mb'] = peak_mem_mb
        _emergency_state['peak_mem_source'] = peak_mem_source
        _emergency_state['status'] = status

    finally:
        # M3: kill server AFTER VmHWM read, BEFORE draining stdout. Order is iron law.
        # Killing the server unblocks the stdout pipe (proc.stdout.read() deadlocks if
        # the server is still alive — root cause of the first CI run producing 0 JSONs).
        if proc is not None:
            kill_server(proc)
            # Drain stdout AFTER kill (pipe unblocks when process exits).
            try:
                out, _ = proc.communicate(timeout=5)
                if out:
                    print("--- server output (tail 4000 chars) ---")
                    print(out[-4000:])
            except Exception:
                pass

        # G4: ALWAYS write a JSON record so the merge step can account for this
        # (variant, quant) even on failure. Without this, _merge_serving_to_dashboard.py
        # finds 0 files and dashboard.json gets 0 serving_records.
        write_output(args, server_args, prompt_set_sha256, n_prompts, prompt_token_counts,
                     measurements, peak_mem_mb, peak_mem_source, ctx_size,
                     max_concurrency, model_size_mb, model_sha256, status=status)
        print("wrote {} (status={}, measurements={}, peak_mem_mb={}, source={})".format(
            args.output, status, len(measurements), peak_mem_mb, peak_mem_source))
        # Disable emergency handler so a late SIGTERM doesn't double-write the JSON.
        _emergency_state['enabled'] = False

    # Non-zero exit on any failure path so CI's `|| echo "::warning::"` triggers.
    # (SystemExit from sys.exit(1) inside try already exits with code 1; this catches
    # the "crashed" path where the exception was caught and handled.)
    if status != "ok":
        sys.exit(1)


def write_output(args, server_args, prompt_set_sha256, n_prompts,
                 prompt_token_counts, measurements, peak_mem_mb, peak_mem_source,
                 ctx_size, max_concurrency, model_size_mb, model_sha256, status):
    out = {
        "variant": args.variant,
        "quant": args.quant,
        "model": args.model,
        "model_sha256": model_sha256,
        "model_size_mb": model_size_mb,
        "server_args": server_args,
        "prompt_set": args.prompts,
        "prompt_set_sha256": prompt_set_sha256,
        "n_prompts": n_prompts,
        "prompt_token_counts": prompt_token_counts,  # Sc
        "measurements": measurements,
        "peak_mem_mb": peak_mem_mb,                 # server-level (c=max peak)
        "peak_mem_source": peak_mem_source,         # M3: proc_vmhwm | unavailable
        "ctx_size": ctx_size,
        "max_concurrency": max_concurrency,
        "llama_commit": args.llama_commit,
        "timestamp": now_iso(),
        "status": status,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    main()
