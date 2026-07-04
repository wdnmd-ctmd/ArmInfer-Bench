# Arm64 LLM 推理优化配方

> 读者:想把 Arm64 LLM 推理优化套到自己模型/项目上的工程师。
> 配套资产:`scripts/build_variant.sh` / `scripts/run_bench.sh` / `scripts/assemble_results.py` / `.github/workflows/bench.yml`。
> 看板(当前数字):https://wdnmd-ctmd.github.io/ArmInfer-Bench/
>
> **S3 诚实约定**:本配方不硬编码会跨轮漂移的 headline 数字(如某档相对某档的加速百分比/倍数)。所有定量结论引用看板 `headlines` 字段的 `verdict`(`kai_wins` / `tie` / `noop`),看板由 `assemble_results.py` 的 `compute_headlines()` 单一计算产出。要拿当前数字,看板为准。

---

## 1. TL;DR 决策树

按量化选最优优化路径(判据:decode 吞吐最高;两档 decode 差在噪声内取峰值内存更低者,即 G5 tie-break):

| 量化 | 最优路径 | 看板 verdict | 理由 |
|------|----------|--------------|------|
| Q4_K_M | **repack**(自带 ARM 重排) | `noop` | KleidiAI 微内核对 k-quant 完全不接管(源码覆盖空),repack 是唯一收益来源 |
| Q4_0 | **KleidiAI 或 repack**(打平,凭内存择 KleidiAI) | `tie` | 两者 decode 持平;KleidiAI 不依赖在线重排,峰值内存更低(G5 tie-break) |
| Q8_0 | **KleidiAI**(真胜) | `kai_wins` | KleidiAI 微内核在 Q8_0 上 decode 显著高于 repack,是 KleidiAI 收益最大的量化档 |

**一句话**:KleidiAI 的价值集中在 Q8_0;Q4_0 上 KleidiAI 的价值是「不依赖 repack 也能拿到同等速度且内存更低」而非「比 repack 更快」;Q4_K_M 上 KleidiAI no-op,别误用。

---

## 2. 三因子拆解(五档消融)

本项目的五档构建把 Arm64 推理优化拆成三个正交因子,逐因子量化净贡献:

| 因子 | 控制方式 | 档位对照 | 净贡献 |
|------|----------|----------|--------|
| **i8mm 指令**(arch) | `-DGGML_CPU_ARM_ARCH=armv9-a+dotprod+i8mm+sve2` vs `armv8-a` | naive → norepack | 仅吃 i8mm/SVE2 指令,不开重排/微内核 |
| **repack**(运行时重排) | `-DGGML_CPU_REPACK=ON/OFF`(cmake,默认 ON) | norepack → repack | llama.cpp 自带 ARM 重排(Q4_0→Q4_X_X / Q4_K 在线重排) |
| **KleidiAI**(微内核) | `-DGGML_CPU_KLEIDIAI=ON/OFF`(cmake) | norepack → kleidiai_only | Arm KleidiAI 微内核接管 Q4_0/Q8_0 matmul |

五档定义:

| 档位 | arch | repack | KleidiAI | 含义 |
|------|------|--------|----------|------|
| naive | armv8-a | OFF | OFF | 真·未优化基线(仍含 NEON,见 §6) |
| norepack | armv9-a+dotprod+i8mm+sve2 | OFF | OFF | 只吃 i8mm 指令 |
| repack | 同上 | ON | OFF | + 自带 ARM 重排 |
| kleidiai_only | 同上 | OFF | ON | + 纯 KleidiAI(隔离) |
| kleidiai | 同上 | ON | ON | 两者全开(真实部署档) |

消融链(每档相对前一档的 delta = 该因子净贡献)等价于 top-down 瓶颈分解,在 PMU/SPE 不可用时(见 §7)作为替代归因方法。

---

## 3. 构建配方

### 3.1 关键 cmake 参数

```bash
cmake -S third_party/llama.cpp -B build-kleidiai \
  -DGGML_NATIVE=OFF \
  -DGGML_CPU_ARM_ARCH=armv9-a+dotprod+i8mm+sve2 \
  -DGGML_CPU_KLEIDIAI=ON \
  -DGGML_CPU_REPACK=ON \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build-kleidiai --target llama-bench llama-perplexity -j"$(nproc)"
```

**必记 gotcha**:

- `-DGGML_NATIVE=OFF`:关掉自动检测本机特性,强制用 `GGML_CPU_ARM_ARCH` 字面值(可复现)。
- `-DGGML_CPU_ARM_ARCH=armv9-a+dotprod+i8mm+sve2`:**必须显式补 `+dotprod`**。KleidiAI cmake 靠字面 `+dotprod` token 选内核,漏了会选错微内核。
- `-DGGML_CPU_REPACK`:默认 ON。naive 基线要显式 OFF(见 §6)。
- `-DGGML_CPU_KLEIDIAI`:默认 OFF。kleidiai_only / kleidiai 档 ON。

### 3.2 naive 基线参数(真·未优化)

```bash
-DGGML_NATIVE=OFF -DGGML_CPU_ARM_ARCH=armv8-a -DGGML_CPU_KLEIDIAI=OFF -DGGML_CPU_REPACK=OFF
```

### 3.3 ccache 跨档共享

五档构建共享 llama/ggml 核心目标,用 `ccache` 让除arch/repack/KleidiAI 切换外的目标只编一次,编译时间砍半。CI 里 `actions/cache` 缓存 `~/.ccache`,key 含 `LLAMA_COMMIT`。

### 3.4 用 build_variant.sh 一键构建

```bash
# 五档(矩阵基准,server OFF)
bash scripts/build_variant.sh kleidiai third_party/llama.cpp build-kleidiai

# serving 档(加第 4 参 BUILD_SERVER=ON,产 llama-server)
bash scripts/build_variant.sh kleidiai third_party/llama.cpp build-kleidiai-server ON
```

第 4 参默认 OFF(矩阵零变化);ON 时翻 `-DLLAMA_BUILD_SERVER=ON` + `--target` 追加 `llama-server`。

---

## 4. 内存换速度 trade-off(repack)

**repack 以显著峰值内存开销换速度**:repack 档峰值内存明显高于 norepack(在线 Q4_0→Q4_X_X / Q4_K 重排的代价,重排后的布局常驻内存)。

- 看板 `speed_records` 里 `repack` 档 `peak_mem_mb` 显著高于 `norepack`(看板数字为准)。
- 决策表 G5 tie-break:两档 decode 差在噪声内时,取峰值内存更低者。Q4_0 上 KleidiAI 与 repack 打平,凭此择 KleidiAI(内存更低)。
- **取舍启示**:内存受限场景(多实例部署 / 边缘设备)优先 KleidiAI;追求绝对速度且内存充裕时 repack 也是合理选择(Q4_K_M 上 KleidiAI no-op,repack 是唯一选项)。

---

## 5. KleidiAI 覆盖 gotcha(k-quant no-op)

**KleidiAI 微内核仅覆盖 Q4_0 和 Q8_0**,对 Q4_K_M 完全 no-op。

- 源码证据(pinned commit `fabde3b`):`kleidiai.cpp` 的 `kleidiai_get_block_args` 只两 case(Q4_0/Q8_0);`kernels.cpp` 的 `select_kernels` 只匹配 Q4_0/Q8_0 表,Q4_K_M 返回 nullptr。
- 行为证据:Q4_K_M 上 kleidiai_only / kleidiai 两档的 prefill/decode 与 norepack 持平(噪声内)。
- 探针:`kleidiai_active=false` + `source=no_runtime_takeover_kquant_noop`。

### 激活探针 4 字段(运行时真检测,非 cmake 推断)

每档每量化记 4 个探针字段,G1 不许循环论证:

| 字段 | 含义 | 采集方法 |
|------|------|---------|
| `kleidiai_compiled` | 符号是否链入 | `nm llama-bench \| grep kai_` 计数 > 0(条件编译,definitive) |
| `kleidiai_active` | 运行时是否真接管 | 严格白名单:compiled AND 量化在覆盖内(Q4_0/Q8_0)AND `-v` 日志含 `kleidiai: primary q4/q8 kernel`。**排除 init/registered/loaded 等模块初始化噪声** |
| `kleidiai_tensors_offloaded` | 分配到 KleidiAI buffer 的张量数 | b9728 op 成功调用静默无 LOG → `null` + `source=unavailable_in_build_log`(诚实标"测不到",不留 -1 像报错) |
| `repack_active` | 重排是否生效 | `-v` 日志 grep `repack tensor` |

**采集优先级**:`verbose_log`(运行时真证据,唯一可单独支撑 active)> `cmake_inferred`/`compiled_inferred`(兜底,绝不单独作 active 依据)> `inconclusive_*`(不硬断言)。

**自动一致性断言**(CI 内):`active=true` 但 speedup 在噪声内 → CI fail(探针与 no-op 行为矛盾);`active=false` 但 Q4_0/Q8_0 speedup 显著高于 norepack → CI fail(行为显示真接管探针漏判)。让探针与行为永远对得上。

### Q4_0 双优化叠加(G2)

kleidiai 档(repack + KleidiAI 都 ON)在 Q4_0 上两者可能竞争同一批张量,无叠加收益(实测 kleidiai 档 decode 不高于 kleidiai_only)。探针记录实际接管者,别把 repack 收益记到 KleidiAI 头上。

---

## 6. naive 诚实标注

`naive` 是 **armv8-a 基础基线**,目标是「尽可能未优化的 armv8-a NEON 基础基线」,而非「零 SIMD 纯标量」:

- 不开 i8mm / 不开 KleidiAI 微内核 / 关闭 ARM 重排(repack OFF)。
- **naive 仍含 NEON,无法完全关闭**:NEON 是 armv8-a ABI 的一部分,编译器与 llama.cpp 默认即产出 NEON 指令,不具备干净的 build-time 关闭开关。所谓"未优化"指上述三因子 OFF,非零 SIMD。
- `repack.cpp` 仍被编译进二进制(`GGML_CPU_SOURCES` 无条件包含),`GGML_CPU_REPACK=OFF` 时运行时不做重排。

---

## 7. PMU / Performix 现实(SPE 不可用)

GitHub Actions 免费 Arm64 runner(`ubuntu-24.04-arm`)上 Arm Performix 的核心价值(SPE,Statistical Profiling Extension)**不可用**:

- `/sys/bus/event_source/devices` 含 `armv8_pmuv3_0`(PMU 硬件设备暴露给 VM),但 `perf stat` 硬件计数器访问被 `perf_event_paranoid` 拦(`<not supported>`)。
- `arm_spe`(SPE)完全不在 `/sys/bus/event_source/devices`。

**锁 fallback 三件套**(替代 top-down 系统级瓶颈分析):

1. `perf stat` 软件事件(可用部分)+ CI artifact `pmu_probe.log`。
2. `llama-bench -v` 运行时日志(repack/kleidiai 接管证据)。
3. **五档消融链当瓶颈分解**:naive→norepack(+i8mm)→repack(+重排)→kleidiai_only(+微内核)逐因子 delta = 各因子净贡献,等价于 top-down 的因子归因。

完整 PMU 探针报告见 [`performix-fallback-report.md`](./performix-fallback-report.md)。

---

## 8. 复用资产指针

| 资产 | 用途 |
|------|------|
| `scripts/fetch_llamacpp.sh` | 浅拉固定 commit 的 llama.cpp 到 `third_party/`(钉 commit 保可复现) |
| `scripts/build_variant.sh` | 按档位参数化构建(五档 + serving 第 4 参) |
| `scripts/run_bench.sh` | 一键:构建→下载模型→基准→输出 JSON(本地复现) |
| `scripts/assemble_results.py` | 装配 speed+ppl JSON → comparison MD + decision table + dashboard.json(S2 冻结,勿改) |
| `scripts/test_assemble_results.py` | assemble 冒烟测试(P3③ 防回归) |
| `.github/workflows/bench.yml` | arm64 CI(M1 拆两次 commit + serving 步 + continue-on-error) |
| `docs/data/dashboard.json` | 看板数据源(单一真相,看板引用) |

**迁移到其他模型**:见 [`migration-template.md`](./migration-template.md)。

---

## 9. serving 场景配方(T4b)

离线 `llama-bench` 测的是单请求 prefill/decode 吞吐,真实部署是 `llama-server` 多请求并发。serving 下 KleidiAI 的收益**远超离线**(批处理放大 prefill 优势),是 KleidiAI 价值最强的展示场景。本节给可照抄的 serving 配方。

### 9.1 llama-server 启动参数(continuous batching)

```bash
./llama-server \
  -m <model.gguf> \
  -c 4096 \                  # context size,够装 prompt+生成
  -np 6 \                    # parallel slots(并发上限)
  --cont-batching \          # continuous batching(请求动态进出批)
  -t 4 \                     # threads(与离线 bench 对齐)
  --port 8080
```

- `-c 4096`:ctx 池子,所有 slot 共享。
- `-np 6`:parallel slots 上限,GHA Arm64 runner(4 vCPU)上 6 是吞吐/延迟折中。
- `--cont-batching`:**关键**,continuous batching 让请求随到随走,不必等整批完成;不开则等价批同步,吞吐退化。
- 矩阵档(naive vs kleidiai)用 `build_variant.sh <V> ... ON` 产的 `llama-server`(第 4 参 `BUILD_SERVER=ON`)。

### 9.2 负载生成(`bench_server.py`)

```bash
python3 scripts/bench_server.py \
  --variant kleidiai --quant q8_0 \
  --model <model.gguf> --server-bin ./llama-server \
  --concurrency 1,2,4,6 --max-tokens 128 --n-requests 4 \
  --prompts scripts/serving_prompts.json \
  --output results/<ts>-serving-kleidiai-q8_0.json
```

- `--concurrency 1,2,4,6`:并发档序列。
- `--n-requests 4`:每并发档发 4 个请求(n_requests 决定采样数 + 真并发上限)。
- 纯 stdlib(http.client + ThreadPoolExecutor,M2),无外部依赖。
- VmHWM 在杀进程前读(M3),保 server 进程还在 `/proc/<pid>/status`。
- 增量 JSON 写:server 启动前占位 `status=starting` + 每并发级覆写 `running` + finally 覆写 `ok/crashed`,防 step timeout SIGKILL 丢数据。

### 9.3 并发档诚实措辞(关键)

**有效并发档 = 1/2/4;c=6 因 `n_requests=4 < 并发上限 6` 退化(等价 c=4)**。如需真测 c=6,设 `--n-requests ≥ 6`。**不要**只写"CPU 4 线程饱和"——那不是 c=6 退化的原因,退化是负载生成器侧的样本数限制。

### 9.4 headline insight(定性,数值引看板)

**主线**:KleidiAI 收益随位宽增长——

- **离线**(`llama-bench`):Q4_0 上 KleidiAI 与 repack 打平(凭 G5 内存优势择 KleidiAI);Q8_0 上 KleidiAI decode 真胜 repack。
- **serving**(`llama-server`):批处理放大 prefill 优势,KleidiAI 相对 naive 的 serving 吞吐 speedup **远超离线 decode speedup**。serving 是 KleidiAI 最强展示场景。

具体数值(各并发档吞吐 / TTFT p50/mean/max / speedup ratio)**随 run 漂移**,不硬编码进本配方。看板"并发服务"区段渲染 `dashboard.json` 的 `serving_records`,实时以看板为准。

### 9.5 repack/KleidiAI:内存换速度( serving 侧继承)

repack/KleidiAI 属"用 ~1.7× 峰值内存换速度"(离线 §4)。serving 侧:server 峰值内存 = model weights + KV cache × slots,KleidiAI 的内存优势(Q4_0 上不依赖 repack)在多 slot 部署下被 KV cache 放大,内存受限场景(多实例 / 边缘)优先 KleidiAI。

---

## 10. 一句话总结

KleidiAI 微内核在 Q8_0 上真胜、Q4_0 上凭内存优势打平、Q4_K_M 上 no-op;repack 以内存换速度且覆盖 Q4_K_M;naive 是含 NEON 的 armv8-a 基线。选哪个量化由体积/速度/内存/质量四维驱动,看板决策表给出每量化的最优路径。
