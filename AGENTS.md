# AGENTS.md

本文件给参赛 AI/协作者提供项目导航。**事实来源(Source of Truth)为 `.trae/specs/bootstrap-arm-infer-bench/spec.md`**;本文件与其保持一致,如有冲突以 spec 为准。

## 项目目标

用 KleidiAI 微内核重构 llama.cpp,做一套开源、纯 GitHub Actions Arm64 runner(`ubuntu-24.04-arm`)可复现的「Arm64 LLM 推理优化 + 一键基准」工具包,产出多档构建 × 多量化的消融对照数据 + 静态看板 + 可复用优化配方。

**参赛背景**:Arm Create: AI Optimization Challenge(Cloud AI 赛道,截止 2026-08-15)。算力只用 GitHub Actions 免费 Arm64 Runner,不依赖本地硬件。

## 本轮范围

**W1 = T0 + T1**(已完成,naive baseline 锚点已立)。**W2 = T2**(已完成:五档 × 两量化同机对照,探针干净 + G1 一致性断言)。**W3 = T3**(进行中:三量化 × 五档 + perplexity 质量列 + Performix PMU 探针)。事实来源:`.trae/specs/bootstrap-arm-infer-bench/spec.md`。T4–T6 为后续阶段。

## 目录结构

```
/LICENSE                      Apache-2.0 全文(版权行 Copyright 2026 wdnmd-ctmd)
/README.md                    三段式:Overview / Functionality / Setup
/AGENTS.md                    项目目标、目录、Arm64 构建运行验证说明、五档定义
/.gitignore                   忽略 third_party/llama.cpp 与其 build/ 等
/.gitattributes               强制 LF 行尾(shell/yml 跨平台)
/scripts/run_bench.sh         一键:构建→下载模型→基准→输出 JSON  (T4)
/scripts/build_variant.sh     按档位参数化构建 llama.cpp            (T2)
/scripts/fetch_llamacpp.sh    浅拉固定 commit 的 llama.cpp 到 third_party/ (T1 本轮)
/.github/workflows/bench.yml  arm64 CI 工作流(concurrency + 路径限定 + action 固定版本) (T1 本轮)
/results/                     基准 JSON 输出(含 .gitkeep)
/dashboard/                   静态看板占位(含 .gitkeep)          (T4)
/third_party/llama.cpp        运行时浅拉(gitignored),其下 build/ 也 gitignored
```

## 五档构建定义表

逐因子拆开 i8mm 指令、llama.cpp 自带 ARM 重排、KleidiAI 微内核三个优化的净贡献:

| 档位 | ARM arch | 重排(repack) | KleidiAI | 含义 |
|------|----------|--------------|----------|------|
| naive | armv8-a | OFF | OFF | 真·未优化基线 |
| norepack | armv9-a+dotprod+i8mm+sve2 | OFF | OFF | 只吃 i8mm 指令 |
| repack | 同上 | ON | OFF | llama.cpp 自带 ARM 重排 |
| kleidiai_only | 同上 | OFF | ON | 纯 KleidiAI(隔离) |
| kleidiai | 同上 | ON | ON | 两者全开(真实部署) |

> 注:R3 完整矩阵在 T2 实现;本轮 T1 仅落地 `naive` 档作为冒烟基线。

## 构建关键参数

- `-DGGML_NATIVE=OFF`
- `-DGGML_CPU_ARM_ARCH=armv9-a+dotprod+i8mm+sve2`(KleidiAI cmake 靠字面 `+dotprod` token 选内核,**必须显式补 `+dotprod`**)
- `-DGGML_CPU_KLEIDIAI=ON/OFF`
- `-DGGML_CPU_REPACK=ON/OFF`(默认 ON;naive 档 OFF,见下节)
- 用 `ccache` 跨档共享 llama/ggml 核心目标,编译时间砍半。

> 本轮 T1 naive 档:`-DGGML_NATIVE=OFF -DGGML_CPU_ARM_ARCH=armv8-a -DGGML_CPU_KLEIDIAI=OFF -DGGML_CPU_REPACK=OFF`,作为真·未优化基线。

## repack 真实关闭机制(T1.6 已核对)

**pinned commit**:`fabde3bf5136940eb03821aa2490e2360093965b`(release b9728,2026-06-19)。事实来源见 spec.md「naive 档"关闭自带 repack"」节。

已在该 commit 上核对:repack 由 cmake option `GGML_CPU_REPACK` 控制(定义于 `ggml/CMakeLists.txt:120`),描述 "ggml: use runtime weight conversion of Q4_0 to Q4_X_X",**默认 ON**。

- **关闭方式**:`-DGGML_CPU_REPACK=OFF`(纯 build-time cmake flag,**无运行时 env var 覆盖**)。
- **源码归属**:`repack.cpp`/`repack.h` 在 `ggml-cpu/` 顶层 + ARM 专属 `ggml-cpu/arch/arm/repack.cpp`;运行时门控 Q4_0→Q4_X_X 在线重排。
- **naive 档构建参数**:`-DGGML_NATIVE=OFF -DGGML_CPU_ARM_ARCH=armv8-a -DGGML_CPU_KLEIDIAI=OFF -DGGML_CPU_REPACK=OFF`。
- **诚实标注**:`repack.cpp` 仍被编译进二进制(单档构建 `GGML_CPU_SOURCES` 无条件包含),`GGML_CPU_REPACK=OFF` 时运行时不做重排;naive 仍含 NEON。
- 候选 `GGML_CPU_AARCH64` **不存在**;运行时 env var / 版本默认行为均非关闭途径。

## KleidiAI 覆盖范围与激活探针(T2 已核对)

### KleidiAI 覆盖范围(k-quant no-op gotcha)

pinned commit `fabde3b` 上核对:KleidiAI 微内核**仅覆盖 Q4_0 和 Q8_0**(`kleidiai.cpp:296-305` `kleidiai_get_block_args` 只两 case;`kernels.cpp:868-902` `select_kernels` 只匹配 Q4_0/Q8_0 表)。**对 Q4_K_M 完全 no-op**(`select_kernels` 返回 nullptr)。

- **Q4_K_M 表**:kleidiai_only/kleidiai 两档 `kleidiai_active` 实测应为 false(source=`no_runtime_takeover_kquant_noop`)、`offloaded=null`(b9728 op 静默无张量计数日志,source=`unavailable_in_build_log`),speedup ≈ norepack(噪声内)。如实记录,不许掩饰。
- **Q4_0 表**:KleidiAI 真接管,`kleidiai_active=true`(source=`verbose_log_primary_kernel`)、`offloaded=null`(b9728 op 成功调用静默无 LOG,以 active 布尔作接管证据),展示真实加速比。
- 这正是 T2 升级为 2D 矩阵(五档 × Q4_K_M+Q4_0)的根因。

#### 诚实 insight(T2 实测,总监已采信)

- **Q4_0 上 KleidiAI 相对自带 repack 边际收益≈0**:kleidiai_only 与 repack 在 Q4_0 上 speedup 持平,kleidiai 档(双 ON)也无叠加增益。KleidiAI 价值在「不依赖 repack 也能拿到同等速度」而非「比 repack 更快」。
- **repack 以 ~1.7× 峰值内存换速度**:repack 档峰值内存显著高于 norepack(在线 Q4_0→Q4_X_X 重排代价),报告须点明「内存换速度」trade-off。
- **Q8_0(KleidiAI 收益最大档)留给 T3**,不在本轮 scope。

### repack 覆盖范围

repack 覆盖 Q4_0(→Q4_X_X)和 Q4_K(Q4_K_M 真生效,ARM i8mm 走 `q4_K_8x8_q8_K`)。对 Q4_K_M 真生效,对 Q4_0 也生效。

### 激活探针机制(4 字段,运行时真检测)

每档每量化各记 4 个探针字段(总监 W2 裁定,G1 不许循环论证):

| 字段 | 含义 | 采集方法 |
|------|------|---------|
| `kleidiai_compiled` | 符号是否链入(bool) | `nm llama-bench \| grep kai_` 计数 >0(definitive,条件编译) |
| `kleidiai_active` | 运行时是否真接管 KleidiAI 微内核(bool) | 严格白名单:compiled AND 量化在 KleidiAI 覆盖内(Q4_0/Q8_0)AND `-v` 日志含 `kleidiai: primary q4/q8 kernel`;**排除 init/registered/loaded/available 等模块初始化噪声**(b9728 `primary q4 kernel` 日志基于 CPU features 在 init 阶段对所有量化打印,非 per-model 接管信号)。Q4_K_M 落 `no_runtime_takeover_kquant_noop`。**verbose_log 唯一可单独支撑 active 断言** |
| `kleidiai_tensors_offloaded` | 分配到 KleidiAI buffer 的张量数(int/null) | b9728 op 成功调用静默无 LOG,不打印张量计数 → **null + source=`unavailable_in_build_log`**(诚实标"测不到",不留 -1 像报错) |
| `repack_active` | 重排是否生效(bool) | `-v` 日志 grep `repack tensor`;verbose_log 优先 |

**采集优先级(G1)**:`verbose_log`(运行时真证据,唯一可单独支撑 active)> `cmake_inferred`/`compiled_inferred`(最后兜底,绝不单独作为 active 依据)> `inconclusive_*`(拿不到证据+行为含糊,不硬断言)。

**行为交叉验证(G1)**:Q4_0 上 kleidiai_only 实测须显著 ≠ norepack(>5%);Q4_K_M 上须 ≈ norepack(<3% 噪声内)。含糊即标 inconclusive。**自动一致性断言**:CI 内对 kleidiai_only 每量化算 pp/tg speedup(vs norepack)——`active=true` 但 speedup≈1(±5% 内)→ `::error::` + `sys.exit(1)`(探针与 no-op 行为矛盾);`active=false` 但 Q4_0/Q8_0 上 speedup>1.10 → `::error::` + `sys.exit(1)`(行为显示真接管探针漏判)。让探针与行为永远对得上。

**Q4_0 双优化叠加(G2)**:kleidiai 档(repack+KleidiAI 都 ON)在 Q4_0 上两者可能竞争同一批张量,探针记录实际接管者,别把 repack 收益记到 KleidiAI 头上。

**CI 时长红线(G3)**:timeout 60min,逼近 50min 先停报告。严禁为省时拆到不同 job/runner(破坏 NF4)。

**T3 新增护栏(G4–G7)**:

- **G4 结果不可丢(最重要)**:15 档核心速度基准是命根,绝不能被可选步拖累而全丢。perplexity 步加 step 级 `timeout-minutes: 12`;PMU 步 `continue-on-error: true`。assembly + commit 步必须用 `if: always()`,确保即使 perplexity/PMU 失败或超时,已跑完的 15 份速度 JSON 仍能装配并 commit 回 main。单 job 60min 硬 timeout——step 级 timeout + always() commit 防 perplexity 卡住把整 job 拖到 60min 丢失 bench 成果。
- **G5 内存 tie-break**:决策表选每量化"最佳档"时,若两档 decode 差在噪声内(<3%),取峰值内存更低者。诚实体现"内存换速度"取舍(如 repack ~1.7× 峰值内存换速度)。
- **G6 G3 降级顺序固定**:逼近预算时先砍 kleidiai-Q4_0 spot-check,再按量化"统一"降 chunks(8→4)。绝不让三量化用不同 chunks 对比。先报再动。
- **G7 公平性断言**:出决策表前 assert 4 份 perplexity JSON 的 `n_chunks` / `n_ctx` / `wikitext_sha256` 完全一致;不一致即 CI fail 或标红。防止意外混档对比污染质量结论。

## naive 诚实标注

`naive` 为 **armv8-a 基础基线**,构建参数为 `-DGGML_NATIVE=OFF -DGGML_CPU_ARM_ARCH=armv8-a -DGGML_CPU_KLEIDIAI=OFF -DGGML_CPU_REPACK=OFF`。

**naive 仍含 NEON,无法完全关闭**。naive 的目标是「尽可能未优化的 armv8-a NEON 基础基线」,而非「零 SIMD 纯标量」。所谓"未优化"指不开 i8mm、不开 KleidiAI 微内核、关闭 ARM 重排;NEON 本身是 armv8-a ABI 的一部分,编译器与 llama.cpp 默认即会产出 NEON 指令,不具备干净的 build-time 关闭开关。

## Arm64 构建运行验证说明

### CPU 特性自证

aarch64 读 CPU 特性要解析 `/proc/cpuinfo` 的 **`Features` 字段**(不是 x86 的 `flags`):

- `dotprod` 在其中叫 **`asimddp`**
- `i8mm` 字面即 `i8mm`
- `sve2` 字面即 `sve2`

bench job 启动构建/基准前,日志中必须打印解析出的 `Features` 字段,并明确标注 `asimddp`/`i8mm`/`sve2` 是否存在。

### TTFT 取数

TTFT 用 `llama-bench` 的 prompt-processing(prefill)吞吐推算,**不要用 `llama-cli` 的 `--timing`**(部分 commit 上无效且交互模式会挂死)。公式:`ttft_ms = pp_n / prefill_tok_s × 1000`,`pp_n = 512`(即 `-p` 值)。

### GGUF 文件名与分片

- **HF 文件名全小写**:HuggingFace 上 GGUF 文件名全小写(形如 `qwen2.5-1.5b-instruct-q4_k_m.gguf`),下载路径勿拼错大小写;按仓库真实文件列表动态发现。
- **7B 分片动态发现**:7B 的 GGUF 在 HF 上可能分片,按仓库真实文件列表动态发现并全部下载,首片交给 llama.cpp 自动加载多片。
- **`model_size_mb` 累加分片**:分片场景要累加所有分片,不能只统计首片。
- **`actions/cache` 缓存 GGUF**:避免每次重下。

### 同机对照原则(NF4,项目级不变量)

对照结论的可信度建立在「同机」之上,从 W1 第一条数据起即遵守:

- **同 job 同 runner**:同一组对照(naive vs 各优化档)必须在同一个 GitHub Actions job、同一台 runner 内连续跑完。不同 job/不同 runner 的绝对数字**不可直接对比**,仅作参考。
- **结论只用同机比值**:作品的优化结论只采用「同机 speedup ratio」(如 `decode_tok_s_kleidiai / decode_tok_s_naive`),绝对值仅作参考。
- **分母按量化各自 naive**:T2 2D 矩阵下,Q4_K_M 表分母 = 本 job 现跑的 naive-q4_k_m;Q4_0 表分母 = 本 job 现跑的 naive-q4_0。绝不用历史 naive 当分母。
- **cpu_model 友好名映射**:`CPU part` 0xd49 → Neoverse-N2,写入 JSON 的 `cpu_model`(如 `Neoverse-N2 (implementer=0x41 part=0xd49)`)。
- **runner CPU 型号记录**:`cpu_model` 解析自 `/proc/cpuinfo` 的 `CPU part`/`CPU implementer` 或 `lscpu`(如 Neoverse-N2 / Cobalt-100),写入 JSON 的 `cpu_model`。
- **W1 起即记录**:W1 虽只有 naive 单档,`cpu_model` 也必须从第一条数据开始记录,为后续同机比值提供基准锚点。

## 结果 JSON schema

终版 schema(与 spec 一致,以此为准):

```
variant, quant, model, model_revision, model_sha256, model_size_mb,
bench_args, pp_n, tg_n, reps,
prefill_tok_s, prefill_stddev, decode_tok_s, decode_stddev,
ttft_ms, ttft_formula, peak_mem_mb, peak_mem_source,
n_threads, cpu_model, cpu_features, compiler, llama_commit, runner_os, timestamp,
# T2 激活探针(4 字段 + source 标注):
kleidiai_compiled, kleidiai_active, kleidiai_tensors_offloaded, repack_active,
kleidiai_active_source, kleidiai_tensors_offloaded_source, repack_active_source,
kleidiai_compiled_nm_count, repack_cmake_state, kleidiai_cmake_state
```

字段来源说明:

- `model_revision` / `model_sha256`:GGUF 按 `resolve/<REV>/<file>` 钉死的 revision 与下载后校验的 sha256。
- `pp_n` / `tg_n` / `reps` / `n_threads`:即 `-p 512` / `-n 128` / `-r 5` / `-t 4`,冗余写入便于校验。
- `prefill_stddev` / `decode_stddev`:来自 `llama-bench -r 5` 重复结果;stddev/avg > 10% 在日志告警。
- `ttft_ms` / `ttft_formula`:由 `pp_n / prefill_tok_s × 1000` 推算,公式字符串一并落盘。
- `peak_mem_mb` / `peak_mem_source`:峰值内存与取数来源;主方法 `/usr/bin/time -v` 的 `Maximum resident set size`,`peak_mem_source` 默认 `time_v_maxrss`。
- `cpu_model`:runner 实际 CPU 型号(`/proc/cpuinfo` 的 `CPU part`/`CPU implementer` 或 `lscpu`)。
- `cpu_features`:`/proc/cpuinfo` 的 `Features` 字段(含 `asimddp`/`i8mm`/`sve2` 标注)。
- `compiler`:`cc --version` / `clang --version` 首行。
- `runner_os`:`lsb_release` 或 `/etc/os-release`。
- `llama_commit`:`fetch_llamacpp.sh` 回校的 SHA。

## T3 perplexity JSON schema(新增,与 speed JSON 分文件存放)

`results/<timestamp>-perplexity-<variant>-<quant>.json`,4 份(naive×3 quants + kleidiai-q4_0 spot-check):

```
variant, quant, model, model_revision, model_sha256, model_size_mb,
perplexity, perplexity_stddev, perplexity_formula,
n_chunks, n_ctx, chunks_tokens, wikitext_sha256,
llama_commit, timestamp
```

- `perplexity` / `perplexity_stddev`:正则解析 `llama-perplexity` 输出 `Final estimate: PPL = X +/- Y`(走 stderr);**不要传 `--ppl-stride`**(走 v2 不打印汇总行)。
- `n_chunks` / `n_ctx` / `chunks_tokens`:`--chunks 8` / `-c 512` / `n_chunks × n_ctx`(=4096 tokens);G7 断言四份 JSON 此三字段一致。
- `wikitext_sha256`:wikitext-2 test set sha256(`https://huggingface.co/datasets/ggml-org/ci/resolve/main/wikitext-2-raw-v1.zip` → `wikitext-2-raw/wiki.test.raw`,仓库无 pin → CI 首次下载算并断言)。
- perplexity 是"量化"的属性 → 在数值参考档(naive)上测 3 量化;优化档抽 kleidiai-Q4_0 做 spot-check(容差 <2%,确认 KleidiAI 不改数值)。

## T3 决策表(`results/<timestamp>-decision-table.md`)

每量化一行,列 = `quant | 体积MB | 最佳档 | prefill tok/s | decode tok/s | perplexity | 峰值内存MB(最佳档) | 最优优化路径 | PPL spot-check`。最佳档判据:decode tok/s 最高,若两档 decode 差<3% 取峰值内存更低者(G5)。附 Q8_0 KleidiAI vs repack headline insight + G5/G7 说明。

**收尾诚实化措辞(T3 签收前裁定,T4 看板必须继承)**:

- **收尾2 PPL 误差棒**:chunks=8 下三量化 PPL 误差棒 ±0.64~0.68,而量化间差值仅 0.1~0.6,个体误差棒重叠。决策表/看板 PPL 一律带 ± 误差棒,并附说明:"排序(Q8_0<Q4_K_M<Q4_0)符合量化理论,同 chunk/同数据集配对测量,是可信的相对排序;但此分辨率下个体误差棒重叠,非精确质量差。量化取舍主由体积/速度/内存驱动。"不重跑,措辞诚实化。
- **收尾3 Q4_0 最优路径措辞**:Q4_0 上 KleidiAI(1.45×)与 repack(1.41×)差 3%(<5% 阈值),判为"打平",仅凭 G5 内存优势(1853<1965MB)择 kleidiai_only。真正的"KleidiAI 胜出"只在 Q8_0(+15.3% decode)。best_path 阈值 1.05(5%),不让 Q4_0 蹭 Q8_0 headline。
- **Q4_K_M decode 差异显著性**:kleidiai_only vs norepack decode 差 5.3σ(统计显著,非噪声),但 KleidiAI 未接管(三重确认:源码覆盖空 + prefill 0.38% 噪声内 + source=no_runtime_takeover_kquant_noop)。差异最可能是编译进 KleidiAI 代码导致的二进制布局扰动效应(kleidiai_compiled=true, nm 447)。结论"未接管"不改,但 stddev 显著性如实标注。
- **收尾1 PMU 探针实测(T3b)**:`/sys/bus/event_source/devices` 含 `armv8_pmuv3_0`(PMU 硬件设备暴露给 VM)但 `perf stat` 硬件计数器访问被 `perf_event_paranoid` 拦(`<not supported>`);`arm_spe`(SPE)完全不在。结论:Performix SPE 功能在 GHA Arm64 VM 不可用,T5 锁 fallback 叙事(perf stat 软件事件 + llama-bench -v + 消融链当瓶颈分解)。完整 `pmu_probe.log` 见 artifact。

## 后续阶段

- **T2**(已完成):五档构建矩阵 × 两量化同机对照(2D:5 variants × Q4_K_M+Q4_0,`build_variant.sh` + 激活探针 4 字段运行时真检测)——实现完整 R3,严格遵守 NF4 同机对照(同 job/同 runner 连续跑完,结论只用 speedup ratio,分母按量化各自 naive)。G1 探针交叉验证 / G2 Q4_0 双优化交互 / G3 50min 红线。T2 实测两条 insight(已采信):Q4_0 上 KleidiAI 相对 repack 边际收益≈0;repack 以 ~1.7× 峰值内存换速度。
- **T3**(进行中):多量化对照(Q4_0/Q4_K_M/Q8_0 + perplexity 质量列)——实现 R4。三量化 × 五档 = 15 速度基准 + 4 份 perplexity(naive×3 + kleidiai-Q4_0 spot-check)+ "选哪个量化"决策表(G5 内存 tie-break)+ Q8_0 KleidiAI vs repack headline + Performix PMU 探针(T3b 非阻塞)。护栏 G4(结果不丢:perplexity step timeout + `if: always()` commit)/ G5(内存 tie-break)/ G6(降级顺序:先砍 spot-check 再统一降 chunks)/ G7(4 份 PPL params 公平性断言)。
- **T4**:一键基准 `run_bench.sh` + 静态看板——实现 R5。
- **T5**:Arm Performix 接入 + 迁移模板 + 优化配方——实现 R6/R7。
- **T6**:三段式 README 终稿 + ≤3min 演示视频脚本——实现 R8 终稿。