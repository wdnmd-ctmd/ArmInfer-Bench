# 选哪个量化:决策表(Qwen2.5-1.5B-Instruct, wikitext-2 PPL, --chunks 8 -c 512)

runner cpu: Neoverse-N2 (implementer=0x41 part=0xd49) | llama_commit: fabde3bf5136940eb03821aa2490e2360093965b | timestamp: 20260701T173912Z

| quant | 体积MB | 最佳档 | prefill tok/s | decode tok/s | perplexity | 峰值内存MB(最佳档) | 最优优化路径 | PPL spot-check |
|-------|--------|--------|---------------|--------------|------------|---------------------|-------------|----------------|
| Q4_K_M | 1065.56 | kleidiai (G5 tie-break: 2 档 decode 差<3%, 取内存最低) | 87.113 | 33.301 | 11.2698 ± 0.68037 | 2062.0 | repack (1.25×) — KleidiAI no-op on k-quant | — |
| Q4_0 | 1016.83 | kleidiai_only (G5 tie-break: 3 档 decode 差<3%, 取内存最低) | 129.175 | 36.214 | 11.3872 ± 0.68512 | 1853.3 | KleidiAI ≈ repack(打平,≈1.41×)— 仅凭 G5 内存优势(1853<1965MB)择 kleidiai_only | PASS (diff 0.045%) |
| Q8_0 | 1806.77 | kleidiai_only (G5 tie-break: 2 档 decode 差<3%, 取内存最低) | 182.288 | 44.504 | 10.6823 ± 0.64232 | 3363.8 | KleidiAI (1.56×) > repack (1.36×) | — |

## Q8_0 KleidiAI vs repack(headline)
- KleidiAI **>** repack:kleidiai_only decode 44.504 vs repack 38.593(KleidiAI 胜 15.3%)
- kleidiai_active(Q8_0) = True(source=verbose_log_primary_kernel)
- 对比 Q4_0(KleidiAI≈repack 打平):Q8_0 上 KleidiAI 是否能胜出是本轮 headline。

## G5 内存 tie-break 说明
- 决策表选每量化'最佳档'时,若两档 decode 差在噪声内(<3%),取峰值内存更低者。
- 诚实体现'内存换速度'取舍(如 repack ~1.7× 峰值内存换速度)。

## G7 公平性断言
- 4 份 perplexity JSON 的 n_chunks=8 / n_ctx=512 / wikitext_sha256 一致 ✓

