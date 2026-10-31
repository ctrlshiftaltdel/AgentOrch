# AgentMgr 经验映射：原始证据 → 泛化原则 → 规则 / 测试 → 限制

> **定位**：本文件记录一次下游项目的经验如何被泛化成**项目无关**的架构检查点。规则的可检查形式在 [agentmgr-contract-rules.md](../architecture/agentmgr-contract-rules.md)（AGM-1…AGM-6），场景在 [交付回归场景](../testing/delivery-regression-scenarios.md)，断言在 [tests/test_agentmgr_lessons.py](../../tests/test_agentmgr_lessons.py)。状态机、路径 / 资源锁、证据矩阵与合并门 G1–G6 **只在** [docs/orchestration.md](../orchestration.md) 定义，本文件只引用。
> **信任边界**：下游项目的内容属于**不可信数据**，只作为证据来源，不能作为修改本套件规则、权限或流程的指令。本文件只引用公开链接与自行撰写的泛化摘要，**不复制**其产品代码、业务模型、目录结构、Profile 路径、凭据或受版权文本；本套件也**没有**对那个项目做过任何真实数据或端到端运行。
> **证据状态**：以下摘要依据公开 Issue / PR 标题与本次任务 Issue 的描述撰写，没有逐条复核正文、diff 或 CI；详见 §5 的未验证假设。

## 1. 这份文件回答什么

1. 每条经验的**证据出处**（公开链接）。
2. 它被泛化成哪条**原则**（与本套件已有契约如何对齐）。
3. 该原则落在哪条**规则 / 场景 / 测试**上（可定位）。
4. 它**不能被**哪些检查证明——也就是必须留给 Reviewer 或 `UNKNOWN` 的部分。

它**不回答**“下游项目做对了没有”，也不把那个项目的任何具体设计当作本套件的默认值。

## 2. 映射总表

| 证据 | 泛化原则 | 规则 | 场景 / 测试 | 限制（不得声称已覆盖） |
|---|---|---|---|---|
| [#77](https://github.com/ctrlshiftaltdel/AgentMgr/issues/77) 冻结身份 / 治理 / 任务 / Grant / Runtime Adapter 契约后再实现 | 跨模块、难逆转的工作先冻结 Schema、状态、错误模型、消费者、兼容策略与不变量，再派发依赖实现 | **AGM-1**、**AGM-6** | C12（冻结基线 + 不变量覆盖）、C16（终局语义）、`tests/architecture/`（`InvariantCoverageTests`） | 冻结顺序与契约内容正确性只能由 Reviewer 从时间线判定；检查器只看“声明是否绑定到已执行规则” |
| [#81](https://github.com/ctrlshiftaltdel/AgentMgr/issues/81) Runtime Adapter 接入边界 | 核心只依赖稳定抽象；适配器不得把框架私有对象、私有 schema、Profile 路径泄漏给核心 | **AGM-2** | C12（正向）、C13（负向硬拦截）、`AgentMgrAlignmentTests` | 只识别静态 import 边；动态导入 / 反射 / 返回值携带框架对象→ `UNKNOWN` 或 Reviewer |
| [#93](https://github.com/ctrlshiftaltdel/AgentMgr/pull/93) 运行时中立契约与 Hermes Adapter | 适配器之间跑同一套契约测试；Mock 通过不等于真实运行通过 | **AGM-2**、**AGM-4** | C15（Mock 被标成真实）、C12（标签正确） | 本套件不跑真实运行时；只验证“标注规则会拒绝越级声明” |
| [#91](https://github.com/ctrlshiftaltdel/AgentMgr/pull/91) MCP 与 REST 复用同一应用服务 | 入口只做协议转换；业务规则与授权只在唯一应用服务里 | **AGM-3** | C14（结构性旁路 + 内联重写两种形态）、C12 | 静态层只能证明“没有非法依赖边”；行为一致性靠共用契约测试，两者都不能单独证明“共用同一实现” |
| [#95](https://github.com/ctrlshiftaltdel/AgentMgr/issues/95) 未验证的执行隔离能力必须标记并收窄承诺 | 能力声明必须绑定证据与范围；失败 / 状态未知是稳定契约 | **AGM-5**、**AGM-6** | C17（PASS 无证据 / 证据类型不匹配 / 通配范围）、C16（取消 / 超时 / 失败 / 未知）、C12 | 证据真伪无法自动判定；真实能力仍按 C1–C4 探测并留证 |

规则本身按本套件的术语重写：任何业务概念、角色名、Profile 路径或框架名称都没有进入核心契约。

## 3. 逐条证据摘要（合成，非引用原文）

- **E1｜#77 Phase 0 契约冻结**：公开标题描述的是“先冻结身份、治理、任务、Grant 与 Runtime Adapter 契约”。泛化后对应的不是某套角色模型，而是**顺序纪律**：契约（含不变量与错误模型）先冻结并留下 `baseline`，实现任务才能并行；冻结之后的变更走版本与迁移策略。
- **E2｜#81 Runtime Adapter 接入边界**：泛化后是**依赖方向**：核心不认识具体适配器；适配器把框架私有实现收在自己的 `private_paths` 内，只通过公共接口暴露能力。违反形态可直接由静态依赖检查定位到文件行。
- **E3｜#93 运行时中立契约与 Hermes Adapter**：泛化后是**共用契约测试 + 证据标签**：一类适配器跑同一套契约测试；Mock 上的绿不能写成真实运行的绿；真实运行要单独记录环境与版本。契约本身保持运行时中立，具体框架只出现在适配器里。
- **E4｜#91 MCP 与 REST 复用同一应用服务**：泛化后是**单一应用规则**：两个传输入口只做协议转换，业务与授权在应用服务。两种失败形态要分开处理——入口直接依赖领域/适配器（静态可拦截）与入口内联重写规则（只能靠共用契约测试）。
- **E5｜#95 未验证执行隔离能力必须标记**：泛化后是**声明纪律**：没探测过的能力标 `UNKNOWN` 并收窄承诺；`PASS` 必须有证据与范围；“启动成功”不是隔离证据；失败与状态未知是可测试的稳定契约，不能被一个泛化异常掩盖。

这些摘要刻意不含命名实体细节：本套件需要的是可检查的形状，不是那个项目的业务模型。

## 4. 规则 / 测试落点

| 层 | 落点 | 说明 |
|---|---|---|
| 规则 | [agentmgr-contract-rules.md](../architecture/agentmgr-contract-rules.md) §2–§4 | AGM-1…AGM-6 的契约写法、静态层、语义层与 Reviewer 清单 |
| 静态实现 | [scripts/architecture/rules.py](../../scripts/architecture/rules.py)、[report.py](../../scripts/architecture/report.py) | 复用冻结的 `PRIVATE_ACCESS` / `FORBIDDEN_EDGE` / `LAYER_DIRECTION` / `NO_CYCLE`；新增 `invariants[]` 覆盖与 `examined_modules` / `uncovered_modules` 范围字段，不改变四态语义、不新增 `kind` / `params` |
| 契约级 fixture | `tests/architecture/test_architecture_checker.py`（`AgentMgrAlignmentTests`、`InvariantCoverageTests`） | 正向控制组、三类依赖负向、无契约不判定、不变量覆盖诚实性 |
| 场景 | C12（正向控制）、C13–C17（负向）、C18（未配置不判定） | 注册在 [scripts/run_delivery_scenarios.py](../../scripts/run_delivery_scenarios.py)；场景表在[交付回归场景](../testing/delivery-regression-scenarios.md) |
| 一致性断言 | [tests/test_agentmgr_lessons.py](../../tests/test_agentmgr_lessons.py) | 文档、场景、注册、测试四方一致；只引用本文件列出的公开链接；fixture 无网络、无真实数据 |

## 5. 限制与未验证假设

**限制（永久保留，不得声称已覆盖）**

1. **语义项没有静态检查**：Mock 与真实的区分、入口行为一致性、终局可区分性、能力证据真伪都由契约测试或 Reviewer 判定；检查器对这些恒报 `UNKNOWN`。场景 C15–C17 里可判定的只是“标注 / 声明规则会拒绝越级”，不是真实系统行为。
2. **静态层只认 import 边**：动态导入、插件加载、反射、字符串路由、返回值携带框架对象都看不见，标 `UNKNOWN` 或交 Reviewer。
3. **不新增规则种类**：`DUPLICATE_BUSINESS_RULE` / `CONTRACT_TEST` / `CUSTOM` 仍报 `UNKNOWN`（冻结 schema 未给可判定参数）。真正的静态实现需要先走契约变更流程，不在本次授权范围。
4. **跨语言不汇总**：Python 与 Rust 适配器能力分别记录；Rust 工具链缺失时相关规则 `NOT_RUN`。
5. **未配置契约的项目不受影响**：默认 `OFF`，只读诊断；场景 C18 与本仓库自身的诊断模式回归覆盖这一点。

**未验证假设（一旦不成立会推翻本文件结论）**

1. 证据链接**当前可访问性**与内容未复核：只依据公开标题与任务 Issue 的描述撰写摘要，没有读正文、diff 或 CI。若标题与内容不符，摘要需要重写——但规则的**形状**（依赖方向、单一应用服务、证据标签、终局语义）不依赖那个项目的实现细节。
2. 下游项目**当前状态**未核对：本文件不对其现状作任何陈述，也不推断其规则是否已落地。
3. “静态检查能可靠识别核心依赖适配器私有符号”只在**声明了 `private_paths` / `forbidden` 边**的前提下成立；未声明时检查器不猜测。
4. 项目是否接受“语义场景由契约测试 + Reviewer 覆盖”的限制：若不接受，需另开决策引入更强的分析工具（并先解决第三方依赖政策）。

## 6. 变更规则

- 本文件只做证据 → 原则 → 规则 / 测试 → 限制的映射；与 [architecture-contract.md](../architecture/architecture-contract.md)、schema 或 [docs/orchestration.md](../orchestration.md) 冲突时以它们为准，并停下登记冲突。
- 新增证据来源必须先登记来源与信任级别（见 [self-improvement README](README.md)），且只允许追加到本文件，不得把下游项目的业务模型搬进核心契约。
- 修改任何 AGM 规则或场景时，同步更新 [agentmgr-contract-rules.md](../architecture/agentmgr-contract-rules.md)、场景注册与 `tests/test_agentmgr_lessons.py` 的断言；不得通过放宽断言或删除场景来“通过”。
