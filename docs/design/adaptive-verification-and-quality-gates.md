# AgentOrch 自适应验证与流程提效：设计与实施计划（提案）

> 状态：PROPOSED / NOT IMPLEMENTED；日期：2026-10-28。本文是专项设计提案，不是新的状态机、合并门或阶段状态权威。
> 设计目标权威：`docs/PLAN.md`；阶段顺序/状态权威：`docs/implementation-plan.md`；现行状态与 G1–G6 门：`kit/orchestration.md`（完成 #151 迁移后以新路径为准）。在 Owner 批准且相关契约更新合并之前，所有现行检查仍然强制执行。

## 1. 背景、证据与目标

当前仓库 `docs/PROJECT.md` 配置了快速校验、完整单测、静态检查及合并后快速校验+完整回归+backup+静态检查；`kit/orchestration.md` G4 要求当前 HEAD 所有必需检查通过，G5 要求真实可定位证据，合并后才能 VERIFIED；`docs/PLAN.md` 将不可绕过门禁、当前 HEAD 证据、两个底座同强度作为设计目标。仓库 2026-10-28 的 `docs/implementation-plan.md` 记录 v1.7.0 1047 tests、71/71 delivery scenarios；这些是历史基线，而不是当前实测。

问题假设：低风险变更重复执行全套测试、修复轮次重复全量审查、角色上下文重复加载，形成非必要成本。**目前没有量化证据证明瓶颈分布**；第 A 阶段先测量，再调整。

目标：在不提高高严重度逃逸缺陷、不削弱验收标准的条件下，降低任务完成总耗时、重复验证次数、Token 与审查成本。建议实验目标（非承诺）：L0/L1 验证耗时下降 ≥40%，L2 ≥20%，重复执行测试次数下降 ≥30%；以统计结果和质量指标决定是否推广。

## 2. 与在途规划协调

- **#146（文档治理、ETR、三角色评估）**：采集耗时/Token/回归缺陷可与 ETR telemetry 对接，但不得将“少测”直接作为 Agent 质量加分；避免同一事件在 ETR 和 AVE 重复采集、重复计分。
- **#150、#151、#154、#158**：遵守文件责任地图及唯一权威，避免向多个模板重复写风险规则；#151 迁移核心契约时，AVE 的路径引用必须同步更新。实施前核实各 Issue/PR 实际状态。
- **#176 任务驱动拓扑与 #174 运行时探测**：Risk/Context/Independence 分类决定执行拓扑，Verification Risk 决定验证强度；两者输入可以共享，但输出与授权必须独立，不能让 Execution 或模型自述降低审查强度。
- **Reviewer 可信底层身份修复**：独立审查若是必需门禁，必须先验证 Reviewer 真实模型身份，缺失时 fail closed，不以“缓存通过”绕过。
- **DSH #161–#167**：三角色接口仍是 `ao_master`/`ao_execution`/`ao_reviewer`；验证引擎为底座无关的纯逻辑/CLI，不创建第四个状态库或工具权限捷径；Kit zip 与 DSH tgz 均需发布级完整验证。
- **self-improvement/update**：从可确认的漏测与反馈生成改进建议；升级、迁移、回滚和发行兼容始终按最高风险执行。

在途 Issue 的依赖、状态与设计文档路径只以 GitHub 实时事实与 `docs/implementation-plan.md` 为准；不得据本文直接宣布任何任务 READY/VERIFIED。

## 3. 精确边界：哪些能省，哪些不能省

不变项：G1–G6 全门必须满足；AC 逐条有可追踪证据；当前 HEAD 批准不可复用；Execution 不自批自合；Reviewer 独立性硬约束；安全/权限/迁移/锁/状态机变更的 Owner 决策；所有必需 CI context 在 HEAD 绿灯；两个底座相同的策略语义；真实运行状态，禁止虚构 PASS；测试删减/弱化/skip 不作为提效方式。

可优化项：在 Owner 明确批准并修改唯一权威契约及 Project Profile **之后**，调整不同风险任务的 *required checks* 集合、回归触发时间、审查输入载荷、单轮修复后的增量验证范围。改变测试执行选择不等于修改覆盖标准。主分支合并后检查是否收缩属于 **契约与 PROJECT.md 变更**，不能由新工具自行覆盖当前硬性指令。

注意：缓存的旧 commit PASS 不等于当前 HEAD PASS。为当前 HEAD 制作新的、可审计的等价性证明可作为补充证据；若 G4 或分支保护要求真实重跑，则必须执行真实 CI；不得把缓存伪装为一次新运行。新 HEAD 发生时旧批准一律失效。

## 4. 四级风险与触发条件

| 级别 | 示例 | 默认候选验证集（批准新规前仅影子输出） |
|---|---|---|
| L0 | 纯文本说明/错字，不变更可执行示例、指令、政策、清单 | 文档校验、引用/路径检查、Diff 检视 |
| L1 | 局部无跨模块行为变更 | 关联单测、语法/格式、变更场景 |
| L2 | 接口变更、多模块功能、提示词/角色选型非授权部分 | 关联单测、集成/负向回归、独立审查 |
| L3 | 状态机、锁、权限、Reviewer 身份、派发、升级、迁移、发布、安全、证据门禁 | 完整相关回归、端到端/负向/失败关闭、双底座与发布兼容、独立审查、Owner 决策（如适用） |

风险计算为 `max(path_rules, semantic_rules, uncertainty_rules, project_overrides)`。文件路径规则不得独自决定风险（例如 docs 中可执行的契约指令应升级）。未知上下文/影响图不完整/检查器故障时保守升至 L2 或 L3；L3 高危分类不可由执行者降级。危险路径或高危语义即使只改一行仍为 L3。分类器记录命中的规则、来源、理由和谁批准覆盖。

## 5. Adaptive Verification Engine (AVE) 模型

AVE 是建议的底座无关逻辑层，并非 Agent Runtime；优先复用现有 Python 标准库脚本与证据矩阵，不新增数据库或网络服务。

- `Risk Classifier`：接受 base/head diff、任务 AC、被影响 API/契约、项目覆盖规则；产出风险级别及理由、未知项、禁止降级触发。
- `Impact Analyzer`：显式映射模块、测试、脚本、CI job、契约和发行包；静态依赖 + 保守兜底；动态导入、模板影响、共享配置应覆盖到集成回归。空选集需要证明，不得默认 PASS。
- `Evidence Manager`：证据包含 `ac_id,check_id,head_sha,source_sha,command,exit_code,artifact_ref,dependency_fingerprint,environment_fingerprint,tool_version,created_at,reuse_decision,reason`；复用仅表示“在当前 HEAD 上判定适用”，不是声称重新执行过。
- `Gate Planner`：输出针对每个 G 门/AC 的“required check、实际已执行、可复用证据、缺失条件和触发升级”，永不单独批准或合并。

结果语义：`PASS_EXECUTED`、`PASS_EQUIVALENCE_ATTESTED`、`FAIL`、`NOT_RUN`、`UNKNOWN`、`BLOCKED`；后者三项不计为合格。是否允许 `PASS_EQUIVALENCE_ATTESTED` 进入某条 G4 检查，必须由唯一权威契约和 Project Profile 明示，默认 **不允许**。对批准/CI-required/安全类强制检查永不使用旧 SHA 的直接通过结果。

## 6. 当前 HEAD 证据复用的验证原则

等价性必须证明测试命令/用例、全部直接及传递输入、被测代码、共享配置、生成资产、运行环境和依赖版本（包括工作区与调用脚本）均未影响结果。至少记录输入/输出哈希、源 SHA、当前目标 SHA、确定性条件、证据 URL/文件定位。若使用无法完全哈希的外部服务、实时数据或非确定性环境，则禁用复用。

旧证据不得改变 G4 当前 HEAD 的 CI 状态，也不得覆盖要求合并后在主分支执行的命令。缓存判定必须可复查、可以禁用、可以按项目独立配置；删除缓存不影响正确执行。不同 Project Profile、底座、机器/运行时不能凭名称相同互信；`.local/` 仅可存局部加速索引，不是状态或门禁权威。

## 7. 时点策略与独立审查

- 开发：快速相关单测，多次修复只重复受影响验证；未通过项必须重跑。
- PR：当前 HEAD 执行所需 CI；Master 逐 AC 检查；L2/L3 保留独立 Reviewer（能力/身份不可信则停止）；L0/L1 是否使用独立 Reviewer 由最终冻结的角色治理规范决定，不预先取消。
- 修复：旧批准自动失效，重新风险评估，Reviewer 对修复 Diff、原问题和新影响面复核；如果触及其他模块/高风险契约，扩展为完整复审。
- 合并：在正式变更 `docs/PROJECT.md`/核心契约之前，维持“快速+完整回归+backup+静态”的现行配置。批准后才可试点按风险分级的合并后清单；L3 一律不后置关键回归。
- 阶段/发布：固定触发完整回归、端到端、跨平台适用矩阵、源包及 DSH bundle 安装/升级/回滚/清单验证；不允许用之前任务的部分测试替代发布证据。
- 不更改状态机、三轮修复上限或任务锁释放逻辑。

## 8. 数据、度量与安全阈值

采集单任务 `baseline_sha/head_sha, risk_level, tests_selected/tests_total, tests_executed, suite_seconds, total_cycle_seconds, review_rounds, reviewer_time, token_usage_if_reported, CI_retries, regression_escape, severity, equivalence_rate, uncertainty, environment`。未知值保留 UNKNOWN，不以估算伪装测量。优先使用 CI 原生计时、任务记录和已许可的 ETR 观察；不泄漏路径/账号/Token。

比较相同风险、任务类型、测试规模、环境的两组样本（或同样本影子双轨），报告样本量、置信区间/方差、失败率与遗漏测试；样本不足只能声称“初步趋势”。

推广必须同时满足：G 门无违规，L3 测试门无降级，严重漏测/逃逸缺陷不增加，风险分类低估率处于 Owner 批准阈值内，回退演练通过；任一重大漏测、误用缓存、必需 CI 缺失，自动回退完整验证并登记缺陷，人工复核后恢复。

## 9. 分阶段实施及任务切分

### A / P0 — 基线测量与规则审计
- 审计 `AGENTS.md`, `docs/PLAN.md`, `docs/PROJECT.md`, `kit/orchestration.md`, `editions/`, `templates/`, `.github/workflows/`, `scripts/`，列出 *当前必须运行* 的项目检查及所有 G 门映射；核查正在推进的 #146、#150–#158、#174、#176 系列和 DSH #161–#167 的改动范围。
- 在不变更实际门禁的前提下，采集 ≥20 个（若无法达到则说明样本不足）代表性任务/CI 执行事件，产出按风险及任务类型分层的基线，明确瓶颈和错误来源；禁止虚构历史 telemetry。
- 出口：可审计基线、冲突表、适用范围、明确是否值得实施 B。

### B / P0 — 只读风险分类与影子 Gate Planner
- 冻结风险/输入/输出 schema、硬风险列表、unknown/fail-closed、与任务驱动拓扑的边界；与 #150/#151 契约迁移对齐。
- 实现 dry-run/plan-only：在真实任务变更上展示“按旧方案会跑什么/按新方案建议跑什么”，**不跳过任何现行命令**；使用合成正负场景验证路径推断、角色独立、跨底座一致、坏数据与过期数据。
- 出口：没有越权 PASS、分类一致性及误分类报告，Master/Owner 审定。

### C / P1 — 增量映射、证据有效性与审查复用
- 用显式依赖表+保守兜底进行测试选择；实现命令证据索引、旧 SHA 等价性判定和审查包 Diff 摘要。默认仅建议，不改变 G4 / VERIFIED 判定。
- 用突变/故意遗漏依赖/环境漂移/修改公共工具/跨模块隐含引用构建反例；证明误用缓存时安全失败，证明 reviewer 不依赖模型 family 自述。
- 出口：覆盖映射可追踪，重新运行与复用决策可审计，零错误跳过 mandatory checks。

### D / P1 — 受控 opt-in 门禁切换
- **前提**：A–C 出口达标；相关结构治理、Reviewer 身份问题不再阻塞；Owner **另行明确批准**具体变更；先冻结 `docs/PLAN.md` 对测试强度/HEAD 证据的目标解释、核心 G4/G5/VERIFIED 与 `docs/PROJECT.md` 检查配置，必要时更新底座模板/发行清单与版本兼容说明。
- 先对 L0/L1 执行 opt-in，再审慎扩大至 L2；L3 强制完整；发布级不减。选择出的 mandatory CI 必须在当前 HEAD 实际执行并保持全绿。
- 出口：跨底座正向、负向、升级/回退、CI branch protection 验证，真实试点质量达标；任意失败一键回退到完整检查。

### E / P1 — 集成发布、监控与持续校准
- 与 #176 任务驱动拓扑、#146 ETR、DSH 发行、self-improvement 聚合指标，保持清晰 owner 与重复数据去重。
- 做完整单测、71+ 场景（以实施时事实为准）、多平台 CI、Kit+DSH 发行物（若已实现）验证及历史版本升级/回滚演练。
- 出口：文档/模板/发行包/示例项目一致、性能与安全报告、已授权发布；将观察到的漏测转化为规则更新任务。

## 10. PR/任务纪律、回退与非目标

每阶段独立分支/PR；任务在 github edition 用 Issue 记录，逐条 AC 有真实证据；不得由本设计直接推进 READY 或 MERGED。文件责任与路径锁由当前有效策略决定。先由 Master 审核依赖是否 VERIFIED，避免与迁移 PR 编辑同一路径；尽量先实现只读独立模块，后单独的契约变更 PR。

回退开关默认 `strict/full`；任何异常恢复到现行所有配置检查，缓存清空后可完整重建，无状态/任务数据库迁移。不得通过降低测试覆盖/删除失败断言、改变 Reviewer 真实性、自动授权子代理、引入第三方运行依赖来提效。

**非目标**：自动部署第三方 CI 服务；代替现有双底座任务治理；改变 Master 的合并权；为成本牺牲关键场景；把“推荐计划”等价于“已执行验证”。

## 11. 冻结前待确认事项

1. 是否允许项目 Profile 按风险配置不同的 *required check*，及合并后原定完整回归何时转为发布/阶段检查？此为 Owner 级产品/门禁决策，未批准前维持现行规则。
2. 哪些测试属于“不得缓存”的发布、安全与实时环境类；默认均不得缓存强制验证结果。
3. 实际 CI branch protection 所需 context 与合并门如何对齐；无法可靠识别时一律按现行完整强制集合执行。
4. #150/#151/#176/Reviewer 身份修复及 DSH 各依赖的实际 VERIFIED 状态；执行前通过 GitHub 事实逐项复核。
