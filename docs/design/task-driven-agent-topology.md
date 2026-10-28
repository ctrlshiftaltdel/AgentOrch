# Task-Driven Agent Topology — 任务驱动的 Agent 执行拓扑改进方案

> 状态：设计提案，尚未批准更改核心角色权限或审查门；2026-10-28。
> 上位目标：`docs/PLAN.md`；状态机、G1–G6、能力探测和审批唯一权威：`kit/orchestration.md`；项目配置：`docs/PROJECT.md`。
> 本文只定义新增设计方案及实施验收；发生冲突以上位契约为准，必须通过独立的契约变更任务解决，绝不隐式修改。
> 依据：Anthropic [Building agents: When to fold subagents into your main agent loop](https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop)；[single-agent-loop-kit](https://github.com/anthropics/single-agent-loop-kit)。两者提供方法论而非本项目的质量证明。

## 1. 目标、边界与基本原则

目标：在保持 AgentOrch 的角色职责、权限边界、可追溯状态、独立审查和证据门禁不变量的前提下，由**具体任务**的性质决定调用 Skill、Tool、当前主 Agent、单独 Execution 实例、并行实例或独立 Reviewer，而不是先按岗位创建常驻 subagent。

原则：

1. **Role defines responsibility**：Master、Execution、Reviewer 是职责、权限、证据和签署者身份，**不是固定的进程数、模型实例数或岗位代理目录**。
2. **Task defines execution**：先形成可验收的事务边界；再判断哪些步骤由确定性代码/Tool 处理、何时加载 Skill、何时需要隔离上下文或并行实例；最后匹配模型/Runtime。
3. **Evidence defines completion**：完成由任务验收、真实执行结果、独立性要求与当前 head 的证据决定，不能依据 Agent 自述。
4. **Governance before optimization**：风险、权限、独立性、文件/资源锁与合并门是硬约束；只有通过硬约束的候选拓扑才能比较成本、耗时与成功率。
5. **Capability is not dispatch**：模型/子代理发现、候选排序、拓扑推荐不得冒充实际 spawn / wait / collect、授予权限、启动任务或通过审查。
6. **Runtime-neutral, fail closed**：AgentOrch 不自建新 Runtime / 网关；运行时适配器只报告可信能力事实，UNKNOWN、STALE、冲突和工具失效时降级人工交接，不自动扩大权限。
7. **Minimum viable context**：主对话仅保留决策所需上下文；独立任务交付必须保留可复核的原始证据引用、限制及未验证假设，避免摘要代替事实。

## 2. 概念模型与架构

- **Role**：治理角色 Master / Execution / Reviewer，定义责任和权限；一个实例在不同阶段可以承担被授权的不同职责，但不能因此伪装成独立的审核主体。
- **Task**：目标、验收标准、依赖、允许修改路径、资源、风险及交付证据构成的工作单元。子任务应按产物与依赖划分，不按照 Backend/Database/Security 等岗位默认拆分。
- **Agent instance**：实际运行的会话/进程/上下文；可能是主 Agent、独立 subagent 或由人工启动的其他会话。
- **Skill**：按需加载的程序性知识/操作规范，**不是**权限边界。
- **Tool**：确定性操作或封装接口；写入、授权、资源消耗和安全前置条件必须由工具/宿主实际执行；不能依赖提示词。
- **Topology**：任务在当前已授权 Runtime 下具体采用的主循环、工具/技能、独立执行实例和审查实例的连接方式；属建议性选择，不替代状态机。
- **Independence**：包括可信模型身份、独立上下文/审查材料、执行者和批准者分离及可审计证据。不同模型名不充分，family/alias 自述也不充分；必须沿用已有可信底层身份校验相关 Issue。

建议工作链：

`Goal → task & acceptance decomposition → risk/independence classification → topology candidates & hard constraints → capability & agent discovery → model/instance matching → authorized dispatch → evidence/independent review → merge & post-merge verification → outcome feedback`

当前 `kit/orchestration.md` 的 C1–C4、G1–G6、状态机、锁及版本化任务记录始终优先。新增流程只在现有「角色选择入口」的前面增加一个**非派发**决策步骤；不改变任何既有状态值。

## 3. 决策算法

### 3.1 输入

读取任务源的目标、AC、依赖、路径、资源锁、验收证据及风险/不可逆动作；Runtime 实际能力事实；项目的硬性人工决策规则；执行上下文估算；当前 scope 中**可靠**的历史表现（足够样本且身份/任务类型一致）。不读取其他 scope 的私有报告，不从聊天文本生成权限。

### 3.2 必要的独立性判断

先判定是否需要：
- 独立判断（例如安全、权限、破坏性变更、高风险复杂重构、具有实质不确定性的结论）；
- 上下文隔离（长日志、大型仓库、大量文档，主 Agent 不应装载全部材料）；
- 并行探索（任务依赖与路径、资源锁允许；不同探索结果可独立验证）；
- 工具/权限隔离（专门账号、网络、文件或写入能力必须隔离）。

即便不满足以上条件，也要执行现有 Master 独立检查/审批契约；任何免除独立 Reviewer 的方案不能默默降低 Master 独立检查。Master 本人执行实现时，既有例外授权规则和不自批约束照旧；若真实独立审批者不可用，则保持阻塞或人工交接，不将同一上下文的自评标记为独立审核通过。

### 3.3 拓扑候选

- **T0 — Tool/Skill in main loop**：确定性步骤、简单查询或无隔离需求；Skill 仅帮助正确调用 Tool。若涉及实现文件变更，仍必须遵守现有 Master 默认不实现、显式例外授权与后续门禁。
- **T1 — Sequential execution**：任务由一个真实 Execution 实例完成，再由 Master 在独立检查阶段验收；不是“执行者自批”。
- **T2 — Execution + independent Reviewer**：风险、审查策略或专业独立判断明确要求时启用，并由 Master 决策。
- **T3 — Parallel isolated executions**：存在多个可独立验收的子任务、依赖为 DAG、路径/资源冲突检查通过时启用；必要时可附加独立 Reviewer。
- **T4 — External/Manual handoff**：运行时不具备已验证的子代理能力、身份或工具不可证时，输出明确可执行任务包，交人工开启隔离会话。不能伪造 spawn。

以上并非强制使用固定数目的角色实例，也不创建 Backend/Frontend/Database 等岗位代理；一个任务的独立 Reviewer 由其独立审查需求定义。

### 3.4 选择流程与不变量

1. 完成任务契约与风险分级（无法判定的高影响事项升级人工决定，不自动判低风险）。
2. 生成候选 T0–T4，先应用硬过滤：权限、模型身份与独立性、C1–C4、锁/依赖、资源、实际 spawn/wait/collect、无权限扩张。
3. 对可执行候选比较预估任务完成率、严重缺陷风险、总体成本与延迟；缺样本标记 UNKNOWN，不伪造精度。
4. 输出结构化推荐与备选、排除原因、实际证据来源/时间、风险、人工决策点。
5. 只有真正取得锁、状态推进且真实执行实例确认接收后才记 DISPATCHED；推荐和调度尝试不算执行成功。
6. 审查、Master 批准与合并后验证不变。若目标漂移或关键不变量失败，回滚或停止，不以成本优势补偿质量退化。

**禁止**在没有正式变更 AGENTS.md 和核心契约、验证矩阵前引入“轻量模式可自批/可自行合并”的例外；本阶段仅优化执行实例拓扑，不缩减治理门禁。

## 4. 输出契约和可审计证据

推荐使用项目无关的 JSON schema + Markdown 简报（CLI 可输出 JSON，终端展示 Markdown），不得与现有角色匹配报告重复建库。建议字段：

- `schema_version`, `task_ref`, `goal_digest`, `acceptance_refs`, `risk`（含原因与来源）, `required_independence`
- `candidate_topologies[]`: `id`、`eligible`、`hard_constraint_results[]`、`tradeoffs`
- `selected_topology` 或 `null`，`selection_status`: `RECOMMENDED | UNKNOWN | BLOCKED | MANUAL_HANDOFF`
- `role_assignments[]`：role 与任务关联、可信候选实例标识、权能范围、必要隔离要求；引用现有角色发现/评分结果而不重新计算一套
- `evidence_refs[]`：运行时能力探测、任务源、模型身份、锁检查和分析依据的引用及时间
- `limitations[]`、`required_human_decisions[]`、`alternatives[]`

隐私：本地模型候选与性能记录仍按 `.local/agent-orch/<scope-id>/` 管理，绝不将敏感候选/运行环境细节复制进公开 Issue/PR。公开任务载体仅记录非敏感的拓扑决策摘要、证据索引与最终真实派发状态。严禁记录密钥及私人路径。

## 5. 评测设计：目标实现而非角色表演

基准分层：文档/配置小任务、跨模块功能、独立并行任务、安全权限/不可逆变更、工具/身份故障与审查对抗样例。每类不少于若干人工审核的真实验收用例；先锁定评价标准，再运行对照，不能只挑有利案例。

硬性验收：越权写入=0、自批冒充独立审查=0、非法锁/依赖绕过=0、未经许可不可逆操作=0、证据伪造=0、关键回归未检测=0。若任何关键硬门禁失败，不允许用成本改善抵消。

量化指标：端到端 AC 通过率、任务实际 VERIFIED 率（附样本及滞后窗口）、首次验收率、修复轮次、后续回归缺陷严重度、Reviewer 真实缺陷发现/误报/漏报、累计模型 Token（包含子代理）、运行耗时、实际/估算成本、跨 Agent handoff 数及不必要上下文载入量。没有真实记录则标 UNKNOWN，不得把估算当成实际。

比较法：在相同任务集与测试环境下做 T1/T2/T3 对照；按任务类别分层并多次重复；包含完整链路回归与相邻任务回归；保留失败轨迹。仅在质量硬约束均满足且端到端目标达成结果不显著退化时才考虑以成本优势折叠子代理。样本不足则建议继续收集，不改变默认行为。

## 6. 兼容、可靠性与回滚

- 默认 **observe-only**：记录候选及决策，不真正启动 Agent、不改变角色权责、锁与状态，且默认沿用现有派发路径。
- 后续受控试点须显式 opt-in；出现 UNKNOWN、身份不可信、Runtime 不支持、并行资源冲突、证据不完整即停止自动调度并转人工。
- 不能由实验拓扑动态修改 `AGENTS.md`、项目权限、角色模型或配置；所有重大变更另经 owner 授权。
- 任务调度前后保留可回退到原角色选择/派发流程的开关；旧记录可读、新字段可选，确保 local/github 双底座一致。
- AgentOrch 仍不是 Runtime。实现 CLI 应保持 Python 标准库零第三方依赖；不将 Anthropic 参考 loop.py 直接植入产品。
- 固定流程优先确定性代码，规则限制下沉到执行工具与宿主审批层；Skill 不能替代技术强制约束。

## 7. 分阶段实施路线与 Issue 边界

### Stage A — 原则与契约冻结（串行）

- 明确治理角色 ≠ Agent 实例；Task/Context/Topology 概念与决策顺序。
- 对 `docs/PLAN.md`、`AGENTS.md`、`kit/orchestration.md`、现有阶段计划先进行差异分析，避免重复权威。核心角色权限或状态变更须独立批准；本阶段不改变权限。
- 审核旧文件迁移相关 Issue 的预期路径，以免在迁移期间编辑两个等价入口。
- DoD：一份唯一权威的新增决策契约；双底座与 Reviewer 独立性不变量测试通过。

### Stage B — 任务分类与拓扑推荐（依赖 A）

- 实现 Task Risk/Context/Independence classifier 与候选 T0–T4；任务类型从任务源提取。
- 实现“先判断是否值得独立 Agent，再调用 S2/S3/S4 模型匹配”。
- 无证据/无权限/无能力时输出 BLOCKED/MANUAL_HANDOFF；确保推荐绝非派发。
- DoD：单元测试覆盖普通/高风险/歧义、缺身份、资源冲突、禁止自批及模式选取。

### Stage C — Runtime Adapter 接线（依赖 B）

- 在已存在的运行时发现与工具事实基础上只增加拓扑建议映射；适配 OpenCode/Codex/DSH 要以各自可信能力实测为依据。
- 实际 spawn/wait/collect 只有可证时才能记成功，保持现有状态与锁、人工交接兼容。
- DoD：模拟 adapter + 至少一种经授权实际运行环境的完整交付证据；不可调用时诚实降级。

### Stage D — 端到端评测和反馈（可与 C 的文档子项并行）

- 复用已有 S4/ETR/评价可信度工作，不另起一套总分；新增端到端目标完成和拓扑消耗归因。
- 构造包含“折叠后邻接任务受损”“独立性绕过”“风险错误降级”的负向对照。
- DoD：基线、比较工具、原始轨迹、覆盖率/样本数、失效回退和审查记录可重现。

### Stage E — 有门禁的试点、发行、文档同步（依赖 C 与 D）

- 默认关闭自动改变拓扑，仅以建议模式覆盖现有工作流；有证据后 opt-in 试点。
- 下游项目旧布局与 DSH 发行包沿用独立既有 Issue，不重复创建并行发行体系。
- DoD：local/github，Windows/Linux，模拟/真实环境边界、发行扫描、回滚、负向安全测试通过；主分支按当前 head 合并后复测。

## 8. 应避免的反模式

- 为 Backend、Database、Security 等岗位永久创建代理，迫使每个任务全链路交接。
- 仅因为三种治理角色存在便默认启动三份模型会话。
- 因任务简单就把“执行者自审”写成独立审核，或取消 Master 的审批责任。
- 把 Prompt 内的安全要求误当强制执行；把同 family alias 或自报底层 ID 当可信独立模型。
- 以 Issue 全关闭、Token 降低或模型自评替代用户目标的真实验收。
- 在无真实样本时引入虚假的“最优拓扑打分”。

## 9. 与现有工作去重

此方案在既有 **角色发现/匹配 S1–S5** 前引入 Task → Topology 的上游判定；重用 ETR 和真实表现记录；独立 Reviewer 可信身份与别名映射仍由已有专门 Issue 负责；DSH 的 `ao_master`、`ao_execution`、`ao_reviewer` 名称保持不变，它们表示治理职责入口而不是岗位常驻 Agent。旧项目 `agentorch/` 接入、发行包、文档迁移沿用现有 Issue，不复制实现工作。

## 10. 交付顺序与停止条件

A 冻结设计 → B 只读推荐 → C 受控 Runtime 接线 → D 客观评测 → E 试点发行。A/B 可先在 `observe-only` 完成；C 与 D 可在接口冻结后按独立文件/任务并行。任一关键风险门禁失败、无可靠 Reviewer 独立性依据、模型身份未知、状态/锁不一致、无足够回归证据，均停止推广并恢复既有路径。

**验收最终判据**：一个项目能够解释“为何此任务使用 Tool/Skill、单执行 Agent 或并行/独立审查 Agent”，能给出真实证据和替代方案，并在质量和权限不退化前提下证明端到端目标达成率或效率得到改善。
