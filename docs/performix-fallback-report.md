# Arm Performix Fallback 报告

> 读者:评审 + 想接 Arm Performix 做系统级瓶颈分析的工程师。
> 关联:spec R6(Arm Performix 接入);T3b 实测;[`optimization-recipe.md`](./optimization-recipe.md) §7。
>
> 结论先行:GitHub Actions 免费 Arm64 runner(`ubuntu-24.04-arm`)上 Arm Performix 的核心价值(SPE)不可用,本作品锁 fallback 叙事。fallback 三件套在 GHA 环境下等价承担"瓶颈分解"职责。

---

## 1. R6 目标

Arm Performix 提供 top-down 系统级瓶颈分析能力,核心依赖:

- **SPE(Statistical Profiling Extension)**:Arm v8.2+ 的硬件采样,自动归因指令/内存瓶颈,是 Performix "top-down" 的数据源。
- **PMU 硬件计数器**:`cycles` / `instructions` / cache miss 等,需 `perf_event_paranoid` 放行。

R6 目标:在 GHA 免费 Arm64 runner 上探明 Performix 无头运行可行性(是否需账号/授权/闭源二进制),若不可行则锁 fallback。

---

## 2. T3b 实测三源

CI 内 PMU 探针步(`continue-on-error: true`,非阻塞)检查三源:

### 源 1:`/sys/bus/event_source/devices`(PMU 设备暴露)

```
armv8_pmuv3_0      ← PMU 硬件设备暴露给 VM(存在)
```

**判定**:PMU 硬件设备本身**暴露**给 GHA Arm64 VM(`armv8_pmuv3_0` 在 `/sys/bus/event_source/devices/`)。这是必要非充分条件——设备在,不代表能访问。

### 源 2:`perf stat` 硬件计数器访问

```
perf stat -e cycles,instructions ls / >/dev/null 2>&1
→ <not supported>      ← 硬件计数器被拦
```

**判定**:`perf stat` 跑得起来(二进制存在),但硬件计数器(cycles/instructions)返回 `<not supported>`。根因:`perf_event_paranoid` 在 GHA VM 上设了较高值,拦掉非特权用户的硬件计数器访问。软件事件(部分)仍可用。

### 源 3:`arm_spe`(SPE 设备)

```
ls /sys/bus/event_source/devices/arm_spe* 2>/dev/null
→ (no arm_spe)      ← SPE 完全不在
```

**判定**:**SPE 设备完全不在** `/sys/bus/event_source/devices/`。VM 未透传 SPE 给 guest。这是 Performix top-down 价值的根本缺失——没有 SPE 采样数据,Performix 的核心分析能力无从发挥。

---

## 3. 结论:SPE 不可用,锁 fallback

| 能力 | GHA Arm64 VM 状态 | 影响 |
|------|-------------------|------|
| PMU 设备暴露(`armv8_pmuv3_0`) | 存在 | 硬件在,但访问被拦 |
| `perf stat` 硬件计数器 | `<not supported>`(`perf_event_paranoid` 拦) | 拿不到 cycles/instructions |
| SPE(`arm_spe`) | 完全不在 | **Performix top-down 核心价值丧失** |

**锁 fallback**:Performix SPE 功能在 GHA Arm64 VM 不可用,无法做 top-down 系统级瓶颈分析。本作品用 fallback 三件套(§4)承担瓶颈分解职责,不出 Performix 报告。

**为何不强制绕过**:

- `perf_event_paranoid` 调低需 root + 内核参数,GHA runner 无此权限。
- SPE 是硬件特性,VM 未透传无法软件模拟。
- 闭源 Performix 二进制即便能跑,无 SPE 数据也无意义。

---

## 4. Fallback 三件套(替代 top-down)

### 件 1:`perf stat` 软件事件 + `pmu_probe.log` artifact

CI PMU 探针步产 `pmu_probe.log`(完整三源输出),作为 artifact 上传(全程 AI 参赛佐证)。软件事件(task-clock / context-switches 等可用部分)记录进 PMU summary,看板 PMU 区段展示。

### 件 2:`llama-bench -v` 运行时日志

bench 步带 `-v` + `GGML_LOG_LEVEL=DEBUG`,stderr 含:

- `kleidiai: primary q4/q8 kernel`(KleidiAI 接管证据)
- `repack tensor`(repack 重排证据)

这是运行时真证据(G1 探针采集优先级最高),直接证明各优化因子是否生效,等价于 top-down 的"哪个路径在跑"。

### 件 3:五档消融链当瓶颈分解

**核心替代**:五档构建把优化拆成三个正交因子,逐档 delta = 该因子净贡献,等价于 top-down 的因子归因:

```
naive          (armv8-a 基线)
  → norepack   (+ i8mm/SVE2 指令)         delta = i8mm 净贡献
  → repack     (+ 自带 ARM 重排)           delta = repack 净贡献
  → kleidiai_only (+ KleidiAI 微内核)      delta = KleidiAI 净贡献
```

每档相对 naive 的 speedup(同机 ratio,NF4)= 累计因子贡献。哪个因子贡献最大 = 瓶颈在哪层。这比 PMU 计数器更直接回答"优化空间在哪"。

看板"同机 Speedup vs Naive"表格 + "激活探针明细矩阵"即此件的可视化。

---

## 5. 消融链即瓶颈分解(详细论证)

top-down 瓶颈分析的本质:把总执行时间归因到各微架构层(前端/后端/内存),指导优化方向。Performix 用 SPE 采样做这件事。

本作品的消融链从另一角度回答同一问题:

- **i8mm 净贡献**(naive→norepack delta):量化"用上 i8mm 指令能拿多少"。delta 大 = 原 baseline 在 i8mm 上有大量未利用的算力 → 瓶颈在指令集未用。
- **repack 净贡献**(norepack→repack delta):量化"在线重排布局能拿多少"。delta 大 = 原布局不适配 ARM matmul → 瓶颈在数据布局。
- **KleidiAI 净贡献**(norepack→kleidiai_only delta):量化"换微内核能拿多少"。delta 大 = 原 matmul 内核次优 → 瓶颈在内核实现。

**与 SPE 的差异**:SPE 给指令级归因(哪条指令 stalling),消融链给因子级归因(哪个优化层贡献最大)。后者对"该投哪个优化"更具操作性,且不依赖特权硬件访问。代价是粒度粗(无指令级 stalling 原因)。

**诚实边界**:消融链不能替代 SPE 的微架构诊断(如 cache miss 比例 / 分支预测失败率),只能归因优化因子的相对贡献。在 GHA 环境约束下,这是可接受的最优解。

---

## 6. 未来路径(若评审环境有裸机 Arm64)

若评审环境提供裸机 Arm64(非 VM)或可调 `perf_event_paranoid` 的 runner:

1. 重跑 PMU 探针步,确认 `perf stat` 硬件计数器可用 + `arm_spe` 存在。
2. 接 Performix 闭源二进制(需 Arm 账号/授权,T6 调研,不在 T5 scope)。
3. 对 kleidiai vs naive 跑 SPE 采样,产出 top-down 报告,与消融链交叉验证。
4. 把 SPE 报告链接进看板 PMU 区段。

**占位**:T6 不展开 Performix 接入细节,只在 README 终稿提"未来路径"。当前作品以 fallback 三件套为 PMU 维度的完整交付。

---

## 7. artifact 指针

- **CI artifact**:`pmu_probe.log`(完整三源探针输出,每次 bench run 上传)。
- **看板 PMU 区段**:`docs/index.html` 的 PMU/Performix section,渲染 `dashboard.json` 的 `pmu_summary`(`armv8_pmuv3_0_present` / `arm_spe_present` / `perf_stat_ok` / `conclusion`)。
- **dashboard.json `pmu_summary`**(线上 latest run):

```json
{
  "armv8_pmuv3_0_present": true,
  "arm_spe_present": false,
  "perf_stat_ok": false,
  "conclusion": "Performix SPE unavailable; fallback narrative locked"
}
```

---

## 8. 一句话

GHA Arm64 VM 透传了 PMU 设备但拦了硬件计数器访问、且完全不透传 SPE,Performix top-down 核心价值丧失;本作品用 perf 软件事件 + llama-bench -v 运行时日志 + 五档消融链三件套承担瓶颈分解,因子级归因在 GHA 约束下是最优解,微架构级诊断留给有裸机 Arm64 的未来环境。
