# AgentOrch 阶段计划（2026-10）

> 本文件是**阶段目标与执行路径的唯一权威**：阶段划分、每阶段的交付与出口、任务顺序与依赖、当前状态都在这里。
> 设计目标（为什么做、做到什么程度、原则与边界）只在 [PLAN.md](PLAN.md)（1.4 起位于 `docs/`）；字段、状态取值与合并门语义只在 [docs/orchestration.md](orchestration.md) 4.1 与第 5 节，本文件只引用不重述。
> 本文件只经任务分支 + PR 修改。与 `PLAN.md` 的设计目标冲突时，以设计目标为准并停下对齐，不得各写一套。

- 版本：v4.7（Issue #162 登记 DSH 角色工具适配与独立发行包阶段 §16，并刷新 #162 / #207 的真实状态；上一版 v4.6 由 Issue #207 登记 Owner 2026-10-29 效率优先 S0–S4 排序，显式覆盖 #197 的旧建议「#185→#186 后立即主动发行」；再上一版 v4.5 登记 #185 VERIFIED / #186 依赖已满足但仍未 READY；再上一版 v4.4 登记跨专项任务顺序与任务驱动分配 #197，含 Owner 2026-10-28「AVE 先行」排序更新；更早 v4.3 登记 Epic #146 新阶段、#169 维护完成事实与 #174 待设计纠偏；更早 v4.2 记录 Release 1.7.0 已发布 / Format 1.7、Phase D 完成；v4.1 记录 Phase D 完成，v4.0 记录 S5 进行中，v3.9 记录 S4 已完成 / `VERIFIED` 与 S5 `READY`，v3.8 记录 S4 进行中，更早的 v3.7 记录 S3 已完成 / `VERIFIED` 与 S4 `READY`，v3.6 记录 S3 进行中，v3.5 记录 S2 已完成 / `VERIFIED` 与 S3 `READY`，v3.4 记录 S2 进行中，v3.3 记录 S2 `READY`，v3.2 记录 S1 进行中（PR #127），v3.1 / v3.0 记录 Phase D 阶段计划登记、#99–#104 收口与 1.6.3 发布事实、真实 `1.6.2 → 1.6.3` 升级 `APPLIED`）
- 日期：2026-10-31（本次 #162 DSH 阶段登记；历史段落保留各自原记录日期）
- 状态：Phase A / B / C / D 已交付；Epic #46 的 S0–S6 与两项支撑修复均已合并并 `VERIFIED`，Epic 已按阶段审计关闭（[审计评论](https://github.com/ctrlshiftaltdel/AgentOrch/issues/46#issuecomment-6415751115)）；**`v1.7.0` 是最新发布**（[Release v1.7.0](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.7.0)，tag `v1.7.0` → `ac3e87f`，资产 `AgentOrch-1.7.0.zip`，602,273 bytes，SHA256 `cbc35fcf…`，与 GitHub API `assets[].digest` 一致），发布属性为 Format 1.7、1047 tests、117 个 `REQUIRED` 文件（发行包 118 = 117 + `MANIFEST.json`）、71/71 场景、四平台 CI 通过（run `40598836550` @ `ac3e87f`），真实 `1.6.3 → 1.7.0` 隔离升级结果为 `APPLIED`（§12.1）；上一个发布 `v1.6.3`（[Release v1.6.3](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.6.3)，tag `v1.6.3` → `2adf431`，资产 `AgentOrch-1.6.3.zip`，461,829 bytes，SHA256 `dcce5b24…`）为 Format 1.6、754 tests、92 个 `REQUIRED` 文件（发行包 93 = 92 + `MANIFEST.json`）、58/58 场景、四平台 CI 通过，真实 `1.6.2 → 1.6.3` 隔离升级结果为 `APPLIED`（§12.2）；再上一个发布 `v1.6.2`（[Release v1.6.2](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.6.2)，资产 `AgentOrch-1.6.2.zip`，SHA256 `096e1fba…`）为 Format 1.6、715 tests、91 文件包（90 `REQUIRED` + `MANIFEST.json`）、58/58 场景、四平台 CI 通过；#85、#86、#93、#95、#99–#104、#113 与 #142 均已 `VERIFIED`；[#73](https://github.com/ctrlshiftaltdel/AgentOrch/issues/73) 已由 PR [#171](https://github.com/ctrlshiftaltdel/AgentOrch/pull/171) 合并 `876b8d54eb6a69b77c55f6de9a2523e8c1ba2e93` 并 `VERIFIED`（[主分支验证](https://github.com/ctrlshiftaltdel/AgentOrch/issues/73#issuecomment-6468978648)：124 `REQUIRED`、1050 tests、71/71 场景），#71、#72 更早已由后续任务收口并 `VERIFIED`——Epic #46 审计登记的三项 `NON_BLOCKING_HARDENING` 至此全部完成；Epic 之外的 validator 维护任务 [#169](https://github.com/ctrlshiftaltdel/AgentOrch/issues/169) / PR [#159](https://github.com/ctrlshiftaltdel/AgentOrch/pull/159) 已于 2026-10-28 合并 `6893a2e` 并 `VERIFIED`（117 `REQUIRED`、1050 tests、71/71 场景、四平台 CI 通过）；**Phase D（Agent 角色发现、Subagent 能力考核与动态模型选型）已按 §13 登记**：设计冻结 #117 已通过 PR #118 合并 `ceb2c0a` 并 `VERIFIED`，阶段计划登记 #125 已 `VERIFIED`；S1 #120 已 `VERIFIED`（PR #127 合并 `818adaa`；主分支 813 tests / 98 `REQUIRED` / 58/58 / `build_release` SHA `e32dda30…`）；S2 #121 已 `VERIFIED`（PR #130 合并 `9c9b1d2`；主分支 888 tests / 105 `REQUIRED` / 58/58 / `build_release` SHA `e879a3ea…`）；S3 #122 已 `VERIFIED`（PR #133 合并 `2b0fd3d`；主分支 957 tests / 114 `REQUIRED` / 58/58 / `build_release` SHA `ee941c7e…`）；S4 #123 已 `VERIFIED`（PR #136 合并 `86463ec`；主分支 1016 tests / 116 `REQUIRED` / 58/58 / `build_release` SHA `9370400f…`）；S5 #124 已 `VERIFIED`（PR #139 合并 `b18d3b2`；主分支 1047 tests / 117 `REQUIRED` / 71/71 场景 / `build_release` SHA `19ca7e8e…`）；S1–S5 全部完成，Phase D 完成，Epic #119 已关闭；Phase D 是新增阶段，不改写既有发布、硬化与验收结论；**新阶段（Epic [#146](https://github.com/ctrlshiftaltdel/AgentOrch/issues/146)：项目规范治理、旧项目隔离接入与 ETR 驱动评价选型）已按 §14 登记**：设计冻结 #147 / PR #148 已合并 `3528e3f` 并 `VERIFIED`，阶段计划登记 #149 已由 PR [#175](https://github.com/ctrlshiftaltdel/AgentOrch/pull/175) 合并 `dedb6f9` 并 `VERIFIED`（[主分支验证](https://github.com/ctrlshiftaltdel/AgentOrch/issues/149#issuecomment-6470004668)），#150–#158 尚未 `READY`；[#174](https://github.com/ctrlshiftaltdel/AgentOrch/issues/174) 为方向已批准、设计未冻结的纠偏任务（见 §14.4）；**跨专项顺序、条件边与建议优先级见 §15**（Issue [#197](https://github.com/ctrlshiftaltdel/AgentOrch/issues/197)；只登记协调事实，不授权发行或降低验证）
- 适用范围：AgentOrch 核心规则、local/github edition、任务模板、验证脚本与发行机制
- 设计目标来源：[PLAN.md](PLAN.md)

## 1. 当前状态（2026-10-30）

状态变化的唯一落点就是这张表；`PLAN.md` 的设计目标不随任务状态变化。

| 任务 | 交付重点 | 依赖 | 状态 | 依据 |
|---|---|---|---|---|
| [#2](https://github.com/ctrlshiftaltdel/AgentOrch/issues/2) 结构化验收证据矩阵 | 任务字段、证据映射、负向 / 迁移场景、结构测试 | Epic [#1](https://github.com/ctrlshiftaltdel/AgentOrch/issues/1) | 已完成 | Issue 已关闭；字段契约落在 `docs/orchestration.md` 4.1 |
| [#4](https://github.com/ctrlshiftaltdel/AgentOrch/issues/4) edition 权威来源与 fail-closed | 区分项目底座与 CLI 配置、合法 / 非法 / 损坏配置测试 | Epic #1 | 已完成 | Issue 已关闭；`docs/PROJECT.md` 的 `edition` 为唯一权威 |
| [#14](https://github.com/ctrlshiftaltdel/AgentOrch/issues/14) GitHub Actions 接入 | PR 上跑规则包校验、单元测试与语法检查 | Epic #1 | 已完成 | Issue 已关闭；`.github/workflows/ci.yml` |
| [#15](https://github.com/ctrlshiftaltdel/AgentOrch/issues/15) 根 `PLAN.md` 与 `init` 初始化 | `init` 生成设计目标文件；阶段目标与执行路径由 #19 拆分到本文件 | #2 | 已完成 | PR #18 已合并；`init` 冒烟与单元测试 |
| [#19](https://github.com/ctrlshiftaltdel/AgentOrch/issues/19) 拆分计划层 | `PLAN.md` 只写设计目标，阶段目标与执行路径移入本文件 | #15 | 已完成 | 本文件 v2.0 与 `templates/implementation-plan.md` |
| [#5](https://github.com/ctrlshiftaltdel/AgentOrch/issues/5) 共享资源锁契约 | 冻结 shared/exclusive、所有权、租约、恢复语义 | #2（模板字段已就绪） | 已完成 | PR #21 已合并（`d89f1f7`）；`docs/design/resource-locks.md` v1 |
| [#3](https://github.com/ctrlshiftaltdel/AgentOrch/issues/3) 设计契约与阶段收口审计模板 | 冻结项 / 待决项、跨模块不变量、审计分类 | #2（可先起草）、#5 口径 | 已完成 | PR #24 已合并（`bea370c`）；三份模板 + 章节校验 |
| [#6](https://github.com/ctrlshiftaltdel/AgentOrch/issues/6) 具名资源锁实现 | 原子获取 / 释放、并发测试、冲突原因、人工恢复 | #5 | 已完成 | PR #23 已合并（`53a0ca6`）；`task resources/renew` 与并发演练 |
| [#7](https://github.com/ctrlshiftaltdel/AgentOrch/issues/7) 发行清单与漂移诊断 | 版本、核心 SHA-256、只读差异报告、打包完整性 | Epic #1 | 已完成 | PR #22 已合并（`d085092`）；`MANIFEST.json` + `check_drift.py` |
| [#8](https://github.com/ctrlshiftaltdel/AgentOrch/issues/8) 运行时能力登记与交付指标 | 能力证据、新鲜度、指标口径、缺失数据 | #2 | 已完成 | PR #26 已合并（`25d1573`）；`report_metrics.py` 七项指标 |
| [#13](https://github.com/ctrlshiftaltdel/AgentOrch/issues/13) edition 旧格式升级说明 | 发布前的迁移说明 | #4 | 已完成 | PR #27 已合并（`870109a`）；README §10 + `docs/upgrade.md` |
| [#17](https://github.com/ctrlshiftaltdel/AgentOrch/issues/17) 发布自检纳入 CI | 把 `build_release` 自检放进流水线 | #14（已满足） | 已完成 | PR #25 已合并（`53a86fc`）；CI 两档跑打包自检，`VERSION.md` 1.3 |
| [#9](https://github.com/ctrlshiftaltdel/AgentOrch/issues/9) 合成回归与通用验收集成 | 故障注入、集成矩阵、失败演练、发布出口 | #2 / #3 / #4 / #5；最终验收等 #6–#8 | 已完成（随本 PR 合并生效） | 本 PR：`scripts/run_delivery_scenarios.py` + 场景文档 + 验收程序 I 段 |
| [#169](https://github.com/ctrlshiftaltdel/AgentOrch/issues/169) | `validate_kit` 文档扫描跳过 vendored `node_modules`（Epic #46 之外的维护任务） | 无阻塞依赖 | 已完成 / `VERIFIED` | PR [#159](https://github.com/ctrlshiftaltdel/AgentOrch/pull/159) 合并 `6893a2eb808eae8e16e5d5f595aef0b7a41a54d3`；主分支 117 `REQUIRED`、1050 tests、71/71 场景、四平台 CI 通过；Issue 已关闭并标 `status:verified` |
| [#146](https://github.com/ctrlshiftaltdel/AgentOrch/issues/146) | Epic：项目规范治理、旧项目隔离接入与 ETR 驱动评价选型 | 汇总 #147、#149、#150–#158 与 #174 | 进行中（Epic；仅设计冻结与阶段计划登记收口） | Epic 正文；§14 |
| [#147](https://github.com/ctrlshiftaltdel/AgentOrch/issues/147) | 新阶段设计冻结：文件责任、核心契约迁移、`agentorch/` 接入、ETR 与三角色评价 | #146 | 已完成 / `VERIFIED` | 设计文稿 [next-phase-governance-and-agent-performance.md](design/next-phase-governance-and-agent-performance.md)；PR [#148](https://github.com/ctrlshiftaltdel/AgentOrch/pull/148) 合并 `3528e3ff5a8e868f92b8b2a36c523823cfe9ad6e`；Issue 已关闭并标 `status:verified` |
| PR [#148](https://github.com/ctrlshiftaltdel/AgentOrch/pull/148) | 承载 #147 设计文稿的合并载体（base `main`） | #147（同一交付） | 已合并（交付随 #147 `VERIFIED`） | PR 时间线；合并提交 `3528e3ff5a8e868f92b8b2a36c523823cfe9ad6e` |
| [#149](https://github.com/ctrlshiftaltdel/AgentOrch/issues/149) | 登记 Epic #146 新阶段、依赖与真实状态 | #147 / PR #148 已 `VERIFIED`；不依赖 #174 | 已完成 / `VERIFIED` | PR [#175](https://github.com/ctrlshiftaltdel/AgentOrch/pull/175) 合并 `dedb6f9`；[主分支验证](https://github.com/ctrlshiftaltdel/AgentOrch/issues/149#issuecomment-6470004668) |
| [#150](https://github.com/ctrlshiftaltdel/AgentOrch/issues/150) | GOV-1 文件责任地图与文档生命周期 | #147 / PR #148、#149（均已 `VERIFIED`） | 未 READY（`OPEN` / `PLANNED`；依赖已满足，等待 Master 置 READY） | Issue 正文；§14.1 / §15.2.1 |
| [#151](https://github.com/ctrlshiftaltdel/AgentOrch/issues/151) | GOV-2 核心编排契约迁移到 `docs/orchestration.md`，保持发行 / 升级兼容 | #150、#169（已 `VERIFIED`）、#162（计划文件顺序） | 未 READY（`OPEN` / `PLANNED`；#150 未 `VERIFIED`） | Issue 正文；§14.1 / §15.3 |
| [#152](https://github.com/ctrlshiftaltdel/AgentOrch/issues/152) | GOV-3 精简本文件并治理过期规划文档 | #150、#151 | 未 READY（`OPEN` / `PLANNED`；#150 / #151 未 `VERIFIED`） | Issue 正文；§14.1 / §15.3 |
| [#153](https://github.com/ctrlshiftaltdel/AgentOrch/issues/153) | I1 旧项目 `agentorch/` 隔离接入、根 `AGENTS.md` 增量区块与安全升级 | #150、#151、#152（共享 `docs/README.md`，先做计划清理） | 未 READY（`OPEN` / `PLANNED`；#150–#152 未 `VERIFIED`） | Issue 正文；§14.1 / §15.3 |
| [#154](https://github.com/ctrlshiftaltdel/AgentOrch/issues/154) | GOV-4 文件责任、文档影响与计划状态一致性门禁 | #150–#153 | 未 READY（`OPEN` / `PLANNED`；#150–#153 未 `VERIFIED`） | Issue 正文；§14.1 / §15.3 |
| [#155](https://github.com/ctrlshiftaltdel/AgentOrch/issues/155) | E1 真实 Token telemetry、ETR、覆盖率与阈值门槛 | #147 / PR #148、#149（均已 `VERIFIED`） | 未 READY（`OPEN` / `PLANNED`；依赖已满足，等待 Master 置 READY） | Issue 正文；§14.1 / §14.3 / §15.2.1 |
| [#156](https://github.com/ctrlshiftaltdel/AgentOrch/issues/156) | E2 三角色互评、双评佐证与评价者可信度 | #155 | 未 READY（`OPEN` / `PLANNED`；#155 未 `VERIFIED`） | Issue 正文；§14.1 / §15.3 |
| [#157](https://github.com/ctrlshiftaltdel/AgentOrch/issues/157) | E3 ETR / 可信度接入预配置 Subagent 选型，Master 手动切换 | #155、#156 | 未 READY（`OPEN` / `PLANNED`；#155 / #156 未 `VERIFIED`） | Issue 正文；§14.1 / §15.3 |
| [#158](https://github.com/ctrlshiftaltdel/AgentOrch/issues/158) | V0 新阶段端到端回归、发行验收与文档同步 | #73（已 `VERIFIED`）、#153–#157 | 未 READY（`OPEN` / `PLANNED`；#153–#157 未 `VERIFIED`） | Issue 正文；§14.1 / §15.3 |
| [#160](https://github.com/ctrlshiftaltdel/AgentOrch/issues/160) | Epic：DSH 角色工具适配与独立发行包 | #161 / #172 已 `VERIFIED`；#151 / #153 仅条件约束 #164 / #167 | 规划中（Epic `OPEN`；#162 已派发、未 `VERIFIED`；#163–#167 未 READY） | Issue 正文；§15.2.2 / §16 |
| [#161](https://github.com/ctrlshiftaltdel/AgentOrch/issues/161) | 冻结 DSH 适配器与三角色工具设计 | 无 | 已完成 / `VERIFIED` | PR [#168](https://github.com/ctrlshiftaltdel/AgentOrch/pull/168) 合并 `3f787c6`；设计文稿 `FROZEN`（≠ 运行时已验证） |
| [#172](https://github.com/ctrlshiftaltdel/AgentOrch/issues/172) | 将 DSH 设计文稿状态标为 `FROZEN` | #161 | 已完成 / `VERIFIED` | PR [#173](https://github.com/ctrlshiftaltdel/AgentOrch/pull/173) 合并 `4e72e8f`；只证明设计状态，不证明运行时 |
| [#162](https://github.com/ctrlshiftaltdel/AgentOrch/issues/162) | P1 将 DSH 角色工具阶段登记到唯一实施计划 | #161 / #172 / #149（均已 `VERIFIED`）；计划文件顺序 #149 → #162 → #151 → #152 | 进行中（`OPEN` / `status:in-progress` / `DISPATCHED`；本任务 PR 未合并、未 `VERIFIED`） | Issue 登记块（2026-10-31 派发）；§16 |
| [#163](https://github.com/ctrlshiftaltdel/AgentOrch/issues/163) | DSH 公开 API、角色级工具隔离与上下文核验 | #161（已 `VERIFIED`） | 未 READY（`OPEN` / `PLANNED`） | Issue 正文；§15.2.2 |
| [#164](https://github.com/ctrlshiftaltdel/AgentOrch/issues/164) | 实现 DSH 三角色工具与安全适配器 | #161–#163、#162；#151 / #153 条件依赖 | 未 READY（`OPEN` / `PLANNED`；#162 / #163 未 `VERIFIED`） | Issue 正文；§15.2.2 / §15.3 |
| [#165](https://github.com/ctrlshiftaltdel/AgentOrch/issues/165) | DSH bundle 独立构建、可信哈希基线与可复现自检 | #161、#163、#162 | 未 READY（`OPEN` / `PLANNED`；#162 / #163 未 `VERIFIED`） | Issue 正文；§15.2.2 / §15.3 |
| [#166](https://github.com/ctrlshiftaltdel/AgentOrch/issues/166) | DSH CI 与同 tag 双资产发布手册 | #161、#163、#165 | 未 READY（`OPEN` / `PLANNED`；#163 / #165 未 `VERIFIED`） | Issue 正文；§15.2.2 |
| [#167](https://github.com/ctrlshiftaltdel/AgentOrch/issues/167) | DSH 隔离、迁移、发行与实机验收 | #162–#166、#161；#151 / #153 / #154 条件依赖 | 未 READY（`OPEN` / `PLANNED`；#162–#166 未全部 `VERIFIED`） | Issue 正文；§15.2.2 |
| [#170](https://github.com/ctrlshiftaltdel/AgentOrch/issues/170) | 修复 Reviewer 独立性绕过：可信底层模型身份与显式别名映射 | 无阻塞依赖；约束 #156 / #157 / #179 的相关审查路径 | 未 READY（`OPEN` / `bug`；身份来源、核验与失效规则未冻结；S0-2 只是路径队列） | Issue 正文；§15.2.6 / §15.5 / §15.8 |
| [#174](https://github.com/ctrlshiftaltdel/AgentOrch/issues/174) | 设计运行时快速探测与受控子代理协作边界（C1–C4 风险提示化的纠偏设计） | #147 / #149 已 `VERIFIED`；#149 不依赖本任务 | 未 READY（`OPEN` / `status:blocked`；方向已批准，设计未冻结；#198 / #199 已 `VERIFIED`） | Issue 正文；[所有者决定](https://github.com/ctrlshiftaltdel/AgentOrch/issues/174#issuecomment-6468107357)；§14.4 / §15.2.6 |
| [#176](https://github.com/ctrlshiftaltdel/AgentOrch/issues/176) | Epic：Task-Driven Agent Topology | #183 / PR #182 已 `VERIFIED`；#177–#181 未 READY | 规划中（Epic `OPEN`；设计仍 `PROPOSED`） | Issue 正文；§15.2.3 |
| [#177](https://github.com/ctrlshiftaltdel/AgentOrch/issues/177) | A / P0：冻结 Role≠Agent Instance 编排契约 | 无 | 未 READY（`OPEN` / `PLANNED`） | Issue 正文；§15.2.3 |
| [#178](https://github.com/ctrlshiftaltdel/AgentOrch/issues/178) | B / P1：任务分类器与只读拓扑推荐 | #177 | 未 READY（`OPEN` / `PLANNED`；#177 未 `VERIFIED`） | Issue 正文；§15.2.3 |
| [#179](https://github.com/ctrlshiftaltdel/AgentOrch/issues/179) | C / P1：执行拓扑与运行时能力适配 | #178；#170 仅约束相关审查路径 | 未 READY（`OPEN` / `PLANNED`；#178 未 `VERIFIED`） | Issue 正文；§15.2.3 / §15.5 |
| [#180](https://github.com/ctrlshiftaltdel/AgentOrch/issues/180) | D / P1：端到端目标达成评测与回退 | #177、#178 | 未 READY（`OPEN` / `PLANNED`；#177 / #178 未 `VERIFIED`；可与 #179 条件并行） | Issue 正文；§15.3 |
| [#181](https://github.com/ctrlshiftaltdel/AgentOrch/issues/181) | E / P2：试点、双底座验收与发行兼容 | #179、#180；核心权限变更须 Owner 批准 | 未 READY（`OPEN` / `PLANNED`；#179 / #180 未 `VERIFIED`） | Issue 正文；§15.3 |
| [#183](https://github.com/ctrlshiftaltdel/AgentOrch/issues/183) | 恢复并交付任务驱动拓扑设计提案 | PR #182（同一交付） | 已完成 / `VERIFIED` | PR [#182](https://github.com/ctrlshiftaltdel/AgentOrch/pull/182) 合并 `5c3b933`；[主分支验证](https://github.com/ctrlshiftaltdel/AgentOrch/issues/183#issuecomment-6471323341)；设计仍 `PROPOSED` |
| [#184](https://github.com/ctrlshiftaltdel/AgentOrch/issues/184) | Epic：Adaptive Verification（AVE） | #199 / PR #190 与 #185 已 `VERIFIED`；#186–#189 未 READY | 规划中（Epic `OPEN`；设计仍 `PROPOSED`） | Issue 正文；§15.2.4 |
| [#185](https://github.com/ctrlshiftaltdel/AgentOrch/issues/185) | A / P0：验证成本基线与门禁审计 | 无功能硬依赖；审计在途任务 | 已完成 / `VERIFIED` | PR [#201](https://github.com/ctrlshiftaltdel/AgentOrch/pull/201) 合并并验证 head `a09fb2e11d2d5d1467b2e07c0ba23854cda1adbe`；[VERIFIED 评论](https://github.com/ctrlshiftaltdel/AgentOrch/issues/185#issuecomment-6484091982)；Issue 已关闭并标 `status:verified` |
| [#186](https://github.com/ctrlshiftaltdel/AgentOrch/issues/186) | B / P0：风险分级与只读影子 Gate Planner | #185 已 `VERIFIED` | 未 READY（`OPEN` / `PLANNED`；设计 DRAFT 未书面冻结） | Issue 正文；[DRAFT v2](https://github.com/ctrlshiftaltdel/AgentOrch/issues/186#issuecomment-6486678110)；§15.2.4 / §15.5 |
| [#187](https://github.com/ctrlshiftaltdel/AgentOrch/issues/187) | C / P1：影响面测试选择与证据等价性 | #186 | 未 READY（`OPEN` / `PLANNED`；#186 未 `VERIFIED`） | Issue 正文；§15.2.4 |
| [#188](https://github.com/ctrlshiftaltdel/AgentOrch/issues/188) | D / P1：Owner 授权后试点风险驱动合并门 | #187；Owner 另行授权 | 未 READY（`OPEN` / `PLANNED`；#187 未 `VERIFIED`；生产门禁未授权） | Issue 正文；§15.5 |
| [#189](https://github.com/ctrlshiftaltdel/AgentOrch/issues/189) | E / P1：AVE 双底座发布兼容与校准 | #188；DSH 按实际进度 | 未 READY（`OPEN` / `PLANNED`；#188 未 `VERIFIED`） | Issue 正文；§15.2.4 |
| [#191](https://github.com/ctrlshiftaltdel/AgentOrch/issues/191) | Epic：主动式 SemVer 发行节奏与版本治理 | #198 / PR #196 已 `VERIFIED`；#192–#195 未 READY | 规划中（Epic `OPEN`；设计仍 `PROPOSED`） | Issue 正文；§15.2.5 |
| [#192](https://github.com/ctrlshiftaltdel/AgentOrch/issues/192) | A / P0：冻结发行分类、批次策略与常设授权边界 | 专项设计 PR #196 已合并；Owner 授权 | 未 READY（`OPEN` / `PLANNED`；常设授权边界待冻结） | Issue 正文；§15.5 |
| [#193](https://github.com/ctrlshiftaltdel/AgentOrch/issues/193) | B / P0：解耦 Release SemVer 与 Format version | #192 | 未 READY（`OPEN` / `PLANNED`；#192 未 `VERIFIED`） | Issue 正文；§15.5 |
| [#194](https://github.com/ctrlshiftaltdel/AgentOrch/issues/194) | C / P1：Master 主动发行准备与批次规划 | #192、#193 | 未 READY（`OPEN` / `PLANNED`；#192 / #193 未 `VERIFIED`） | Issue 正文；§15.2.5 |
| [#195](https://github.com/ctrlshiftaltdel/AgentOrch/issues/195) | D / P1：常规发行与 Kit / DSH 同版本交付验证 | #193、#194；Owner 预授权与 DSH 实际进度 | 未 READY（`OPEN` / `PLANNED`；#193 / #194 未 `VERIFIED`） | Issue 正文；§15.5 |
| [#198](https://github.com/ctrlshiftaltdel/AgentOrch/issues/198) | 恢复并交付主动发行设计提案 | PR #196（同一交付） | 已完成 / `VERIFIED` | PR [#196](https://github.com/ctrlshiftaltdel/AgentOrch/pull/196) 合并 `c1f2745`；[主分支验证](https://github.com/ctrlshiftaltdel/AgentOrch/issues/198#issuecomment-6472660211)；设计仍 `PROPOSED` |
| [#199](https://github.com/ctrlshiftaltdel/AgentOrch/issues/199) | 恢复并交付自适应验证设计提案 | PR #190（同一交付） | 已完成 / `VERIFIED` | PR [#190](https://github.com/ctrlshiftaltdel/AgentOrch/pull/190) 合并 `16753d9`；[主分支验证](https://github.com/ctrlshiftaltdel/AgentOrch/issues/199#issuecomment-6473226701)；设计仍 `PROPOSED` |
| [#202](https://github.com/ctrlshiftaltdel/AgentOrch/issues/202) | docs(plan)：同步 #185 `VERIFIED` 与 #186 依赖状态 | #185 已 `VERIFIED`；单占 `docs/implementation-plan.md` | 已完成 / `VERIFIED` | PR [#203](https://github.com/ctrlshiftaltdel/AgentOrch/pull/203) 合并 `c812666781b28a3a893d1d469f5f4f98bd7f65d4`；Issue 已关闭并标 `status:verified` |
| PR [#203](https://github.com/ctrlshiftaltdel/AgentOrch/pull/203) | 承载 #202 的合并载体（base `main`） | #202（同一交付） | 已合并（交付随 #202 `VERIFIED`） | PR 时间线；合并提交 `c812666781b28a3a893d1d469f5f4f98bd7f65d4`；本文件 v4.5 即其合并内容 |
| [#204](https://github.com/ctrlshiftaltdel/AgentOrch/issues/204) | 消除合并后 backup 与 `.orchestration` 禁改政策的重复阻塞 | 无功能硬依赖；#185 / #202 只是证据输入，不是前置；**不依赖 #207**；S0-1 是处理顺序，不是 `#202 → #204` 边 | 已完成 / `VERIFIED` | PR [#208](https://github.com/ctrlshiftaltdel/AgentOrch/pull/208) 合并 `b64de061f32176acf896fda4de3c801007ecd132`；Issue 已关闭并标 `status:verified` |
| PR [#208](https://github.com/ctrlshiftaltdel/AgentOrch/pull/208) | 承载 #204 政策维护的合并载体（base `main`） | #204（同一交付） | 已合并（交付随 #204 `VERIFIED`） | PR 时间线；合并提交 `b64de061f32176acf896fda4de3c801007ecd132` |
| [#205](https://github.com/ctrlshiftaltdel/AgentOrch/issues/205) | 修复新总日期掩盖过期 C1–C4 单项证据的资格误判 | 就绪治理门：#207 合并并 `VERIFIED`（§15.8）；代码路径序 #205 → #206 → #170 | 未 READY（`OPEN` / `bug`；未派发） | Issue 正文；§15.8；S0-2 顺位不使其 `READY` |
| [#206](https://github.com/ctrlshiftaltdel/AgentOrch/issues/206) | 修复空路径通配与父目录越界导致的权限覆盖误判 | 就绪治理门：#207 合并并 `VERIFIED`（§15.8）；代码路径序 #205 → #206 → #170 | 未 READY（`OPEN` / `bug`；未派发） | Issue 正文；§15.8 |
| [#207](https://github.com/ctrlshiftaltdel/AgentOrch/issues/207) | 登记 Owner 2026-10-29 效率优先 S0–S4 排序到本文件 §1 / §15 | #202 / PR #203 已 `VERIFIED`（共享计划文件）；#147 / #149 已 `VERIFIED`。**#204 是已完成的排程事实，不是 `#204 → #207` 功能边** | 已完成 / `VERIFIED` | PR [#209](https://github.com/ctrlshiftaltdel/AgentOrch/pull/209) 合并 `578022ab837033ede96579cb71ba7750e14cc758`；Issue 已关闭并标 `status:verified`；§1.1 / §15.8 |
| PR [#182](https://github.com/ctrlshiftaltdel/AgentOrch/pull/182) | 承载任务驱动拓扑设计提案的合并载体 | #183（同一交付） | 已合并（交付随 #183 `VERIFIED`） | PR 时间线；合并提交 `5c3b9339d676224ed7b64dd2b610460816739a72`；设计仍 `PROPOSED` |
| PR [#190](https://github.com/ctrlshiftaltdel/AgentOrch/pull/190) | 承载自适应验证设计提案的合并载体 | #199（同一交付） | 已合并（交付随 #199 `VERIFIED`） | PR 时间线；合并提交 `16753d96102754062e42c327e0ea244b4516d093`；设计仍 `PROPOSED` |
| PR [#196](https://github.com/ctrlshiftaltdel/AgentOrch/pull/196) | 承载主动发行设计提案的合并载体 | #198（同一交付） | 已合并（交付随 #198 `VERIFIED`） | PR 时间线；合并提交 `c1f274593fd8ad6f3aa5ee53e06ab0040f425470`；设计仍 `PROPOSED` |

已完成只代表交付完成，不代表该阶段已验证；每条交付仍以对应 PR 的逐条验收结果、以及合并后主分支上重跑的结果为准。Phase A / B / C 的原有任务已交付。Epic [#46](https://github.com/ctrlshiftaltdel/AgentOrch/issues/46) 的 S0–S6（[#47](https://github.com/ctrlshiftaltdel/AgentOrch/issues/47)–[#52](https://github.com/ctrlshiftaltdel/AgentOrch/issues/52)）、首批经验落地（[#55](https://github.com/ctrlshiftaltdel/AgentOrch/issues/55)、[#56](https://github.com/ctrlshiftaltdel/AgentOrch/issues/56)）与两项支撑修复（[#62](https://github.com/ctrlshiftaltdel/AgentOrch/issues/62)、[#66](https://github.com/ctrlshiftaltdel/AgentOrch/issues/66)）均已合并并按任务载体标为 `VERIFIED`；最终验收任务 [#53](https://github.com/ctrlshiftaltdel/AgentOrch/issues/53) 经 PR [#70](https://github.com/ctrlshiftaltdel/AgentOrch/pull/70) 合并 `6ac9573` 后标为 `VERIFIED` 并关闭。Epic #46 已于 2026-10-26 按 `templates/milestone-audit.md` 完成阶段审计并关闭（[审计评论](https://github.com/ctrlshiftaltdel/AgentOrch/issues/46#issuecomment-6415751115)），审计无 `BLOCKING` 项。审计登记的三项 `NON_BLOCKING_HARDENING` 中，[#71](https://github.com/ctrlshiftaltdel/AgentOrch/issues/71)（真实 Release 升级，PR #82 合并 `4a7069e`）与 [#72](https://github.com/ctrlshiftaltdel/AgentOrch/issues/72)（跨平台实机，由 #77 的跨平台 CI 收口，PR #79 合并 `e26902f`）已完成并标 `status:verified`；[#85](https://github.com/ctrlshiftaltdel/AgentOrch/issues/85)（下游包修复，PR #87 合并 `72a39e4`）与 [#86](https://github.com/ctrlshiftaltdel/AgentOrch/issues/86)（发布 1.6.1，PR #88 合并 `baa5c91`）也已 `VERIFIED`，`v1.6.1` 已发布（资产 `AgentOrch-1.6.1.zip`，SHA256 `7b70d722…`）。1.6.1 之后的补丁链同样收口：[#93](https://github.com/ctrlshiftaltdel/AgentOrch/issues/93)（`task add` 重复多值选项修复，PR [#94](https://github.com/ctrlshiftaltdel/AgentOrch/pull/94) 合并 `35fb924`）与 [#95](https://github.com/ctrlshiftaltdel/AgentOrch/issues/95)（发布 1.6.2，PR [#96](https://github.com/ctrlshiftaltdel/AgentOrch/pull/96) 合并 `bab65b6`）已 `VERIFIED`，`v1.6.2` 已发布（[Release v1.6.2](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.6.2)，资产 `AgentOrch-1.6.2.zip`，SHA256 `096e1fba…`；Format 1.6、715 tests、58/58 场景、四平台 CI 通过）。1.6.2 发布后的 [#99](https://github.com/ctrlshiftaltdel/AgentOrch/issues/99)–[#104](https://github.com/ctrlshiftaltdel/AgentOrch/issues/104) 六项（对应 PR #105–#110）也已合并并 `VERIFIED`，同样不属于审计硬化项；#104 合并后的 `main`（`51e418f`）的 754 tests、92 个 `REQUIRED` 文件（发行包 93）、58/58 场景与四平台 CI 反映的是这六项合并后的状态；`v1.6.3` 已在其上于 2026-10-27 发布（tag `v1.6.3` → `2adf431`，详见 §12.2）。[#73](https://github.com/ctrlshiftaltdel/AgentOrch/issues/73)（场景运行器拆分）也已由 PR [#171](https://github.com/ctrlshiftaltdel/AgentOrch/pull/171) 合并 `876b8d54eb6a69b77c55f6de9a2523e8c1ba2e93` 并 `VERIFIED`（证据见 §11 的历史登记与[主分支验证](https://github.com/ctrlshiftaltdel/AgentOrch/issues/73#issuecomment-6468978648)）；不得据此宣称真实 1.6.0 → 1.6.1、1.6.0 / 1.6.1 → 1.6.2 升级、真实 1.3 / 1.4 资产升级、以 **1.6.0 / 1.6.1** 为已安装基线、真实断网、HD-11 或非 GitHub-hosted 平台已验证。

### 1.1 Owner 最新排程（2026-10-29，Issue #207）

Owner 于 2026-10-29 明确要求“按项目规则发送整改 issues（不用亲自实施），最高优先级；优化后续 issue 顺序，以推进效率为目标”。本节登记的 S0–S4 队列是当前调度顺序，**显式覆盖 #197 / PR #200 的旧建议「#185→#186 后立即主动发行」**（§15.1 / §15.3.4 保留为历史记录）。覆盖只改变优先级：既有功能硬依赖、设计冻结、`READY` 门与发布 / 生产门禁的专属授权继续有效，不因排序变化而消失。完整理由、放行条件、路径序与无环核对见 §15.8；本节只登记顺序摘要，不授予任何状态。

1. **S0-1 / P0**：#204 备份政策自举整改 → #202 / PR #203 收口 → 本计划登记任务（#207）。#204 / PR #208 与 #202 / PR #203 均已 `VERIFIED`；这是处理顺序，不是 `#202 → #204` 或 `#204 → #207` 功能边；#207 已由 PR #209 合并并 `VERIFIED`。
2. **S0-2 / P0**：#205 单项证据时间 → #206 路径权限 → #170 可信模型身份。同一 `runtime_adapter` / 测试 / 设计文件必须串行，每个任务 `VERIFIED` 后从新基线派下一个；三者均未 READY。
3. **S1-1 / P1**：#186 书面冻结及完整任务包 → 只读 Planner 实现 / 验证 → #187。优先拿到可验证的提效工具与对照数据；#186 仍为 DRAFT v2，排程不使其冻结或 `READY`；#187 仍等 #186 `VERIFIED`。
4. **S1-2 / P1**：#174 快速探测 / 告警策略设计；#150 文件责任地图；#162 DSH 计划登记；#163 DSH API 核验。各自前置齐备且与主线路径不相交时可作为独立入口；#174 仍只交付设计，冻结后由 Master 另行登记最小契约与选择器原子切换任务；#162 的计划路径已由 #207 释放并在 2026-10-31 派发（本任务，未 `VERIFIED`）；#163 不必等待 DSH 实现链。
5. **S2 / P1**：#192 发行分类与授权范围 → #193 Release/Format 兼容解耦 → #194 只读发行准备。保留 v1.7.0 旧客户端桥接正负验收；未获常设授权不真实发布，也不因 DSH 未交付而伪造双资产。
6. **S3 / P1**：#155 真实 Token / ETR 基础；#177 拓扑契约 → #178 只读推荐 → #180 对照评测。先可观测、再证明编排收益；#180 可与 #179 在无路径冲突时分开推进；未接入的 telemetry 明确 `UNKNOWN`。
7. **S4 / P2**：#156 → #157；#179 → #181；#151 → #152 → #153 → #154 → #158；DSH 侧 #164 / #165 在 #162 / #163 后按真实路径串行 → #166 → #167 → #195。评价 / 自动接线与大范围目录迁移在上述提效主线之后，各自完整前置保留。
8. **条件项 / 授权门**：#188 → #189，只在 #186 / #187 等 A–C `VERIFIED` 且 Owner 单独明确授权后启动生产门禁试点。等待该决定不得阻塞独立可交付任务；本轮“提效”指令不代替该授权。

- 一次只选最高顺位中已就绪、可独立验收的任务；可以完成不依赖阻塞决定的只读准备，但不能把等待中的任务标 `READY`。
- 不设固定并发数；共享 `docs/implementation-plan.md`、`runtime_adapter.py`、测试目录、设计文件或父子路径一律按实际锁串行。各 Issue 允许范围含 `docs/README.md` 时，#150 等任务也可能与整改相交，必须重新检查，不能照表直接并行。
- 计划文件路径序 **#202 / PR #203 → #207 → #162 → #151 → #152**，代码共享路径序 **#205 → #206 → #170**。两者都是路径队列，不是新的功能依赖；临时路径冲突不永久化为功能依赖。
- 没有任务包、真实执行实例与路径冲突检查，不标 `READY` 或 `DISPATCHED`；不得复用旧 SHA 的 `PASS` 冒充当前 head，也不得为提效取消完整回归、降低 Reviewer 独立性或删减测试。
- #151 未 `VERIFIED` 前，编排契约（含本文件对字段 / 状态 / 合并门的引用）的权威路径是 [docs/orchestration.md](orchestration.md)；不得据此宣称迁移已验收。

## 2. 为什么要分阶段

本轮升级不重新发明多 Agent 工作流，而是补足现有流程中依靠执行者 / 审查者自觉完成的部分：把关键规则转成结构化字段、可重复的检查、故障演练和可追溯报告。三个近期项目给出的输入：

- **AgentMgr：治理状态与权限边界。** 授权撤销、任务重试 / 租约、审计上下文与审批可见性，需要负向、竞态与跨接口验证，而不只验证正常请求。
- **TalkDND：规则版本、旧数据与发行可靠性。** 规则目录隔离与角色数据迁移说明，功能新增需考虑旧数据、幂等重跑、版本隔离与回滚。
- **NotDND：系统级不变量。** 设计冻结与 E2E 工作、战斗时钟、行动者权限、事件链等在办问题说明，模块测试通过不代表完整交互闭环正确。

这些项目只作为经验来源；回归场景使用合成 fixture，不复制产品仓库的内部数据、私有信息、凭据或受版权保护内容。

当前已有资产（不需要重建）：两种底座与共用核心契约；任务状态机、依赖、活动路径锁与异常锁保留策略；合并门、head SHA 绑定与合并后验证；运行时能力探测与降级为多会话交接的原则；任务包 / 计划 / 审查 / 验收模板；纯标准库实现与套件结构校验。

## 3. 待解决的差距

1. 合并门要求逐条证据，但任务模板缺统一的、可机器检查的证据映射、负向场景与跨模块不变量结构。
2. 复杂项目需要设计契约与阶段审计，但阶段检查依赖 Master 阅读多处文档、凭上下文汇总。
3. 路径锁无法表达测试数据库、共享构建环境、共享生成产物等具名资源冲突。
4. 底座要求非法 `edition` 失败关闭，而 CLI 配置读取对若干非法 / 缺失值回退默认；项目底座与本地 CLI 配置的边界需要说清并测试（#4 已关闭，边界仍要在 #13 的升级说明里固化）。
5. 下游项目复制套件后缺少版本清单 / 漂移诊断；自动同步又可能覆盖项目特有政策。
6. 运行时能力探测是必要基础，但缺少面向交付物类型、权限、环境与证据新鲜度的登记；流程指标也缺统一口径。
7. 套件自身需要能反复演练跨模块失败场景，而不只在文档里要求 Agent“检查”。

## 4. 阶段目标、交付与出口

### Phase A — 契约和验收基础

- **阶段目标**：让“任务该怎么被验收”与“共享资源怎么被占用”都有唯一、可冻结的契约，后续实现不再各自发明字段。
- **交付**：
  - 任务包证据矩阵、负向场景、迁移场景与跨模块不变量字段（#2，已完成）。
  - edition 权威来源和 fail-closed 语义（#4，已完成）。
  - 根 `PLAN.md` 计划初始化与计划层拆分（#15、#19，已完成）。
  - Design Contract 与 Milestone Audit 模板（#3）。
  - 共享资源锁设计契约（#5）。
- **出口条件**：
  - 核心字段、证据与状态语义有唯一权威定义，模板只引用不重述。
  - 两个底座字段含义一致，差异只在载体与证据来源。
  - 资源锁契约覆盖并发、崩溃、租约过期、强制解锁与恢复行为。
  - 配置缺失 / 非法行为可用测试证明，且不破坏既有 `local` 状态库。
  - 新增文档内容通过规则包校验，命令证据来自真实执行。

### Phase B — 调度、漂移和能力可观测性

- **阶段目标**：把 Phase A 冻结的契约落成可运行能力，并让“漂移”和“能力”都有只读、可复核的观测手段。
- **交付**：
  - 按冻结契约实现具名资源锁及冲突诊断（#6），保持既有路径锁向后兼容。
  - 发行清单及只读的套件版本 / 核心契约漂移诊断（#7）。
  - 运行时能力登记、度量定义及最小只读报告（#8）。
- **出口条件**：
  - 锁竞争不会产生半套锁、重复所有权或静默释放。
  - 漂移诊断不修改目标仓库；下游特有文件被识别为扩展，而不是自动覆盖对象。
  - 能力清单区分支持 / 不支持 / 未知 / 过期，并链接真实测试证据。
  - 指标定义精确、可复现，显式报告缺失数据与样本量。

### Phase C — 合成回归与发布验收

- **阶段目标**：用可重复的合成场景证明整套流程会在该失败的地方失败，而不是只在文档里要求“要检查”。
- **交付**：
  - 四类合成场景：治理 / 授权、数据迁移 / 版本隔离、跨模块闭环、编排底座与证据失效（#9）。
  - 把新检查纳入通用验收程序与 README。
  - 在隔离环境中做可控失败演练，并保存最终证据。
- **出口条件**：
  - 正向控制组与负向失败组都覆盖；场景可重复运行，不依赖真实密钥、外部模型、外部业务服务或私有数据。
  - `local` / `github` 核心验收语义一致；无法离线验证的 GitHub 特有动作明确标为待验证 / 阻塞。
  - 旧 SHA、证据缺失、锁冲突、异常测试终止与待人工决定项不会被误报为 `VERIFIED`。
  - 完整测试在当前 head 实际通过，或未通过项有明确阻塞记录；不以历史 CI 或旧 commit 代替。

### 发布门前置（与 Phase B / C 并行）

- **阶段目标**：补齐下一次发布前必须闭环的两项，避免发布说明缺迁移路径、发布自检漏出流水线。
- **交付**：#13 edition 旧格式升级说明；#17 把 `scripts/build_release.py` 自检纳入 CI。
- **出口条件**：升级说明覆盖旧格式项目；发布自检在 CI 上随 PR 执行并在当前 head 通过。

## 5. 任务顺序与依赖

派发顺序（依赖未满足就停在 `PLANNED`，不提前开工）：

1. **#5**（Phase A，设计冻结）——下一步。
2. **#3**（Phase A，模板与契约口径对齐；#5 结论定稿后再定稿）。
3. **#6**（Phase B，依赖 #5 已验收）。
4. **#7** 与 **#8**（Phase B，可并行，路径不重叠）。
5. **#9**（Phase C，依赖 #2 / #3 / #4 / #5；最终验收等 #6–#8）。
6. **#13** 与 **#17**（发布门前置，可与 Phase B / C 并行）。

并行与路径规则：

- #2 与 #4 的锁定路径在 `scripts/validate_kit.py`、`tests/`、`docs/orchestration.md` 上相交，按路径锁规则**串行**完成，并非并行完成的先例。跨文件并行的前提是锁定路径不相交；不得用“计划上允许并行”代替实际的冲突检查。
- #3 的模板可先起草；与任务模板共用路径时必须协调所有权，不能并行编辑同一 Worktree 或同一文件。
- #5 是设计冻结任务；#6 不得在 #5 验收前开始实现锁语义。
- #7 与 #8 可并行，但须分配不重叠路径；变更发行清单 / 校验器的任务独占相关路径。
- #9 可提前起草场景，最终集成验收必须等依赖能力完成并在当前 head 上重跑。
- 全量测试资源用独占资源锁；并发数按机器余量与任务冲突判断，不以固定数字取代检查。
- 任何修改超出任务路径必须停下、更新授权并重新检查冲突。
- Phase D（#117 / #119 / #120–#124）的顺序、依赖与出口条件见 §13。
- 新阶段（Epic #146 / #147 / #149 / #150–#158）的顺序、依赖与出口条件见 §14；#174 只登记为待设计纠偏任务（§14.4），不进入本阶段实施顺序。
- 跨专项（治理 / ETR、DSH、任务驱动拓扑、自适应验证、主动发行）的实时状态、条件边与建议顺序见 §15；§15 只登记协调事实，不改变本节既有依赖。
- DSH 阶段的目标、子任务拆分、无环依赖与入口 / 出口见 §16；实时状态仍以第 1 节与 §15.2.2 为准。

## 6. 跨项目合成回归矩阵（Phase C 的输入）

| 场景组 | 经验来源 | 合成失败注入 | 预期拦截 |
|---|---|---|---|
| G — 治理 / 授权 | AgentMgr 的 grant 撤销、重复写、审计与任务重试 | 过期 / 撤销授权、重复提交、审计关联缺失、并发状态变化 | 负向验收失败；授权 / 审计证据不完整不能通过 |
| M — 迁移 / 版本 | TalkDND 的旧角色数据与规则版本隔离 | 旧格式升级、迁移重跑、目标版本混用、写入一半后故障 | 幂等 / 回滚验收失败时阻断；迁移证据明确 |
| X — 跨模块闭环 | NotDND 的战斗状态、时钟、单位所有权与事件链 | 契约规则未接线、越权对象 ID、事件屏障遗漏、非断言异常 | 端到端 / 不变量检查失败；测试仍输出总结报告 |
| O — 编排器本身 | AgentOrch 的底座、锁与证据门 | 非法 edition、重叠资源锁、旧 SHA、缺证据、超过修复上限 | 不能进入 `APPROVED` / `MERGED` / `VERIFIED`；原因可定位 |

所有 fixture 只模拟数据结构与行为，不复制三个产品项目的专有业务数据或受版权文本。至少保留一个通过控制组，避免回归套件只会报告失败。

## 7. 阶段收口（Milestone Audit）

阶段结束不以“子任务全部关闭”为判定依据。Master 在阶段收口时汇总：

- 必需功能与不变量是否逐项验证；
- 安全 / 隐私、迁移兼容性与回滚；
- 端到端完整路径与错误恢复；
- 性能基线（只有实际测量才下结论）；
- 已知缺口分类及其后续任务；
- 当前主分支 SHA 上的完整检查结果。

缺口按三类处理：`BLOCKING`（阶段不得标为完成）、`NON_BLOCKING_HARDENING`（可继续，但须写明风险、跟踪任务与接受依据）、`NEEDS_HUMAN_DECISION`（保留阻塞，不代替所有者做决定）。

单个任务的验收字段、证据条目格式与合并门判定，直接在 `docs/orchestration.md` 4.1 与第 5 节执行；本文件不复制这些定义，也不替它们做解释。

## 8. 回退与风险处理

- **文档 / 模板变更**：通过单独分支与 PR 回退；不删改历史记录。
- **资源锁实现**：先兼容既有路径锁；新资源锁异常不得自动解锁全部活动任务，状态不明先阻断并人工复核。
- **配置行为**：旧合法 `local` 配置保持兼容；迁移需要单独测试与记录。不支持的 `github` CLI 行为明确失败，不冒充可执行。
- **版本漂移**：诊断只读，不自动覆盖；修复由各项目自己的任务 / PR 完成。
- **指标**：缺数据标 `UNKNOWN` / coverage，不用估算值填补。
- **测试**：不得通过删除断言、放宽验收、吞掉异常或把检查移出配置来“修绿”。
- **发布**：清单、包内容、脚本与模板校验通过后才能发布；发布说明标出新增功能、兼容性边界与未验证能力。

## 9. 阶段收口检查清单

- [ ] 两个底座使用相同的任务与审查语义。
- [ ] 任务证据矩阵、设计契约、阶段审计模板齐全。
- [ ] 配置缺失、非法、损坏与未支持的底座行为均有测试。
- [ ] 路径锁与具名资源锁职责明确，且有并发 / 故障演练。
- [ ] 漂移诊断只读，清单可重现、哈希可复核。
- [ ] 运行时能力与质量指标基于真实来源并标明缺失数据。
- [ ] 四类合成回归场景含正向组、负向组、证据与清理步骤。
- [ ] 当前 head 的规则包验证、单元测试、发行包检查全部实际执行。
- [ ] 主分支合并后重跑完整验证，并记录命令、退出码、SHA、审查结论与遗留风险。

项目级、可度量的全局验收标准在 [PLAN.md](PLAN.md) 第 6 节；本节只列阶段收口还需要确认的项。

## 10. 维护规则

- **只经 PR 修改**：本文件的任何变更都要走任务分支 + PR + 独立审查；不在会话里口头改计划而不落文件。
- **状态只写第 1 节**：任务状态、当前阶段与“下一步”只改第 1 节；设计目标改动才动 `PLAN.md`。
- **先计划后登记**：新任务先写进第 1 节与第 5 节顺序，再登记为 Issue / 任务；关闭或取消的任务保留行并注明结果，不删除历史。
- **不复制定义**：字段、状态与门禁语义始终引用 `docs/orchestration.md`；发现本文件与核心文档口径不一致，先停下对齐，不在两处各写一套。
- **冲突处理**：与本文件的设计目标来源 `PLAN.md` 冲突时，以设计目标为准并停下对齐。


## 11. 自我完善与安全更新（新增规划，2026-10-26）

详细设计唯一落点：[docs/self-improvement-and-update-plan.md](self-improvement-and-update-plan.md)。本节只维护阶段顺序、依赖和状态，不复制设计契约。

| Issue | 交付重点 | 依赖 | 状态 |
|---|---|---|---|
| [#47](https://github.com/ctrlshiftaltdel/AgentOrch/issues/47) | 冻结来源注册表、Lesson 生命周期、Architecture Contract、更新安全与威胁模型 | Epic #46 | 已完成（PR #57 合并 `73a9c03`；Issue 已关闭并标 `status:verified`） |
| [#48](https://github.com/ctrlshiftaltdel/AgentOrch/issues/48) | 项目可配置架构契约与依赖边界检查器 | #47 | 已完成（PR #59 合并 `a9312dc`；Issue 已关闭并标 `status:verified`） |
| [#49](https://github.com/ctrlshiftaltdel/AgentOrch/issues/49) | TalkDND / AgentMgr 可配置来源、经验采集、验证与去重 | #47；共享路径需等 #48 | 已完成（PR #61 合并 `eaeb4e9`；Issue 已关闭并标 `status:verified`） |
| [#55](https://github.com/ctrlshiftaltdel/AgentOrch/issues/55) | TalkDND 已知模块边界、共享状态、并发、回滚和失败语义经验的首批规则 / 回归场景 | #47；架构专项检查依赖 #48 | 已完成（PR #63 合并 `6666e15`；Issue 已关闭并标 `status:verified`） |
| [#56](https://github.com/ctrlshiftaltdel/AgentOrch/issues/56) | AgentMgr 已知契约优先、Runtime Adapter 隔离、共用应用层和证据边界经验的首批架构回归场景 | #47、#48 | 已完成（PR #64 合并 `698f779`；Issue 已关闭并标 `status:verified`） |
| [#50](https://github.com/ctrlshiftaltdel/AgentOrch/issues/50) | “进行自我完善”触发、计划、派发、执行与验证闭环 | #48、#49；首批经验场景参考 #55、#56 | 已完成（PR #68 合并 `2d55da9`；Issue 已关闭并标 `status:verified`） |
| [#51](https://github.com/ctrlshiftaltdel/AgentOrch/issues/51) | 官方 Release 校验、三方比较、冲突分类、备份与回滚 | #47；共享路径需等 #48 | 已完成（PR #60 合并 `a9ab779`；Issue 已关闭并标 `status:verified`） |
| [#62](https://github.com/ctrlshiftaltdel/AgentOrch/issues/62) | 对齐 Lesson T10 与 `GENERAL_LESSON` 的 `NEEDS_VERIFICATION` 状态 | #47 | 已完成（PR #65 合并 `c09352c`；Issue 已关闭并标 `status:verified`） |
| [#66](https://github.com/ctrlshiftaltdel/AgentOrch/issues/66) | 隔离 architecture 路径穿越测试的固定 `/tmp` 文件 | #48 | 已完成（PR #67 合并 `66a659e`；Issue 已关闭并标 `status:verified`） |
| [#52](https://github.com/ctrlshiftaltdel/AgentOrch/issues/52) | “更新 AgentOrch”入口、迁移指南与发行包接入 | #51 | 已完成（PR #69 合并 `6756249`；Issue 已关闭并标 `status:verified`） |
| [#53](https://github.com/ctrlshiftaltdel/AgentOrch/issues/53) | 自我完善 / 更新流程合成回归、端到端演练与趋势报告：A8–A15 / B5–B14 / C19–C20 / D8–D9 场景与指标契约测试接入；CI 与发行包自检纳入 | #48–#52、#55、#56 | 已完成 / `VERIFIED`（PR [#70](https://github.com/ctrlshiftaltdel/AgentOrch/pull/70) 合并 `6ac9573`；Issue 已关闭并标 `status:verified`；当前 head 的逐条证据见 [VERIFIED 评论](https://github.com/ctrlshiftaltdel/AgentOrch/issues/53#issuecomment-6415723538)） |
| [#74](https://github.com/ctrlshiftaltdel/AgentOrch/issues/74) | 用 TalkDND / AgentMgr 运行首轮真实经验扫描 | #47、#49 | 已完成 / `VERIFIED`（PR [#80](https://github.com/ctrlshiftaltdel/AgentOrch/pull/80) 合并 `43f9c3c`；539 条证据（TalkDND 316 / AgentMgr 223），两来源均 `PARTIAL`，无 `OK` 水位；与既有经验去重后无候选；Issue 已关闭并标 `status:verified`） |
| [#76](https://github.com/ctrlshiftaltdel/AgentOrch/issues/76) | 发布 AgentOrch 1.6（`AgentOrch-1.6.zip`，Format 1.6） | #52、#74 | 已完成 / `VERIFIED`（PR [#81](https://github.com/ctrlshiftaltdel/AgentOrch/pull/81) 合并 `eac7f8e`；[Release v1.6](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.6)，资产 SHA256 `bc0a64e3…`；Issue 已关闭并标 `status:verified`） |
| [#77](https://github.com/ctrlshiftaltdel/AgentOrch/issues/77) | CI 增加 macOS / Windows 实机 job | #14、#53 | 已完成 / `VERIFIED`（PR [#79](https://github.com/ctrlshiftaltdel/AgentOrch/pull/79) 合并 `e26902f`；四平台四 job 全绿：Linux 3.12 / 3.13、macOS 3.13、Windows 3.13；Issue 已关闭并标 `status:verified`） |
| [#71](https://github.com/ctrlshiftaltdel/AgentOrch/issues/71) | 验证真实 GitHub Release 下载、签名与已发布旧包升级 | #76 | 已完成 / `VERIFIED`（PR [#82](https://github.com/ctrlshiftaltdel/AgentOrch/pull/82) 合并 `4a7069e`；真实 v1.5 → v1.6 升级 `APPLIED`，Format 1.5 → 1.6、90/90 包内文件一致、项目自有文件不变；真实 1.3 / 1.4 资产与以 1.6 为已安装基线仍 `NOT_RUN`，签名校验 `NOT_APPLICABLE`） |
| [#72](https://github.com/ctrlshiftaltdel/AgentOrch/issues/72) | Windows / macOS 实机验证发行包与更新引擎 | 由 #77 收口 | 已完成 / `VERIFIED`（实现任务 #77 / PR [#79](https://github.com/ctrlshiftaltdel/AgentOrch/pull/79) 合并 `e26902f`；Issue 已关闭并标 `status:verified`；GitHub-hosted runner 之外的安装器 / 桌面环境与自托管平台仍 `UNKNOWN`） |
| [#85](https://github.com/ctrlshiftaltdel/AgentOrch/issues/85) | 下游项目有 `docs/` 时 1.6 包无法通过 `validate_kit` 与自带测试（含大小写路径残留） | #76 | 已完成 / `VERIFIED`（PR [#87](https://github.com/ctrlshiftaltdel/AgentOrch/pull/87) 合并 `72a39e4`；下游 `validate_kit` 与包内测试通过，`tree mode` 区分纯净包 / 下游 / 上游三态；Issue 已关闭并标 `status:verified`） |
| [#86](https://github.com/ctrlshiftaltdel/AgentOrch/issues/86) | 发布 AgentOrch 1.6.1（`AgentOrch-1.6.1.zip`，Format 1.6） | #85 | 已完成 / `VERIFIED`（PR [#88](https://github.com/ctrlshiftaltdel/AgentOrch/pull/88) 合并 `baa5c91`；[Release v1.6.1](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.6.1)，资产 SHA256 `7b70d722…`；Issue 已关闭并标 `status:verified`） |
| [#93](https://github.com/ctrlshiftaltdel/AgentOrch/issues/93) | `task add` 重复多值选项静默丢值修复（`--acceptance` / `--paths` / `--depends` / `--resources`） | 无阻塞依赖（1.6.1 后 bugfix） | 已完成 / `VERIFIED`（PR [#94](https://github.com/ctrlshiftaltdel/AgentOrch/pull/94) 合并 `35fb924`；重复出现改为合并保留，`--acceptance` 保序、`--paths` / `--depends` 仍排序去重，单次多值写法兼容；ready 前缀冲突、`VERIFIED` 必须带 note、approval SHA 不匹配拒绝、非法状态跳转拒绝 + events 四条安全行为未回归；测试 703 → 715；Issue 已关闭并标 `status:verified`） |
| [#95](https://github.com/ctrlshiftaltdel/AgentOrch/issues/95) | 发布 AgentOrch 1.6.2（`AgentOrch-1.6.2.zip`，Format 1.6 不变） | #93 | 已完成 / `VERIFIED`（PR [#96](https://github.com/ctrlshiftaltdel/AgentOrch/pull/96) 合并 `bab65b6`；[Release v1.6.2](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.6.2)，资产 SHA256 `096e1fba…`；真实 1.6.0 / 1.6.1 → 1.6.2 升级仍 `NOT_RUN`；Issue 已关闭并标 `status:verified`） |
| [#99](https://github.com/ctrlshiftaltdel/AgentOrch/issues/99) | 路径冲突事件 / M4 path 采集 / ready 独立事件 | 无阻塞依赖 | 已完成 / `VERIFIED`（PR [#109](https://github.com/ctrlshiftaltdel/AgentOrch/pull/109) 合并 `636572d`；写入已定义的 `CONFLICT_RECORDED`，`report_metrics` 采集 M4 path 冲突，`TASK_READY` 拆为独立事件；Issue 已关闭并标 `status:verified`） |
| [#100](https://github.com/ctrlshiftaltdel/AgentOrch/issues/100) | `task amend --paths`（阻塞后更新登记）原子重写路径与锁 | 无阻塞依赖 | 已完成 / `VERIFIED`（PR [#106](https://github.com/ctrlshiftaltdel/AgentOrch/pull/106) 合并 `6653acc`；阻塞任务可原子重写允许路径与路径锁；Issue 已关闭并标 `status:verified`） |
| [#101](https://github.com/ctrlshiftaltdel/AgentOrch/issues/101) | `check_scope.py` + G2 范围证据 + `REQUIRED` 90 → 92 | 无阻塞依赖 | 已完成 / `VERIFIED`（PR [#107](https://github.com/ctrlshiftaltdel/AgentOrch/pull/107) 合并 `c4b699e`；新增范围检查脚本与审查清单 G2 证据形态，`REQUIRED` 90 → 92；Issue 已关闭并标 `status:verified`） |
| [#102](https://github.com/ctrlshiftaltdel/AgentOrch/issues/102) | 澄清会话入口：kit 第三部分按需读 | 无阻塞依赖 | 已完成 / `VERIFIED`（PR [#105](https://github.com/ctrlshiftaltdel/AgentOrch/pull/105) 合并 `81df375`；三个入口统一回指 `AGENTS.md` 的阅读口径，第三部分触发条件不变；Issue 已关闭并标 `status:verified`） |
| [#103](https://github.com/ctrlshiftaltdel/AgentOrch/issues/103) | Risks 填写深度口径（不新增字段） | 无阻塞依赖 | 已完成 / `VERIFIED`（PR [#108](https://github.com/ctrlshiftaltdel/AgentOrch/pull/108) 合并 `4e8922e`；统一 G-2 / G-3 的 Risks 填写深度，未新增字段；Issue 已关闭并标 `status:verified`） |
| [#104](https://github.com/ctrlshiftaltdel/AgentOrch/issues/104) | 可选文档收口：P1-3 未覆盖范围 + O-1～O-3 口径 + metrics 同步 | 无阻塞依赖（与 #101 文件重叠时可协作） | 已完成 / `VERIFIED`（PR [#110](https://github.com/ctrlshiftaltdel/AgentOrch/pull/110) 合并 `51e418f`；收口 P1-3 与 O-1～O-3 文档口径并同步 metrics 数据来源；Issue 已关闭并标 `status:verified`） |
| [#113](https://github.com/ctrlshiftaltdel/AgentOrch/issues/113) | 发布 AgentOrch 1.6.3（`AgentOrch-1.6.3.zip`，Format 1.6 不变） | #99–#104（均已 `VERIFIED`） | 已完成 / `VERIFIED`（PR [#114](https://github.com/ctrlshiftaltdel/AgentOrch/pull/114) 合并 `2adf431`；[Release v1.6.3](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.6.3)，资产 461,829 bytes，SHA256 `dcce5b24…` 与 GitHub API `assets[].digest` 一致；真实 `1.6.2 → 1.6.3` 隔离升级 `APPLIED`，证据见 §12.2 与 `docs/upgrade.md` §14.5；Issue 已关闭并标 `status:verified`） |
| [#142](https://github.com/ctrlshiftaltdel/AgentOrch/issues/142) | 发布 AgentOrch 1.7.0（`AgentOrch-1.7.0.zip`，Format 1.7，Phase D） | Phase D S1–S5（#120–#124，均已 `VERIFIED`） | 已完成 / `VERIFIED`（PR [#143](https://github.com/ctrlshiftaltdel/AgentOrch/pull/143) 合并 `ac3e87f`；[Release v1.7.0](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.7.0)，资产 602,273 bytes，SHA256 `cbc35fcf…` 与 GitHub API `assets[].digest` 一致；真实 `1.6.3 → 1.7.0` 升级 `APPLIED`，证据见 §12.1 / `docs/upgrade.md` §14.6；Issue 已关闭并标 `status:verified`） |
| [#117](https://github.com/ctrlshiftaltdel/AgentOrch/issues/117) | Phase D S0 设计冻结：角色契约、能力分层、发现与降级、动态选型、终端私有记录 | 无阻塞依赖 | 已完成 / `VERIFIED`（PR [#118](https://github.com/ctrlshiftaltdel/AgentOrch/pull/118) 合并 `ceb2c0a`；设计文档 `docs/design/agent-role-discovery-and-model-selection.md`；Issue 已关闭并标 `status:verified`） |
| [#119](https://github.com/ctrlshiftaltdel/AgentOrch/issues/119) | Phase D Epic：汇总 #117、#125 与 S1–S5（#120–#124） | 汇总 #117、#125、#120–#124 | 已完成 / `VERIFIED`（Epic；S1–S5 全部 `VERIFIED`；Issue #119 已关闭） | Epic Issue；阶段顺序、依赖与出口见 §13 |
| [#120](https://github.com/ctrlshiftaltdel/AgentOrch/issues/120) | Phase D S1：每终端独立 scope 与 Markdown 私有记录基座 | #117、#125 | 已完成 / `VERIFIED`（PR #127 合并 `818adaa`；主分支验证 813 tests / 98 required / 58/58） | Issue 正文；§13 |
| [#121](https://github.com/ctrlshiftaltdel/AgentOrch/issues/121) | Phase D S2：Runtime adapter、Agent 发现与角色匹配 | #117、#120 | 已完成 / `VERIFIED`（PR #130 合并 `9c9b1d2`；主分支验证 888 tests / 105 required / 58/58） | Issue 正文；§13 |
| [#122](https://github.com/ctrlshiftaltdel/AgentOrch/issues/122) | Phase D S3：Artificial Analysis 官方数据源与角色专项动态选型 | #117、#120、#121 | 已完成 / `VERIFIED`（PR #133 合并 `2b0fd3d`；主分支验证 957 tests / 114 required / 58/58） | Issue 正文；§13 |
| [#123](https://github.com/ctrlshiftaltdel/AgentOrch/issues/123) | Phase D S4：本机角色表现反馈与可解释校准 | #120–#122 | 已完成 / `VERIFIED`（PR #136 合并 `86463ec`；主分支验证 1016 tests / 116 required / 58/58） | Issue 正文；§13 |
| [#124](https://github.com/ctrlshiftaltdel/AgentOrch/issues/124) | Phase D S5：入口集成与端到端合成回归 | #120–#123 | 已完成 / `VERIFIED`（PR #139 合并 `b18d3b2`；主分支验证 1047 tests / 117 required / 71/71 场景） | Issue 正文；§13 |

**Epic #46 收口与残留任务：** Epic [#46](https://github.com/ctrlshiftaltdel/AgentOrch/issues/46) 已按 `templates/milestone-audit.md` 于 2026-10-26 完成阶段审计并关闭（[审计评论](https://github.com/ctrlshiftaltdel/AgentOrch/issues/46#issuecomment-6415751115)），S0–S6 全部完成。审计 `BLOCKING` / `NEEDS_HUMAN_DECISION` 均为 0，登记的三项 `NON_BLOCKING_HARDENING` 中 [#71](https://github.com/ctrlshiftaltdel/AgentOrch/issues/71)、[#72](https://github.com/ctrlshiftaltdel/AgentOrch/issues/72) 已由后续任务收口（#71 → PR #82；#72 → #77 的跨平台 CI）；审计后的 [#85](https://github.com/ctrlshiftaltdel/AgentOrch/issues/85)、[#86](https://github.com/ctrlshiftaltdel/AgentOrch/issues/86) 也已 `VERIFIED`（#85 → PR #87；#86 → PR #88，发布 1.6.1），不属于审计硬化项；1.6.1 之后的 [#93](https://github.com/ctrlshiftaltdel/AgentOrch/issues/93)（PR #94，1.6.2 补丁）与 [#95](https://github.com/ctrlshiftaltdel/AgentOrch/issues/95)（PR #96，发布 1.6.2）同样已 `VERIFIED`，也不属于审计硬化项；1.6.2 之后的 [#99](https://github.com/ctrlshiftaltdel/AgentOrch/issues/99)–[#104](https://github.com/ctrlshiftaltdel/AgentOrch/issues/104) 六项（PR #105–#110）同样已 `VERIFIED`，它们是 1.6.2 之后的独立改进与文档收口批次，**不属于审计硬化项**，也不改变本审计结论。**只剩下列一项 hardening（现由后续任务收口；保留行以存历史，不删除）**：

| 遗留任务 | 交付重点 | 当前状态与依据 |
|---|---|---|
| [#73](https://github.com/ctrlshiftaltdel/AgentOrch/issues/73) | 拆分 delivery scenarios 场景实现为按组模块 | 已完成 / `VERIFIED`：PR [#171](https://github.com/ctrlshiftaltdel/AgentOrch/pull/171) 合并 `876b8d54eb6a69b77c55f6de9a2523e8c1ba2e93`；主分支 124 `REQUIRED`、1050 tests、71/71 场景通过，证据见[主分支验证](https://github.com/ctrlshiftaltdel/AgentOrch/issues/73#issuecomment-6468978648)；Issue 已关闭并标 `status:verified`；审计第 10 节 `NON_BLOCKING_HARDENING` 已收口 |

首批经验落地（不是等未来采集机制完成后才处理）：
- TalkDND 的具体经验已拆至 [#55](https://github.com/ctrlshiftaltdel/AgentOrch/issues/55)：模块拆分后的兼容验证、共享状态全调用点审计、锁保护完整性、回滚后的最终状态、阶段化失败语义。
- AgentMgr 的具体经验已拆至 [#56](https://github.com/ctrlshiftaltdel/AgentOrch/issues/56)：契约优先、核心与具体 Runtime Adapter 解耦、MCP/REST 共用应用服务、Mock 与真实验证证据分离、失败 / 状态未知语义。
- 两项任务把来源案例转为项目无关规则和合成测试，不修改 TalkDND 或 AgentMgr 产品代码。

执行纪律：
- #47 契约未冻结前，不得开始依赖该契约的完整实现；隔离的只读探测可以先做，但不得形成不可逆设计承诺。
- #48 与 #49 只有在锁定路径不重叠时才可并行；两者共享 `docs/self-improvement/`、`scripts/validate_kit.py`、`scripts/build_release.py` 与 CI，当前按路径锁串行。#51 同理等待共享路径释放。
- #50 依赖架构检查与经验管线；#52 依赖安全更新引擎；#53 最终验收依赖所有实现合并。
- 每个任务都按现有任务状态、路径锁、证据矩阵、独立审查和当前 head 验证执行。Issue 关闭不等于阶段验证完成。
- 该能力不能覆盖或弱化现有核心协作契约；涉及权限 / 安全边界、锁与状态机、数据迁移、破坏兼容或不可逆行为时，必须保留人工决策门。

## 12. 发布事实：1.7.0 / Format 1.7（2026-10-27）

### 12.1 Release 1.7.0

- **决定（2026-10-27）**：本次发布 `1.7.0`，Format 从 `1.6` 升到 `1.7`；资产名 `AgentOrch-1.7.0.zip`，tag `v1.7.0`。
- **Format 1.7 的原因**：Phase D 在包内新增 25 个 `REQUIRED` 文件（`scripts/agent_selection/` 资产族与随包测试 `tests/agent_selection/`），包内文件从 v1.6.3 的 93 个增至 118 个；更新引擎的 `VERSION_MATCH` 要求 release tag 的 major.minor 等于 `MANIFEST.json` 的 `format_version`，`v1.7.0` 必须配 Format `1.7`，否则会被判为不匹配并拒绝。因此 `VERSION.md` 同时把 `Format version` 1.6 → 1.7、`Release version` 1.6.3 → 1.7.0。
- **包内容**：发行包 118 个文件（117 `REQUIRED` + `MANIFEST.json`），相对 v1.6.3 的 93 个（92 + 1）新增 25 个 `REQUIRED` 文件；`MANIFEST.json` schema 仍为 1，`format_version` 为 `1.7`。
- **无移动 / 改名 / 删除**：既有路径、`MANIFEST.json` schema 1 与已有 `REQUIRED` 条目全部保留，新增的只是文件与子目录；更新引擎按“新增上游文件”处理，不需要手工搬迁。
- **发布结果（已完成）**：[Release v1.7.0](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.7.0) 已于 2026-10-27 发布；tag `v1.7.0` → `ac3e87f2840eb35e6c0e18205990ad38dbc282aa`；资产 `AgentOrch-1.7.0.zip`，602,273 bytes，SHA256 `cbc35fcf5ddf2aa8c7c799e52c7f5fa83ec8943a7691e1053155cc85988a4c10`（与 GitHub API `assets[].digest` 一致）；主分支 CI run [40598836550](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40598836550) @ `ac3e87f` 四平台全绿。
- **发布后真实升级（已完成）**：从 Release 下载资产并复核 SHA256 已执行；真实 `1.6.3 → 1.7.0` 隔离升级为 `APPLIED`——`PLAN` 的 10 项包校验全 `PASS`，`APPLY` 后 Format 1.6 → 1.7、`MANIFEST.json` files 92 → 117、117/117 包内文件与目标包逐字节一致、项目自有 `docs/PROJECT.md` / `docs/PLAN.md` / `LOCAL-NOTES.md` SHA256 前后不变、备份 `required=true` / `verified=true`、`rollback.result=NOT_NEEDED`、head 前后不变；升级后项目 `validate_kit` 退出码 0（downstream，117 required），包内 `unittest` 968 tests `OK`（skip 19）。详细记录见 `docs/upgrade.md` §14.6。
- **范围边界**：本节只覆盖 `1.6.3 → 1.7.0` 这一次真实升级；真实 `1.6.0` / `1.6.1` → `1.6.2`、真实 1.3 / 1.4 资产、真实断网 / 限速 / DNS、`HD-11`、非 GitHub-hosted 平台仍为 `NOT_RUN` / `UNKNOWN`，不得用本次结果替代。
- 当前 `main@ac3e87f` 的 1047 tests、117 个 `REQUIRED` 文件（发行包 118 = 117 + `MANIFEST.json`）、71/71 场景与四平台 CI 即 `v1.7.0` 发布基线的属性；`v1.6.3` 的 754 tests、92 个 `REQUIRED` 文件（发行包 93 = 92 + `MANIFEST.json`）与资产 SHA256 `dcce5b24…` 保持为历史发布事实。

### 12.2 历史：Release 1.6.3 / Format 1.6

- **决定（2026-10-27）**：本次发布 `1.6.3`，Format 保持 `1.6`；资产名 `AgentOrch-1.6.3.zip`，tag `v1.6.3`。`tag_matches_format('v1.6.3', '1.6')` 为真，仍满足更新引擎的 `VERSION_MATCH`（major.minor = 1.6）。
- **理由**：本批是加法能力（#99 路径冲突事件与 M4 path 采集、#100 `task amend --paths`、#101 新增 2 个 `REQUIRED` 文件、#102–#104 文档口径），没有文件移动 / 改名 / 删除，`MANIFEST.json` schema 仍为 1；按 `release/README` §5「只增加能力、不改包格式时 Format 不变」，只升对外 Release 版本。
- **未选择 Format 升级**：本批没有新增格式字段、载体契约或布局变化，不需要 Format 升级及其迁移说明。
- **发布结果（已完成）**：[Release v1.6.3](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.6.3) 已于 2026-10-27 发布；tag `v1.6.3` → `2adf4319c5826f97c6c98b8fa401473115f9b76f`；资产 `AgentOrch-1.6.3.zip`，461,829 bytes，SHA256 `dcce5b242997ccac44ee1dea225ca13c2e9af6583dc6bca3d5bdc35acb046885`（与 GitHub API `assets[].digest` 一致）；主分支 CI run [40471657312](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40471657312) @ `2adf431` 四平台全绿。
- **发布后补证（已完成）**：从 Release 下载资产并复核 SHA256 已执行；真实 `1.6.2 → 1.6.3` 隔离升级为 `APPLIED`——`PLAN` 的 10 项包校验全 `PASS`，`APPLY` 后 Format 1.6 → 1.6、`MANIFEST.json` files 90 → 92、92/92 包内文件与目标包逐字节一致、项目自有 `docs/PROJECT.md` / `docs/PLAN.md` / `LOCAL-NOTES.md` SHA256 前后不变、备份 `required=true` / `verified=true`、head 前后不变；升级后项目 `validate_kit` 退出码 0（downstream 模式），包内 `unittest` 675 tests `OK`（skip 19）。详细记录见 `docs/upgrade.md` §14.5。
- **范围边界**：本节只覆盖 `1.6.2 → 1.6.3` 这一次真实升级；真实 `1.6.0` / `1.6.1` → `1.6.2`、真实 1.3 / 1.4 资产、真实断网 / 限速 / DNS、`HD-11`、非 GitHub-hosted 平台仍为 `NOT_RUN` / `UNKNOWN`，不得用本次结果替代。
- 当前 `main` 的 754 tests、92 个 `REQUIRED` 文件（发行包 93 = 92 + `MANIFEST.json`）与 58/58 场景即 `v1.6.3` 发布基线的属性；`v1.6.2` 的 715 tests、91 文件包与资产 SHA256 `096e1fba…` 保持为历史发布事实。

## 13. Phase D — Agent 角色发现、Subagent 能力考核与动态模型选型（2026-10-27）

**目标**：在真实运行时能力、候选 Agent / 模型证据与本机私有记录的基础上，为每个任务选择 Execution 与 Reviewer；能力缺失、数据过期或来源不可用时安全降级并如实报告。设计冻结见 [agent-role-discovery-and-model-selection.md](design/agent-role-discovery-and-model-selection.md)。

本阶段是增量能力：不改变任务状态机、路径 / 资源锁、G1–G6 合并门，也不改变 C1–C4 / C1–C8 的能力语义。这些定义仍只在 [docs/orchestration.md](orchestration.md) 与 [templates/runtime-capability.md](../templates/runtime-capability.md) 中，本节只引用不重述。

### 13.1 阶段顺序、交付与出口条件

| 阶段 | 承接 Issue | 交付 | 依赖 | 出口条件 |
|---|---|---|---|---|
| S0 设计冻结 | [#117](https://github.com/ctrlshiftaltdel/AgentOrch/issues/117) | 设计文档 `docs/design/agent-role-discovery-and-model-selection.md`、文档导航、`.local/README.md` 的用途 / 隔离 / 隐私说明 | 无 | PR [#118](https://github.com/ctrlshiftaltdel/AgentOrch/pull/118) 合并到主分支且主分支 `VERIFIED` |
| P 阶段计划登记 | [#125](https://github.com/ctrlshiftaltdel/AgentOrch/issues/125) | 本节与第 1 节的登记；不实现运行时逻辑 | S0 已 `VERIFIED` | #125 `VERIFIED`；此前 S1–S5 不得进入 `READY`，也不得开始实现 |
| S1 每终端 scope + Markdown store | [#120](https://github.com/ctrlshiftaltdel/AgentOrch/issues/120) | 每终端独立 scope 解析、路径安全校验、仅 Markdown 的读写接口与写入失败语义 | #117、#125 | scope 隔离、路径安全、Markdown-only、写入失败语义与对应测试证据齐备 |
| S2 Runtime adapter / Agent discovery / 角色匹配 | [#121](https://github.com/ctrlshiftaltdel/AgentOrch/issues/121) | 运行时适配接口、候选注册、C1–C4 门与角色匹配报告、缺角色提醒 | #117、#120、#125 | C1–C4 门先于任何排序、显式注册表回退可用、缺角色提醒与 fixture 测试齐备 |
| S3 Artificial Analysis 数据源 + 动态选型 | [#122](https://github.com/ctrlshiftaltdel/AgentOrch/issues/122) | 官方 Data API 适配、供应商无关数据对象、硬筛选与解释性排序、新鲜度与降级记录 | #117、#120、#121、#125 | 官方 API 条款 / 字段 / 配额核验、密钥处理、硬筛选优先于排序、离线 / 过期降级与 fixture 测试齐备 |
| S4 本机项目反馈 | [#123](https://github.com/ctrlshiftaltdel/AgentOrch/issues/123) | 按 scope 记录 Master / Execution / Reviewer 的项目内表现与可解释校准建议 | #120–#122、#125 | 每 scope Markdown、样本量与 coverage 口径、无跨 scope 读取、不自动切换模型 |
| S5 入口集成 + 端到端合成回归 | [#124](https://github.com/ctrlshiftaltdel/AgentOrch/issues/124) | 会话入口接线、正向与负向合成场景、场景注册与发行检查 | #120–#123、#125 | 正向 / 负向场景均覆盖、两个 edition 语义一致、场景注册与发行检查通过 |

表中不含当前状态：任务状态只写第 1 节（见 §10 维护规则）。

### 13.2 依赖图与启动门

```text
#117 (S0 设计冻结)
  └─ #125 (P 阶段计划登记)
       └─ #120 (S1)
            └─ #121 (S2)
                 └─ #122 (S3)
                      └─ #123 (S4)
                           └─ #124 (S5)
```

- 无环依赖链：#117 → #125 → #120 → #121 → #122 → #123 → #124。
- 链上叠加的依赖：#122 同时依赖 #120 与 #121；#123 依赖 #120–#122；#124 依赖 #120–#123。
- Epic [#119](https://github.com/ctrlshiftaltdel/AgentOrch/issues/119) 汇总 #117、#125 与 #120–#124；它不替代任何单任务的验收，也不单独构成门。

启动门：

- #125 自身须在 #117 / PR #118 合并并 `VERIFIED` 后才可派发；本节的登记动作不改变 #125 的状态。
- #125 `VERIFIED` 之前，S1–S5 不得进入 `READY`，也不得开始实现；#125 通过后由 Master 按上表依赖逐任务另行置 `READY` / 派发。
- 每个实现任务派发前仍须按自身允许路径重新核对最新 `main` 与开放 PR 的路径冲突。
- S1–S5 的真实状态以任务载体上的证据为准；§1 已记录 S1–S5 全部 `VERIFIED`，Phase D 完成。

### 13.3 阶段边界与失败处理

- 不改任务状态机、路径 / 资源锁、G1–G6、C1–C4 或 C1–C8；不新增角色级门禁，也不建立第二套证据定义。
- 只用 Python 3 标准库，不引入第三方依赖；基础 CLI 在离线条件下照常可用。
- Artificial Analysis 是可选适配器：不可用、限流或数据过期时不阻塞治理流程，相关数据如实标 `UNKNOWN` / `STALE` / `BLOCKED`，不冒充实时查询成功。
- `.local/agent-orch/<scope-id>/` 的记录不入 Git、不进发行包、不跨终端同步；不同 scope 之间不得互相读取或继承。
- Reviewer 与 Execution 必须使用不同底层模型 ID；不同模型别名、温度或系统提示词不算独立。
- 不自动切换模型、不自动授权、不自动创建 Subagent；无合格候选时停止自动选择并请求 Master / 用户决策。
- 失败保持 `BLOCKED` / `NEEDS_HUMAN_DECISION`，并写明缺失项与降级方式；登记动作本身不得把任何后续阶段置 `READY`。

### 13.4 与历史阶段 / 设计目标的关系

- Phase A / B / C 与 1.6.x 的发布 / 验收事实（第 1 节、§11、§12.2）原样保留；Phase D 是增量阶段，不改写历史数字与结论。
- [PLAN.md](PLAN.md) 仍是设计目标权威；本文件只登记阶段顺序、依赖与当前状态，状态只落在第 1 节。
- 本节与设计冻结文档或 `PLAN.md` 冲突时，停下对齐，不在两处各写一套。

## 14. 新阶段：项目治理、旧项目隔离接入与 ETR 驱动评价选型（Epic #146，2026-10-28）

**目标**：让文件职责、核心契约位置、旧项目接入布局与 Agent 表现评价（真实 Token telemetry / ETR、三角色互评、多候选选型）各有唯一权威落点，并保留 Master 手动选型边界。设计依据见 [next-phase-governance-and-agent-performance.md](design/next-phase-governance-and-agent-performance.md)（设计冻结 [#147](https://github.com/ctrlshiftaltdel/AgentOrch/issues/147) / PR [#148](https://github.com/ctrlshiftaltdel/AgentOrch/pull/148)）；本节只登记阶段顺序、依赖与真实状态，不复制设计契约、状态机或 G1–G6。

**设计 / 计划门禁（当前事实）**：#147 已 `VERIFIED`（PR #148 合并 `3528e3ff5a8e868f92b8b2a36c523823cfe9ad6e`，交付记录见 [Issue #147](https://github.com/ctrlshiftaltdel/AgentOrch/issues/147)）；但**治理设计文稿** [`docs/design/next-phase-governance-and-agent-performance.md`](design/next-phase-governance-and-agent-performance.md) 头部仍登记 `PROPOSED`、其 §14 仍写“待冻结”，该文本状态未随 Issue 的 `VERIFIED` 一并更新——这是已知的文档不一致，留待文档治理任务处理，本节不改写设计文稿。本阶段计划登记即 #149；#149 `VERIFIED` 之前，#150–#158 不得进入 `READY`，也不得开始实现。

### 14.1 阶段顺序、交付与出口条件

| 阶段 | 承接 Issue | 交付 | 依赖 | 出口条件 |
|---|---|---|---|---|
| S0 设计冻结 | [#147](https://github.com/ctrlshiftaltdel/AgentOrch/issues/147) / PR [#148](https://github.com/ctrlshiftaltdel/AgentOrch/pull/148) | 设计文稿与关键决策经独立审查并合并 | Epic [#146](https://github.com/ctrlshiftaltdel/AgentOrch/issues/146) | 已满足：#147 `VERIFIED`（主分支 `3528e3f`） |
| P0 阶段计划登记 | [#149](https://github.com/ctrlshiftaltdel/AgentOrch/issues/149) | 本节与第 1 节登记；不实现功能 | S0 | #149 `VERIFIED`；此前 GOV-* / I1 / E* / V0 不得 `READY` |
| P1 DSH 阶段登记 | [#162](https://github.com/ctrlshiftaltdel/AgentOrch/issues/162) | 把 DSH 角色工具阶段登记进本文件，工具名固定 `ao_master` / `ao_execution` / `ao_reviewer`；阶段目标、子任务出口与无环依赖见 §16；`FROZEN` 只表示设计冻结，不表示 DSH 运行时已支持 | P0；[#161](https://github.com/ctrlshiftaltdel/AgentOrch/issues/161) / PR [#168](https://github.com/ctrlshiftaltdel/AgentOrch/pull/168) 与 [#172](https://github.com/ctrlshiftaltdel/AgentOrch/issues/172) 均已 `VERIFIED` | #162 `VERIFIED`；此后 #151 / #152 必须保留该登记段 |
| GOV-1 文件地图 / 生命周期 | [#150](https://github.com/ctrlshiftaltdel/AgentOrch/issues/150) | 文件责任、权威来源与过期退出机制 | S0、P0；与 P1 路径不重叠，可并行 | 责任地图与生命周期规则落地，并在当前 head 验证 |
| GOV-2 核心契约迁移 | [#151](https://github.com/ctrlshiftaltdel/AgentOrch/issues/151) | `kit/orchestration.md` → `docs/orchestration.md`，保持发行 / 升级兼容 | GOV-1、P1、[#169](https://github.com/ctrlshiftaltdel/AgentOrch/issues/169)（已 `VERIFIED`） | 引用、发行包、包内自检、旧版升级与回滚全部通过 |
| GOV-3 计划精简 | [#152](https://github.com/ctrlshiftaltdel/AgentOrch/issues/152) | 精简本文件、治理过期规划文档 | GOV-1、GOV-2 | 历史信息可追溯，当前未完成风险不丢失 |
| I1 旧项目隔离接入 | [#153](https://github.com/ctrlshiftaltdel/AgentOrch/issues/153) | 下游 `agentorch/` 布局、根 `AGENTS.md` 增量区块、安全升级 | GOV-1–GOV-3（与 GOV-3 共享 `docs/README.md`，先完成计划 / 导航清理） | 宿主布局不被扩散、根区块幂等、冲突失败关闭、真实迁移 / 回滚演练 |
| GOV-4 文档一致性门禁 | [#154](https://github.com/ctrlshiftaltdel/AgentOrch/issues/154) | 文件责任、链接、文档影响、计划状态与发行清单检查 | GOV-1–GOV-3、I1 | 正向 / 负向样例都能真实检测 |
| E1 Token telemetry / ETR | [#155](https://github.com/ctrlshiftaltdel/AgentOrch/issues/155) | 真实 Token 用量、贡献权重、覆盖率与阈值 | S0、P0 | 固定 Token 数据可复算；`NO_DATA` / `UNKNOWN` 与低覆盖率不触发阈值动作 |
| E2 三角色互评 / 可信度 | [#156](https://github.com/ctrlshiftaltdel/AgentOrch/issues/156) | 独立双评、冲突处理与评价者可信度 | E1 | 单评 / 双评一致 / 伪双评 / 冲突 / 错误评价者等场景齐备 |
| E3 多候选路由 | [#157](https://github.com/ctrlshiftaltdel/AgentOrch/issues/157) | 基于 ETR 与可信度选择已预配置的 Execution / Reviewer，Master 手动 | E1、E2 | 先硬筛选再排序；不自动创建 / 授权；Master 只接受用户手动切换 |
| V0 集成回归 | [#158](https://github.com/ctrlshiftaltdel/AgentOrch/issues/158) | 旧项目迁移演练、负向场景、发行与文档验收 | GOV-4、I1、E1–E3；[#73](https://github.com/ctrlshiftaltdel/AgentOrch/issues/73) `VERIFIED`（已满足；共享 delivery-scenario runner 与场景注册） | 当前 head 全量检查与四平台 CI 通过，未执行项标 `NOT_RUN` / `BLOCKED` |

表中“已满足”“已 `VERIFIED`”只针对列出的既有事实；未写为已完成的阶段一律视为未开始。

### 14.2 依赖图、路径顺序与启动门

依赖边（无环）：

- S0 #147 → P0 #149 → P1 #162 / GOV-1 #150 / E1 #155。
- GOV-1 #150 → GOV-2 #151 → GOV-3 #152 → I1 #153 → GOV-4 #154。
- GOV-2 #151 另依赖 P1 #162；GOV-3 #152 另依赖 GOV-1；I1 #153 另依赖 GOV-1–GOV-2。
- GOV-4 #154 依赖 GOV-1–GOV-3 与 I1；V0 #158 依赖 GOV-4、I1 与 E1–E3。
- E1 #155 → E2 #156 → E3 #157。
- E1 #155 另依赖 S0、P0。

路径顺序约束（与依赖同等有效）：

- **计划文件编辑串行 `#149` → `#162` → `#151` → `#152`**：四个任务都写本文件，必须按此顺序串行、各自从最新主分支重验；`#151` / `#152` 必须保留 `#162` 登记的 DSH 阶段段落。`#162` 的 DSH 阶段内容由该任务自行登记，本节不复制。
- **`#150` 与 `#162` 可并行**：两者路径不重叠（`docs/architecture/**` 与本文件）。
- **`#152` → `#153`**：两者共享 `docs/README.md`，先完成计划精简与导航清理。
- **`#73` `VERIFIED` → `#158`**，且仅指向 `#158`：两者共享 delivery-scenario runner 与场景注册；`#73` 已（PR #171 合并 `876b8d5`）`VERIFIED`，该路径顺序约束仍有效；**不新增 `#73` → `#151`**。
- **`#149` 与 `#174`**：`#149` 不依赖 `#174`；`#174` 等待 `#149` `VERIFIED` 后才进入设计执行，二者不构成环。

启动门：

- S0 已满足；P0（本任务）交付后仍须 Master 审查、合并并在主分支验证，才可把 #150–#158 逐项置 `READY`。
- #162 除 #149 外还需 #161 / #172 `VERIFIED`（均已满足）；#162 已于 2026-10-31 派发（`DISPATCHED`），本任务只登记 DSH 阶段，不改写其他任务状态。
- 每个实现任务派发前仍须按自身允许路径重新核对最新 `main` 与开放 PR 的路径冲突；依赖满足不代表必须并行。

### 14.3 阶段边界、指标口径与失败处理

- 不改任务状态机、路径 / 资源锁、G1–G6、C1–C4 / C1–C8；不新增角色级门禁，也不建立第二套证据定义。
- ETR 的 50% 只是 `PROVISIONAL` 中性先验，与实测 ETR 分开存储 / 表达，不计入实测值；**它只在候选缺少合格实测 ETR 的冷启动比较中**可作为排序信息，且不得压过任何具有可比较、合格实测 ETR 的候选；具体字段、权重与 tie-break 不在本计划预设（设计条款见 `docs/design/next-phase-governance-and-agent-performance.md` §7.3，所有者决定见 [Issue #147 评论](https://github.com/ctrlshiftaltdel/AgentOrch/issues/147#issuecomment-6464262117)）。公式与阈值定义只在设计文稿 §7，本节不复制。
- Token telemetry 不可信时 ETR 必须 `UNKNOWN`；观测总量为 0 记 `NO_DATA`，不得填成 0 或 100，也不得触发 40% / 80% 阈值动作。
- DSH 角色工具名固定为 `ao_master` / `ao_execution` / `ao_reviewer`；此处的 `FROZEN` 指 **DSH 设计文稿** [`docs/design/deepseek-harness-agent-role-tools.md`](design/deepseek-harness-agent-role-tools.md) 的设计冻结（[#161](https://github.com/ctrlshiftaltdel/AgentOrch/issues/161) / [#172](https://github.com/ctrlshiftaltdel/AgentOrch/issues/172)），**不指仍标 `PROPOSED` 的治理设计文稿**。`FROZEN` 只表示设计冻结，真实 DSH 运行时支持仍待 [#163](https://github.com/ctrlshiftaltdel/AgentOrch/issues/163) / [#167](https://github.com/ctrlshiftaltdel/AgentOrch/issues/167) 验证，不得据此声称已实现。
- 失败保持 `BLOCKED` / `NEEDS_HUMAN_DECISION` 并写明缺失项与降级方式；登记动作本身不把任何后续阶段置 `READY`，也不改变任何现有任务状态。
- 本阶段是增量能力：Phase A–D 与 1.6.x / 1.7.0 的发布和验收事实、§11–§13 原样保留。

### 14.4 待推进的纠偏设计 #174（方向已批准，设计未冻结）

- [#174](https://github.com/ctrlshiftaltdel/AgentOrch/issues/174) 设计运行时快速探测与受控子代理协作边界。项目所有者已批准该纠偏方向（[记录](https://github.com/ctrlshiftaltdel/AgentOrch/issues/174#issuecomment-6467712789)），但设计**尚未冻结**：本节不把任何 C1–C4 行为写成现行规则，也不声称长期设计已修改。
- 依赖方向：`#149 VERIFIED` → #174 设计执行；`#149` 不依赖 `#174`。当前 #174 状态以 Issue 为准（`OPEN` / `status:blocked`），本节只登记依赖与方向。
- 所有者方向（[决定](https://github.com/ctrlshiftaltdel/AgentOrch/issues/174#issuecomment-6468107357)）：未来正常项目作业中，subagent 能力探测问题应显示为风险 / 未验证提示，而不是默认阻断。**这是待设计的目标方向，不是当前已发布行为**；它不放开任务依赖、路径 / 资源锁、允许路径、G1–G6 或审批 / 合并权限，也不把未探测 / 失败的能力记为 `PASS`。调用实际失败、实例不存在、结果为空或无法核对时，仍须如实报告失败 / 未完成。
- 临时授权口径见 [#146 决定记录](https://github.com/ctrlshiftaltdel/AgentOrch/issues/146#issuecomment-6468103536)；该授权仅覆盖本轮路由，不构成能力 `PASS` 或身份 / 隔离证明。

## 15. 跨专项任务顺序与任务驱动分配（Issue #197，2026-10-28；2026-10-29 效率优先排序见 §15.8）

**登记边界**：本节只记录执行时的真实状态、已有依赖、条件边、路径 / 资源串行与建议顺序，供 Master 调度。它不新增任务、不改变任何 Issue / PR 的状态、依赖、正文或 registry、不授予 `READY`、不改变 `docs/PLAN.md`、核心契约、G1–G6、C1–C4 / C1–C8、路径 / 资源锁或发行授权。任务状态仍以第 1 节与 Issue / PR 载体为准；字段、状态取值和证据定义仍只在 [docs/orchestration.md](orchestration.md) 4.1 与第 5 节。

### 15.1 实时基线与提案状态

- **执行基线**：`main` @ `16753d96102754062e42c327e0ea244b4516d093`；本任务首次派发时无开放 PR——这是 **PR #200 创建前的派发快照**，PR #200 已于 2026-10-28 15:42 UTC 创建，因此当前并非无开放 PR；派发快照中的活动登记块只有本任务 #197（其 `locked_paths` 为 `docs/implementation-plan.md`）。
- **三份专案提案已交付、设计仍未冻结**：PR [#182](https://github.com/ctrlshiftaltdel/AgentOrch/pull/182) → #183、PR [#190](https://github.com/ctrlshiftaltdel/AgentOrch/pull/190) → #199、PR [#196](https://github.com/ctrlshiftaltdel/AgentOrch/pull/196) → #198 均为 `MERGED`，对应 Issue 均 `VERIFIED`；但 [task-driven-agent-topology.md](design/task-driven-agent-topology.md)、[adaptive-verification-and-quality-gates.md](design/adaptive-verification-and-quality-gates.md)、[semver-proactive-release-policy.md](design/semver-proactive-release-policy.md) 的文稿状态仍是 `PROPOSED`。**提案交付 ≠ 契约冻结 ≠ 运行时已实现 ≠ 发行已授权。**
- **DSH 设计已冻结、运行时未验证**：设计 #161 与状态修正 #172 已 `VERIFIED`，[deepseek-harness-agent-role-tools.md](design/deepseek-harness-agent-role-tools.md) 为 `FROZEN`；这只表示设计冻结，真实 DSH API、角色隔离与实机行为仍待 #163 / #167。
- **身份与探测纠偏仍未冻结**：#170 为 `OPEN`（`bug`），可信底层模型身份来源、核验与失效规则未冻结；#174 为 `OPEN` / `status:blocked`，所有者已批准方案 1 方向，但设计未冻结、未派发、未实现。
- **开放任务状态**：#150–#158、#162–#167、#177–#181、#186–#189、#192–#195 均为 `OPEN`；除 #174 的 `status:blocked` 外无状态标签，均按 `PLANNED` / 未 READY 登记（#162 后于 2026-10-31 派发，见 §1 / §15.8）。没有除 #197 之外的活动登记块或路径锁。#185 已完成 / `VERIFIED` 并关闭，不再计入开放任务（见第 1 节与 §15.2.4）。
- **上一句是 #197 当时的快照，不是 2026-10-31 的锁状态**：当前 `docs/implementation-plan.md` 的活动登记块与锁状态见 §15.8（本任务 #162 持有该路径）。
- **Owner 2026-10-28 排序决定（历史记录；已被 2026-10-29 S0–S4 排序覆盖，见 §15.8）**：在 #197 本计划任务收口之后，优先推进 AVE #184 的 [#185](https://github.com/ctrlshiftaltdel/AgentOrch/issues/185) → [#186](https://github.com/ctrlshiftaltdel/AgentOrch/issues/186)，完成这两步后再推进主动发行 #192–#195（[决定记录](https://github.com/ctrlshiftaltdel/AgentOrch/issues/197#issuecomment-6473530069)）。该决定只改变调度优先级，不新增硬依赖、不改变各 Epic 内部顺序、不取消发行工作；#185 只做真实测量审计，#186 只在只读 / shadow 模式运行，均不改变或跳过现行门禁；#188 仍须 A–C `VERIFIED` 后另行明确 Owner 授权，当前禁止切换生产门禁。

### 15.2 五组专项的真实阶段状态

#### 15.2.1 治理、文档与 ETR（Epic #146）

**唯一来源**：Epic [#146](https://github.com/ctrlshiftaltdel/AgentOrch/issues/146)、设计 [#147](https://github.com/ctrlshiftaltdel/AgentOrch/issues/147) / PR [#148](https://github.com/ctrlshiftaltdel/AgentOrch/pull/148)、阶段计划登记 [#149](https://github.com/ctrlshiftaltdel/AgentOrch/issues/149)，以及 #150–#158 各自正文。治理设计文稿 [next-phase-governance-and-agent-performance.md](design/next-phase-governance-and-agent-performance.md) 头部仍为 `PROPOSED`（已知不一致见 §14）。

| Issue | 交付 | 真实状态 | 已有依赖 / 条件 |
|---|---|---|---|
| [#150](https://github.com/ctrlshiftaltdel/AgentOrch/issues/150) | 文件责任地图与文档生命周期 | `OPEN` / `PLANNED`；未 READY | #147 / #149 已 `VERIFIED` |
| [#151](https://github.com/ctrlshiftaltdel/AgentOrch/issues/151) | 核心契约迁移到 `docs/orchestration.md` | `OPEN` / `PLANNED`；未 READY | #150 未 `VERIFIED`；#169 已 `VERIFIED`；计划文件顺序 #162 → #151 |
| [#152](https://github.com/ctrlshiftaltdel/AgentOrch/issues/152) | 精简本文件并治理过期规划文档 | `OPEN` / `PLANNED`；未 READY | #150、#151 未 `VERIFIED` |
| [#153](https://github.com/ctrlshiftaltdel/AgentOrch/issues/153) | 旧项目 `agentorch/` 隔离接入与安全升级 | `OPEN` / `PLANNED`；未 READY | #150–#152 未 `VERIFIED`；与 #152 共享 `docs/README.md` |
| [#154](https://github.com/ctrlshiftaltdel/AgentOrch/issues/154) | 文件责任 / 文档影响 / 计划状态门禁 | `OPEN` / `PLANNED`；未 READY | #150–#153 未 `VERIFIED` |
| [#155](https://github.com/ctrlshiftaltdel/AgentOrch/issues/155) | 真实 Token telemetry、ETR、覆盖率与阈值 | `OPEN` / `PLANNED`；未 READY | #147 / #149 已 `VERIFIED` |
| [#156](https://github.com/ctrlshiftaltdel/AgentOrch/issues/156) | 三角色互评、双评与评价者可信度 | `OPEN` / `PLANNED`；未 READY | #155 未 `VERIFIED` |
| [#157](https://github.com/ctrlshiftaltdel/AgentOrch/issues/157) | ETR / 可信度接入预配置 Subagent 选择 | `OPEN` / `PLANNED`；未 READY | #155、#156 未 `VERIFIED` |
| [#158](https://github.com/ctrlshiftaltdel/AgentOrch/issues/158) | 新阶段端到端回归、发行验收与文档同步 | `OPEN` / `PLANNED`；未 READY | #153–#157 未 `VERIFIED`；#73 已 `VERIFIED` |

#### 15.2.2 DSH 适配与独立发行包（Epic #160）

**唯一来源**：Epic [#160](https://github.com/ctrlshiftaltdel/AgentOrch/issues/160) 正文的依赖表、设计 [#161](https://github.com/ctrlshiftaltdel/AgentOrch/issues/161) / PR [#168](https://github.com/ctrlshiftaltdel/AgentOrch/pull/168) 与 [DSH 设计文稿](design/deepseek-harness-agent-role-tools.md)。工具名固定为 `ao_master` / `ao_execution` / `ao_reviewer`；本文不复制其接口契约。阶段目标、子任务拆分、无环依赖与入口 / 出口见 §16；本表只保留实时状态快照，不重复阶段登记。

| Issue | 交付 | 真实状态 | 已有依赖 / 条件 |
|---|---|---|---|
| [#162](https://github.com/ctrlshiftaltdel/AgentOrch/issues/162) | 把 DSH 阶段登记进唯一实施计划 | `OPEN` / `status:in-progress` / `DISPATCHED`；未 `VERIFIED` | #161 / #172 / #149 已 `VERIFIED`；计划文件顺序 #149 → #162 → #151 → #152；阶段登记见 §16 |
| [#163](https://github.com/ctrlshiftaltdel/AgentOrch/issues/163) | 公开 API、角色级工具隔离与上下文核验 | `OPEN` / `PLANNED`；未 READY | #161 已 `VERIFIED` |
| [#164](https://github.com/ctrlshiftaltdel/AgentOrch/issues/164) | 三角色工具与安全适配器 | `OPEN` / `PLANNED`；未 READY | #161–#163、#162；#151 / #153 条件依赖 |
| [#165](https://github.com/ctrlshiftaltdel/AgentOrch/issues/165) | bundle 构建、可信哈希基线与可复现自检 | `OPEN` / `PLANNED`；未 READY | #161、#163、#162 |
| [#166](https://github.com/ctrlshiftaltdel/AgentOrch/issues/166) | CI 与同 tag 双资产发布手册 | `OPEN` / `PLANNED`；未 READY | #161、#163、#165 |
| [#167](https://github.com/ctrlshiftaltdel/AgentOrch/issues/167) | 隔离、迁移、发行与实机验收 | `OPEN` / `PLANNED`；未 READY | #162–#166、#161；#151 / #153 / #154 条件依赖 |

#### 15.2.3 任务驱动拓扑（Epic #176）

**唯一来源**：Epic [#176](https://github.com/ctrlshiftaltdel/AgentOrch/issues/176)、设计提案 [#183](https://github.com/ctrlshiftaltdel/AgentOrch/issues/183) / PR [#182](https://github.com/ctrlshiftaltdel/AgentOrch/pull/182) 与 [task-driven-agent-topology.md](design/task-driven-agent-topology.md)。该文稿仍是 `PROPOSED`，T0–T4、Stage A–E 与任何 CLI / adapter 均为待规划或待实现内容。

| Issue | 交付 | 真实状态 | 已有依赖 / 条件 |
|---|---|---|---|
| [#177](https://github.com/ctrlshiftaltdel/AgentOrch/issues/177) | A / P0 冻结 Role≠Agent Instance 契约 | `OPEN` / `PLANNED`；未 READY | 无 |
| [#178](https://github.com/ctrlshiftaltdel/AgentOrch/issues/178) | B / P1 任务分类器与只读拓扑推荐 | `OPEN` / `PLANNED`；未 READY | #177 未 `VERIFIED` |
| [#179](https://github.com/ctrlshiftaltdel/AgentOrch/issues/179) | C / P1 执行拓扑与运行时能力适配 | `OPEN` / `PLANNED`；未 READY | #178 未 `VERIFIED`；#170 仅约束相关审查路径 |
| [#180](https://github.com/ctrlshiftaltdel/AgentOrch/issues/180) | D / P1 端到端目标达成评测与回退 | `OPEN` / `PLANNED`；未 READY | #177、#178 未 `VERIFIED`；可与 #179 条件并行 |
| [#181](https://github.com/ctrlshiftaltdel/AgentOrch/issues/181) | E / P2 试点、双底座验收与发行兼容 | `OPEN` / `PLANNED`；未 READY | #179、#180 未 `VERIFIED`；核心权限变更须 Owner 批准 |

#### 15.2.4 自适应验证 AVE（Epic #184）

**唯一来源**：Epic [#184](https://github.com/ctrlshiftaltdel/AgentOrch/issues/184)、提案 [#199](https://github.com/ctrlshiftaltdel/AgentOrch/issues/199) / PR [#190](https://github.com/ctrlshiftaltdel/AgentOrch/pull/190) 与 [adaptive-verification-and-quality-gates.md](design/adaptive-verification-and-quality-gates.md)。该文稿为 `PROPOSED` / `NOT IMPLEMENTED`；**现行 G1–G6、当前 head CI、合并后完整回归与测试强度不因本提案改变。** 组内进度：#185 已完成 / `VERIFIED`；#186–#189 仍为 `OPEN` / `PLANNED`、未 READY。

| Issue | 交付 | 真实状态 | 已有依赖 / 条件 |
|---|---|---|---|
| [#185](https://github.com/ctrlshiftaltdel/AgentOrch/issues/185) | A / P0 验证成本基线与门禁审计 | 已完成 / `VERIFIED` | 无功能硬依赖；PR [#201](https://github.com/ctrlshiftaltdel/AgentOrch/pull/201) 合并并验证 head `a09fb2e11d2d5d1467b2e07c0ba23854cda1adbe`；[VERIFIED 评论](https://github.com/ctrlshiftaltdel/AgentOrch/issues/185#issuecomment-6484091982) |
| [#186](https://github.com/ctrlshiftaltdel/AgentOrch/issues/186) | B / P0 风险分级与只读影子 Gate Planner | `OPEN` / `PLANNED`；未 READY（设计 DRAFT 未书面冻结） | #185 已 `VERIFIED`；[DRAFT v2](https://github.com/ctrlshiftaltdel/AgentOrch/issues/186#issuecomment-6486678110) |
| [#187](https://github.com/ctrlshiftaltdel/AgentOrch/issues/187) | C / P1 影响面测试选择与证据等价性 | `OPEN` / `PLANNED`；未 READY | #186 未 `VERIFIED` |
| [#188](https://github.com/ctrlshiftaltdel/AgentOrch/issues/188) | D / P1 Owner 授权后试点风险驱动合并门 | `OPEN` / `PLANNED`；未 READY | #187 未 `VERIFIED`；生产门禁须 Owner 另行授权 |
| [#189](https://github.com/ctrlshiftaltdel/AgentOrch/issues/189) | E / P1 AVE 双底座发布兼容与校准 | `OPEN` / `PLANNED`；未 READY | #188 未 `VERIFIED`；DSH 按实际进度 |

#### 15.2.5 主动发行（Epic #191）

**唯一来源**：Epic [#191](https://github.com/ctrlshiftaltdel/AgentOrch/issues/191)、提案 [#198](https://github.com/ctrlshiftaltdel/AgentOrch/issues/198) / PR [#196](https://github.com/ctrlshiftaltdel/AgentOrch/pull/196) 与 [semver-proactive-release-policy.md](design/semver-proactive-release-policy.md)。该文稿为 `PROPOSED` / `NOT IMPLEMENTED`；当前发布仍按 [release/README.md](../release/README.md) 与现行授权规则，**本节不授权任何 tag / Release**。

| Issue | 交付 | 真实状态 | 已有依赖 / 条件 |
|---|---|---|---|
| [#192](https://github.com/ctrlshiftaltdel/AgentOrch/issues/192) | A / P0 发行分类、批次策略与常设授权边界 | `OPEN` / `PLANNED`；未 READY | 专项设计 PR 已合并；Owner 授权边界待冻结 |
| [#193](https://github.com/ctrlshiftaltdel/AgentOrch/issues/193) | B / P0 解耦 Release SemVer 与 Format version | `OPEN` / `PLANNED`；未 READY | #192 未 `VERIFIED` |
| [#194](https://github.com/ctrlshiftaltdel/AgentOrch/issues/194) | C / P1 Master 主动发行准备与批次规划 | `OPEN` / `PLANNED`；未 READY | #192、#193 未 `VERIFIED` |
| [#195](https://github.com/ctrlshiftaltdel/AgentOrch/issues/195) | D / P1 常规发行与 Kit / DSH 同版本交付验证 | `OPEN` / `PLANNED`；未 READY | #193、#194 未 `VERIFIED`；Owner 预授权与 DSH 实际进度 |

#### 15.2.6 横切能力与身份（#170 / #174）

- [#170](https://github.com/ctrlshiftaltdel/AgentOrch/issues/170)：`OPEN` / `bug`，未 READY。可信底层模型身份来源、核验方式与失效规则未冻结；不得用模型自述、不同 alias、温度或 Prompt 差异证明独立。它只约束 #156 / #157 / #179 中需要可信独立审查身份的路径，不是这些任务的整任务硬前置。
- [#174](https://github.com/ctrlshiftaltdel/AgentOrch/issues/174)：`OPEN` / `status:blocked`，未派发。所有者已批准“能力 / 任务风险分层”方向（[决定](https://github.com/ctrlshiftaltdel/AgentOrch/issues/174#issuecomment-6468107357)），但设计未冻结；不得把未来告警策略写成现行 C1–C4 规则。

### 15.3 已有硬依赖、条件边与路径串行

#### 15.3.1 硬依赖（只登记 Issue 正文已写明的边）

```text
#147✓ → #149✓
#149✓ → #150, #155, #162
#147✓ → #150, #155
#169✓ → #151
#150 → #151, #152
#151 → #152, #153
#152 → #153
#153 → #154, #158
#154 → #158
#155 → #156 → #157
#157 → #158
#73✓ → #158
#161✓ → #162, #163
#172✓ → #162
#162 → #164, #165, #167
#163 → #164, #165, #166, #167
#164 → #167
#165 → #166, #167
#166 → #167
#177 → #178 → #179
#177 → #180
#178 → #180
#179 → #181
#180 → #181
#185 → #186 → #187 → #188 → #189
#192 → #193 → #194 → #195
```

- 上图无环；✓ 表示该前置已 `VERIFIED`。它只表示“下游仍须等这些前置 `VERIFIED`”，不表示前置满足后必须立即派发。
- **不存在的边**：不新增 `#73 → #151`；#73 的路径 / 场景顺序只指向 #158。不把 PR 并行路径冲突写成功能硬依赖。

#### 15.3.2 条件边与授权门（不等于硬依赖）

- **#151 / #153 → #164 / #167（条件）**：仅当 DSH 实现要支持目标 `agentorch/` 布局时成立；否则必须把范围准确限定到当前已验证布局，或等待 #151 / #153 `VERIFIED`。不得默认它们已完成。
- **#170 → #156 / #157 / #179（条件）**：仅适用于需要“可信独立 Reviewer 身份”的审查路径；不自动成为整任务硬前置，也不把第二意见冒充独立身份。
- **#188（Owner 授权门）**：A–C 真实验证后仍需所有者明确批准，才能变更生产合并门；批准前只允许影子 / 只读模式。
- **#192（Owner 授权门）**：常规 PATCH / MINOR 的常设发行授权边界必须由 #192 冻结并经 Owner 确认；未冻结前不得推定已生效。
- **#194 / #195（协调边）**：#194 需与 #176 的运行时变更协调；#195 需与 DSH 实际进度协调，且 Owner 预授权到位后才验证常规发行。这些是协调条件，不写成未授权的硬依赖。

#### 15.3.3 路径 / 资源串行（动态选择，不是永久阶段依赖）

- **计划文件**：既有串行链 **#149 → #162 → #151 → #152** 保持不变；本任务 #197 也独占 `docs/implementation-plan.md`，按动态路径锁与上述链串行。当前 #197 持锁，完成后才放行 #162；该先后是锁顺序，不是新增功能硬依赖，也不改写既有链。
- **#150 与 #162 可并行**：`docs/architecture/**` 与本文件路径不重叠。
- **#152 → #153**：两者共享 `docs/README.md`，先完成计划精简与导航清理。
- **#150–#154 / #158**：共享 `scripts/validate_kit.py`、`scripts/build_release.py`、`docs/README.md` 与场景注册时，按实际 `locked_paths` 串行，不因计划允许而并行。
- **#155 → #156 → #157**：共享 `scripts/agent_selection/**`，串行。
- **#164 / #165**：共享 `release/dsh/**` 与 `release/dsh/tests/**`；只有明确拆分路径时才可并行，否则串行。#164 若触及 `runtime_adapter.py`，须先与 #156 / #157 检查路径锁。
- **#179 / #180 条件并行**：仅当文件范围与资源锁不冲突；推荐只在 #177 / #178 冻结接口后尝试。
- **#185–#189**：共享 `scripts/validate_kit.py`、`templates/**` 与文档路径，按组内顺序串行。
- **#192–#195**：共享 `release/README.md`、`VERSION.md`、`scripts/update_agentorch/**` 与发行清单，串行。

#### 15.3.4 建议顺序（优先级不绕过依赖与授权）

本小节记录 2026-10-28 的调度建议，已被 §15.8 的 2026-10-29 S0–S4 排序覆盖（保留为历史记录）；其中“优先级不绕过依赖与授权”的口径继续有效。

Owner 2026-10-28 排序决定覆盖更早“主动发行优先”的顺序：**#197 收口后先推进 AVE #185 → #186，再推进主动发行 #192–#195**（[决定记录](https://github.com/ctrlshiftaltdel/AgentOrch/issues/197#issuecomment-6473530069)）。该决定只改变调度优先级，不新增硬依赖、不改变各 Epic 内部顺序、不取消发行工作。据此给出调度建议：

1. **第一优先（Owner 最新决定）：AVE 前置两步** #185 → #186。#185 只基于真实记录做测量与门禁审计，**不改任何门禁**；#186 只在只读 / shadow 模式输出差异，**不改变或跳过现行 required check**。两步均 `VERIFIED` 后才进入下一优先项。
2. **第二优先：主动发行** #192 → #193 → #194 → #195。#192 先冻结分类与常设授权边界；#193 必须按 [#192 评论](https://github.com/ctrlshiftaltdel/AgentOrch/issues/192#issuecomment-6472566437) 与 [#193 评论](https://github.com/ctrlshiftaltdel/AgentOrch/issues/193#issuecomment-6472566611) 覆盖已发布旧客户端的 `VERSION_MATCH` 桥接风险与真实端到端升级；#194 只做只读发行准备；#195 验证 Kit / DSH 同版本完整交付。**优先级不改变既有依赖，不跳过授权、旧客户端兼容或实物校验；本排序也不取消发行工作。**
3. **可早启动的独立入口**：#150（文件地图）、#162（DSH 计划登记，受计划文件锁）、#163（DSH API 核验）、#177（拓扑契约）。它们路径不同且前置已满足或仅需设计，可按实际锁并行；并行不等于同时派发。
4. **治理 / ETR 链**：#151 → #152 → #153 → #154；#155 → #156 → #157 可与治理链在路径不冲突时并行；#158 收口。
5. **DSH 实现链**：#164 / #165（默认串行）→ #166 → #167。
6. **拓扑链**：#178 → #179 / #180（条件并行）→ #181。
7. **AVE 后段**：#187 → #188 → #189，保持 Epic 内部顺序不变；#188 须 A–C 完成并 `VERIFIED` 后另行取得 Owner 明确授权，授权前只允许影子 / 只读，**当前生产门禁禁止切换**。
8. **#174 设计** 与 **#170 身份修复**按 Master 的实际锁与 Owner 排期推进；两者都不是全局阻断，但都不能被伪报为已解决。

**任何优先级都不能**：把未 `VERIFIED` 的依赖置为 `READY`、绕过 `status:blocked` / `NEEDS_HUMAN_DECISION`、跳过当前 head CI 或完整回归、创建 tag / Release、或把提案写成已发布政策。

### 15.4 待澄清事项（来自评审评论，尚未生效）

- [#186 评论](https://github.com/ctrlshiftaltdel/AgentOrch/issues/186#issuecomment-6473153904) 指出：[adaptive-verification-and-quality-gates.md](design/adaptive-verification-and-quality-gates.md) §7 的“能力 / 身份不可信则停止”位于**独立 Reviewer 审查语境**，易与 §2 的 C1–C4 探针风险告警混读。后续 #186 冻结风险分类时必须区分：
  - 无法满足任务所要求的独立 Reviewer / 身份证据；
  - 探针缺失、`UNKNOWN`、`STALE` 或**未证明客观缺失**的探针 `FAIL`（按所有者已批准的未来方向告警，不单凭探针状态禁止获授权尝试）；
  - **真实调用失败、权限、锁、任务特定安全与 G1–G6**（分别判定）。
- 同一评论要求核对设计稿 §5 的枚举措辞，确保 `FAIL` / `NOT_RUN` / `UNKNOWN` / `BLOCKED` 均不能被表示为合格；状态语义不得与核心证据矩阵形成第二套权威。
- 以上是**待 #186 冻结时澄清**的事项，不是当前已生效规则；不得据草稿把强制门禁精简写成已生效。

### 15.5 风险登记与不可越过的边界

- **#174 能力探测**：未来方向是“正常项目作业中，C1–C4 探测缺失 / `UNKNOWN` / `STALE` / 未证明客观缺失的 `FAIL` 显示风险提示，而非默认阻断”；但该方向**仍待设计冻结与后续实现**，当前 C1–C4 规则未改变。实际调用失败、结果为空、权限 / 锁 / 身份 / G1–G6 条件仍分别判定，不得伪成功。本轮临时派发豁免仅覆盖本轮，不构成能力 `PASS`、隔离证明或长期政策。
- **#170 身份**：#170 未完成前，额外 Reviewer 只能作为第二意见，不得声称底层模型独立；不得用模型家族自述、alias、温度或 Prompt 差异冒充可信身份。#179 的 #170 条件只约束相关审查路径。
- **ETR**：50% 仅为独立冷启动中性先验，与实测 ETR 分开表达；它只在候选缺少合格实测 ETR 时作为排序信息，不得压过任何具有可比较、合格实测 ETR 的候选。具体口径只引用 §14.3 与 [治理设计 §7.3](design/next-phase-governance-and-agent-performance.md)，本节不复制第二套公式或阈值。
- **DSH**：工具名固定 `ao_master` / `ao_execution` / `ao_reviewer`；`FROZEN` 不是 runtime 已验证。#163 / #167 的真实 API、隔离、安装、调用与终局证据缺一不可；stub / 静态 YAML 测试不能替代实机。
- **#184 自适应验证**：当前 G1–G6、当前 head CI、合并后完整回归、测试强度与发行级完整验证**仍然强制**。Owner 最新排序把 #185 → #186 置于主动发行之前，但 #185 只做真实测量审计、#186 只在只读 / shadow 模式运行，二者都**不改变或跳过现行门禁**；#188 改变生产门禁仍须 A–C 完成并 `VERIFIED` 后另行取得 Owner 明确授权，授权前只能影子 / 只读，不得减少检查。
- **#191 主动发行**：#192 的常设授权边界尚待冻结，不推定已生效；#193 的旧客户端 `VERSION_MATCH` 桥接风险与真实旧版本端到端验收仍须解决（见 #192 / #193 评论），不得以新解析器单元测试冒充旧客户端升级成功；本计划不创建真实 tag / Release，也不授权自动发布。
- **提案 ≠ 已发布政策**：三份 `PROPOSED` 文稿、任何 T0–T4 / AVE / SemVer 建议在被独立任务冻结、实现并验证前，都不改变现行产品行为、权限或门禁。

### 15.6 T0–T4 路由建议（符合现行权限）

**现行默认规则、临时授权与未来方向必须分开**：

1. **现行默认规则（当前生效）**：[docs/orchestration.md](orchestration.md) 第一部分“角色选择入口”与第二部分 §5 规定，C1–C4 任一项不是 `PASS`（包括 `UNKNOWN` / `STALE` / 已过期）时，**停止自动派发并转人工交接**。`UNKNOWN` / `FAIL` 一律不等于 `PASS`；没有真实证据就不能推进依赖该能力的状态。
2. **#146 临时授权（原文范围为“本轮相关任务”）**：[#146 评论 6468103536](https://github.com/ctrlshiftaltdel/AgentOrch/issues/146#issuecomment-6468103536) 的原文只写“本轮相关任务”，**该评论没有枚举具体 Issue ID**。因此不得把未列出的清单说成已经明确，也不得自行推断它覆盖哪些 future task；**某个任务是否适用，须以 Owner 对该任务的明确授权 / 确认记录为准**。它不是长期政策，也不构成能力 `PASS`、上下文隔离或 Reviewer 身份证明。
3. **#174 未来方向（尚未生效）**：[#174 评论 6468107357](https://github.com/ctrlshiftaltdel/AgentOrch/issues/174#issuecomment-6468107357) 的“能力探测缺失 / `UNKNOWN` / `STALE` / 未证明客观缺失的 `FAIL` 以风险提示为主、而非默认阻断”仍是待设计冻结、实现与回归的目标方向，**当前不生效**，不得据草稿扩写成新政策。

| 拓扑 | 适用 | 现行边界 |
|---|---|---|
| **T0 工具 / 主循环** | 只读或确定性工具完成状态、依赖与证据检查；简单查询 | Master 默认不实现产品文件；涉及实现文件仍需明确例外授权，不得以 T0 绕开探测或权限 |
| **T1 单执行实例** | 需要文档 / 代码产物的单任务 | 交给真实 Execution 实例与专属 worktree；真实失败照实记录，不伪造派发 |
| **T2 执行 + 独立 Reviewer** | 风险或任务策略明确要求独立判断 | 需要**可信独立身份证据**；#170 未解决前，额外 Reviewer 只能作为第二意见，不证明 T2 独立身份 |
| **T3 并行隔离执行** | 多个可独立验收的子任务，依赖为 DAG | 仅在路径 / 资源锁不冲突时并行；并发数按机器余量，不写固定 Agent 数量 |
| **T4 外部 / 人工交接** | 工具 / 实例客观不存在、本任务实际调用已失败，或任务特定权限 / 安全前置无法满足 | 输出可执行交接包；**现行默认规则下，C1–C4 任一项不是 `PASS`（含 `UNKNOWN` / `STALE` / 已过期）仍停止自动派发并转人工交接**（见上方三段区分）；#146 临时授权的任务级适用性须以 Owner 对该任务的明确授权 / 确认记录为准；`UNKNOWN` / `FAIL` 不得写成 `PASS`，也不得凭 #174 未生效方向排除 T4 或扩写为新政策 |

- 本轮 #197 实际采用工具核验与真实执行会话；这不能证明 C1–C4 全部能力、上下文隔离或 Reviewer 底层身份独立。
- **实际调用失败、结果为空、句柄失效或无法核对**时，继续如实记为失败 / 未完成 / 交接，不得伪成功；**权限、安全、锁、Reviewer 身份与 G1–G6 保持各自独立边界**，不因能力探测状态而放宽或合并判定。
- 任何“已启动 / 已完成 / 已独立审查”的说法都必须有真实句柄、产物、退出码与当前 head 证据；没有就如实标 `NOT_RUN` / `BLOCKED` / `UNKNOWN`。

### 15.7 本任务交付证据

- **允许路径**：仅 `docs/implementation-plan.md`；实际 changed files、`git diff --check`、依赖 DAG 与相对链接核对结果记录在本任务 PR 评论。
- **验证命令**（须在最终 head 真实执行并记录退出码）：`uv run python scripts/validate_kit.py`；`uv run python -m unittest discover -s tests`；`python3 -m py_compile scripts/orchestrator.py scripts/validate_kit.py`。
- **CI**：四平台（Linux 3.12 / 3.13、macOS、Windows）必须绑定本任务最终 head；未运行或未绑定最终 head 的旧结果不标 `PASS`。
- **回退**：本任务只改计划文本；如需回退，单独 revert 本任务的主分支合并提交，不改写其他任务状态或历史事实。
- **未完成 / 待决**：所有五组专项的实现、授权与冻结均未由本任务完成；本任务只登记协调事实，不声称任何后续阶段已 `READY`、已实现或已获授权。

### 15.8 Owner 2026-10-29 效率优先排序与路径队列（Issue #207）

**登记边界**：本节只登记 Owner 2026-10-29 的最新排序、优先理由、放行条件与路径队列，供 Master 调度。它不新增任务、不改变任何 Issue / PR 的状态、依赖、正文或 registry，不授予 `READY` / `DISPATCHED`，也不改变 `docs/PLAN.md`、核心契约、G1–G6、C1–C4 / C1–C8、路径 / 资源锁或发行授权。状态权威仍在第 1 节与各任务载体；字段、状态取值与证据语义仍只引用 [docs/orchestration.md](orchestration.md) 4.1 与第 5 节，本文件不另立第二套。

**覆盖关系**：Owner 2026-10-29 的排序显式覆盖 #197 / PR #200 的旧建议「#185→#186 后立即主动发行」（[#197 旧记录](https://github.com/ctrlshiftaltdel/AgentOrch/issues/197#issuecomment-6473530069)；§15.1 / §15.3.4 保留历史原文）。覆盖只作用于调度优先级；§15.3.1 的功能硬依赖、设计冻结、`READY` 门，以及发布 / 生产门禁的专属授权继续有效，不得因排序变化被视为已满足或已取消。

**2026-10-31 刷新事实**：`origin/main` @ `ce80d7917c992dad88615fd18b6fe6b61e79e18a`；#202 / PR #203、#204 / PR #208、#185、#147、#149、#161、#172、#207 / PR #209 均 `VERIFIED` 并关闭；#162 已派发（`DISPATCHED`，本任务，未 `VERIFIED`）；#186、#174、#188、#170、#205、#206、#151 等仍 `OPEN` 且未 READY（#174 带 `status:blocked`）；开放 PR 为空，`docs/implementation-plan.md` 由本任务 #162 持有活动锁。这些是核对快照，不是永久就绪证明。

**#204 不依赖本任务**：S0-1 中 #204 排在本任务之前，只是政策 / 路径阻塞的处理顺序；#204 / PR #208 已 `VERIFIED`，不反向等待 #207。

| 顺位 | 执行队列 / 可独立入口 | 优先理由 | 放行条件（不因顺位自动满足） |
|---|---|---|---|
| S0-1 / P0 | #204 → #202 / PR #203 → #207 | 先解决每次合并都重复等待的规则冲突与状态漂移 | #204、#202 已 `VERIFIED`；#207 只登记排序，不代替状态；共享计划文件串行 |
| S0-2 / P0 | #205 → #206 → #170 | 最高优先级代码整改：证据时间、路径权限、可信身份 | 三者均需完整任务包与路径检查；共享代码路径按序串行，每个 `VERIFIED` 后从新基线派发 |
| S1-1 / P1 | #186 书面冻结及完整任务包 → 只读 Planner 实现 / 验证 → #187 | 优先拿到可验证的提效工具与对照数据 | #186 DRAFT v2 未冻结；#187 仍等 #186 `VERIFIED`；影子结果不得宣称已节省成本 |
| S1-2 / P1 | #174 设计；#150；#162；#163 | 减少等待、反复阅读与下游返工 | #174 只交付设计；#162 的计划路径已释放并已派发（本任务，未 `VERIFIED`）；#163 不必等 DSH 实现链；各自完整前置不变 |
| S2 / P1 | #192 → #193 → #194 | 减少版本耦合与重复人工提醒 | 保留 v1.7.0 旧客户端桥接正负验收；未获常设授权不真实发布 |
| S3 / P1 | #155；#177 → #178 → #180 | 先可观测、再证明编排收益 | #180 可与 #179 在无路径冲突时分开推进；未接入的 telemetry 明确 `UNKNOWN` |
| S4 / P2 | #156 → #157；#179 → #181；#151 → #152 → #153 → #154 → #158 | 评价 / 自动接线与大范围目录迁移后置 | 保留各自完整前置；#151 仍须 #150 / #162 / #169；#158 仍须治理、旧项目接入、ETR / 评价 / 选型与 #73 等全部 `VERIFIED` |
| S4 / P2 | #164 / #165 在 #162 / #163 后串行 → #166 → #167 → #195 | DSH 扩展、实机验收与双资产发行后置 | 涉及未来布局时等 #151 / #153 或按已批准范围明确支持现布局；#195 的 #193 / #194 / Owner 授权 / 实际 DSH 资产条件保留 |
| 条件项 | #188 → #189 | 生产门禁试点只在可信提效证据齐备后 | 仅在 #186 / #187 等 A–C `VERIFIED` 且 Owner 单独明确授权后启动；等待不阻塞独立可交付任务；本轮提效指令不代替该授权 |

#### 15.8.1 类型化依赖与路径序（无环核对）

- **功能硬依赖不变**：仍以 §15.3.1 的图为唯一登记；本节不新增类型化功能边。S0-1 的 `#204 → #202 / PR #203 → #207` 是处理顺序，不是类型化边；不存在 `#202 → #204`、`#204 → #207` 或 `#207 → #204`。#204 无功能硬依赖（#185 / #202 只是证据输入），也不依赖 #207。
- **计划文件路径序**：`#202 / PR #203 → #207 → #162 → #151 → #152`。这是 `docs/implementation-plan.md` 的路径锁队列（#207 的计划文件前驱只有 #202，不含 #204；#149 已在更早链上完成），不是功能依赖，也不改写 #149 → #162 → #151 → #152 的既有文档顺序。
- **代码共享路径序**：`#205 → #206 → #170`。三项共用 `runtime_adapter` / 测试 / 设计文件，必须串行；每个任务 `VERIFIED` 后从新基线派下一个。#207 与它们无路径交集。
- **无环核对**：类型化功能图仍为 §15.3.1，本节未新增边。计划文件链只表达路径锁（#207 的前驱只有 #202），代码链（#205 → #206 → #170）与它不相交；两条链只汇入既有硬依赖图（#149✓、#150、#161✓ / #172✓、#169✓ 等），未引入反向边，故合并后无环。
- **临时性**：路径序只是当前锁竞争的排队，任务完成或路径释放后即消失，不固化为功能依赖；任何一方改变允许路径时须重新核对冲突。

#### 15.8.2 排程不等于实施 / 授权状态

- **#186**：仍为 `OPEN` / `PLANNED`，设计 DRAFT v2 未书面冻结、无完整任务包；S1-1 的顺位不使其 `READY`。
- **#174**：只交付运行时快速探测与协作边界设计；方向获 Owner 批准不等于设计冻结，更不等于告警策略上线。冻结后由 Master 另行登记最小契约与选择器原子切换任务。
- **#188**：仍须 A–C `VERIFIED` 后由 Owner 单独明确授权；本轮“提效”指令不代替该授权，#189 随之。
- **#170**：仍是身份来源 / 核验 / 失效规则未冻结的 `OPEN` / `bug`；S0-2 只是路径队列，不能写成已修复或已独立。
- **#205 / #206 / #170**：以 #207 合并并 `VERIFIED` 作为就绪治理门；未满足前不标 `READY` 或 `DISPATCHED`。
- **#151 契约指向**：编排契约、字段语义与合并门的唯一权威路径是 [docs/orchestration.md](orchestration.md)（迁移前为 `kit/orchestration.md`，不再分发）；#151 未 `VERIFIED` 前不得宣称迁移已验收，也不得复制第二份状态表。
- **通用**：没有任务包、真实执行实例与路径冲突检查，不标 `READY` 或 `DISPATCHED`；优先级不是 `PASS`、完成或授权。

**本登记任务的证据**：#207 只改本文件；其 changed paths、命令 / 退出码、九列 AC 矩阵与四平台 CI 记录在对应 PR 正文或评论与当前 head，不在本节预写 `PASS`。

## 16. Phase E — DSH 角色工具适配与独立发行包（Epic #160，2026-10-31）

**目标**：在不重建第二套 AgentOrch、不改现有状态机 / 权限 / 发行清单的前提下，让已配置的 DSH Agent 通过三个固定角色工具接入现有治理能力，并把 DSH bundle 作为同一 Release 的独立第二资产交付。设计已在 [deepseek-harness-agent-role-tools.md](design/deepseek-harness-agent-role-tools.md) 冻结（[#161](https://github.com/ctrlshiftaltdel/AgentOrch/issues/161) / [#172](https://github.com/ctrlshiftaltdel/AgentOrch/issues/172) 已 `VERIFIED`）；**设计冻结不等于 DSH 运行时已支持、已兼容或已实测**。

**固定工具名**：`ao_master`、`ao_execution`、`ao_reviewer`。工具名只表达角色，不构成身份认证；调用者身份与工具可见范围必须由 [#163](https://github.com/ctrlshiftaltdel/AgentOrch/issues/163) 实测的 DSH scope / 配置绑定证明，模型自报 role 不授予任何权限。接口、schema、错误码、安全模型与发布步骤只在设计文稿及子 Issue 定义，本节不复制。

**基线事实与本阶段目标必须分开**：

- **已发布基线**：`v1.7.0` / Format 1.7 是当前主线最新发布（§12.1），Kit 包为 117 个 `REQUIRED` 文件加 `MANIFEST.json`；Phase D（#120–#124）已完成并应增量复用。
- **当前核心契约是** [docs/orchestration.md](orchestration.md)（#151 路径迁移；迁移前为 `kit/orchestration.md`，不再分发）；[#151](https://github.com/ctrlshiftaltdel/AgentOrch/issues/151) **尚未 `VERIFIED`**，不得据此宣称迁移已验收。
- **下游 `agentorch/` 隔离布局**是 [#153](https://github.com/ctrlshiftaltdel/AgentOrch/issues/153) 的目标，**尚未 `VERIFIED`**；#164 / #167 只能按已实测布局限定范围，或把该布局写成依赖 / 目标。
- **DSH 运行时能力**：公开 API、per-agent 工具隔离、调用上下文、bundle 加载与安装行为均待 #163 / [#167](https://github.com/ctrlshiftaltdel/AgentOrch/issues/167) 实测；未实测项记 `UNKNOWN` / `NOT_RUN`，不得由 stub 或静态 YAML 测试代替。
- **edition**：本仓库为 `github`；DSH 适配不得用本地 SQLite 冒充 GitHub 任务载体。

### 16.1 阶段顺序、交付与出口

| 阶段 | 承接 Issue | 交付 | 依赖 | 出口条件 |
|---|---|---|---|---|
| E0 设计冻结 | [#161](https://github.com/ctrlshiftaltdel/AgentOrch/issues/161) / PR [#168](https://github.com/ctrlshiftaltdel/AgentOrch/pull/168)；[#172](https://github.com/ctrlshiftaltdel/AgentOrch/issues/172) / PR [#173](https://github.com/ctrlshiftaltdel/AgentOrch/pull/173) | 冻结 DSH 适配器与三角色工具设计；把文稿状态修正为 `FROZEN` | Epic [#160](https://github.com/ctrlshiftaltdel/AgentOrch/issues/160) | 已满足：#161 / #172 均 `VERIFIED`；只证明设计状态，不证明运行时 |
| P1 阶段计划登记 | [#162](https://github.com/ctrlshiftaltdel/AgentOrch/issues/162) | 本节与第 1 节 / §14 / §15 的交叉引用；不实现工具 | E0；[#149](https://github.com/ctrlshiftaltdel/AgentOrch/issues/149) 已 `VERIFIED`；计划文件顺序 #149 → #162 | #162 `VERIFIED`；后续 #151 / #152 必须保留本段 |
| E1 API 能力核验 | [#163](https://github.com/ctrlshiftaltdel/AgentOrch/issues/163) | 固定 DSH 版本上的工具注册、角色可见性、调用者身份 / 工作目录与 engine range 实测报告 | E0 | 角色隔离正负向、身份 / 上下文、版本兼容与真实包加载均有可定位证据 |
| E2 角色工具与安全适配器 | [#164](https://github.com/ctrlshiftaltdel/AgentOrch/issues/164) | `ao_master` / `ao_execution` / `ao_reviewer` 三个工具与安全适配器；不新增状态机 | #162、#163；涉及目标布局时 #151 / #153 | 三工具名称固定、角色越权被拒绝、fail-closed 与 edition 分流有测试 |
| E3 bundle 独立构建 | [#165](https://github.com/ctrlshiftaltdel/AgentOrch/issues/165) | 独立 DSH bundle builder、可信哈希基线与可复现自检；不改 Kit 的 `REQUIRED` / `MANIFEST.json` | #162、#163 | 同一 commit / 版本重复构建 SHA-256 一致；拒绝版本、路径与私密数据异常 |
| E4 CI / Release 文档 | [#166](https://github.com/ctrlshiftaltdel/AgentOrch/issues/166) | DSH CI job 与同 tag 双资产发布手册；CI 不自动打 tag / 建 Release | #161、#163、#165 | Kit 现有四平台 CI 不削弱；DSH job 通过；发布仍由 Master 人工执行 |
| E5 最终验收 | [#167](https://github.com/ctrlshiftaltdel/AgentOrch/issues/167) | 隔离安装、迁移、回滚、真实 DSH E2E 与发行验收 | #162–#166、#161；#151 / #153 / [#154](https://github.com/ctrlshiftaltdel/AgentOrch/issues/154) 达到可支持状态 | 真实 DSH 运行证据齐备；未验证平台与版本明示 `NOT_RUN` / `UNKNOWN` |

表中不写实时状态：任务状态只写第 1 节；DSH 各 Issue 的实时状态与条件边见 §15.2.2 / §15.3，本节不另建状态表。

### 16.2 依赖图与无环核对

```text
#161✓ → #162
#172✓ → #162
#149✓ → #162
#161✓ → #163
#162 → #164, #165, #167
#163 → #164, #165, #166, #167
#164 → #167
#165 → #166, #167
#166 → #167
```

- **无环**：可按 `#161 / #172 / #149 → #162 → #163 → #164 → #165 → #166 → #167` 拓扑排序；#164 同时依赖 #162 / #163，#165 同时依赖 #162 / #163，#167 汇合 #162–#166。
- **条件边（不是硬依赖）**：`#151 / #153 → #164 / #167` 仅当实现要支持目标 `agentorch/` 布局时成立；否则必须把范围准确限定到已实测布局，或等待 #151 / #153 `VERIFIED`。#154 是后续文档一致性门禁，不是本阶段登记前置。
- **计划文件路径序**：`#149 → #162 → #151 → #152`。这是同一文件的编辑 / 锁顺序，**不是功能硬依赖**；#151 / #152 必须从最新主分支重验并保留本段。
- **排程**：§15.8 的优先级只调整执行先后，不新增本图之外的边，也不改变任何 `READY` / 授权门。

### 16.3 入口、出口与失败处理

- **入口**：#161 / #172 / #149 已 `VERIFIED`；#162 完成登记并经 Master 审查、合并与主分支验证后，才可把 #163–#167 逐项置 `READY`。任何前置未 `VERIFIED` 前，下游只登记 `PLANNED` / 未 READY。
- **独立入口**：#163 可在 #161 后独立开始，不必等 #162 或实现链。#164 / #165 须等 #162 / #163 `VERIFIED`；#166 须等 #161 / #163 / #165；#167 须等 #162–#166 与治理依赖达到可支持状态。
- **阶段出口**：#163–#167 全部 `VERIFIED`，并在当前主分支 head 有可复核证据：三工具角色可见性与负向调用、调用者身份 / 工作目录、local / github edition 安全分流、脚本篡改拦截、平铺与目标 `agentorch/` 布局（或明确限定）、版本不兼容、bundle 可复现、安装 / 升级 / 回滚，以及发布后资产 SHA 复核。
- **失败处理**：API 不足、per-agent scope 不可证明、根 / 身份无法确定、脚本哈希不一致、GitHub 后端缺失或 DSH engine 不兼容时 fail-closed，返回 `UNSUPPORTED` / `UNKNOWN` / `NEEDS_HANDOFF` / `BLOCKED`；不得退回全局工具暴露、不得写本地 SQLite、不得静默安装或自动修改用户 profile。
- Epic #160 的关闭条件不是子 Issue 全部关闭，而是最终 E2E / 发布验收通过、`release/README.md` 已更新、剩余风险均有明确结论。

### 16.4 阶段边界与风险

- 不改任务状态机、路径 / 资源锁、G1–G6、C1–C4 / C1–C8、edition 语义或 Kit 的 `REQUIRED` / `MANIFEST.json`；不新增第四种角色，不自动创建 / 授权 subagent，不自动切换 Master 模型，不自动打 tag / 建 Release / 上传资产。
- DSH 官方 Release 条目会变化：设计文稿 §2.1 的快照带查询时间，只是历史事实；#163 必须在固定版本上重新查询并记录新的时间戳与 tag，不能把预发布条目当作已兼容。
- #153 未 `VERIFIED` 前，只能支持已实测的现行布局；`agentorch/` 布局只能写为目标 / 依赖，不能写成已完成。#151 的契约路径迁移在 `VERIFIED` 前也不得写成已验收。
- 任何未在当前 head 真实执行的平台、profile 或 engine 版本记 `NOT_RUN` / `UNKNOWN`；Node 单测、mock registry 或 YAML 结构检查不能证明真实 DSH 加载与角色隔离。
- 本节只登记阶段目标、顺序、依赖与出口；接口契约、字段、错误码、安全模型与发布步骤以设计文稿和各子 Issue 正文为准，本文件不复制。
