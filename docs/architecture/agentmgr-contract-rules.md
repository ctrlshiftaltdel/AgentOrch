# AgentMgr 经验 → Architecture Contract 规则（AGM-1…AGM-6）

> **定位**：本文件把六条经验落成 Architecture Contract 里**可检查的规则形式**与 **Reviewer 清单**。字段名、枚举与判定规则仍只由 [architecture-contract.md](architecture-contract.md) 与 [schema](../contracts/schemas/architecture-contract.schema.json) 定义（#47 冻结，本任务未修改 schema、未新增 `kind`、未新增 `params`）；任务状态机、路径 / 资源锁、证据矩阵与合并门 G1–G6 **只在** [docs/orchestration.md](../orchestration.md) 定义，本文件只引用，不复制、不改写。
> **证据来源**：下游项目的公开 Issue / PR，逐条映射见 [agentmgr-lessons.md](../self-improvement/agentmgr-lessons.md)。本文件只引用公开链接与自行撰写的泛化摘要，不复制其代码、业务模型、目录结构、Profile 路径或凭据；所有 fixture 都是合成数据。
> **不强制统一目录 / 框架**：本文件不规定目录名、语言、框架、模块数量或分层叫法。`module_id` 与 `paths` 由项目自己声明；检查器不猜测模块边界，没有契约的项目保持只读诊断（AGM-6 与 §6 的限制同样适用）。

## 1. 怎么用

1. **契约是项目自有的可选文件**。未配置时检查器等价 `OFF`：只输出只读盘点，不产生硬失败，也不从代码里推断模块划分。六条规则不是默认规则集。
2. **按项目自己的边界选择 2–4 条**写成契约规则（`rules[]` + `dependencies.forbidden[]` + `modules[].private_paths`）。`severity` 与 `enforcement_mode` 决定是否阻断：只有 `ERROR` 级、`ENFORCE` 且未被有效豁免的发现才会让 CI 失败。
3. **每条规则有三层证据**：静态层（检查器能证明什么）、语义层（项目自己的共用契约测试）、Reviewer 清单。**标 `Reviewer 判定` 的条目没有自动检查**，不得写成“已被静态分析覆盖”。
4. 任务包 / Issue 的 `invariants`、`negative_cases`、`integration_paths` 按 [4.1 节](../orchestration.md)填写；本文件只说明这些表面在架构维度应覆盖什么，不新增字段。

## 2. 六条规则总表

| 规则 | 泛化原则 | 冻结契约里的声明形式 | 静态层（检查器） | 语义层 | 场景 |
|---|---|---|---|---|---|
| **AGM-1** | 先冻结契约，再并行实现 | `baseline.frozen_at` / `frozen_by`；`invariants[]` 带 `checked_by` 与 `rule_ids`；`contract_version` | 只检查不变量**覆盖**：声明本身不算覆盖 | 冻结顺序、错误模型与兼容策略 | C12、C18 |
| **AGM-2** | 核心领域不依赖具体适配器 | 适配器模块的 `private_paths`；核心 → 适配器的 `dependencies.forbidden` | `PRIVATE_ACCESS`（跨模块导入私有路径）、`FORBIDDEN_EDGE`（整条边禁止）**硬拦截** | 适配器是否真的把框架对象藏在私有路径 | C13、C12 |
| **AGM-3** | 多个入口共享唯一应用规则 | `boundaries`（domain / application / adapter / layer）；`forbidden`：入口 → 领域、入口 → 适配器 | `FORBIDDEN_EDGE` / `LAYER_DIRECTION` 拦结构性旁路 | 入口行为一致性（共用契约测试） | C14、C12 |
| **AGM-4** | 共用契约测试，区分 Mock 与真实集成 | 不变量 `checked_by: CONTRACT_TEST`（检查器不执行） | 无：检查器不跑项目测试 | 证据标签不得越级；Mock ≠ 真实 | C15、C12 |
| **AGM-5** | 能力声明绑定证据与范围 | `invariants[]`；报告 `examined_modules` / `uncovered_modules` | 只检查覆盖与范围，不判断证据真伪 | 探测记录完整性、范围、是否越级声明 | C17、C12 |
| **AGM-6** | 失败 / 状态未知是稳定契约 | 不变量 + 任务包 `negative_cases` 写明每种终局 | 无：检查器不判定运行时终局 | 终局可区分、未知不被改写 | C16、C12 |

场景定义与预期拦截门只在 [交付回归场景](../testing/delivery-regression-scenarios.md)；本文件不重复场景表的字段。

## 3. 规则明细

### AGM-1 先冻结契约，再并行实现

- **要求**：跨模块、难以逆转的工作先明确 Schema、状态、错误模型、消费者、兼容策略与不变量，再派发依赖它的实现任务。契约先有 `baseline`（谁、何时冻结）与 `invariants`（哪些性质必须成立、由什么检查），实现任务才能并行。
- **契约写法**：`baseline.frozen_at` / `frozen_by` 必填；每条不变量声明 `checked_by`（`STATIC_RULE` / `CONTRACT_TEST` / `MANUAL_REVIEW` / `UNKNOWN`），`STATIC_RULE` 必须列出 `rule_ids`。`dependencies.allowed[].reason` 写明允许这条边的理由，而不是只写“允许”。
- **静态层**：报告新增 `invariants[]`（见 §5）。`STATIC_RULE` 不变量只有在其引用的规则**实际执行且通过**时才算覆盖；规则被禁用、未执行、报 `UNKNOWN`，或存在无法归属到规则的 `UNKNOWN`（动态导入、文件不可解析）时，不变量一律降为 `UNKNOWN`。`CONTRACT_TEST` / `MANUAL_REVIEW` / `UNKNOWN` 恒报 `UNKNOWN`：检查器既跑不了项目测试，也做不了人工审查。
- **`Reviewer 判定`**：“先冻结后派发”是过程事实。Reviewer 看 Issue / PR 时间线、契约 commit 与实现任务派发时间（或 `baseline.frozen_at` 与首个实现 commit 的先后），并核对错误模型、兼容策略与回滚路径是否写进契约而不是事后补写。
- **负向案例**：契约只有 `frozen_at` 时间戳、没有不变量；把不变量写成 `STATIC_RULE` 但 `rule_ids` 为空；用 `CONTRACT_TEST` 声明却拿不出测试结果；实现先合入、契约后补。
- **限制**：静态检查只能证明“声明与已执行规则的关系”，不能证明冻结发生在实现之前，也不能证明契约内容正确。

### AGM-2 核心领域不依赖具体适配器

- **要求**：核心服务只依赖稳定的 Runtime / Storage / Transport 抽象；适配器负责把框架私有对象、私有数据库 schema、用户 Profile 路径挡在边界内。核心需要的能力通过适配器的公共接口暴露。
- **契约写法**：给适配器模块声明 `private_paths`（框架运行时内部目录、内部 session、私有 schema、Profile 路径所在目录），核心模块只允许引用其 `public_interfaces`。两种声明都合法：`PRIVATE_ACCESS`（只禁私有路径）或 `dependencies.forbidden`（核心 → 适配器整条边禁止，适合“核心完全不感知具体适配器”的设计）。
- **静态层**：`PRIVATE_ACCESS` 对“跨模块导入落在对方 `private_paths` 内”的每一条 import 产出 `ERROR`，带 `module_id`、`target_module`、`file`、`line`；`FORBIDDEN_EDGE` 对允许 `*` 通配的边同样定位到文件行。`ERROR` + `ENFORCE` 才阻断。
- **`Reviewer 判定`**：适配器是否把**全部**框架私有对象放在 `private_paths` 之下（而不是换个名字放回公共模块）；`public_interfaces` 的 `stability` 是否与实际使用相符；是否通过返回值把框架对象“带出”边界（例如把 SDK 客户端实例原样返回）。
- **负向案例**：核心 `from adapters.x.runtime import session`；核心持有框架的 response / request 对象；Profile 绝对路径从适配器泄漏到领域层；把私有模块改名成 `helpers` 后继续跨模块引用。
- **限制**：Python 适配器只识别静态 `import` / `from … import`；运行时注入、插件加载、字符串路由、`__import__` 一律 `UNKNOWN`，不会被当成通过。Rust 适配器在工具链缺失时规则 `NOT_RUN`、能力 `UNKNOWN`。“对象是否泄漏”属于语义判断，不在静态层。

### AGM-3 多个入口共享唯一应用规则

- **要求**：REST / MCP / CLI / 事件等入口只做协议转换与参数校验；业务规则与授权逻辑在唯一应用服务里，所有入口调用同一处。
- **契约写法**：用 `boundaries` 标出 domain / application / adapter / layer 的成员（成员仍是项目自己的 `module_id`）；`dependencies.forbidden[]` 声明“每个入口 → domain”“每个入口 → 具体适配器”，并引用同一条 `ERROR` 规则；或者用 `LAYER_DIRECTION` 给出显式的层序。
- **静态层**：`FORBIDDEN_EDGE` / `LAYER_DIRECTION` 会拦住**结构性旁路**——入口直接 import 领域模块或具体适配器。这是本项目唯一能可靠识别的形态，按 `ERROR` 硬拦截。
- **语义层（共用契约测试）**：入口把规则**内联重写**时没有任何 import 边，静态层天然看不见（场景 C14 的 `inline` 变体就把这件事显式演示出来）。此时必须由项目自己的共用契约测试覆盖：同一组请求喂给两个入口的实现，比较**决策结果**是否一致。这类不变量必须写成 `checked_by: CONTRACT_TEST`，检查器保持 `UNKNOWN`，不得因为静态层 `PASS` 就当作已覆盖。
- **`Reviewer 判定`**：两个入口是否真的调用同一个应用服务（而不是各自拷贝一份行为相同的实现）；授权判定是否也在应用服务内；`integration_paths` 是否列出全部入口。
- **负向案例**：MCP 入口内联一套金额/权限规则；两个入口分别实现同一份授权检查；入口直接调用具体适配器；契约把这类一致性写成 `STATIC_RULE`（声明越级）。
- **限制**：静态层只能证明“没有非法依赖边”，不能证明“行为一致”；反过来，行为一致也不能证明共用同一实现（复制粘贴同样一致）。两者都必须叠加 Reviewer 判断。

### AGM-4 共用契约测试，区分 Mock 与真实集成

- **要求**：同一类适配器跑同一套契约测试；Mock 通过只能说明“在 Mock 上通过”，不能被描述成“真实运行时已验证”；真实集成结果单独标记，并记录环境与版本。
- **契约写法**：契约里只声明“必须存在共用契约测试”的不变量（`checked_by: CONTRACT_TEST`）与对应的 `negative_cases`。检查器**不执行**这套测试，因此该不变量在报告里保持 `UNKNOWN`；测试结果作为独立证据条目附在证据矩阵里，标明证据类型。
- **可判定的语义规则**（场景 C15 的合成实现）：声明的验证级别不得高于证据级别——`REAL_RUNTIME` 声明只有在证据本身是 `REAL_RUNTIME` 时成立；契约套件未通过时，任何声明都不成立；Mock 运行对“真实行为”的验证状态恒为 `UNKNOWN`。
- **`Reviewer 判定`**：每个适配器是否有共用套件（而不是各写一套）；Mock 行是否被写成真实集成；真实集成的环境、版本、时间与 CI 链接是否记录；契约套件是否覆盖创建 / 取消 / 状态等关键路径。
- **负向案例**：Mock 通过后写“真实运行时已验证”；用 `STARTUP_OK` 或设计文档当作集成证据；契约套件失败但仍声明适配器可用；只跑一个“快乐路径”用例就宣布契约通过。
- **限制**：本套件**不跑**真实运行时，只验证“标注规则会拒绝越级声明”。真实集成必须由项目自己提供可定位的运行记录；本文件不得被引用为“某适配器已真实验证”的依据。

### AGM-5 能力声明绑定证据与范围

- **要求**：执行隔离、取消、凭据注入、资源边界这类能力，未探测就不得标 `PASS`；“服务启动成功”不是隔离证据，设计文档不是取消证据；剩余风险必须保留在记录里。
- **契约写法**：把能力性质写成 `invariants[]`（`severity` 明确），能力记录本身使用 [runtime-capability 模板](../../templates/runtime-capability.md)（本文件不重述其中字段）。检查器侧：报告给出 `invariants[]` 覆盖，以及 `examined_modules` / `uncovered_modules`——`PASS` 只有在 `examined_modules` 非空且 `uncovered_modules` 为空时才有意义。
- **可判定的语义规则**（场景 C17 的合成实现）：`PASS` 必须带证据（`probe_id`、`probed_at`、`environment`），证据类型必须匹配被声明的能力（`STARTUP_OK` / `DESIGN_DOC` 不是隔离证据），`scope` 不能是整仓通配 `*`，探针覆盖的能力必须等于声明能力；非 `PASS` 的声明**不得被升级**为通过。
- **`Reviewer 判定`**：证据是否真实（探针可以伪造，静态检查无能为力）；能力记录的探测时间 / 环境 / 证据引用是否齐全；`PASS` 是否被表述成“已获得权限”（能力记录不授予权限）。
- **负向案例**：`PASS` 无任何探测；用“启动成功”证明隔离；`scope: *`；探针测的是 A 能力却声明 B；把过期或换版本前的证据继续当作当前结论。
- **限制**：只能判定“声明是否越级、结构是否完整”，不能判定“证据是否真实”；真实能力探测仍按 [docs/orchestration.md](../orchestration.md) 第 5 节的 C1–C4 流程执行并留证。

### AGM-6 失败 / 状态未知是稳定契约

- **要求**：超时、取消、提交结果不明、重试与审计关联都要有可测试语义。不能用同一个泛化异常吞掉“取消 / 超时 / 未知”，也不能把未知改写成失败或成功。
- **契约写法**：把终局性质写进 `invariants`（`checked_by: CONTRACT_TEST`），并在任务包 `negative_cases` 逐条列出每种终局与期望记录。检查器不判定运行时终局（报 `UNKNOWN`），不得写成已覆盖。
- **可判定的语义规则**（场景 C16 的合成实现）：每个非成功终局有各自稳定错误码；`UNKNOWN` 保持为 `UNKNOWN`，不得折叠进失败或成功；任何非成功终局的 `success` 必须为 `False`；重试策略只对可重试码生效。
- **`Reviewer 判定`**：取消是否可观测、超时是否可重试、提交结果不明是否保留可核对记录（审计 / 幂等键）、未知状态是否被运维当成成功处理。
- **负向案例**：`except Exception: raise DispatchError()` 把四种终局变一种；超时与取消共用一个码；未知被记成失败并触发不必要的回滚；重试没有终止条件。
- **限制**：本套件只有合成模型；真实运行时的终局区分必须由 C1–C4 探测记录支撑（见 [runtime-capability 模板](../../templates/runtime-capability.md)）。崩溃瞬间的持久化状态无法离线验证，标 `UNKNOWN` 并交 Reviewer。

## 4. Reviewer 清单（AGM 审查项）

审查包 / PR 上逐条给结论；无法判定时写 `UNKNOWN` 并说明缺什么证据，**不得留空或默认通过**。

| ID | 检查项 | 通过条件 | 不通过 / `UNKNOWN` 条件 |
|---|---|---|---|
| AGM-1.a | 冻结先于实现 | 契约 commit（或 `baseline.frozen_at`）不晚于首个实现 commit；错误模型与兼容策略在契约里 | 只有时间戳、没有内容；实现先合入；无法从时间线判定 → `UNKNOWN` |
| AGM-1.b | 不变量有检查归属 | 每条不变量有 `checked_by`；`STATIC_RULE` 有 `rule_ids`；`CONTRACT_TEST` 有可定位的测试结果 | 声明了检查却没有对应证据；报告 `invariants[]` 为 `UNKNOWN` |
| AGM-2.a | 适配器私有面已声明 | 适配器的框架私有目录 / 私有 schema / Profile 路径都在 `private_paths`（或核心 → 适配器整条边被禁止） | 私有对象放在公共模块；`PRIVATE_ACCESS` / `FORBIDDEN_EDGE` 报 `ERROR` |
| AGM-2.b | 没有框架对象穿越边界 | 公共接口只返回领域类型，不返回 SDK / response / session 对象 | 返回值把框架对象带出边界 |
| AGM-3.a | 入口只做协议转换 | 每个入口 → 应用服务的依赖存在，入口未直接依赖领域 / 适配器 | 静态报 `ERROR`；入口内联业务规则 |
| AGM-3.b | 入口行为一致 | 共用契约测试在全部入口上通过（附结果） | 无测试；只测一个入口；两个入口结果不同 |
| AGM-4.a | 共用契约测试存在 | 每个适配器都有同一套契约测试，覆盖关键路径 | 各写一套；只有快乐路径 |
| AGM-4.b | 证据标签不越级 | Mock 行标 Mock；真实行附环境 / 版本 / CI 链接 | Mock 被写成真实；`claim exceeds evidence` |
| AGM-5.a | 能力声明绑定证据与范围 | `PASS` 有探测记录（时间 / 环境 / 引用）与非通配范围 | 无证据、`STARTUP_OK`、`scope: *`、探针与能力不匹配 |
| AGM-5.b | 剩余风险保留 | 未验证范围写在记录里，没有被表述成“已获得权限” | 把 `PASS` 说成权限；删除未验证说明 |
| AGM-6.a | 终局可区分 | 四种非成功终局各有稳定错误码 / 状态；未知保持未知 | 一个泛化异常；未知被记成失败或成功 |
| AGM-6.b | 失败有可核对记录 | 审计 / 幂等键 / 备份位置可定位；重试有终止条件 | 只报异常不报副作用；重试无上限 |

## 5. 报告字段（本任务追加的增量数据）

检查器（[check.py](../../scripts/architecture/check.py)）在原有报告上追加三个字段，**不改变** `ERROR` / `WARNING` / `UNKNOWN` / `NOT_RUN` 的语义、不新增状态取值、不参与 `blocking` 计算：

| 字段 | 含义 | 读法 |
|---|---|---|
| `invariants[]` | 每条不变量的覆盖：`invariant_id`、`checked_by`、`rule_ids`、`status`、`message` | `status` 取报告已有的五态；`STATIC_RULE` 取其引用规则的最差状态；存在无法归属到规则的 `UNKNOWN` 时降为 `UNKNOWN`；`CONTRACT_TEST` / `MANUAL_REVIEW` / `UNKNOWN` 恒 `UNKNOWN` |
| `examined_modules` | 本次真正被检查覆盖的模块 | 为空表示“没有任何模块被证明”，此时规则的 `PASS` 不成立 |
| `uncovered_modules` | 有源码但无可用适配器覆盖的模块 | 非空时相关规则为 `NOT_RUN`，不得读作通过 |

`invariants[]` 是**声明 → 证据**的桥：它把“我声明了这条性质”与“哪条规则真的跑过并给出了什么状态”分开记录，因此声明本身永远不会变成 `PASS`。它也不是 `findings`，不改变计数与退出码。

## 6. 已知限制（不得声称已覆盖）

1. **静态层只认 import 边**。入口内联重写、运行时注入、插件 / 反射 / 字符串路由都看不见；对应 `UNKNOWN` 或 `Reviewer 判定`。
2. **`DUPLICATE_BUSINESS_RULE` / `CONTRACT_TEST` / `CUSTOM` 仍是 `UNKNOWN`**。冻结 schema 没有给它们可判定参数，强行实现等于引入未冻结的猜测规则；本任务只把它们当声明位使用（见 [templates/architecture/README.md](../../templates/architecture/README.md)）。若要真正的静态实现，需先按#47 的流程改契约（新 Issue + 决策记录），不在本任务的授权范围内。
3. **Mock / 真实边界**：本套件不跑任何真实运行时；C15 只证明“标注规则会拒绝越级声明”。
4. **能力证据真伪**：探针可以伪造，静态检查只能判定结构完整性与声明是否越级。
5. **跨语言**：Python 静态扫描与 Rust 词法扫描分别记录能力，不汇总成“整体通过”；Rust 工具链缺失时相关规则 `NOT_RUN`。
6. **未配置契约的项目**：默认 `OFF`，不猜测模块、不施加硬失败（场景 C18 与仓库自身的诊断模式回归覆盖这一点）。

## 7. 变更规则

- 本文件只增加架构维度的检查形式和 Reviewer 问题；与 [architecture-contract.md](architecture-contract.md) 或 schema 冲突时以冻结契约为准，并停下登记冲突，不得各写一套。
- 新增 / 修改规则或场景必须同步更新 [交付回归场景](../testing/delivery-regression-scenarios.md)、`scripts/run_delivery_scenarios.py` 的注册与 [agentmgr-lessons.md](../self-improvement/agentmgr-lessons.md) 的映射；`tests/test_agentmgr_lessons.py` 会检查文档、场景与测试三方一致。
- 不得为了“让检查通过”而放宽契约、删除断言或把语义项改写成静态项。
