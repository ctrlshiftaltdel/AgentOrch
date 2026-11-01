# AgentOrch 阶段计划（当前推进指针）

> 本文件是**阶段目标与执行路径的唯一权威**：阶段划分、每阶段的交付与出口、任务顺序与依赖、当前状态都在这里。
> 设计目标（为什么做、做到什么程度、原则与边界）只在 [PLAN.md](PLAN.md)；字段、状态取值与合并门语义只在 [docs/orchestration.md](orchestration.md) 4.1 与第 5 节，本文件只引用不重述。
> 本文件只经任务分支 + PR 修改。与 `PLAN.md` 的设计目标冲突时，以设计目标为准并停下对齐，不得各写一套。

- 版本：v5.0（2026-11-01，Issue #152 将本文件收为当前推进指针；已完成详情改为 Issue / PR / Release 链接，历史归属见 §5）
- 日期：2026-11-01
- 状态：Phase A–D 已交付；最新发布为 [v1.7.0](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.7.0)（Format 1.7）。当前主线是 Epic [#146](https://github.com/ctrlshiftaltdel/AgentOrch/issues/146) 的治理收尾与 DSH Epic [#160](https://github.com/ctrlshiftaltdel/AgentOrch/issues/160) 的 API 核验；DSH 运行时仍未实测，拓扑 [#179](https://github.com/ctrlshiftaltdel/AgentOrch/issues/179) 的实机权限 / 隔离链路仍 `NOT_RUN`。
- 适用范围：AgentOrch 核心规则、local/github edition、任务模板、验证脚本与发行机制
- 设计目标来源：[PLAN.md](PLAN.md)

## 1. 当前状态（唯一状态表）

任务状态、阻塞与下一步只在第 1 节更新。本表只列**未完成、未验证或阻塞**的任务；已完成任务的分组链接见 §5。状态值、证据矩阵与门禁判定只在 [docs/orchestration.md](orchestration.md) 4.1 与第 5 节定义。

**当前推进指针（2026-11-01）**

- **治理收尾（Epic #146）**：[#153](https://github.com/ctrlshiftaltdel/AgentOrch/issues/153) 旧项目隔离接入 → [#154](https://github.com/ctrlshiftaltdel/AgentOrch/issues/154) 文档一致性门禁 → [#158](https://github.com/ctrlshiftaltdel/AgentOrch/issues/158) 端到端回归与发行验收。本任务 #152 完成计划精简后才放行 #153（两者共享 `docs/README.md`）。
- **DSH（Phase E，Epic #160）**：设计已 `FROZEN`，阶段登记 #162 已 `VERIFIED`；[#163](https://github.com/ctrlshiftaltdel/AgentOrch/issues/163) 因本机没有可执行的 DSH 而 `BLOCKED`，运行时能力 `NOT_RUN`；#164–#167 未 READY。
- **任务驱动拓扑（Epic #176）**：[#179](https://github.com/ctrlshiftaltdel/AgentOrch/issues/179) 处于 `status:in-progress`，PR [#231](https://github.com/ctrlshiftaltdel/AgentOrch/pull/231) 未合并；实机权限 / 隔离链路 `NOT_RUN`。[#181](https://github.com/ctrlshiftaltdel/AgentOrch/issues/181) 随后。
- **AVE / 主动发行 / 自主推进**：[#189](https://github.com/ctrlshiftaltdel/AgentOrch/issues/189)、[#195](https://github.com/ctrlshiftaltdel/AgentOrch/issues/195)、[#229](https://github.com/ctrlshiftaltdel/AgentOrch/issues/229) / [#230](https://github.com/ctrlshiftaltdel/AgentOrch/issues/230) 分别受其前置与授权边界约束。
- **核心契约**：唯一权威路径是 [docs/orchestration.md](orchestration.md)（#151 已 `VERIFIED`，PR [#232](https://github.com/ctrlshiftaltdel/AgentOrch/pull/232)）；不再存在 `kit/orchestration.md` 第二权威。
- **最新发布**：[Release v1.7.0](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.7.0)（Format 1.7）；更早的 1.6.x 发布事实与真实升级证据见 [docs/upgrade.md](upgrade.md) §14.1–§14.7。

**阶段概览**

| 阶段 | 状态 | 权威依据 |
|---|---|---|
| Phase A–C | 已交付 | Epic [#1](https://github.com/ctrlshiftaltdel/AgentOrch/issues/1)、[#46](https://github.com/ctrlshiftaltdel/AgentOrch/issues/46)；§5 |
| Phase D | 已交付，随 v1.7.0 发布 | [#117](https://github.com/ctrlshiftaltdel/AgentOrch/issues/117)–[#124](https://github.com/ctrlshiftaltdel/AgentOrch/issues/124)、[Release v1.7.0](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.7.0) |
| 治理 / ETR 新阶段（Epic #146） | 进行中：治理子链未完成 | #153 / #154 / #158 未完成；#155–#157 已 `VERIFIED` |
| DSH Phase E（Epic #160） | 阶段已登记；运行时未实测 | #162 `VERIFIED`；#163 `BLOCKED`；#164–#167 未 READY |
| 任务驱动拓扑（Epic #176） | 部分交付 | #177 / #178 / #180 `VERIFIED`；#179 / #181 开放 |
| 自适应验证（Epic #184） | 部分交付 | #185–#188 `VERIFIED`；#189 开放 |
| 主动发行（Epic #191） | 部分交付 | #192–#194 `VERIFIED`；#195 开放 |
| 自主推进与轻量任务 | 未开始 | #229 → #230 开放 |

**未完成任务**

| 任务 | 交付 | 状态 | 依赖 / 阻塞 / 下一步 |
|---|---|---|---|
| [#146](https://github.com/ctrlshiftaltdel/AgentOrch/issues/146) | Epic：项目规范治理、旧项目隔离接入与 ETR 驱动评价选型 | `OPEN`（Epic） | #147 / #149 / #150 / #151 与 #155–#157 已 `VERIFIED`；剩余 #152 / #153 / #154 / #158 |
| [#152](https://github.com/ctrlshiftaltdel/AgentOrch/issues/152) | 精简本文件并治理过期规划文档 | `OPEN` / `status:in-progress`；本任务执行中，未 `VERIFIED` | 前置 #150、#151 已 `VERIFIED`；完成后放行 #153 |
| [#153](https://github.com/ctrlshiftaltdel/AgentOrch/issues/153) | 旧项目 `agentorch/` 隔离接入与安全升级 | `OPEN` / `PLANNED`，未 READY | 依赖 #150–#152 均 `VERIFIED`；与 #152 共享 `docs/README.md`，必须待本任务收口 |
| [#154](https://github.com/ctrlshiftaltdel/AgentOrch/issues/154) | 文件责任、文档影响与计划状态一致性门禁 | `OPEN` / `PLANNED`，未 READY | 依赖 #150–#153；#153 未 `VERIFIED` |
| [#158](https://github.com/ctrlshiftaltdel/AgentOrch/issues/158) | 新阶段端到端回归、发行验收与文档同步 | `OPEN` / `PLANNED`，未 READY | 依赖 #153、#154 与 #155–#157；#155–#157 与 #73 已 `VERIFIED`，仍待 #153 / #154 |
| [#160](https://github.com/ctrlshiftaltdel/AgentOrch/issues/160) | Epic：DSH 角色工具适配与独立发行包 | `OPEN`（Epic） | #161 / #162 / #172 已 `VERIFIED`；#163 `BLOCKED`；#164–#167 未 READY |
| [#163](https://github.com/ctrlshiftaltdel/AgentOrch/issues/163) | DSH 公开 API、角色级工具隔离与上下文核验 | `OPEN` / `status:blocked`；`BLOCKED` | 本机没有可执行的 DSH、隔离 profile 或固定的已发布版本；API 支持 `NOT_RUN`；恢复条件是可安装的目标 DSH 版本 |
| [#164](https://github.com/ctrlshiftaltdel/AgentOrch/issues/164) | 实现 DSH 三角色工具与安全适配器 | `OPEN` / `PLANNED`，未 READY | 依赖 #161–#163、#162；#163 未 `VERIFIED`；涉及目标布局时另受 #151 / #153 条件约束 |
| [#165](https://github.com/ctrlshiftaltdel/AgentOrch/issues/165) | DSH bundle 独立构建、可信哈希基线与可复现自检 | `OPEN` / `PLANNED`，未 READY | 依赖 #161、#162、#163；#163 未 `VERIFIED` |
| [#166](https://github.com/ctrlshiftaltdel/AgentOrch/issues/166) | DSH CI 与同 tag 双资产发布手册 | `OPEN` / `PLANNED`，未 READY | 依赖 #161、#163、#165；#163 / #165 未 `VERIFIED` |
| [#167](https://github.com/ctrlshiftaltdel/AgentOrch/issues/167) | DSH 隔离、迁移、发行与实机验收 | `OPEN` / `PLANNED`，未 READY | 依赖 #162–#166、#161；#151 / #153 / #154 条件依赖；#163–#166 未 `VERIFIED` |
| [#176](https://github.com/ctrlshiftaltdel/AgentOrch/issues/176) | Epic：Task-Driven Agent Topology | `OPEN`（Epic） | #177 / #178 / #180 已 `VERIFIED`；#179 / #181 开放 |
| [#179](https://github.com/ctrlshiftaltdel/AgentOrch/issues/179) | 执行拓扑与运行时能力适配，保留真实派发门禁 | `OPEN` / `status:in-progress` | PR [#231](https://github.com/ctrlshiftaltdel/AgentOrch/pull/231) 未合并；实机权限 / 隔离链路 `NOT_RUN`；只读工具与夹具通过不等于 `VERIFIED` |
| [#181](https://github.com/ctrlshiftaltdel/AgentOrch/issues/181) | 拓扑试点、双底座验收与发行兼容 | `OPEN` / `PLANNED`，未 READY | 依赖 #179、#180；#180 已 `VERIFIED`，#179 未 `VERIFIED`；核心权限变更须 Owner 批准 |
| [#184](https://github.com/ctrlshiftaltdel/AgentOrch/issues/184) | Epic：Adaptive Verification（AVE） | `OPEN`（Epic） | #185–#188 已 `VERIFIED`；#189 开放 |
| [#189](https://github.com/ctrlshiftaltdel/AgentOrch/issues/189) | AVE 双底座发布兼容与校准 | `OPEN` / `PLANNED`，未 READY | #188 已 `VERIFIED`；DSH 按实际进度 |
| [#191](https://github.com/ctrlshiftaltdel/AgentOrch/issues/191) | Epic：主动式 SemVer 发行节奏与版本治理 | `OPEN`（Epic） | #192–#194 已 `VERIFIED`；#195 开放 |
| [#195](https://github.com/ctrlshiftaltdel/AgentOrch/issues/195) | 常规发行与 Kit / DSH 同版本交付验证 | `OPEN` / `PLANNED`，未 READY | #193 / #194 已 `VERIFIED`；Owner 预授权与 DSH 实际进度未满足 |
| [#229](https://github.com/ctrlshiftaltdel/AgentOrch/issues/229) | 冻结自主推进、持续授权与轻量任务边界 | `OPEN` / `PLANNED`，未 READY | 无功能硬依赖；需要所有者决策；未派发，不构成权限或常设授权 |
| [#230](https://github.com/ctrlshiftaltdel/AgentOrch/issues/230) | 实现自主推进与轻量任务流程 | `OPEN` / `PLANNED`，未 READY | 依赖 #229 `VERIFIED`；未派发 |

## 2. 阶段顺序与依赖

以下是当前仍需推进的依赖边（无环）；已完成边不再重复。

```text
#152（本任务） → #153 → #154 → #158
#163 → #164, #165 → #166 → #167
#179 → #181
#229 → #230
```

- **治理链**：#153 → #154，两者都汇入 #158；#158 另需 #155–#157 与 #73（均已 `VERIFIED`）。
- **DSH 链**：#163 是运行时核验起点；#164 / #165 需 #162 / #163；#166 需 #165；#167 汇合 #162–#166 与 #161。涉及目标 `agentorch/` 布局时，#164 / #167 另受 #151 / #153 条件约束（#151 已 `VERIFIED`，#153 未完成）。
- **拓扑链**：#179 需 #178（已 `VERIFIED`）；#181 需 #179 / #180（#180 已 `VERIFIED`）。
- **AVE 链**：#189 需 #188（已 `VERIFIED`）。
- **主动发行链**：#195 需 #193 / #194（均已 `VERIFIED`），且 Owner 预授权与 DSH 实际进度满足。
- **自主推进链**：#230 需 #229 `VERIFIED`。

**路径串行**（不新增功能依赖）：

- `docs/implementation-plan.md`：#152 完成后才放行 #153；本文件只经任务分支 + PR 修改。
- `docs/README.md`：#152 → #153，先完成计划精简与导航清理。
- 其他共享路径按任务登记块的实际 `locked_paths` 串行；计划表的顺序不等于可并行。

## 3. 阻塞、风险与授权边界

- **#163 / DSH 运行时**：本机没有可执行的 DSH，也没有隔离 profile 与固定的已发布版本；工具注册、角色隔离、调用上下文与 bundle 兼容均为 `NOT_RUN`。恢复条件是可安装的目标 DSH 版本 + 隔离 profile。设计 `FROZEN` 不等于运行时已支持。
- **#179 / 真实运行时**：只读交接工具与夹具场景已通过，但真实权限拒绝与上下文隔离仍是 `NOT_RUN`；PR #231 未合并。不得用一句 `PONG` 或夹具冒充实机链路，也不得据此标 `VERIFIED`。
- **#188 / 风险试点**：Owner 已授权并 `VERIFIED` 的只是 L0 合并后例外；默认仍是“快速测试 + 完整回归 + `backup` + 静态检查”。只有 `verification_profile: risk_pilot` 且调用者用证据证明变更为 L0 时才可省略合并后完整回归；CI 全套、L1–L3、发布 / 安全 / 权限 / 身份 / 迁移 / 锁 / 状态机相关检查一律不减。详见 [docs/PROJECT.md](PROJECT.md)。
- **设计提案不是现行规则**：[task-driven-agent-topology.md](design/task-driven-agent-topology.md)、[adaptive-verification-and-quality-gates.md](design/adaptive-verification-and-quality-gates.md) 与 [semver-proactive-release-policy.md](design/semver-proactive-release-policy.md) 仍是 `PROPOSED`；冻结子契约（[verification-planner-b-contract.md](design/verification-planner-b-contract.md)、[verification-impact-c-contract.md](design/verification-impact-c-contract.md)）只约束各自的只读工具。
- **#229 / #230**：自主推进与轻量任务边界尚未冻结；#229 只登记需求，不构成权限、常设授权、自动合并或减检。
- **契约唯一路径**：核心编排契约只在 [docs/orchestration.md](orchestration.md)；#151 已完成迁移并 `VERIFIED`。
- **已知文档不一致**：治理设计文稿 [next-phase-governance-and-agent-performance.md](design/next-phase-governance-and-agent-performance.md) 文件头仍写 `PROPOSED`，但 #147 已 `VERIFIED`；差异登记在 [repository-map.md](architecture/repository-map.md) §9.3，由后续文档治理任务处理，不影响 #147 的结论。
- **未验证项保留**：历史发布升级只覆盖 [docs/upgrade.md](upgrade.md) §14.5（1.6.2 → 1.6.3）与 §14.6（1.6.3 → 1.7.0）；真实 1.3 / 1.4 资产、真实 1.6.0 / 1.6.1 → 1.6.2、真实断网 / 限速 / DNS、`HD-11`、签名校验（`NOT_APPLICABLE`，无签名资产）与非 GitHub-hosted 平台仍为 `NOT_RUN` / `UNKNOWN` / `NOT_APPLICABLE`。

## 4. 阶段出口

- **治理 / ETR 阶段（Epic #146）**：#153、#154、#158 全部 `VERIFIED`，并在当前主分支 head 有可复核证据；Epic 关闭前按阶段审计处理剩余风险。
- **DSH Phase E（Epic #160）**：#163–#167 全部 `VERIFIED`，其中 #163 / #167 必须包含固定 DSH 版本上的真实注册、角色隔离、调用上下文、bundle 加载与安装 / 升级 / 回滚证据；未执行平台与版本明示 `NOT_RUN` / `UNKNOWN`。Epic 不以“子 Issue 全部关闭”自动完成，仍以最终 E2E / 发布验收与 `release/README.md` 更新为准。
- **任务驱动拓扑（Epic #176）**：#179 完成真实运行时链路（当前 `NOT_RUN`），且 #181 完成双底座试点与发行兼容。
- **自适应验证（Epic #184）**：#189 完成双底座发布兼容与校准；#188 的 L0 试点继续按 §3 的边界执行。
- **主动发行（Epic #191）**：#195 在 Owner 预授权与 DSH 实际进度满足后验证常规发行与同版本交付。
- **自主推进（#229 / #230）**：#229 冻结契约并经 Owner 审阅，#230 按该契约实现并验证；未完成前不改变现行权限与门禁。

## 5. 已完成与历史追溯

### 5.1 已完成（只保留分组链接）

| 范围 | 结果 | 权威依据 |
|---|---|---|
| Phase A / B / C 与 Epic #1 | 已交付并关闭 | [Epic #1](https://github.com/ctrlshiftaltdel/AgentOrch/issues/1)、#2–#19 |
| Epic #46 自我完善与安全更新（S0–S6） | 已关闭；阶段审计无 `BLOCKING` | [Epic #46](https://github.com/ctrlshiftaltdel/AgentOrch/issues/46)、[审计评论](https://github.com/ctrlshiftaltdel/AgentOrch/issues/46#issuecomment-6415751115)、[docs/self-improvement-and-update-plan.md](self-improvement-and-update-plan.md) |
| Epic #46 硬化项与后续补丁链 | #71 / #72 / #73 / #85 / #86 / #93 / #95 / #99–#104 / #113 均已 `VERIFIED` | 各 Issue、[docs/upgrade.md](upgrade.md) |
| 1.6.x 发布链 | v1.6 → v1.6.1 → v1.6.2 → v1.6.3 均已发布 | [Releases](https://github.com/ctrlshiftaltdel/AgentOrch/releases)、[docs/upgrade.md](upgrade.md) §14.1–§14.5 |
| Phase D（#117–#124） | 全部 `VERIFIED`；随 v1.7.0 发布 | [Epic #119](https://github.com/ctrlshiftaltdel/AgentOrch/issues/119)、[agent-role-discovery-and-model-selection.md](design/agent-role-discovery-and-model-selection.md)、[Release v1.7.0](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.7.0) |
| Epic #146 已完成子项 | #147 / #149 / #150 / #151 与 #155 / #156 / #157 均 `VERIFIED` | 各 Issue；#151 的契约在 [docs/orchestration.md](orchestration.md) |
| DSH 设计冻结与阶段登记 | #161 / #172 / #162 `VERIFIED`；运行时未验证 | [Epic #160](https://github.com/ctrlshiftaltdel/AgentOrch/issues/160)、[deepseek-harness-agent-role-tools.md](design/deepseek-harness-agent-role-tools.md)、[#162](https://github.com/ctrlshiftaltdel/AgentOrch/issues/162) |
| 拓扑 A / B / D | #177 / #178 / #180 `VERIFIED` | [Epic #176](https://github.com/ctrlshiftaltdel/AgentOrch/issues/176) |
| AVE A–D | #185 / #186 / #187 / #188 `VERIFIED`（#188 为已授权 L0 试点） | [Epic #184](https://github.com/ctrlshiftaltdel/AgentOrch/issues/184) |
| 主动发行 A–C | #192 / #193 / #194 `VERIFIED` | [Epic #191](https://github.com/ctrlshiftaltdel/AgentOrch/issues/191) |
| 计划与政策维护 | #202 / #204 / #207 `VERIFIED` | 各 Issue；#207 的排序已被本文件的当前指针取代，历史见 Issue |

### 5.2 清理前后内容归属

本次 #152 未创建独立归档文件：下列历史均可由 Issue / PR / Release / 现有文档恢复，且没有“仅本文件保存”的有效审计事实。

| 原章节 | 现归属 |
|---|---|
| 原 §1 全量状态表、§13–§16 重复状态叙述 | 本文件 §1（只保留未完成 / 阻塞）；已完成行回链原 Issue / PR |
| 原 §2–§9 Phase A–C、差距、阶段收口清单 | [Epic #1](https://github.com/ctrlshiftaltdel/AgentOrch/issues/1)、[Epic #46](https://github.com/ctrlshiftaltdel/AgentOrch/issues/46)、各 Issue；本文件 §4 只保留出口 |
| 原 §11 自我完善阶段表 | [Epic #46](https://github.com/ctrlshiftaltdel/AgentOrch/issues/46)、[docs/self-improvement/README.md](self-improvement/README.md)、[docs/self-improvement-and-update-plan.md](self-improvement-and-update-plan.md) |
| 原 §12 发布事实与升级细节 | [Release v1.7.0](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.7.0)、[Release v1.6.3](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.6.3)、[docs/upgrade.md](upgrade.md) §14.5 / §14.6 |
| 原 §13 Phase D | [Epic #119](https://github.com/ctrlshiftaltdel/AgentOrch/issues/119)、[agent-role-discovery-and-model-selection.md](design/agent-role-discovery-and-model-selection.md) |
| 原 §14–§15 治理 / 跨专项登记 | 各 Epic / Issue；本文件 §2–§4 只保留当前顺序、阻塞与出口 |
| 原 §16 DSH 阶段登记 | 本文件 §6（保留登记）；实时状态见 §1；运行时证据仍待 #163 / #167 |

## 6. Phase E — DSH 角色工具适配与独立发行包（Epic #160，阶段登记由 #162 保留）

**目标**：让已配置的 DSH Agent 通过三个固定角色工具接入现有治理能力，并把 DSH bundle 作为同一 Release 的独立第二资产交付。设计与运行时事实必须分开：设计已 `FROZEN`（#161 / #172 `VERIFIED`），**DSH 运行时 API、角色隔离与实机行为仍未实测**。

**固定工具名**：`ao_master`、`ao_execution`、`ao_reviewer`。工具名只表达角色，不构成身份认证；调用者身份与工具可见范围必须由 #163 实测的 DSH scope / 配置绑定证明，模型自报 role 不授予任何权限。

**基线与当前事实**：

- 最新发布 v1.7.0 / Format 1.7；Kit 包为 117 个 `REQUIRED` 文件加 `MANIFEST.json`。
- 核心契约唯一路径是 [docs/orchestration.md](orchestration.md)（#151 `VERIFIED`）。
- 下游 `agentorch/` 隔离布局是 #153 的目标，尚未 `VERIFIED`；#164 / #167 只能按已实测布局限定范围，或把该布局写成依赖。
- 公开 API、per-agent 工具隔离、调用上下文、bundle 加载与安装行为待 #163 / #167 实测；未实测项记 `UNKNOWN` / `NOT_RUN`。
- 本仓库为 `github`；DSH 适配不得用本地 SQLite 冒充 GitHub 任务载体。

### 6.1 阶段顺序、交付与出口

| 阶段 | 承接 Issue | 交付 | 依赖 | 出口条件 |
|---|---|---|---|---|
| E0 设计冻结 | [#161](https://github.com/ctrlshiftaltdel/AgentOrch/issues/161) / PR [#168](https://github.com/ctrlshiftaltdel/AgentOrch/pull/168)；[#172](https://github.com/ctrlshiftaltdel/AgentOrch/issues/172) / PR [#173](https://github.com/ctrlshiftaltdel/AgentOrch/pull/173) | 冻结 DSH 适配器与三角色工具设计；把文稿状态修正为 `FROZEN` | Epic [#160](https://github.com/ctrlshiftaltdel/AgentOrch/issues/160) | 已满足：#161 / #172 均 `VERIFIED`；只证明设计状态，不证明运行时 |
| P1 阶段计划登记 | [#162](https://github.com/ctrlshiftaltdel/AgentOrch/issues/162) | 本节与第 1 节 / §2 的交叉引用；不实现工具 | E0；#149 已 `VERIFIED` | 已满足：#162 `VERIFIED`；#151 / #152 已保留本段 |
| E1 API 能力核验 | [#163](https://github.com/ctrlshiftaltdel/AgentOrch/issues/163) | 固定 DSH 版本上的工具注册、角色可见性、调用者身份 / 工作目录与 engine range 实测报告 | E0 | 角色隔离正负向、身份 / 上下文、版本兼容与真实包加载均有可定位证据 |
| E2 角色工具与安全适配器 | [#164](https://github.com/ctrlshiftaltdel/AgentOrch/issues/164) | `ao_master` / `ao_execution` / `ao_reviewer` 三个工具与安全适配器；不新增状态机 | #162、#163；涉及目标布局时 #151 / #153 | 三工具名称固定、角色越权被拒绝、fail-closed 与 edition 分流有测试 |
| E3 bundle 独立构建 | [#165](https://github.com/ctrlshiftaltdel/AgentOrch/issues/165) | 独立 DSH bundle builder、可信哈希基线与可复现自检；不改 Kit 的 `REQUIRED` / `MANIFEST.json` | #162、#163 | 同一 commit / 版本重复构建 SHA-256 一致；拒绝版本、路径与私密数据异常 |
| E4 CI / Release 文档 | [#166](https://github.com/ctrlshiftaltdel/AgentOrch/issues/166) | DSH CI job 与同 tag 双资产发布手册；CI 不自动打 tag / 建 Release | #161、#163、#165 | Kit 现有四平台 CI 不削弱；DSH job 通过；发布仍由 Master 人工执行 |
| E5 最终验收 | [#167](https://github.com/ctrlshiftaltdel/AgentOrch/issues/167) | 隔离安装、迁移、回滚、真实 DSH E2E 与发行验收 | #162–#166、#161；#151 / #153 / #154 达到可支持状态 | 真实 DSH 运行证据齐备；未验证平台与版本明示 `NOT_RUN` / `UNKNOWN` |

### 6.2 依赖与边界

```text
#161✓ / #172✓ / #149✓ → #162✓
#161✓ → #163
#162✓ → #164, #165, #167
#163 → #164, #165, #166, #167
#164 → #167
#165 → #166, #167
#166 → #167
```

- 无环；#167 汇合 #162–#166 与 #161。
- 条件边 `#151 / #153 → #164 / #167` 只在实现要支持目标 `agentorch/` 布局时成立；否则必须明确限定到已实测布局。
- 不改任务状态机、路径 / 资源锁、G1–G6、C1–C4 / C1–C8、edition 语义或 Kit 的 `REQUIRED` / `MANIFEST.json`；不新增第四种角色，不自动创建 / 授权 subagent，不自动切换 Master 模型，不自动打 tag / 建 Release / 上传资产。
- 失败 fail-closed：API 不足、scope 不可证明、根 / 身份无法确定、脚本哈希不一致、后端缺失或 engine 不兼容时返回 `UNSUPPORTED` / `UNKNOWN` / `NEEDS_HANDOFF` / `BLOCKED`，不得退回全局工具暴露、写本地 SQLite 或静默安装。
- 未在当前 head 真实执行的平台 / profile / engine 版本记 `NOT_RUN` / `UNKNOWN`；Node 单测、mock registry 或 YAML 结构检查不能证明真实 DSH 加载与角色隔离。

## 7. 维护规则

- **只经 PR 修改**：本文件的任何变更都要走任务分支 + PR + 独立审查；不在会话里口头改计划而不落文件。
- **状态只写第 1 节**：任务状态、阻塞与下一步只改第 1 节；设计目标改动才动 `PLAN.md`。
- **已完成回链**：已完成任务不内联长摘要；在本文件保留分组链接，详情回原 Issue / PR / Release。
- **不复制定义**：字段、状态与门禁语义始终引用 [docs/orchestration.md](orchestration.md)；发现本文件与核心文档口径不一致，先停下对齐。
- **冲突处理**：与 `PLAN.md` 冲突时以设计目标为准并停下对齐。
