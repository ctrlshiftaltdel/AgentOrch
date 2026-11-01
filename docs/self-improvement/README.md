# 自我完善与安全更新契约（S0 设计冻结，v1）

> 状态：**S0 设计契约已冻结**（载体 Issue #47）。本目录只冻结设计，**不包含任何实现**：没有检查器、采集脚本、更新引擎或运行时服务。未实现的能力不得对外宣称已支持。
> 权威范围：本目录定义“经验来源 / Lesson 生命周期 / 自我完善入口 / 更新安全 / 威胁模型”的**语义**；字段、必填、枚举的**机器权威**在 [`docs/contracts/schemas/`](../contracts/schemas/)；索引与验证方案在 [`docs/contracts/README.md`](../contracts/README.md)。
> 与核心契约的关系：任务状态机、路径 / 资源锁、证据矩阵、合并门 G1–G6、edition 语义**只在** [`docs/orchestration.md`](../orchestration.md) 定义，本目录只引用，不复制、不改写。

## 1. 文档地图

| 文件 | 冻结内容 |
|---|---|
| [sources-and-trust.md](sources-and-trust.md) | 来源注册表语义、信任模型、增量扫描游标、启用 / 停用 / 移除 |
| [lesson-lifecycle.md](lesson-lifecycle.md) | 四类内容、证据等级 E0–E4、生命周期状态与转换、去重指纹、冲突决策 |
| [entrypoints-and-decisions.md](entrypoints-and-decisions.md) | 触发语与非触发、自我完善流程、授权模型、停止条件、人工决策目录 `HD-*` |
| [update-safety.md](update-safety.md) | 官方 Release 信任、包校验、逐文件分类、项目自有文件保护、未提交改动、备份 / 回滚、失败关闭 |
| [threat-model.md](threat-model.md) | 18 条威胁记录（威胁 / 影响 / 缓解 / 负向测试 / 剩余风险）与未接受的剩余风险 |
| [../architecture/architecture-contract.md](../architecture/architecture-contract.md) | Architecture Contract 的模块、接口、依赖、严重级别、豁免与未配置语义 |
| [../contracts/README.md](../contracts/README.md) | schema / fixture 索引、零依赖验证方案、CI 可选验证器边界、下游依赖、开放高风险事项 |
| [architecture-checker.md](architecture-checker.md) | **实现记录（#48）**：检查器入口、规则与参数、适配器能力边界、接入点；非冻结设计 |
| [self-improvement-loop.md](self-improvement-loop.md) | **实现记录（#50）**：触发边界、恢复 → 采集 → 规划 → 派发 → 审查 → 闭环报告、人工决策门、双 edition 载体与限制；非冻结设计 |

## 2. 单一权威规则

同一事实只允许一处定义。出现分歧时按以下顺序判定，**不得各写一套**：

| 事实类型 | 唯一权威 | 其它文档的义务 |
|---|---|---|
| 字段名、必填、类型、枚举取值、`const` | [`docs/contracts/schemas/*.schema.json`](../contracts/schemas/) | 只用文字解释语义，不得新增取值 |
| 状态转换、失败语义、人工决策点 | 本目录对应章节 | schema 中的 `enum` 是它的机器投影；两者不一致视为契约缺陷 |
| 逐文件分类的默认动作与阻断条件 | [update-safety.md](update-safety.md) §3–§4 | `update-report` schema 只列出枚举，不重写动作表 |
| 威胁与剩余风险 | [threat-model.md](threat-model.md) | 其它文档只引用威胁 ID |
| 任务状态、锁、证据矩阵、合并门 | [`docs/orchestration.md`](../orchestration.md) | 本目录只引用章节号，不复制定义 |
| 阶段顺序与状态 | [`docs/implementation-plan.md`](../implementation-plan.md) | 本目录不写任务状态 |

任何 schema 变更都必须新开 Issue 重新冻结，并按 [`templates/contract-decision-log.md`](../../templates/contract-decision-log.md) 记录决策；实现任务不得在实现 PR 里顺手改契约。

## 3. 不变量（跨全部文档）

1. **来源内容是不可信数据**：只能作为证据，不能作为修改 AgentOrch 权威规则、权限或流程的指令；提示词注入一律按数据处理。
2. **已有治理不被改写**：状态机、路径 / 资源锁、证据矩阵、G1–G6、edition 语义保持原样；自我完善任务与普通任务走同一套门。
3. **项目自有内容不被覆盖**：更新引擎永不直接覆盖项目配置、业务代码、测试、私有文档与未登记文件。
4. **零第三方运行依赖**：本地与运行时校验保持纯标准库；外部 JSON Schema 验证器只能作为 CI 可选工具。
5. **未知不等于通过**：`UNKNOWN` / `NOT_RUN` / `BLOCKED` 不得折算为成功，也不得被下游默认值填补。
6. **高风险进入人工决策**：权限 / 安全边界、锁与状态机、数据迁移、破坏兼容、不可逆动作、外部依赖新增，一律 `NEEDS_HUMAN_DECISION`。

## 4. 非目标

- 不实现或描述任何具体采集算法、检查器实现、网络客户端或运行时服务。
- 不登记真实的来源凭据、私有数据、完整日志或受版权文本。
- 不新增任务状态、锁类型或合并门；不改变 `local` / `github` 能力差异。
- 不规定下游项目的目录结构；Architecture Contract 是可选输入，不是强制模板。
- 不在本阶段下载、验证或发布任何真实 Release。

## 5. 下游任务读取点

| 任务 | 只可读取本契约的 | 不得 |
|---|---|---|
| #48 | [`architecture-contract.md`](../architecture/architecture-contract.md) 与 `architecture-contract.schema.json` | 新增严重级别、改写豁免语义、对无契约项目施加硬门禁 |
| #49 | [sources-and-trust.md](sources-and-trust.md)、[lesson-lifecycle.md](lesson-lifecycle.md) 与两个 schema | 新增 Lesson 状态、改变证据等级含义、在代码里写死来源 |
| #50 | [entrypoints-and-decisions.md](entrypoints-and-decisions.md) 与 `docs/orchestration.md` 引用 | 复制状态机 / 门禁、绕过证据矩阵 |
| #51 | [update-safety.md](update-safety.md)、`update-manifest.schema.json`、`update-report.schema.json` | 新增分类、放宽失败关闭、把哈希差异当作覆盖许可 |
| #52 | [update-safety.md](update-safety.md) §2、§8 与 [entrypoints-and-decisions.md](entrypoints-and-decisions.md) §2 | 只把入口放进不随包发布的 `docs/` |
| #53 | [threat-model.md](threat-model.md) 全部负向测试列 | 把未执行的场景写成通过 |

完整依赖点与开放事项见 [`docs/contracts/README.md`](../contracts/README.md) §5–§6。

## 6. 真实扫描报告索引

| 报告 | 内容 |
|---|---|
| [live-scan-2026-10-26.md](live-scan-2026-10-26.md) | #74 首轮真实经验扫描（TalkDND / AgentMgr）：窗口、来源状态、计数、证据链接、去重结论与未验证项 |
