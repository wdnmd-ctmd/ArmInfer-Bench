#!/usr/bin/env python3
# scripts/_merge_serving_to_dashboard.py
#
# T4b S2: serving data injector. Reads results/<ts>-serving-*.json and injects
# them into docs/data/dashboard.json under runs[ts].serving_records.
#
# S2 core: assemble_results.py is FROZEN (golden-diff 0 lines). Serving is a
# "second-class citizen mounted on the dashboard" — this script runs AFTER
# assemble_results.py produces dashboard.json, mutates only the serving_records
# field, and writes back. Failure/timeout of serving does NOT affect core
# assembly (G4).
#
# Usage:
#   python3 scripts/_merge_serving_to_dashboard.py <timestamp> \
#       [--results-dir results] [--docs-dir docs]

import argparse
import glob
import json
import os
import sys


EXPECTED_CONCURRENCIES = 4  # 1/2/4/6
EXPECTED_VARIANTS = ["naive", "kleidiai"]
EXPECTED_QUANTS = ["q4_0", "q8_0"]


def main():
    ap = argparse.ArgumentParser(description="T4b S2: merge serving JSON into dashboard.json")
    ap.add_argument("timestamp", help="run timestamp (e.g. 20260702T175259Z)")
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--docs-dir", default="docs")
    args = ap.parse_args()

    ts = args.timestamp
    dashboard_path = os.path.join(args.docs_dir, "data", "dashboard.json")

    if not os.path.exists(dashboard_path):
        print("::error::dashboard.json not found at {} (run assemble_results.py first)".format(dashboard_path))
        sys.exit(2)

    with open(dashboard_path, "r", encoding="utf-8") as f:
        dashboard = json.load(f)

    if "runs" not in dashboard or not isinstance(dashboard["runs"], dict):
        print("::error::dashboard.json has no 'runs' object")
        sys.exit(2)

    # Resolve ts: prefer explicit arg, fallback to latest_timestamp.
    if ts not in dashboard["runs"]:
        latest = dashboard.get("latest_timestamp")
        if latest and latest in dashboard["runs"]:
            print("::warning::timestamp '{}' not in dashboard; falling back to latest_timestamp '{}'".format(ts, latest))
            ts = latest
        else:
            print("::error::timestamp '{}' not in dashboard.runs and no latest_timestamp fallback".format(ts))
            sys.exit(2)

    run = dashboard["runs"][ts]

    # Glob serving JSON files for this ts.
    pattern = os.path.join(args.results_dir, "{}-serving-*.json".format(ts))
    serving_files = sorted(glob.glob(pattern))
    print("glob: {} -> {} file(s)".format(pattern, len(serving_files)))

    serving_records = {}
    total_measurements = 0
    seen_keys = set()

    for path in serving_files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                rec = json.load(f)
        except Exception as e:
            print("::warning::failed to load {}: {}".format(path, e))
            continue

        v = rec.get("variant")
        q = rec.get("quant")
        if not v or not q:
            print("::warning::{} missing variant/quant; skipping".format(path))
            continue
        key = "{}-{}".format(v, q)
        if key in seen_keys:
            print("::warning::duplicate serving record for {}; using {}".format(key, path))
        seen_keys.add(key)

        measurements = rec.get("measurements") or []
        total_measurements += len(measurements)
        serving_records[key] = rec

        # P3①-style assert: measurements count should be EXPECTED_CONCURRENCIES.
        if len(measurements) != EXPECTED_CONCURRENCIES:
            print("::warning::{} has {} measurements (expected {}); keeping anyway (G4 non-fatal)".format(
                key, len(measurements), EXPECTED_CONCURRENCIES))

        print("  loaded {}: status={}, measurements={}, peak_mem_mb={}, source={}".format(
            key, rec.get("status"), len(measurements),
            rec.get("peak_mem_mb"), rec.get("peak_mem_source")))

    # Warn on missing expected (variant, quant) combinations.
    for v in EXPECTED_VARIANTS:
        for q in EXPECTED_QUANTS:
            key = "{}-{}".format(v, q)
            if key not in serving_records:
                print("::warning::missing serving record for {} (serving step may have failed/timed out)".format(key))

    # Inject serving_records into the run.
    run["serving_records"] = serving_records
    if total_measurements > 0:
        run["serving_summary"] = {
            "n_records": len(serving_records),
            "total_measurements": total_measurements,
            "expected_concurrency_levels": EXPECTED_CONCURRENCIES,
            "note": "serving data injected by _merge_serving_to_dashboard.py (S2; assemble_results.py frozen)"
        }
        # Bump generated_at to reflect the merge.
        from datetime import datetime, timezone
        run["serving_merged_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Write back dashboard.json (preserve indent=2, LF, ensure_ascii=False).
    with open(dashboard_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dashboard, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print("merged: serving_records={}, total_measurements={} -> {}".format(
        len(serving_records), total_measurements, dashboard_path))


if __name__ == "__main__":
    main()
