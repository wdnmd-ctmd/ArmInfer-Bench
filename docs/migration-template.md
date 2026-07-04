# 迁移模板:把本流水线套到你的模型

> 读者:换一个模型(如 Qwen2.5-7B / Llama3-8B / 其他 GGUF)套本基准流水线的人。
> 目标:从「跑通 Arm64 五档 × 三量化基准 + 看板」到「换模型后 7 步复现」。
> 前置:已读 [`optimization-recipe.md`](./optimization-recipe.md) 了解三因子/五档/探针。

本模板以「Qwen2.5-1.5B → 你的模型」为例,逐步可执行。每步给出改什么、为什么、怎么验证。

---

## Step 1:钉 llama.cpp commit

**改**:`scripts/fetch_llamacpp.sh` 里的 `LLAMA_COMMIT`(或 bench.yml 的 `env.LLAMA_COMMIT`)。

**为什么钉 commit**:repack 机制由 `GGML_CPU_REPACK` cmake option 控制(定义位置、默认值、关闭途径都可能随 commit 变),KleidiAI 微内核覆盖范围也会演进。不钉 commit = 不可复现。

**怎么钉**:

```bash
# 选一个 llama.cpp release(建议最新稳定 release,或与本项目的 fabde3b 同代)
cd third_party
git clone --depth 1 https://github.com/ggml-org/llama.cpp
cd llama.cpp
git fetch --depth 1 origin <commit-sha>
git checkout <commit-sha>
git rev-parse HEAD  # 回校,写入 bench.yml env.LLAMA_COMMIT
```

**核对 repack 机制**(关键,naive 档依赖):

- `ggml/CMakeLists.txt` 里搜 `GGML_CPU_REPACK`,确认 option 存在 + 默认值(本项目 fabde3b 上默认 ON)。
- 若默认值变了或 option 改名,调整 `build_variant.sh` 的 naive 档参数。
- 若 commit 上 `repack.cpp` 位置变了,更新 AGENTS.md 的源码归属说明。

**坑**:`GGML_CPU_AARCH64` 不存在(曾误传);运行时 env var 不能覆盖 cmake option,只能 build-time 改。

---

## Step 2:钉 GGUF(模型文件)

**改**:`.github/workflows/bench.yml` 的 `env`:

```yaml
env:
  GGUF_REPO: <你的模型 org/repo>           # 例:Qwen/Qwen2.5-7B-Instruct-GGUF
  GGUF_REV: <revision-sha>                  # HF resolve/<REV>/<file>,钉死
  GGUF_FILE_Q4_K_M: <全小写文件名>.gguf      # HF 文件名全小写!
  GGUF_SHA256_Q4_K_M: <已知则填,未知留空>    # 留空则 CI 首次算并记入 JSON
  # 同款填 Q4_0 / Q8_0(若你的模型提供)
```

**为什么**:

- `resolve/<REV>/<file>` 钉 revision,避免 HF 仓库更新导致 sha256 漂移。
- sha256 首次 CI 下载时算并记入 JSON + `$GITHUB_ENV`(本项目 Q4_0/Q8_0 即此模式);已知则提前填死严格校验。

**7B+ 分片 gotcha**:

- 7B 的 GGUF 在 HF 上可能分片(`-00001-of-0000X.gguf`),按仓库真实文件列表动态发现并全部下载,首片交给 llama.cpp 自动加载多片。
- `model_size_mb` 累加所有分片,不能只统计首片。
- 用 `actions/cache` 缓存 GGUF(避免每次重下,key = 文件名 + revision)。

**坑**:HF 文件名全小写(形如 `qwen2.5-1.5b-instruct-q4_k_m.gguf`),路径勿拼错大小写;按仓库真实文件列表动态发现,别猜文件名。

---

## Step 3:构建五档

**用 `build_variant.sh`**(已参数化,无需改):

```bash
# 克隆并钉 commit(Step 1)
bash scripts/fetch_llamacpp.sh

# 五档构建(矩阵基准,server OFF)
for V in naive norepack repack kleidiai_only kleidiai; do
  bash scripts/build_variant.sh "$V" third_party/llama.cpp "third_party/llama.cpp/build-$V"
done
```

五档参数表(已内建,无需手填):

| 档位 | arch | repack | KleidiAI |
|------|------|--------|----------|
| naive | armv8-a | OFF | OFF |
| norepack | armv9-a+dotprod+i8mm+sve2 | OFF | OFF |
| repack | 同上 | ON | OFF |
| kleidiai_only | 同上 | OFF | ON |
| kleidiai | 同上 | ON | ON |

**验证**:

```bash
# 每档 bin 存在
ls third_party/llama.cpp/build-{naive,norepack,repack,kleidiai_only,kleidiai}/bin/llama-bench

# KleidiAI 符号链入(kleidiai_only / kleidiai 档 count > 0)
nm third_party/llama.cpp/build-kleidiai/bin/llama-bench | grep kai_ | wc -l
```

**坑**:

- `+dotprod` 必须显式补(KleidiAI cmake 靠字面 token 选内核)。
- naive 档 `GGML_CPU_REPACK=OFF`(默认 ON,naive 要显式关)。
- naive 仍含 NEON(无法关闭,见 optimization-recipe §6)。

**serving 档**(可选,T4b):加第 4 参 `ON` 产 `llama-server`,独立 build dir(`build-<V>-server`)避免污染 matrix build;ccache 共享核心库。

**serving 基准配方**(T4b,若跑):用 `scripts/bench_server.py` 跑 `naive vs kleidiai × Q4_0/Q8_0 × 并发 1/2/4/6`。`llama-server` 启动 `-c 4096 -np 6 --cont-batching`(continuous batching 是吞吐关键,不开退化)。

**并发档诚实措辞(关键)**:`--n-requests 4` 下**有效并发档 = 1/2/4;c=6 因 `n_requests=4 < 并发上限 6` 退化(等价 c=4)**。如需真测 c=6,设 `--n-requests ≥ 6`。**不要**只写"CPU 4 线程饱和"——退化是负载生成器侧样本数限制,不是 CPU 侧。serving 吞吐数值随 run 漂移,文档/看板引用 `dashboard.json` 的 `serving_records` 实时值,不硬编码(S3)。

---

## Step 4:跑基准矩阵

**本地一键**(参数与 CI 对齐):

```bash
bash scripts/run_bench.sh -t 4 -p 512 -n 128 -r 5
```

**或推 CI**(`.github/workflows/bench.yml`):push 到 main 自动触发。

**固定 bench args**(NF3,便于横比):

- `-t 4`(threads)
- `-p 512`(prefill tokens)
- `-n 128`(decode tokens)
- `-r 5`(repeats)
- `-o json`(输出格式)

**激活探针**(G1):bench 时带 `-v` + `GGML_LOG_LEVEL=DEBUG`,stderr 含 repack/kleidiai 运行时证据。assemble_results.py 解析 4 探针字段(见 optimization-recipe §5)。

**坑**:

- TTFT 用 `llama-bench` prefill 吞吐推算(`ttft_ms = pp_n / prefill_tok_s × 1000`),**不要用 `llama-cli --timing`**(部分 commit 无效且交互模式挂死)。
- CPU 特性自证:解析 `/proc/cpuinfo` 的 `Features` 字段(`asimddp`=dotprod / `i8mm` / `sve2`),不是 x86 的 `flags`。

---

## Step 5:装配 + 看板

**assemble_results.py** 装配 speed + perplexity JSON:

```bash
python3 scripts/assemble_results.py <timestamp>
```

产出:

- `results/<ts>-comparison-<quant>.md` × 3(每量化一份对照)
- `results/<ts>-decision-table.md`(决策表)
- `results/manifest.json`(文件清单,P3① assert 文件存在)
- `docs/data/dashboard.json`(P1 自包含:15 speed + 4 ppl + headlines + decision_table_md + comparisons_md + pmu_summary)

**看板**:GitHub Pages 渲染 `docs/index.html` + `docs/app.js` fetch `./data/dashboard.json`。配置 Pages 指向 `main` 分支 `/docs` 目录。

**P2 单源 headlines**:`compute_headlines()` 从 speed_records 单一计算 verdict + narrative,看板全站引用同一值,不手打数字。

**serving 数据**(T4b,若跑):由 `_merge_serving_to_dashboard.py` 注入 dashboard.json 的 `runs[ts].serving_records`(S2:不动 assemble_results.py)。

**坑**:

- assemble_results.py 是 S2 冻结资产,golden-diff 0 行签收。serving 数据走独立 merge 脚本,别塞进 assemble。
- dashboard.json 必须 P1 自包含(看板只 fetch 同目录 `./data/dashboard.json`,不跨目录,避免 Pages 404)。

---

## Step 6:读决策表

`results/<ts>-decision-table.md` 每量化一行:

```
quant | 体积MB | 最佳档 | prefill tok/s | decode tok/s | perplexity | 峰值内存MB | 最优路径 | PPL spot-check
```

**最佳档判据**:decode tok/s 最高;若两档 decode 差在噪声内(G5 阈值)取峰值内存更低者。

**G7 公平性断言**:出决策表前 assert 4 份 perplexity JSON 的 `n_chunks` / `n_ctx` / `wikitext_sha256` 完全一致(防止混档对比污染质量结论)。

**PPL 误差棒**(诚实):chunks 较少时个体误差棒可能重叠,排序可信但非精确质量差。量化取舍主由体积/速度/内存驱动。

---

## Step 7:加量化 / 变体

**加量化**(如 Q5_K_M):

- `assemble_results.py` 的 `QUANTS` 常量加 `'q5_k_m'`。
- `bench.yml` 的下载步加 Q5_K_M GGUF + cache + sha256 逻辑。
- `run_bench.sh` 的 quants 列表加 `q5_k_m`。
- `docs/app.js` 的 `QUANTS` / `QUANT_LABELS` 加对应项。

**加变体**(如自定义 arch):

- `build_variant.sh` 的 case 加新档位 + 参数。
- `assemble_results.py` 的 `VARIANTS` 加对应项。
- `docs/app.js` 的 `VARIANTS` 加对应项。

**坑**:KleidiAI 仅覆盖 Q4_0/Q8_0,新量化若是 k-quant(Q4_K / Q5_K / Q6_K)KleidiAI no-op,别期待加速;新量化加入后 KleidiAI 档的 `kleidiai_active` 应为 false(行为交叉验证)。

---

## 坑表(全汇总)

| 坑 | 表现 | 解法 |
|----|------|------|
| HF 文件名大小写 | 下载 404 | 文件名全小写,按仓库真实列表动态发现 |
| 漏 `+dotprod` | KleidiAI 选错微内核 / 不接管 | arch 字面显式补 `+dotprod` |
| repack 默认 ON | naive 档不是真基线 | naive 显式 `-DGGML_CPU_REPACK=OFF` |
| KleidiAI k-quant no-op | Q4_K_M 上期待加速却持平 | KleidiAI 仅 Q4_0/Q8_0,k-quant 用 repack |
| naive 仍含 NEON | 以为是零 SIMD 基线 | NEON 是 armv8-a ABI,无法 build-time 关 |
| `GGML_CPU_AARCH64` 不存在 | cmake 报 unknown option | 用 `GGML_CPU_ARM_ARCH` |
| TTFT 用 llama-cli | 挂死 / 无效 | 用 llama-bench prefill 推算 |
| CPU Features 字段名 | 找不到 dotprod | `Features` 字段,`asimddp`=dotprod |
| assemble 加 serving | 破坏 golden-diff | serving 走独立 merge 脚本(S2) |
| dashboard 跨目录 fetch | Pages 404 | P1 自包含,fetch `./data/dashboard.json` |
| 7B 分片 model_size | 只统计首片 | 累加所有分片 |
| job 级 timeout 丢数据 | serving 拖垮 job 丢核心 JSON | M1 拆两次 commit,核心数据先落 main |
| serving c=6 退化误判 | 以为 c=6 吞吐 = c=4 因 CPU 饱和 | 实为 `n_requests=4 < 并发上限 6`,负载生成器侧限制;真测 c=6 需 `n_requests≥6` |

---

## 一句话

钉 commit + 钉 GGUF + 五档构建 + 跑 bench + assemble + 读决策表 + 按需扩量化/变体;12 个坑已汇总,新模型套流水线 7 步到位。
