# 选哪个量化:决策表(Qwen2.5-1.5B-Instruct, wikitext-2 PPL, --chunks 8 -c 512)

runner cpu: Neoverse-N2 (implementer=0x41 part=0xd49) | llama_commit: fabde3bf5136940eb03821aa2490e2360093965b | timestamp: 20260705T065022Z

| quant | 体积MB | 最佳档 | prefill tok/s | decode tok/s | perplexity | 峰值内存MB(最佳档) | 最优优化路径 | PPL spot-check |
|-------|--------|--------|---------------|--------------|------------|---------------------|-------------|----------------|
| Q4_K_M | 1065.56 | kleidiai (G5 tie-break: 2 档 decode 差<5%, 取内存最低) | 87.074 | 33.213 | 11.2698 ± 0.68037 | 2065.7 | repack (1.22×) — KleidiAI no-op on k-quant | — |
| Q4_0 | 1016.83 | kleidiai_only (G5 tie-break: 3 档 decode 差<5%, 取内存最低) | 129.323 | 36.617 | 11.3872 ± 0.68512 | 1853.1 | KleidiAI ≈ repack(打平,≈1.45×)— 仅凭 G5 内存优势(1853<1965MB)择 kleidiai_only | PASS (diff 0.045%) |
| Q8_0 | 1806.77 | kleidiai (G5 tie-break: 2 档 decode 差<5%, 取内存最低) | 181.503 | 44.056 | 10.6823 ± 0.64232 | 3372.7 | KleidiAI (1.56×) > repack (1.39×) | — |

## Q8_0 KleidiAI vs repack(headline)
- KleidiAI **>** repack:kleidiai_only decode 44.654 vs repack 39.662(KleidiAI 胜 12.6%)
- kleidiai_active(Q8_0) = True(source=verbose_log_primary_kernel)
- 对比 Q4_0(KleidiAI≈repack 打平):Q8_0 上 KleidiAI 是否能胜出是本轮 headline。

## G5 内存 tie-break 说明
- 决策表选每量化'最佳档'时,若两档 decode 差在噪声内(<5%),取峰值内存更低者。
- 诚实体现'内存换速度'取舍(如 repack ~1.7× 峰值内存换速度)。

## G7 公平性断言
- 4 份 perplexity JSON 的 n_chunks=8 / n_ctx=512 / wikitext_sha256 一致 ✓

## perplexity 误差棒诚实说明(收尾2)
- chunks=8 下三量化 PPL 误差棒 ±0.64~0.68,而量化间差值仅 0.1~0.6 —— 个体误差棒重叠。
- 排序(Q8_0<Q4_K_M<Q4_0)符合量化理论,且三者同 chunk/同数据集配对测量,是可信的相对排序;
  但在此分辨率下个体误差棒重叠,非精确质量差。量化取舍主由体积/速度/内存驱动。
- 这不是数据问题,是措辞诚实度问题(不重跑);T4 看板 PPL 一律带 ± 误差棒。

## Q4_K_M decode 差异显著性(诚实标注,不重跑)
- kleidiai_only decode 27.684 ± 0.118 vs norepack 27.227 ± 0.271
- diff=0.458, combined σ=0.296 → 1.5σ(噪声内)
- prefill diff: kleidiai_only 58.583 vs norepack 58.551(差 0.05%, 噪声内)
- decode delta 在 within-run 尺度 1.5σ(本轮 +1.68%),但 T2↔T3 跨轮对照(T2 -0.83% / T3 首轮 +4.65% 5.3σ / 本轮 +1.68% 1.5σ)符号与幅度波动大,揭示 within-run stddev(5 reps)低估真实跨轮方差;成因未定(跨轮方差/次要布局),KleidiAI 确未接管(三重确认:源码覆盖空 + prefill 噪声内 + source=no_runtime_takeover_kquant_noop)。

## PMU 探针实测结论(T3b Performix feasibility,收尾1)
- /sys/bus/event_source/devices 含 armv8_pmuv3_0: True(PMU 硬件设备是否暴露给 VM)
- arm_spe(SPE)存在: False
- perf stat cycles/instructions: FAILED(硬件计数器访问被 perf_event_paranoid 拦,<not supported>)
- 结论:PMU 硬件设备暴露给 VM 但访问被拦;SPE 完全不在 → Performix SPE 功能不可用,锁 fallback 叙事(perf stat 软件事件 + llama-bench -v + 消融链当瓶颈分解)。
- 完整 pmu_probe.log 见 artifact(全程 AI 参赛佐证)。

