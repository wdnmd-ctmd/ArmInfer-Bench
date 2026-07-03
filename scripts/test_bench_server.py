#!/usr/bin/env python3
# scripts/test_bench_server.py
#
# T4b P3③ smoke test for the serving merge injector (_merge_serving_to_dashboard.py).
# Creates synthetic serving JSON + minimal dashboard.json in a temp dir, runs the
# merge script, asserts serving_records injected + measurements count = 4 + dashboard
# still valid JSON. Prevents merge-script regression. <5s, no network, no llama-server.
#
# Run: python scripts/test_bench_server.py

import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MERGE_SCRIPT = os.path.join(REPO_ROOT, "scripts", "_merge_serving_to_dashboard.py")

failures = 0


def assert_true(cond, msg):
    global failures
    if cond:
        print("  PASS: " + msg)
    else:
        print("  FAIL: " + msg)
        failures += 1


def make_serving_json(variant, quant, ts):
    """Synthetic serving record with 4 measurements (c=1/2/4/6)."""
    return {
        "variant": variant,
        "quant": quant,
        "model": "Qwen2.5-1.5B-Instruct",
        "model_sha256": None,
        "model_size_mb": 1016.83,
        "server_args": {
            "binary": "build-{}-server/bin/llama-server".format(variant),
            "host": "127.0.0.1", "port": 8080, "threads": 4,
            "ctx_size": 4096, "parallel": 6, "cont_batching": True,
            "max_tokens": 128, "n_requests": 16,
        },
        "prompt_set": "scripts/serving_prompts.json",
        "prompt_set_sha256": "abc123",
        "n_prompts": 8,
        "prompt_token_counts": [42, 55, 60, 110, 70, 80, 50, 65],
        "measurements": [
            {
                "concurrency": c,
                "n_requests": 16,
                "n_valid": 16,
                "n_errors": 0,
                "total_tokens": 128 * 16,
                "wall_clock_s": 100.0 / c,
                "throughput_tok_s": 20.48 * c,
                "ttft_p50_ms": 50.0 + c * 10,
                "ttft_mean_ms": 55.0 + c * 10,
                "ttft_max_ms": 80.0 + c * 10,
                "ttft_max_label": "样本 N=16 的最大值",
                "token_count_source": "usage_field",
                "warmup_done": True,
            } for c in [1, 2, 4, 6]
        ],
        "peak_mem_mb": 1500.0 + (50 if variant == "kleidiai" else 0),
        "peak_mem_source": "proc_vmhwm",
        "ctx_size": 4096,
        "max_concurrency": 6,
        "llama_commit": "fabde3bf5136940eb03821aa2490e2360093965b",
        "timestamp": "20260703T000000Z",
        "status": "ok",
    }


def main():
    tmpdir = tempfile.mkdtemp(prefix="test_bench_server_")
    try:
        results_dir = os.path.join(tmpdir, "results")
        docs_dir = os.path.join(tmpdir, "docs", "data")  # _merge looks for <docs>/data/dashboard.json
        os.makedirs(results_dir)
        os.makedirs(docs_dir)

        ts = "20260703T000000Z"

        # Minimal dashboard.json with one run.
        dashboard = {
            "latest_timestamp": ts,
            "generated_at": "2026-07-03T00:00:00Z",
            "runs": {
                ts: {
                    "cpu_model": "Neoverse-N2 (test)",
                    "llama_commit": "fabde3bf5136940eb03821aa2490e2360093965b",
                    "speed_records": {},
                }
            },
        }
        dashboard_path = os.path.join(docs_dir, "dashboard.json")
        with open(dashboard_path, "w") as f:
            json.dump(dashboard, f)

        # Write 4 synthetic serving JSONs.
        expected_keys = []
        for v in ["naive", "kleidiai"]:
            for q in ["q4_0", "q8_0"]:
                rec = make_serving_json(v, q, ts)
                fname = "{}-serving-{}-{}.json".format(ts, v, q)
                with open(os.path.join(results_dir, fname), "w") as f:
                    json.dump(rec, f)
                expected_keys.append("{}-{}".format(v, q))

        print("=== Run merge script ===")
        result = subprocess.run(
            [sys.executable, MERGE_SCRIPT, ts,
             "--results-dir", results_dir,
             "--docs-dir", os.path.join(tmpdir, "docs")],
            capture_output=True, text=True,
        )
        print(result.stdout)
        if result.returncode != 0:
            print("STDERR:", result.stderr)
        assert_true(result.returncode == 0, "merge script exits 0")

        print("\n=== Assert serving_records injected ===")
        with open(dashboard_path) as f:
            merged = json.load(f)
        run = merged["runs"][ts]
        serving_records = run.get("serving_records", {})
        assert_true(isinstance(serving_records, dict), "serving_records is a dict")
        assert_true(len(serving_records) == 4, "4 serving records injected (got {})".format(len(serving_records)))

        for key in expected_keys:
            rec = serving_records.get(key)
            assert_true(rec is not None, "serving_records[{}] present".format(key))
            if rec:
                measurements = rec.get("measurements", [])
                assert_true(len(measurements) == 4, "{} has 4 measurements (got {})".format(key, len(measurements)))
                concurrencies = [m["concurrency"] for m in measurements]
                assert_true(concurrencies == [1, 2, 4, 6], "{} concurrencies = [1,2,4,6]".format(key))

        print("\n=== Assert dashboard.json still valid JSON + core fields intact ===")
        assert_true(merged["latest_timestamp"] == ts, "latest_timestamp unchanged")
        assert_true("speed_records" in run, "speed_records still present (merge didn't clobber)")
        assert_true(run["cpu_model"] == "Neoverse-N2 (test)", "cpu_model unchanged")

        summary = run.get("serving_summary", {})
        assert_true(summary.get("n_records") == 4, "serving_summary.n_records == 4")
        assert_true(summary.get("total_measurements") == 16, "serving_summary.total_measurements == 16")

        print("\n=== Assert missing-serving-files path (warning, not fail) ===")
        # Run merge again with a ts that has no serving files — should warn but not crash.
        result2 = subprocess.run(
            [sys.executable, MERGE_SCRIPT, "99999999T999999Z",
             "--results-dir", results_dir,
             "--docs-dir", os.path.join(tmpdir, "docs")],
            capture_output=True, text=True,
        )
        # Falls back to latest_timestamp (ts), finds the 4 serving files.
        assert_true(result2.returncode == 0, "merge with unknown ts falls back to latest_timestamp (exit 0)")

        print("\n=== Summary ===")
        if failures == 0:
            print("ALL PASS")
            sys.exit(0)
        else:
            print("{} FAILURE(S)".format(failures))
            sys.exit(1)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
