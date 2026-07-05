# ArmInfer-Bench

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](./LICENSE)
[![Live Dashboard](https://img.shields.io/badge/dashboard-live-success)](https://wdnmd-ctmd.github.io/ArmInfer-Bench/)

> **Live 看板**:https://wdnmd-ctmd.github.io/ArmInfer-Bench/
> **License**:Apache-2.0(`./LICENSE`,Copyright 2026 wdnmd-ctmd)

开源、纯 GitHub Actions Arm64 runner(`runs-on: ubuntu-24.04-arm`)即可复现的「Arm64 LLM 推理优化 + 一键基准」工具包。用 Arm KleidiAI 微内核重构 llama.cpp 构建,做五档构建矩阵 × 三量化的消融对照,自动产出 tok/s、TTFT、峰值内存、perplexity、serving 吞吐等结构化数据,配套静态看板与可复用优化配方。

## Overview

本项目参加 **Arm Create: AI Optimization Challenge**(Cloud AI 赛道,截止 2026-08-15)。算力只用 GitHub Actions 免费 Arm64 Runner,不依赖任何本地硬件——可复现是最高优先级。

### 三条主打叙事

1. **全程 AI 完成**:$0 算力、$0 人工编码。总监用 Notion AI 拆任务 + 工程师用 Trae CN(GLM-5.2)执行,从脚手架到看板到优化配方全部由 AI 在 GHA 免费 Arm64 runner 上跑通。仓库内的 `AGENTS.md` / `docs/` 完整记录每轮决策与诚实边界,可审计。
2. **一条命令在真 Arm64 上复现**:fork → push(或手动 `workflow_dispatch`)→ 等 CI(~40min)→ 开 Pages 看板。本地 Arm64 机器也可 `bash scripts/run_bench.sh -t 4 -p 512 -n 128 -r 5` 一键复现全流程(构建五档 → 下载 GGUF → 15 速度基准 + perplexity → 装配看板数据)。
3. **KleidiAI 收益随量化位宽增长**:Q4_K_M 上 KleidiAI no-op(k-quant 不在覆盖内)、Q4_0 上 KleidiAI 与 repack 打平(凭内存优势择 KleidiAI)、Q8_0 上 KleidiAI 离线 decode 真胜 repack;**serving 下批处理放大 prefill 优势,KleidiAI 相对 naive 的吞吐 speedup 远超离线**——serving 是 KleidiAI 最强展示场景。具体数值随 run 漂移,**以 live 看板为准**。

### 命中官方 6/6 优化方向

| 方向 | 本项目落点 |
|------|-----------|
| 模型体积 | 三量化对照(Q4_0 / Q4_K_M / Q8_0),看板决策表给「体积 vs 速度 vs 内存 vs 质量」取舍 |
| 推理质量 | wikitext-2 perplexity(naive × 3 量化 + kleidiai-Q4_0 spot-check 数值一致性) |
| 推理速度 | `llama-bench` 五档消融 × 三量化 = 15 速度基准,prefill/decode tok/s + 推算 TTFT |
| Serving 吞吐 | `llama-server` naive vs kleidiai × Q4_0/Q8_0 × 并发 1/2/4/6,wall-clock 吞吐 + TTFT p50/mean/max |
| 开发者体验 | 一键 `run_bench.sh` + 静态看板(GitHub Pages)+ 优化配方 + 迁移模板,可复用可迁移 |
| Arm 专用优化 | KleidiAI 微内核 + i8mm/SVE2 指令 + ARM repack 三因子逐档消融,激活探针四字段运行时自证 |

## Functionality

### 架构与数据流

```
┌─────────────────────────────────────────────────────────────────┐
│  scripts/fetch_llamacpp.sh  →  third_party/llama.cpp (pinned)   │
│  scripts/build_variant.sh   →  build-{naive,…,kleidiai}/bin/    │
│                                  (+ build-<V>-server for T4b)   │
└─────────────────────────────────────────────────────────────────┘
        │  (5 variants × 3 quants, same job/runner — NF4)
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  llama-bench  -t 4 -p 512 -n 128 -r 5 -v  →  15 speed JSON      │
│  llama-perplexity --chunks 8 -c 512       →  4 ppl JSON         │
│  (T4b) bench_server.py → 4 serving JSON (naive/kleidiai×Q4_0/Q8_0)│
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  scripts/assemble_results.py  (S2 frozen, single-source)        │
│    → results/<ts>-comparison-<quant>.md × 3                      │
│    → results/<ts>-decision-table.md                              │
│    → results/manifest.json                                        │
│    → docs/data/dashboard.json (P1 self-contained)                │
│  scripts/_merge_serving_to_dashboard.py  (T4b, injects serving)  │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  docs/index.html + app.js + style.css  (GitHub Pages)           │
│  fetch ./data/dashboard.json → render speedup / decision /       │
│  PMU / probe-matrix / serving 区段 + T5 文档导航(blob view)     │
└─────────────────────────────────────────────────────────────────┘
```

### 五档构建消融(三因子拆解)

| 档位 | arch | repack | KleidiAI | 含义 |
|------|------|--------|----------|------|
| naive | armv8-a | OFF | OFF | 真·未优化基线(仍含 NEON,见下) |
| norepack | armv9-a+dotprod+i8mm+sve2 | OFF | OFF | 只吃 i8mm 指令 |
| repack | 同上 | ON | OFF | + 自带 ARM 重排 |
| kleidiai_only | 同上 | OFF | ON | + 纯 KleidiAI(隔离) |
| kleidiai | 同上 | ON | ON | 两者全开(真实部署档) |

逐档 delta = 该因子净贡献,等价于 top-down 瓶颈分解(在 PMU/SPE 不可用时作为替代归因)。

### 关键机制(诚实说明)

- **KleidiAI 仅覆盖 Q4_0 / Q8_0**:对 Q4_K_M 完全 no-op(`select_kernels` 返回 nullptr,k-quant 不在微内核覆盖内)。Q4_K_M 的唯一收益来源是 repack。
- **repack 以内存换速度**:repack 档峰值内存显著高于 norepack(在线 Q4_0→Q4_X_X / Q4_K 重排的代价,~1.7× 内存开销属结构性 trade-off,非速度指标)。决策表 G5 tie-break:两档 decode 差在噪声内取峰值内存更低者。
- **激活探针四字段自证**:每档每量化记 `kleidiai_compiled` / `kleidiai_active` / `kleidiai_tensors_offloaded` / `repack_active`(+ source 标注),运行时 `-v` 日志取证,CI 内 G1 一致性断言(探针与行为矛盾即 fail)。
- **NF4 同机对照**:五档 × 三量化必须在同一 job / 同一 runner 内连续跑完,结论只用「同机 speedup ratio」,分母 = 本 job 现跑的 naive。不同 job/runner 绝对值不可直接对比。
- **naive 仍含 NEON**:armv8-a ABI 的一部分,无干净 build-time 关闭开关;所谓"未优化"指不开 i8mm / KleidiAI / repack,非零 SIMD。

### Serving(T4b)

`llama-server -c 4096 -np 6 --cont-batching` 跑 naive vs kleidiai × Q4_0/Q8_0 × 并发 1/2/4/6。**有效并发档 = 1/2/4;c=6 因 `n_requests=4 < 并发上限 6` 退化(等价 c=4)**,如需真测 c=6 需 `n_requests≥6`。吞吐 = 总 token / wall-clock(S1),TTFT 报 p50/mean/max(S5)。serving 数值随 run 漂移,以看板 `serving_records` 为准。

### 配套文档(T5)

- [优化配方](https://github.com/wdnmd-ctmd/ArmInfer-Bench/blob/main/docs/optimization-recipe.md)——三因子拆解 / 构建配方 / KleidiAI 覆盖 gotcha / serving 场景配方 / PMU fallback
- [迁移模板](https://github.com/wdnmd-ctmd/ArmInfer-Bench/blob/main/docs/migration-template.md)——7 步迁到其他模型 + 12 坑表
- [Performix 降级报告](https://github.com/wdnmd-ctmd/ArmInfer-Bench/blob/main/docs/performix-fallback-report.md)——SPE 不可用 + fallback 三件套
- [演示视频脚本](https://github.com/wdnmd-ctmd/ArmInfer-Bench/blob/main/docs/video-script.md)——≤3min 分镜 + 旁白

## Setup

### 路径 A:GitHub Actions(推荐,零本地硬件)

```bash
# 1. Fork 仓库到自己的 GitHub 账号
# 2. (可选)Clone 到本地改 bench.yml env(换模型/量化)
git clone https://github.com/<你的账号>/ArmInfer-Bench.git
cd ArmInfer-Bench

# 3. Push 到 main 触发 CI(或仓库 Actions 页 → "Arm64 Bench" → Run workflow 手动触发)
git push origin main
```

CI 在 `ubuntu-24.04-arm` runner 上自动完成:构建五档 → 下载 GGUF + wikitext → 15 速度基准 + 4 perplexity + PMU 探针 → 装配 JSON/MD/dashboard.json → commit#1 核心数据落 main → serving 基准 → merge serving → commit#2 → artifact 上传。**预计耗时 ~40min**。

```bash
# 4. 开 GitHub Pages(一次性配置)
#    仓库 Settings → Pages → Source: Deploy from branch → main /docs
#    配置后看板在 https://<你的账号>.github.io/ArmInfer-Bench/
```

### 路径 B:本地 aarch64 Linux

**Prerequisites**:

- aarch64 Linux(arm64,需 `asimddp`/`i8mm`/`sve2` 特性,KleidiAI 档才有效)
- `git` / `cmake` ≥ 3.14 / `ccache` / `curl` / C/C++ 工具链(`gcc`/`g++` 或 `clang`)
- `python3` ≥ 3.8(装配结果 + 看板数据)
- `/usr/bin/time`(取峰值内存 RSS,通常 coreutils 自带)

```bash
# 一键复现(参数与 CI 完全对齐)
bash scripts/fetch_llamacpp.sh                    # 浅拉 pinned commit 的 llama.cpp
bash scripts/run_bench.sh -t 4 -p 512 -n 128 -r 5  # 构建+下载+基准+装配
```

`run_bench.sh` 等价 CI 全流程:构建五档 → 下载 GGUF → 跑 15 速度基准 + perplexity → 调 `assemble_results.py` 装配结果到 `results/` + `docs/data/dashboard.json`。CI 与本地共用同一份 `fetch_llamacpp.sh` / `build_variant.sh` / `assemble_results.py`,保证一致。

### 路径 C:复现 serving 基准(T4b)

serving 需要单独构建 `llama-server`(矩阵基准默认只产 `llama-bench`/`llama-perplexity`):

```bash
bash scripts/fetch_llamacpp.sh

# 构建 naive + kleidiai 的 server 档(第 4 参 BUILD_SERVER=ON,独立 build dir)
bash scripts/build_variant.sh naive     third_party/llama.cpp build-naive-server     ON
bash scripts/build_variant.sh kleidiai  third_party/llama.cpp build-kleidiai-server  ON

# 跑 serving 基准(naive vs kleidiai × Q4_0/Q8_0 × 并发 1/2/4/6)
# 假设 models/qwen2.5-1.5b-instruct-q4_0.gguf 已下载(路径 B 会下)
python3 scripts/bench_server.py \
  --variant kleidiai --quant q8_0 \
  --model models/qwen2.5-1.5b-instruct-q8_0.gguf \
  --server-bin build-kleidiai-server/bin/llama-server \
  --concurrency 1,2,4,6 --max-tokens 128 --n-requests 4 \
  --prompts scripts/serving_prompts.json \
  --output results/serving-kleidiai-q8_0.json

# 注入 serving 数据到看板(不动 assemble_results.py,S2 冻结)
python3 scripts/_merge_serving_to_dashboard.py <timestamp>
```

### 钉死的 bench 参数(NF3,便于横比)

| 参数 | 值 | 含义 |
|------|----|----|
| `-t` | 4 | threads |
| `-p` | 512 | prefill tokens(TTFT 推算分母) |
| `-n` | 128 | decode tokens |
| `-r` | 5 | repeats(算 stddev) |
| `-o` | json | 输出格式 |

TTFT 推算公式:`ttft_ms = pp_n / prefill_tok_s × 1000`(用 `llama-bench` prefill 吞吐,**不要用 `llama-cli --timing`**,部分 commit 无效且交互模式挂死)。

## License

本项目按 **Apache License 2.0** 开源。版权声明:

```
Copyright 2026 wdnmd-ctmd
```

根目录 [`LICENSE`](./LICENSE) 为 Apache-2.0 全文。第三方依赖(llama.cpp)保留其各自许可证,`third_party/` 运行时浅拉、gitignored,不纳入本仓库。
