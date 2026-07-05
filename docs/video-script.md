# 演示视频脚本(≤3min)

> 目标:公开视频(YouTube/Vimeo/Youku),≤3 分钟,展示项目在 Arm64 上真实运行 + 3 条主打叙事可视化。
> 录制:谢特本人(Windows 端,OBS Studio 录屏 + 麦克风旁白)。
> 约束:**禁第三方商标**(不开 Arm/GitHub/Qwen logo 特写,只展示产品界面)、**禁版权音乐**(用免版权背景乐或无声)、公开可访问。
> 配套:README Setup 是评委复跑命门,本视频是 WOW 抓手——重点是「真 Arm64 跑通」+「KleidiAI 随位宽增长」的可视化证据。

---

## 时间轴总览(0:00–3:00)

| 时间 | 镜号 | 时长 | 画面 | 旁白主线 |
|------|------|------|------|----------|
| 0:00–0:15 | S1 | 15s | 项目 GitHub 仓库首页 + live 看板并排 | 一句话定位 + 全程 AI |
| 0:15–0:45 | S2 | 30s | GitHub Actions CI 运行页(run #28698521281 全绿) | 一条命令在真 Arm64 复现 |
| 0:45–1:30 | S3 | 45s | 看板「同机 Speedup vs Naive」表(Q4_K_M/Q4_0/Q8_0 切换) | KleidiAI 收益随位宽增长 |
| 1:30–2:15 | S4 | 45s | 看板「并发服务」区段(serving 表 + c=1/2/4 吞吐曲线) | serving 放大 KleidiAI 优势 |
| 2:15–2:45 | S5 | 30s | 看板「决策表」+「激活探针矩阵」+ PMU fallback 区段 | 诚实边界 + 可复用 |
| 2:45–3:00 | S6 | 15s | README Setup 路径 A 三行命令 + License badge | 收尾:fork 即复现 |

---

## 分镜详表

### S1(0:00–0:15,15s)项目定位 + 全程 AI

**画面**:
- 左半屏:GitHub 仓库首页(`https://github.com/wdnmd-ctmd/ArmInfer-Bench`),顶部 README 的 License badge + live 看板链接高亮。
- 右半屏:live 看板(`https://wdnmd-ctmd.github.io/ArmInfer-Bench/`)首页,Headline 结论卡片可见。
- 镜头静止,鼠标在 README「三条主打叙事」段轻轻划过。

**旁白**(15s,约 60 字):
> 「ArmInfer-Bench——开源、纯 GitHub Actions Arm64 runner 即可复现的 LLM 推理优化工具包。全程 AI 完成:总监用 Notion AI 拆任务,工程师用 Trae CN 执行,零人工编码、零本地算力。」

---

### S2(0:15–0:45,30s)一条命令在真 Arm64 复现

**画面**:
- GitHub Actions 页面,`Arm64 Bench` workflow 的 run #28698521281,状态 `success`(全 23 步绿)。
- 镜头缓慢下滚,展示步骤名:「CPU features self-evidence」(标 `asimddp`/`i8mm`/`sve2` PRESENT)→「Build 5 variants」→「Run 15 benchmarks」→「serving bench」→「Commit#1/Commit#2」。
- 右下角小字标注:`runs-on: ubuntu-24.04-arm` + 总耗时 `39.5min`。

**旁白**(30s,约 110 字):
> 「复现只要一条命令:fork 仓库,push 到 main,CI 自动在免费的 Arm64 runner 上跑通全流程——构建五档 llama.cpp、下载三个量化模型、跑 15 次速度基准加 4 份 perplexity、再加 serving 并发基准。约 40 分钟,全部数据自动 commit 回 main 并推到看板。评委无需任何本地硬件。」

---

### S3(0:45–1:30,45s)KleidiAI 收益随位宽增长(离线)

**画面**:
- 看板「同机 Speedup vs Naive」表格,Quant Tab 切换:Q4_K_M → Q4_0 → Q8_0。
- Q4_K_M 切换时:kleidiai_only 行 speedup ≈ 1(标灰),探针 `kleidiai_active=false`,旁注「k-quant no-op」。
- Q4_0 切换时:kleidiai_only 与 repack 行 speedup 持平(都高),G5 内存 tie-break 标注 KleidiAI 更省内存。
- Q8_0 切换时:kleidiai_only 行 speedup 显著高于 repack,Headline 卡片「Q8_0 KleidiAI 真胜」高亮。
- 数值用看板实时值,不硬编码到视频字幕(看板数字随 run 漂移,视频只秀趋势)。

**旁白**(45s,约 170 字):
> 「核心发现:KleidiAI 微内核的收益随量化位宽增长。Q4_K_M 上 KleidiAI 完全 no-op——它只覆盖 Q4_0 和 Q8_0,k-quant 不在微内核覆盖内,这点我们诚实标注,不掩饰。Q4_0 上 KleidiAI 与 llama.cpp 自带 repack 打平,但 KleidiAI 不依赖在线重排,峰值内存更低,决策表凭此择 KleidiAI。Q8_0 上 KleidiAI 离线 decode 真正胜出 repack,是 KleidiAI 收益最大的量化档。这条趋势——位宽越高,KleidiAI 越值——是我们五档消融矩阵干净拆出来的。」

---

### S4(1:30–2:15,45s)serving 放大 KleidiAI 优势

**画面**:
- 看板「并发服务」区段,serving 表格:naive vs kleidiai × Q4_0/Q8_0 × 并发 1/2/4。
- 镜头从 c=1 行扫到 c=4 行,KleidiAI 档吞吐增长曲线明显陡于 naive。
- 右侧 serving note 高亮:「有效并发档 = 1/2/4;c=6 因 n_requests=4<并发上限6 退化(等价 c=4)」。
- 不显示具体 speedup 倍率字幕(数值引看板),只秀曲线形状对比。

**旁白**(45s,约 170 字):
> 「但 KleidiAI 最强的展示场景不是离线,是 serving。用 llama-server 开 continuous batching,跑 naive 对 kleidiai 的并发基准。批处理放大了 KleidiAI 在 prefill 上的优势——并发从 1 涨到 4,KleidiAI 档的吞吐增长远超 naive,相对 speedup 比离线 decode 还大。诚实标注:c=6 因为我们的样本数 n_requests 等于 4 小于并发上限,退化为等价 c=4;要真测 c=6 需把样本数提到 6 以上。serving 才是 KleidiAI 价值放大的舞台。」

---

### S5(2:15–2:45,30s)诚实边界 + 可复用

**画面**:
- 看板「决策表」:每量化一行(体积/最佳档/速度/perplexity/内存/最优路径)。
- 切到「激活探针明细矩阵」:四字段(kleidiai_compiled/active/offloaded + repack_active)+ source 标注,展示运行时真证据。
- 切到「PMU/Performix 实测结论」区段:`arm_spe_present=false` + `conclusion: Performix SPE unavailable; fallback narrative locked`。
- 镜头最后停在脚注「诚实说明」区(NF4 同机对照 / TTFT 推算 / PPL 误差棒 / naive 仍含 NEON)。

**旁白**(30s,约 110 字):
> 「我们坚持诚实边界:激活探针四字段运行时自证,CI 内一致性断言让探针与行为永远对得上;PMU 硬件计数器在 GHA Arm64 VM 被拦、SPE 完全不透传,我们锁 fallback 叙事——用五档消融链当瓶颈分解,不硬吹 Performix。所有结论只用同机 speedup ratio,不同 runner 绝对值不直接比。」

---

### S6(2:45–3:00,15s)收尾:fork 即复现

**画面**:
- README 的「Setup 路径 A」三行命令特写:
  ```
  git clone https://github.com/<你的账号>/ArmInfer-Bench.git
  cd ArmInfer-Bench
  git push origin main   # 触发 CI,~40min 出看板
  ```
- 镜头拉远,显示完整 README 顶部(badge + live 看板链接 + Apache-2.0)。
- 最后 2s 静态画面:仓库地址 + 看板地址 + License 行。

**旁白**(15s,约 50 字):
> 「ArmInfer-Bench,Apache-2.0 开源。fork 即复现,一条命令在真 Arm64 上验证 KleidiAI 的收益。看板链接见 README,欢迎评委盲跑。」

---

## 录制清单(谢特执行)

- [ ] 工具:OBS Studio(录屏 1080p 60fps + 麦克风降噪)。
- [ ] 素材:GitHub 仓库 + GitHub Actions run #28698521281 + live 看板(三者均已公开)。
- [ ] 旁白:按上述文案录,语速 ~4 字/秒,总字数 ~670 字 → 约 2:50,留 10s 缓冲。
- [ ] 背景:无声或免版权轻音乐(推荐无音乐,纯旁白更干净)。
- [ ] 商标:不特写 Arm/GitHub/Qwen logo,只展示产品界面(仓库页/CI 页/看板)。
- [ ] 字幕:数值类(加速比/吞吐)不硬编码字幕,口播「以看板为准」;定性结论可上字幕。
- [ ] 导出:MP4 H.264,≤100MB,公开上传 YouTube/Vimeo/Youku 任一。
- [ ] 时长:严格 ≤3:00,目标 2:50–2:55。

## S3 合规(视频同样适用)

- 视频内**不硬编码会跨轮漂移的 headline 数字**(具体 speedup 倍率/百分比)。展示看板实时值即可,旁白只说趋势(「真胜」「打平」「no-op」「远超」),不说具体倍率。
- `~1.7×` 仅在「repack 内存换速度」语境可口播,不当速度指标。
- 并发措辞:凡提 serving 吞吐,口播「有效并发档 1/2/4,c=6 退化等价 c=4」,不只说「CPU 饱和」。
