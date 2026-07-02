> 进度:W1–W3(T0–T3)已完成,T4 一键基准 + 静态看板进行中。事实来源见 `.trae/specs/bootstrap-arm-infer-bench/spec.md`。

# ArmInfer-Bench

## Overview

ArmInfer-Bench 是一个开源、纯 GitHub Actions Arm64 runner(`runs-on: ubuntu-24.04-arm`)即可复现的「Arm64 LLM 推理优化 + 一键基准」工具包。它用 Arm KleidiAI 微内核重构 llama.cpp 的构建,做多档构建矩阵 × 多量化的消融对照,自动产出 tok/s、TTFT、峰值内存等结构化数据,并配套静态看板与可复用优化配方,供他人直接迁移复用。

算力约束:**只用 GitHub Actions 免费 Arm64 Runner,不依赖任何本地硬件**。可复现是最高优先级——评委按 Setup 复跑即评分命门。

本项目参加 **Arm Create: AI Optimization Challenge**(Cloud AI 赛道,截止 2026-08-15)。

## Functionality

- **五档构建矩阵**:`naive / norepack / repack / kleidiai_only / kleidiai`,逐因子拆开 i8mm 指令、llama.cpp 自带 ARM 重排、KleidiAI 微内核三个优化的净贡献。
- **多量化对照**:`Q4_0 / Q4_K_M / Q8_0`,比较体积、速度、质量(perplexity)。
- **基准采集**:用 `llama-bench` 钉死参数(`-t 4 -p 512 -n 128 -r 5`)采集 prefill/decode tok/s(+stddev)、推算 TTFT、`/usr/bin/time -v` 取峰值内存。
- **静态看板**:GitHub Pages 托管,纯前端 vanilla JS,fetch 同目录 `docs/data/dashboard.json`(由 `scripts/assemble_results.py` 自包含产出,避免 Pages 跨目录 404),展示同机 speedup vs naive、决策表、PMU 探针、激活探针矩阵。
- **复用资产**:优化构建配方、迁移模板、AGENTS.md(骨架就位,配方在 T5 产出)。
- **已落地范围**:W1–W3 已完成五档 × 三量化(Q4_0/Q4_K_M/Q8_0)同机对照 + perplexity 质量列 + PMU 探针;T4 一键基准 + 静态看板进行中。

## Setup

Arm64 从零复跑,有两条路径:

1. **GitHub Actions(推荐)**:在仓库 Actions 页手动触发 `.github/workflows/bench.yml` 的 `workflow_dispatch`,在 `ubuntu-24.04-arm` runner 上自动完成「构建 → 下载模型 → 基准 → 装配 JSON + 看板数据 → commit 回 main + artifact」。
2. **本地 aarch64 Linux**:`bash scripts/run_bench.sh` 一键复现(等价 CI 全流程:构建五档 → 下载 GGUF → 跑 15 次基准 + perplexity → 调 `assemble_results.py` 装配结果)。CI 与本地共用同一份 `fetch_llamacpp.sh` / `build_variant.sh` / `assemble_results.py`,保证一致。

**Prerequisites**(本地路径需要):

- aarch64 Linux(arm64)
- `git`
- `cmake` ≥ 3.14
- `ccache`(跨档共享 llama/ggml 核心目标,控制编译时间)
- `curl` 或 `wget`(下载 GGUF,加重试)
- C/C++ 工具链(`gcc`/`g++` 或 `clang`)
- `python3` ≥ 3.8(装配结果 + 看板数据)

## Naive baseline 说明

`naive` 为 **armv8-a 基础基线**,构建参数为 `-DGGML_NATIVE=OFF -DGGML_CPU_ARM_ARCH=armv8-a -DGGML_CPU_KLEIDIAI=OFF -DGGML_CPU_REPACK=OFF`。

诚实标注:**naive 仍含 NEON,无法完全关闭**。naive 的目标是「尽可能未优化的 armv8-a NEON 基础基线」,而非「零 SIMD 纯标量」。所谓"未优化"指不开 i8mm、不开 KleidiAI 微内核、关闭 ARM 重排;NEON 本身是 armv8-a ABI 的一部分,编译器与 llama.cpp 默认即会产出 NEON 指令,不具备干净的 build-time 关闭开关。

`repack`(ARM 重排)的关闭机制已在 T1.6 于 pinned commit `fabde3b`(release b9728)上核对:由 cmake option `GGML_CPU_REPACK`(默认 ON)控制,`-DGGML_CPU_REPACK=OFF` 即可关闭运行时 Q4_0→Q4_X_X 重排。详见 `AGENTS.md`「repack 真实关闭机制」。

## License

本项目按 Apache License 2.0 开源。版权声明:

```
Copyright 2026 wdnmd-ctmd
```

根目录 `LICENSE` 为 Apache-2.0 全文(已就位,保持不动)。
