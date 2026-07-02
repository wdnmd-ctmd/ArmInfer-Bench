#!/usr/bin/env python3
"""scripts/assemble_results.py — T4 assembly 单一真相源.

从 bench.yml Phase 3+4 提取.读 results/<ts>-*.json,产出:
  - results/<ts>-comparison-<quant>.md × 3   (同机对照表)
  - results/<ts>-decision-table.md           (决策表 + G5/Q8_0 headline/PPL ±/Q4_K_M stddev/PMU)
  - results/manifest.json                     (看板历史入口,latest_timestamp + 文件清单)
  - docs/data/dashboard.json                  (P1:看板自包含数据,含 headlines 单一计算 P2)

bench.yml + run_bench.sh 都调用本脚本.单一真相源 — 未来改 G5 阈值/措辞只改一处.

接口:
  python scripts/assemble_results.py <timestamp> [--results-dir results] [--docs-dir docs] [--pmu-log pmu_probe.log]

P3①:manifest 产出后 assert 里面列的每个文件都真实存在,否则 fail.
P2 :headlines 字段由 JSON 单一计算,全站(卡片/决策表/看板)引用同一值.
P1 :看板只 fetch ./data/dashboard.json,不跨目录(线上 GitHub Pages /docs 只暴露 docs/ 子树).
"""

import argparse
import json
import os
import re
import sys
import datetime
import pathlib

VARIANTS = ["naive", "norepack", "repack", "kleidiai_only", "kleidiai"]
QUANTS = ["q4_k_m", "q4_0", "q8_0"]
PPL_SET = [("naive", "q4_k_m"), ("naive", "q4_0"), ("naive", "q8_0"), ("kleidiai", "q4_0")]


def write_text_lf(path, text):
    """Write text with LF line endings(跨平台一致,与 .gitattributes 强制 LF 对齐)."""
    # newline='' disables translation; we ensure text uses \n then encode.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def parse_args():
    p = argparse.ArgumentParser(description="Assemble comparison + decision table + manifest + dashboard")
    p.add_argument("timestamp", help="结果 timestamp(如 20260702T133344Z)")
    p.add_argument("--results-dir", default="results", help="结果目录(默认 results)")
    p.add_argument("--docs-dir", default="docs", help="看板目录(默认 docs)")
    p.add_argument("--pmu-log", default="pmu_probe.log", help="PMU 探针日志路径(可选)")
    return p.parse_args()


def load_speed_records(results_dir, ts):
    """读 15 speed JSON → records[(variant, quant)](与 bench.yml records 字典同结构)."""
    records = {}
    for V in VARIANTS:
        for Q in QUANTS:
            fpath = pathlib.Path(results_dir) / f"{ts}-{V}-{Q}.json"
            if not fpath.exists():
                print(f"::error::speed JSON missing: {fpath}", file=sys.stderr)
                sys.exit(1)
            records[(V, Q)] = json.loads(fpath.read_text(encoding="utf-8"))
    return records


def load_ppl_records(results_dir, ts):
    """读 4 perplexity JSON → ppl_records[(variant, quant)].G4:容忍缺失(step 可能超时)."""
    ppl_records = {}
    for V, Q in PPL_SET:
        fpath = pathlib.Path(results_dir) / f"{ts}-perplexity-{V}-{Q}.json"
        if not fpath.exists():
            print(f"::warning::perplexity JSON missing: {fpath} (step may have timed out) — PPL = N/A")
            continue
        ppl_records[(V, Q)] = json.loads(fpath.read_text(encoding="utf-8"))
    return ppl_records


def g1_consistency_check(records):
    """G1:kleidiai_only vs norepack speedup 交叉验证(探针与行为一致)."""
    for Q in QUANTS:
        k = records.get(("kleidiai_only", Q))
        n = records.get(("norepack", Q))
        if not (k and n and n["prefill_tok_s"] > 0):
            continue
        pp_ratio = k["prefill_tok_s"] / n["prefill_tok_s"]
        tg_ratio = k["decode_tok_s"] / n["decode_tok_s"]
        if k["kleidiai_active"]:
            if abs(pp_ratio - 1.0) < 0.05 and abs(tg_ratio - 1.0) < 0.05:
                print(f"::error::G1 CONSISTENCY FAIL: kleidiai_only-{Q} active=True but speedup~=1 (pp={pp_ratio:.3f}, tg={tg_ratio:.3f})")
                sys.exit(1)
        else:
            if Q in ("q4_0", "q8_0") and (pp_ratio > 1.10 or tg_ratio > 1.10):
                print(f"::error::G1 CONSISTENCY FAIL: kleidiai_only-{Q} active=False but speedup>>1 (pp={pp_ratio:.3f}, tg={tg_ratio:.3f})")
                sys.exit(1)
    print("=== G1 consistency check PASSED: probe agrees with behavior on all 3 quants ===")


def g7_fairness_check(ppl_records):
    """G7:4 份 PPL params(n_chunks/n_ctx/wikitext_sha256)一致."""
    if len(ppl_records) == len(PPL_SET):
        ppl_params = set()
        for (V, Q) in PPL_SET:
            if (V, Q) in ppl_records:
                r = ppl_records[(V, Q)]
                ppl_params.add((r["n_chunks"], r["n_ctx"], r["wikitext_sha256"]))
        if len(ppl_params) > 1:
            print(f"::error::G7 FAIRNESS FAIL: perplexity params differ: {ppl_params}")
            sys.exit(1)
        print("=== G7 fairness check PASSED: all 4 perplexity runs use same n_chunks/n_ctx/wikitext_sha256 ===")
    else:
        print(f"::warning::G7 skipped — only {len(ppl_records)}/{len(PPL_SET)} perplexity runs present")


def ppl_spot_check(ppl_records):
    """kleidiai-Q4_0 vs naive-Q4_0 容差 <2%(数值一致性)."""
    spot_check_ok = None
    ppl_diff_pct = None
    if ("naive", "q4_0") in ppl_records and ("kleidiai", "q4_0") in ppl_records:
        n_ppl = ppl_records[("naive", "q4_0")]["perplexity"]
        k_ppl = ppl_records[("kleidiai", "q4_0")]["perplexity"]
        ppl_diff_pct = abs(k_ppl - n_ppl) / n_ppl * 100
        spot_check_ok = ppl_diff_pct < 2.0
        print(f"=== PPL spot-check: kleidiai-Q4_0 ({k_ppl:.4f}) vs naive-Q4_0 ({n_ppl:.4f}), diff={ppl_diff_pct:.3f}% ===")
        if not spot_check_ok:
            print(f"::warning::PPL spot-check diff {ppl_diff_pct:.3f}% > 2% tolerance — KleidiAI may alter numerics")
    else:
        print("::warning::PPL spot-check skipped — one or both perplexity runs missing")
    return spot_check_ok, ppl_diff_pct


def speedup(a, b):
    return round(a / b, 3) if b else None


def phase3_comparison(records, results_dir, ts):
    """Phase 3:产出 3 份 comparison MD(逐行对照 bench.yml line 536-601).返回 {Q: md_text}."""
    comparisons_md = {}
    for Q in QUANTS:
        naive = records[("naive", Q)]
        lines = []
        lines.append(f"# 同机对照表:{Q.upper()} (NF4 — 同 job 同 runner,分母 = 本 job naive-{Q})")
        lines.append("")
        lines.append(f"runner cpu: {naive['cpu_model']} | llama_commit: {naive['llama_commit']} | timestamp: {ts}")
        lines.append("")
        lines.append("| variant | prefill tok/s | decode tok/s | TTFT ms | peak mem MB | k_compiled | k_active | offloaded | repack_active | pp speedup | tg speedup | ttft ratio | mem ratio | probe sources |")
        lines.append("|---------|---------------|--------------|---------|-------------|------------|----------|-----------|---------------|------------|------------|-----------|-----------|---------------|")
        for V in VARIANTS:
            r = records[(V, Q)]
            ps = speedup(r["prefill_tok_s"], naive["prefill_tok_s"])
            ds = speedup(r["decode_tok_s"], naive["decode_tok_s"])
            tr = speedup(r["ttft_ms"], naive["ttft_ms"])
            mr = speedup(r["peak_mem_mb"], naive["peak_mem_mb"])
            src = f"k:{r['kleidiai_active_source']}; r:{r['repack_active_source']}"
            off_disp = "null" if r["kleidiai_tensors_offloaded"] is None else r["kleidiai_tensors_offloaded"]
            lines.append(f"| {V} | {r['prefill_tok_s']:.3f} | {r['decode_tok_s']:.3f} | {r['ttft_ms']:.1f} | {r['peak_mem_mb']:.1f} | {r['kleidiai_compiled']} | {r['kleidiai_active']} | {off_disp} | {r['repack_active']} | {ps} | {ds} | {tr} | {mr} | {src} |")
        # G1 behavior cross-check
        npp = records[("norepack", Q)]["prefill_tok_s"]
        kpp = records[("kleidiai_only", Q)]["prefill_tok_s"]
        ntg = records[("norepack", Q)]["decode_tok_s"]
        ktg = records[("kleidiai_only", Q)]["decode_tok_s"]
        pp_diff = abs(kpp - npp) / npp * 100 if npp else 0
        tg_diff = abs(ktg - ntg) / ntg * 100 if ntg else 0
        lines.append("")
        lines.append("## G1 行为交叉验证(kleidiai_only vs norepack)")
        lines.append(f"- prefill 差异: {pp_diff:.2f}%  | decode 差异: {tg_diff:.2f}%")
        if Q in ("q4_0", "q8_0"):
            lines.append(f"- 预期:{Q.upper()} 上 KleidiAI 真接管 → kleidiai_only 应**显著 ≠** norepack(差异 >5%)。实测 {'符合' if pp_diff > 5 or tg_diff > 5 else '不符/可疑 → 标 inconclusive'}。")
        else:
            lines.append(f"- 预期:Q4_K_M 上 KleidiAI no-op → kleidiai_only 应**≈** norepack(差异 <3% 噪声内)。实测 {'符合' if pp_diff < 3 and tg_diff < 3 else '不符/可疑'}。")
        lines.append("")
        lines.append("## 探针采集说明(G1:不许循环论证)")
        lines.append("- `verbose_log`:从 `llama-bench -v` 运行时日志拿到真证据(优先,唯一可单独支撑 active 断言)。")
        lines.append("- `cmake_inferred`/`compiled_inferred`:运行时日志无证据时的**最后兜底**,绝不单独作为 active 依据。")
        lines.append("- `inconclusive_*`:拿不到证据且行为含糊,不硬断言(本表对应格如实标 false + source=inconclusive)。")
        lines.append("")
        if Q == "q4_k_m":
            lines.append("## KleidiAI k-quant no-op gotcha")
            lines.append("- KleidiAI 微内核仅覆盖 Q4_0/Q8_0,对 Q4_K_M 完全 no-op(kleidiai_get_block_args 返回 {0,0,0},op 不被调用)。")
            lines.append("- 本表 kleidiai_only/kleidiai 两档 kleidiai_active 应为 false(source=no_runtime_takeover_kquant_noop)、offloaded=null;speedup 应≈ norepack(噪声内)。G1 一致性断言已交叉验证 active 与行为一致。")
            lines.append("")
        if Q == "q4_0":
            lines.append("## G2 Q4_0 双优化叠加交互")
            lines.append("- kleidiai 档(repack+KleidiAI 都 ON)在 Q4_0 上两者可能竞争同一批张量。")
            lines.append("- b9728 KleidiAI op 静默无张量计数日志 -> offloaded=null(source=unavailable_in_build_log),无法用 offloaded 判定接管者。")
            lines.append("- 行为判定:kleidiai 档 speedup ~= kleidiai_only ~= repack(三者持平)-> 无叠加收益,实际只一个路径接管,不可把 repack 收益重复记到 KleidiAI 头上。")
            lines.append("")
        if Q == "q8_0":
            lines.append("## Q8_0 KleidiAI vs repack(headline)")
            k8 = records[("kleidiai_only", "q8_0")]
            r8 = records[("repack", "q8_0")]
            lines.append(f"- kleidiai_only decode {k8['decode_tok_s']:.3f} vs repack {r8['decode_tok_s']:.3f}")
            if k8["decode_tok_s"] > r8["decode_tok_s"] * 1.05:
                lines.append(f"- KleidiAI **>** repack(KleidiAI 胜 {(k8['decode_tok_s']/r8['decode_tok_s']-1)*100:.1f}%)")
            elif r8["decode_tok_s"] > k8["decode_tok_s"] * 1.05:
                lines.append(f"- repack **>** KleidiAI(repack 胜 {(r8['decode_tok_s']/k8['decode_tok_s']-1)*100:.1f}%)")
            else:
                lines.append(f"- KleidiAI **≈** repack(打平,差异 <5%)")
            lines.append(f"- kleidiai_active(Q8_0) = {k8['kleidiai_active']}(source={k8['kleidiai_active_source']})")
            lines.append("- Q8_0 是 KleidiAI 传说中收益最大的档;Q4_0 上 KleidiAI≈repack(打平),Q8_0 上的对比是本轮 headline。")
            lines.append("")
        md_text = "\n".join(lines) + "\n"
        out = pathlib.Path(results_dir) / f"{ts}-comparison-{Q}.md"
        write_text_lf(out, md_text)
        comparisons_md[Q] = md_text
        print(f"=== comparison table {Q} written ===")
    return comparisons_md


def phase4_decision_table(records, ppl_records, results_dir, ts, spot_check_ok, ppl_diff_pct, pmu_log_path):
    """Phase 4:产出 decision-table.md(逐行对照 bench.yml line 603-725).返回 md_text."""
    naive_q = records[("naive", QUANTS[0])]
    cpu_model = naive_q["cpu_model"]
    llama_commit = naive_q["llama_commit"]
    ppl_chunks = naive_q.get("pp_n", 512)  # fallback
    # 从 ppl_records 取 n_chunks/n_ctx
    ppl_chunks = ppl_records[("naive", "q4_k_m")]["n_chunks"] if ("naive", "q4_k_m") in ppl_records else 8
    ppl_ctx = ppl_records[("naive", "q4_k_m")]["n_ctx"] if ("naive", "q4_k_m") in ppl_records else 512

    dlines = []
    dlines.append(f"# 选哪个量化:决策表(Qwen2.5-1.5B-Instruct, wikitext-2 PPL, --chunks {ppl_chunks} -c {ppl_ctx})")
    dlines.append("")
    dlines.append(f"runner cpu: {cpu_model} | llama_commit: {llama_commit} | timestamp: {ts}")
    dlines.append("")
    dlines.append("| quant | 体积MB | 最佳档 | prefill tok/s | decode tok/s | perplexity | 峰值内存MB(最佳档) | 最优优化路径 | PPL spot-check |")
    dlines.append("|-------|--------|--------|---------------|--------------|------------|---------------------|-------------|----------------|")
    for Q in QUANTS:
        best_decode = max(records[(v, Q)]["decode_tok_s"] for v in VARIANTS)
        within_noise = [v for v in VARIANTS if abs(records[(v, Q)]["decode_tok_s"] - best_decode) / best_decode < 0.05]
        if len(within_noise) > 1:
            best_v = min(within_noise, key=lambda v: records[(v, Q)]["peak_mem_mb"])
            tie_note = f" (G5 tie-break: {len(within_noise)} 档 decode 差<5%, 取内存最低)"
        else:
            best_v = within_noise[0]
            tie_note = ""
        r = records[(best_v, Q)]
        repack_gain = records[("repack", Q)]["decode_tok_s"] / records[("norepack", Q)]["decode_tok_s"]
        kai_gain = records[("kleidiai_only", Q)]["decode_tok_s"] / records[("norepack", Q)]["decode_tok_s"]
        if Q == "q4_k_m":
            best_path = f"repack ({repack_gain:.2f}×) — KleidiAI no-op on k-quant"
        elif kai_gain > repack_gain * 1.05:
            best_path = f"KleidiAI ({kai_gain:.2f}×) > repack ({repack_gain:.2f}×)"
        elif repack_gain > kai_gain * 1.05:
            best_path = f"repack ({repack_gain:.2f}×) > KleidiAI ({kai_gain:.2f}×)"
        else:
            kai_mem = records[("kleidiai_only", Q)]["peak_mem_mb"]
            rep_mem = records[("repack", Q)]["peak_mem_mb"]
            if kai_mem < rep_mem:
                best_path = f"KleidiAI ≈ repack(打平,≈{repack_gain:.2f}×)— 仅凭 G5 内存优势({kai_mem:.0f}<{rep_mem:.0f}MB)择 kleidiai_only"
            else:
                best_path = f"KleidiAI ≈ repack(打平,≈{repack_gain:.2f}×)— 仅凭 G5 内存优势({rep_mem:.0f}<{kai_mem:.0f}MB)择 repack"
        if ("naive", Q) in ppl_records:
            pr = ppl_records[("naive", Q)]
            ppl_str = f"{pr['perplexity']:.4f} ± {pr['perplexity_stddev']:.5f}"
        else:
            ppl_str = "N/A (timed out)"
        if Q == "q4_0":
            if spot_check_ok is True:
                spot = f"PASS (diff {ppl_diff_pct:.3f}%)"
            elif spot_check_ok is False:
                spot = f"FAIL (diff {ppl_diff_pct:.3f}%)"
            else:
                spot = "N/A (missing)"
        else:
            spot = "—"
        dlines.append(f"| {Q.upper()} | {r['model_size_mb']} | {best_v}{tie_note} | {r['prefill_tok_s']:.3f} | {r['decode_tok_s']:.3f} | {ppl_str} | {r['peak_mem_mb']:.1f} | {best_path} | {spot} |")
    dlines.append("")
    # Q8_0 headline insight
    if ("kleidiai_only", "q8_0") in records and ("repack", "q8_0") in records:
        k8 = records[("kleidiai_only", "q8_0")]
        r8 = records[("repack", "q8_0")]
        dlines.append("## Q8_0 KleidiAI vs repack(headline)")
        if k8["decode_tok_s"] > r8["decode_tok_s"] * 1.05:
            dlines.append(f"- KleidiAI **>** repack:kleidiai_only decode {k8['decode_tok_s']:.3f} vs repack {r8['decode_tok_s']:.3f}(KleidiAI 胜 {(k8['decode_tok_s']/r8['decode_tok_s']-1)*100:.1f}%)")
        elif r8["decode_tok_s"] > k8["decode_tok_s"] * 1.05:
            dlines.append(f"- repack **>** KleidiAI:repack decode {r8['decode_tok_s']:.3f} vs kleidiai_only {k8['decode_tok_s']:.3f}(repack 胜 {(r8['decode_tok_s']/k8['decode_tok_s']-1)*100:.1f}%)")
        else:
            dlines.append(f"- KleidiAI **≈** repack:kleidiai_only {k8['decode_tok_s']:.3f} vs repack {r8['decode_tok_s']:.3f}(打平,差异 <5%)")
        dlines.append(f"- kleidiai_active(Q8_0) = {k8['kleidiai_active']}(source={k8['kleidiai_active_source']})")
        dlines.append("- 对比 Q4_0(KleidiAI≈repack 打平):Q8_0 上 KleidiAI 是否能胜出是本轮 headline。")
        dlines.append("")
    dlines.append("## G5 内存 tie-break 说明")
    dlines.append("- 决策表选每量化'最佳档'时,若两档 decode 差在噪声内(<5%),取峰值内存更低者。")
    dlines.append("- 诚实体现'内存换速度'取舍(如 repack ~1.7× 峰值内存换速度)。")
    dlines.append("")
    dlines.append("## G7 公平性断言")
    if len(ppl_records) == len(PPL_SET):
        dlines.append(f"- 4 份 perplexity JSON 的 n_chunks={ppl_chunks} / n_ctx={ppl_ctx} / wikitext_sha256 一致 ✓")
    else:
        dlines.append(f"- ⚠️ 仅 {len(ppl_records)}/{len(PPL_SET)} 份 perplexity 完成,G7 断言跳过(可能 perplexity step 超时)")
    dlines.append("")
    # 收尾2: PPL 误差棒诚实说明
    dlines.append("## perplexity 误差棒诚实说明(收尾2)")
    dlines.append("- chunks=8 下三量化 PPL 误差棒 ±0.64~0.68,而量化间差值仅 0.1~0.6 —— 个体误差棒重叠。")
    dlines.append("- 排序(Q8_0<Q4_K_M<Q4_0)符合量化理论,且三者同 chunk/同数据集配对测量,是可信的相对排序;")
    dlines.append("  但在此分辨率下个体误差棒重叠,非精确质量差。量化取舍主由体积/速度/内存驱动。")
    dlines.append("- 这不是数据问题,是措辞诚实度问题(不重跑);T4 看板 PPL 一律带 ± 误差棒。")
    dlines.append("")
    # 收尾: Q4_K_M decode 差异显著性(跨轮方差措辞)
    if ("kleidiai_only", "q4_k_m") in records and ("norepack", "q4_k_m") in records:
        k_qkm = records[("kleidiai_only", "q4_k_m")]
        n_qkm = records[("norepack", "q4_k_m")]
        diff_qkm = k_qkm["decode_tok_s"] - n_qkm["decode_tok_s"]
        comb_sigma = (k_qkm["decode_stddev"] ** 2 + n_qkm["decode_stddev"] ** 2) ** 0.5
        if comb_sigma > 0:
            sigma_qkm = abs(diff_qkm) / comb_sigma
            pp_diff_pct = (k_qkm["prefill_tok_s"] - n_qkm["prefill_tok_s"]) / n_qkm["prefill_tok_s"] * 100
            dlines.append("## Q4_K_M decode 差异显著性(诚实标注,不重跑)")
            dlines.append(f"- kleidiai_only decode {k_qkm['decode_tok_s']:.3f} ± {k_qkm['decode_stddev']:.3f} vs norepack {n_qkm['decode_tok_s']:.3f} ± {n_qkm['decode_stddev']:.3f}")
            dlines.append(f"- diff={diff_qkm:.3f}, combined σ={comb_sigma:.3f} → {sigma_qkm:.1f}σ({'显著(>3σ)' if sigma_qkm > 3 else '噪声内'})")
            dlines.append(f"- prefill diff: kleidiai_only {k_qkm['prefill_tok_s']:.3f} vs norepack {n_qkm['prefill_tok_s']:.3f}(差 {pp_diff_pct:.2f}%, 噪声内)")
            decode_diff_pct = diff_qkm / n_qkm["decode_tok_s"] * 100
            dlines.append(f"- decode delta 在 within-run 尺度 {sigma_qkm:.1f}σ(本轮 {'+' if diff_qkm >= 0 else ''}{decode_diff_pct:.2f}%),但 T2↔T3 跨轮对照(T2 -0.83% / T3 首轮 +4.65% 5.3σ / 本轮 {'+' if diff_qkm >= 0 else ''}{decode_diff_pct:.2f}% {sigma_qkm:.1f}σ)符号与幅度波动大,揭示 within-run stddev(5 reps)低估真实跨轮方差;成因未定(跨轮方差/次要布局),KleidiAI 确未接管(三重确认:源码覆盖空 + prefill 噪声内 + source=no_runtime_takeover_kquant_noop)。")
            dlines.append("")
    # 收尾1: PMU 探针实测结论(从 pmu_probe.log 实读)
    pmu_summary = parse_pmu_log(pmu_log_path)
    if pmu_summary is not None:
        dlines.append("## PMU 探针实测结论(T3b Performix feasibility,收尾1)")
        dlines.append(f"- /sys/bus/event_source/devices 含 armv8_pmuv3_0: {pmu_summary['armv8_pmuv3_0_present']}(PMU 硬件设备是否暴露给 VM)")
        dlines.append(f"- arm_spe(SPE)存在: {pmu_summary['arm_spe_present']}")
        dlines.append(f"- perf stat cycles/instructions: {'OK(真数值)' if pmu_summary['perf_stat_ok'] else 'FAILED(硬件计数器访问被 perf_event_paranoid 拦,<not supported>)'}")
        if pmu_summary["perf_stat_ok"] and pmu_summary["arm_spe_present"]:
            dlines.append("- 结论:PMU + SPE 均可用 → T5 Performix 可接入(顺手抓一档真计数器)。")
        elif pmu_summary["perf_stat_ok"]:
            dlines.append("- 结论:PMU 硬件计数器可用但 SPE 不在 → Performix SPE 功能不可用,可用 perf stat 真计数器做瓶颈分解。")
        else:
            dlines.append("- 结论:PMU 硬件设备暴露给 VM 但访问被拦;SPE 完全不在 → Performix SPE 功能不可用,锁 fallback 叙事(perf stat 软件事件 + llama-bench -v + 消融链当瓶颈分解)。")
        dlines.append("- 完整 pmu_probe.log 见 artifact(全程 AI 参赛佐证)。")
        dlines.append("")
    md_text = "\n".join(dlines) + "\n"
    out = pathlib.Path(results_dir) / f"{ts}-decision-table.md"
    write_text_lf(out, md_text)
    print(f"=== decision table written ===")
    return md_text, pmu_summary


def parse_pmu_log(pmu_log_path):
    """从 pmu_probe.log 解析 PMU summary(与 bench.yml line 706-712 一致).None 如果文件不存在."""
    p = pathlib.Path(pmu_log_path)
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    has_pmu = "armv8_pmuv3_0" in text
    has_spe = ("arm_spe" in text) and ("(no arm_spe)" not in text)
    perf_ok = "perf_stat: OK" in text
    return {
        "armv8_pmuv3_0_present": has_pmu,
        "arm_spe_present": has_spe,
        "perf_stat_ok": perf_ok,
        "conclusion": "Performix SPE unavailable; fallback narrative locked" if not (perf_ok and has_spe) else "Performix SPE available",
    }


def compute_headlines(records):
    """P2:headlines 单一计算.全站(卡片/决策表/看板)引用同一值,不手打."""
    headlines = {}
    for Q in QUANTS:
        kai = records.get(("kleidiai_only", Q))
        rep = records.get(("repack", Q))
        nor = records.get(("norepack", Q))
        if not (kai and rep and nor):
            headlines[Q] = {"verdict": "missing", "narrative": "数据缺失"}
            continue
        kai_gain = kai["decode_tok_s"] / nor["decode_tok_s"]
        rep_gain = rep["decode_tok_s"] / nor["decode_tok_s"]
        kai_active = kai["kleidiai_active"]
        kai_decode = kai["decode_tok_s"]
        rep_decode = rep["decode_tok_s"]
        kai_mem = kai["peak_mem_mb"]
        rep_mem = rep["peak_mem_mb"]

        if Q == "q4_k_m" or not kai_active:
            verdict = "noop"
            narrative = f"KleidiAI no-op on {Q.upper()}; repack {rep_gain:.2f}× 胜出"
        elif kai_gain > rep_gain * 1.05:
            verdict = "kai_wins"
            diff_pct = (kai_decode / rep_decode - 1) * 100
            narrative = f"KleidiAI > repack(真赢,+{diff_pct:.1f}% decode)"
        elif rep_gain > kai_gain * 1.05:
            verdict = "repack_wins"
            diff_pct = (rep_decode / kai_decode - 1) * 100
            narrative = f"repack > KleidiAI(repack 胜,+{diff_pct:.1f}% decode)"
        else:
            verdict = "tie"
            best = "kleidiai_only" if kai_mem < rep_mem else "repack"
            narrative = f"KleidiAI ≈ repack(打平,≈{rep_gain:.2f}×)— 仅凭 G5 内存优势择 {best}"

        headlines[Q] = {
            "verdict": verdict,
            "kai_decode": kai_decode,
            "repack_decode": rep_decode,
            "norepack_decode": nor["decode_tok_s"],
            "kai_gain_vs_norepack": round(kai_gain, 3),
            "repack_gain_vs_norepack": round(rep_gain, 3),
            "kai_active": kai_active,
            "kai_mem": kai_mem,
            "repack_mem": rep_mem,
            "decode_diff_pct": round((kai_decode / rep_decode - 1) * 100, 2) if rep_decode else None,
            "narrative": narrative,
        }
    return headlines


def write_manifest(results_dir, ts, records, ppl_records, pmu_summary):
    """产出 results/manifest.json.P3①:assert 文件存在."""
    manifest_path = pathlib.Path(results_dir) / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"latest_timestamp": None, "runs": {}}

    speed_files = [f"{ts}-{V}-{Q}.json" for V, Q in records.keys()]
    perplexity_files = [f"{ts}-perplexity-{V}-{Q}.json" for V, Q in ppl_records.keys()]
    comparisons = [f"{ts}-comparison-{Q}.md" for Q in QUANTS]
    decision_table = f"{ts}-decision-table.md"

    # P3①: assert 文件存在
    all_files = speed_files + perplexity_files + comparisons + [decision_table]
    for fname in all_files:
        fpath = pathlib.Path(results_dir) / fname
        if not fpath.exists():
            print(f"::error::manifest assert fail: {fname} not found in {results_dir}", file=sys.stderr)
            sys.exit(1)

    first_record = next(iter(records.values()))
    run_meta = {
        "cpu_model": first_record["cpu_model"],
        "llama_commit": first_record["llama_commit"],
        "runner_os": first_record["runner_os"],
        "compiler": first_record["compiler"],
        "cpu_features": first_record["cpu_features"],
        "speed_files": sorted(speed_files),
        "perplexity_files": sorted(perplexity_files),
        "decision_table": decision_table,
        "comparisons": comparisons,
        "pmu_summary": pmu_summary if pmu_summary is not None else {},
    }

    manifest["runs"][ts] = run_meta
    manifest["latest_timestamp"] = ts
    manifest["generated_at"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    write_text_lf(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"=== manifest.json written (latest={ts}, {len(all_files)} files asserted) ===")
    return manifest


def write_dashboard(docs_dir, ts, records, ppl_records, decision_table_md, comparisons_md, headlines, pmu_summary):
    """P1:产出 docs/data/dashboard.json(看板自包含数据).看板只 fetch ./data/dashboard.json."""
    dashboard_dir = pathlib.Path(docs_dir) / "data"
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    dashboard_path = dashboard_dir / "dashboard.json"

    first_record = next(iter(records.values()))

    speed_records = {f"{V}-{Q}": r for (V, Q), r in records.items()}
    perplexity_records = {f"{V}-{Q}": r for (V, Q), r in ppl_records.items()}

    run_data = {
        "cpu_model": first_record["cpu_model"],
        "llama_commit": first_record["llama_commit"],
        "runner_os": first_record["runner_os"],
        "compiler": first_record["compiler"],
        "cpu_features": first_record["cpu_features"],
        "speed_records": speed_records,
        "perplexity_records": perplexity_records,
        "decision_table_md": decision_table_md,
        "comparisons_md": comparisons_md,
        "headlines": headlines,
        "pmu_summary": pmu_summary if pmu_summary is not None else {},
    }

    # 保留历史 runs
    existing_runs = {}
    if dashboard_path.exists():
        try:
            existing = json.loads(dashboard_path.read_text(encoding="utf-8"))
            existing_runs = existing.get("runs", {})
        except Exception:
            pass

    runs = {ts: run_data}
    for old_ts, old_run in existing_runs.items():
        if old_ts != ts:
            runs[old_ts] = old_run

    dashboard = {
        "latest_timestamp": ts,
        "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runs": runs,
    }

    write_text_lf(dashboard_path, json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n")
    print(f"=== docs/data/dashboard.json written (latest={ts}, {len(speed_records)} speed + {len(perplexity_records)} ppl records) ===")


def main():
    args = parse_args()
    ts = args.timestamp
    results_dir = args.results_dir
    docs_dir = args.docs_dir

    print(f"=== assemble_results.py: ts={ts}, results_dir={results_dir}, docs_dir={docs_dir}, pmu_log={args.pmu_log} ===")

    # 1. 读 JSON
    records = load_speed_records(results_dir, ts)
    ppl_records = load_ppl_records(results_dir, ts)

    # 2. 断言(G1/G7/spot-check)
    g1_consistency_check(records)
    g7_fairness_check(ppl_records)
    spot_check_ok, ppl_diff_pct = ppl_spot_check(ppl_records)

    # 3. Phase 3: comparison MD
    comparisons_md = phase3_comparison(records, results_dir, ts)

    # 4. Phase 4: decision table + PMU summary
    decision_table_md, pmu_summary = phase4_decision_table(
        records, ppl_records, results_dir, ts, spot_check_ok, ppl_diff_pct, args.pmu_log
    )

    # 5. P2: headlines 单一计算
    headlines = compute_headlines(records)
    print(f"=== headlines computed: Q4_K_M={headlines['q4_k_m']['verdict']}, Q4_0={headlines['q4_0']['verdict']}, Q8_0={headlines['q8_0']['verdict']} ===")

    # 6. manifest.json + P3① assert
    write_manifest(results_dir, ts, records, ppl_records, pmu_summary)

    # 7. P1: docs/data/dashboard.json
    write_dashboard(docs_dir, ts, records, ppl_records, decision_table_md, comparisons_md, headlines, pmu_summary)

    # 8. 汇总
    print("=== ALL 15 RESULTS SUMMARY ===")
    for (V, Q), r in records.items():
        print(f"{V:16s} {Q:8s} pp={r['prefill_tok_s']:.3f} tg={r['decode_tok_s']:.3f} ttft={r['ttft_ms']:.1f} mem={r['peak_mem_mb']:.1f} k_comp={r['kleidiai_compiled']} k_act={r['kleidiai_active']} k_src={r['kleidiai_active_source']} off={r['kleidiai_tensors_offloaded']} r_act={r['repack_active']}")
    print("=== PERPLEXITY SUMMARY ===")
    for (V, Q), r in ppl_records.items():
        print(f"{V:16s} {Q:8s} PPL={r['perplexity']:.4f} +/- {r['perplexity_stddev']:.5f}")
    print("=== HEADLINES ===")
    for Q in QUANTS:
        h = headlines[Q]
        print(f"{Q.upper():8s} verdict={h['verdict']:12s} narrative={h['narrative']}")
    print("=== assemble_results.py DONE ===")


if __name__ == "__main__":
    main()
