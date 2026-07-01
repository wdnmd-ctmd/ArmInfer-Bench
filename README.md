> 本轮范围:W1 = T0 + T1。事实来源见 `.trae/specs/bootstrap-arm-infer-bench/spec.md`。

# ArmInfer-Bench

## Overview

ArmInfer-Bench 是一个开源、纯 GitHub Actions Arm64 runner(`runs-on: ubuntu-24.04-arm`)即可复现的「Arm64 LLM 推理优化 + 一键基准」工具包。它用 Arm KleidiAI 微内核重构 llama.cpp 的构建,做多档构建矩阵 × 多量化的消融对照,自动产出 tok/s、TTFT、峰值内存等结构化数据,并配套静态看板与可复用优化配方,供他人直接迁移复用。

算力约束:**只用 GitHub Actions 免费 Arm64 Runner,不依赖任何本地硬件**。可复现是最高优先级——评委按 Setup 复跑即评分命门。

本项目参加 **Arm Create: AI Optimization Challenge**(Cloud AI 赛道,截止 2026-08-15)。

## Functionality

- **五档构建矩阵**:`naive / norepack / repack / kleidiai_only / kleidiai`,逐因子拆开 i8mm 指令、llama.cpp 自带 ARM 重排、KleidiAI 微内核三个优化的净贡献。
- **多量化对照**:`Q4_0 / Q4_K_M / Q8_0`,比较体积、速度、质量(perplexity)。
- **基准采集**:用 `llama-bench` 钉死参数(`-t 4 -p 512 -n 128 -r 5`)采集 prefill/decode tok/s(+stddev)、推算 TTFT、`/usr/bin/time -v` 取峰值内存。
- **静态看板**:读 `results/*.json` 渲染 HTML/Markdown 结果(本轮占位)。
- **复用资产**:优化构建配方、迁移模板、AGENTS.md(本轮骨架就位)。
- **本轮仅落地 `naive` 档冒烟基线**(Qwen2.5-1.5B-Instruct Q4_K_M),五档矩阵与多量化在后续阶段展开。

## Setup

Arm64 从零复跑,本轮有两条路径:

1. **GitHub Actions(推荐,本轮主路径)**:在仓库 Actions 页手动触发 `.github/workflows/bench.yml` 的 `workflow_dispatch`,在 `ubuntu-24.04-arm` runner 上自动完成「构建 → 下载模型 → 基准 → 输出 JSON artifact」。
2. **本地 aarch64 Linux**:`bash scripts/run_bench.sh` 一键复现。> 注:`run_bench.sh` 在 T4 阶段才完整,本轮先以 CI 为准;本地等价步骤参见 `scripts/fetch_llamacpp.sh` + `AGENTS.md` 的构建参数。

**Prerequisites**(本地路径需要):

- aarch64 Linux(arm64)
- `git`
- `cmake` ≥ 3.14
- `ccache`(跨档共享 llama/ggml 核心目标,控制编译时间)
- `curl` 或 `wget`(下载 GGUF,加重试)
- C/C++ 工具链(`gcc`/`g++` 或 `clang`)

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
