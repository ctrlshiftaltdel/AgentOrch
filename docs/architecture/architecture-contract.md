# Architecture Contract 契约（v1）

> 机器定义：[`architecture-contract.schema.json`](../contracts/schemas/architecture-contract.schema.json)。本文件定义模块职责、接口、允许 / 禁止依赖、严重级别、豁免与**未配置时的只读诊断语义**；schema 与本文件冲突时视为契约缺陷。
> 任务状态机、路径 / 资源锁、证据矩阵与合并门只在 [`docs/orchestration.md`](../orchestration.md) 定义，本文件只引用。
> 消费者：#48（检查器实现）、#53（合成回归）。

## 1. 目的与边界

Architecture Contract 让项目**显式**声明模块边界，使检查器能判断：

1. 模块 / 层是什么，各自负责什么。
2. 哪些接口是公共的，哪些实现是私有的。
3. 允许与禁止的依赖方向。
4. 跨模块不变量与状态所有权。
5. 违规的严重级别与豁免期限。

**非目标**：

- 不规定固定目录结构、语言或框架；不生成或猜测模块划分。
- 不把静态指标当作“高内聚低耦合”的客观证明。
- 不替代代码审查、测试或核心门禁；检查器只是证据来源之一。
- 不在未配置契约的项目上自动启用硬门禁。

## 2. 配置位置与可选性

| 项 | 规则 |
|---|---|
| 默认路径 | `docs/architecture/contract.json`（项目自有文件，不随发行包发布） |
| 覆盖键 | `docs/PROJECT.md` 的可选键 `architecture_contract_path`（由 #48 接入；本阶段不改 `docs/PROJECT.md`） |
| 未提供 | 视为 `OFF`：只读诊断，不猜测、不阻断（§5） |
| 已声明路径但文件缺失 / 不可读 | **配置错误，失败关闭**：明确报告路径，而不是静默退回 `OFF` |
| 文件存在但 schema 非法 | §6：`DIAGNOSTIC_ONLY` 下标配置错误；`ENFORCE` 下非零退出 |
| 作用域 | 单项目、单契约；多包仓库应在一个契约中声明多个模块，而不是多份互相矛盾的契约 |

契约文件按项目配置管理，变更走项目自己的审查流程；不得由更新引擎覆盖（见 [update-safety.md](../self-improvement/update-safety.md) §5）。

## 3. 数据模型

字段与枚举的唯一机器定义在 schema。语义要点：

### 3.1 模块

- `module_id`：稳定标识，小写开头、`[a-z0-9_.-]`、2–64 字符；全契约唯一（语义检查）。
- `paths`：项目内相对路径或目录前缀；禁止绝对路径、`..`、反斜杠。
- `responsibilities`：非空的人类可读职责；用于审查“模块是否做了不该做的事”，不参与自动判定。
- `owns_state`：该模块独占的状态；其它模块直接读写即违规（需跨模块规则支持）。
- `public_interfaces`：允许其它模块使用的接口；`kind` 取 `python_module` / `cli_command` / `http_api` / `event` / `file`，`stability` 取 `stable` / `additive_only` / `experimental`。
- `private_paths`：不允许跨模块访问的实现路径；用于 `PRIVATE_ACCESS` 规则。

### 3.2 依赖与边界

- `dependencies.allowed`：显式的允许边（`from` → `to` + 理由）。**未列出的边不等于允许**；由规则决定默认策略。
- `dependencies.forbidden`：显式禁止边，必须引用一条 `rule_id`；`from` / `to` 允许 `*` 通配（仅 forbidden）。
- `boundaries`：层 / 领域 / 应用 / 适配器 / 基础设施 / 安全 / 生成代码的边界声明；`members` 必须引用已声明模块（语义检查）。
- `invariants`：跨模块不变量；`checked_by` 取 `STATIC_RULE` / `CONTRACT_TEST` / `MANUAL_REVIEW` / `UNKNOWN`。`UNKNOWN` 表示当前没有自动检查，不得写成已覆盖。

### 3.3 规则与基线

- `rules`：至少一条；`rule_id` 全契约唯一。
- `rule.kind`：`NO_CYCLE` / `FORBIDDEN_EDGE` / `LAYER_DIRECTION` / `PRIVATE_ACCESS` / `DUPLICATE_BUSINESS_RULE` / `MODULE_FAN_OUT` / `MODULE_SIZE` / `INTERFACE_GROWTH` / `CONTRACT_TEST` / `CUSTOM`。
- `rule.severity`：`ERROR` 或 `WARNING`（§4）。
- `rule.params`：规则参数；**未知参数不得静默忽略**，实现必须在报告中标配置错误或 `UNKNOWN`。
- `baseline`：`frozen_at` / `frozen_by` 必填；破坏性变更需 `migration_notes`。

## 4. 严重级别与报告状态

### 4.1 严重级别（契约内，唯一枚举）

| 级别 | 含义 | 是否可阻断 |
|---|---|---|
| `ERROR` | 明确违反契约，且在 `ENFORCE` 下应使 CI 失败 | 是（仅 `ENFORCE`） |
| `WARNING` | 可疑但不构成明确违约；只报告 | 否 |

硬阻断**只能**来自 `ERROR` 级规则，并且必须同时满足：契约合法、`enforcement_mode=ENFORCE`、违规未被有效豁免。静态指标（扇入 / 扇出、模块大小、接口增长）默认 `WARNING`，不得仅凭阈值阻断。

### 4.2 报告状态（检查器输出，唯一枚举）

| 状态 | 含义 | 退出码影响 |
|---|---|---|
| `ERROR` | 命中至少一条未豁免的 `ERROR` 级违规 | `ENFORCE` 下非零 |
| `WARNING` | 命中 `WARNING` 级违规，或命中被有效豁免的 `ERROR`（附 `waiver_id`） | 不影响 |
| `UNKNOWN` | 能力不可用或无法可靠判定（如缺少外部工具链、无法解析的构造） | 不影响，但必须显示 |
| `NOT_RUN` | 规则被配置为启用但本次未执行（未安装依赖、被跳过） | 不影响，但不得折算为通过 |

规则：

1. 每个检查项都必须给出上述四种状态之一；缺省不等于通过。
2. `UNKNOWN` / `NOT_RUN` 不得在汇总里计为 `PASS`，报告需给出计数。
3. 报告必须绑定当前 head SHA 与契约 `contract_version`。
4. CI 绿灯只在 `ENFORCE` 且全部执行时有意义；`DIAGNOSTIC_ONLY` 永远不阻断。

## 5. 未配置时的语义（只读诊断）

项目没有契约（未声明路径且默认路径不存在）时：

- 模式等价 `OFF`：**不猜测**模块边界、不生成默认目录规则、不施加任何硬失败。
- 允许输出**只读诊断 / 契约草案**：目录与依赖盘点、候选模块、风险提示；必须标注 `DIAGNOSTIC_ONLY` 且注明“未配置契约，结论不作为门禁”。
- 退出码 0；不改变现有 CI 行为。
- 草案不得被自动写入项目；是否采纳由项目所有者决定（`HD-14`）。

## 6. 非法契约的失败语义

| 情况 | `DIAGNOSTIC_ONLY` / `WARN` | `ENFORCE` |
|---|---|---|
| JSON 不可解析 / schema 非法（缺必填、未知字段、非法枚举） | 报告配置错误，退出码 0，不产生规则结论 | 非零退出（配置错误），不产生“通过”结论 |
| `contract_version` 不受支持 | 同上 + 提示升级 / 迁移 | 非零退出 |
| 引用不存在的模块 / 规则 ID、重复 ID | 语义检查失败，同上 | 非零退出 |
| 规则启用但工具链缺失 | 该规则 `UNKNOWN`，其余继续 | 非零仅当该规则为 `ERROR` 且无法降级；否则 `UNKNOWN` 并明确列出 |

**禁止**：把“契约解析失败”报告成“没有违规”，或用默认规则替代损坏的契约。

## 7. 豁免（waiver）

- `waiver_id` 唯一；必须包含理由、所有者、到期日 `expires_at`、状态与具体范围。
- 状态：`ACTIVE`（有效）/ `EXPIRED`（已过期）/ `REVOKED`（撤销）。
- 到期后必须转为 `EXPIRED` 并**立即恢复阻断**；不得自动续期。
- `ACTIVE` 豁免的 `scope` 不得是整仓通配 `*`；必须是具体路径 / 模块 / 违规实例。
- 豁免不删除发现：报告中该违规降为 `WARNING` 并附 `waiver_id`，保持可见；豁免计数单独汇总。
- 豁免只对声明它的 `contract_version` 有效；契约主版本升级后需重新确认。

## 8. 能力边界（未实测标 UNKNOWN）

- Python：能否用标准库 AST 可靠识别所有目标依赖边（动态导入、字符串导入、条件导入）**未实测**；无法可靠识别的模式必须标 `UNKNOWN`，不得伪报 `PASS`。
- Rust：`cargo` / `rustc` 是否在 CI 与本地可用**未实测**；缺失时对应规则 `NOT_RUN` / `UNKNOWN`，不得声称已检查。
- 跨语言统一结论不成立：每种语言的检查能力分别记录；`UNKNOWN` 不得汇总成“整体通过”。
- 检查器不得引入第三方 Python 运行依赖。

## 9. 版本、变更与迁移

- `contract_version` 为语义化版本；新增可选字段 / 新规则为次版本，破坏性语义变化为主版本。
- 破坏性变更必须提供 `baseline.migration_notes` 并走 `HD-13`。
- 旧版 schema 的读取策略：只接受已声明支持的版本；不支持时失败关闭（§6），不得猜读。
- 规则删除 / 模块重命名导致旧引用失效时，标配置错误或 `UNKNOWN`，不得静默忽略。
- 契约变更必须与实现 PR 分离审查；实现任务不得为“让检查通过”而放宽契约。

## 10. 与核心契约的关系

- 架构违规属于**证据**，不改变任务状态机；是否阻断 PR 由项目配置的必需检查决定，仍是 G4 的一部分。
- 检查报告可作为 G1 证据条目，但 CI 绿灯不替代逐条验收（G1）。
- 检查器自身的实现、测试与接入必须走正常任务流程（#48），受 G1–G6 约束。

## 11. 人工决策点

- `HD-13`：schema / 契约的破坏性版本升级。
- `HD-14`：首次为项目启用 `ENFORCE`（硬门禁）及规则 / 豁免范围。
- `HD-15`：引入外部工具链（Rust、第三方验证器）或依赖。

## 12. 非目标汇总

不定义具体检查算法、不内置语言适配器、不生成模块图、不提供“架构得分”、不自动修改代码或契约。
