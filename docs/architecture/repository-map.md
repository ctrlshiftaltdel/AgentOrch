# 仓库文件责任地图

> **状态：`ACTIVE`。** 本文件是当前仓库「目录与关键文件职责、用途标签、分发范围、首要阅读入口」的唯一盘点位置；文件生命周期状态与流转规则只在 [document-lifecycle.md](document-lifecycle.md) 定义。两者都只登记文件归属，不重述状态机、G1–G6、C1–C4、能力字段、锁语义或任务证据矩阵——那些规则仍只在各自权威文件定义。
>
> 盘点基线：`main` @ `ce80d7917c992dad88615fd18b6fe6b61e79e18a`（2026-10-31）。本文件不移动、不删除、不改写任何被盘点文件；文件新增、迁移、弃用或分发范围变化时，按 [document-lifecycle.md](document-lifecycle.md) 同步更新。

## 1. 怎么用这张地图

1. **先定位规则类型，再看权威文件。** 任务状态机、合并门、证据矩阵只在 [`kit/orchestration.md`](../../kit/orchestration.md) 与 [`AGENTS.md`](../../AGENTS.md) 定义；`edition`、路径边界与验证命令只在 [`docs/PROJECT.md`](../PROJECT.md) 定义；设计目标只在 [`docs/PLAN.md`](../PLAN.md) 定义；阶段状态只在 [`docs/implementation-plan.md`](../implementation-plan.md) 定义。
2. **这里只登记“文件是什么、归谁、发不发”。** 需要具体规则时，点进权威文件；不要从本文件转述出第二套规则。
3. **用途标签不等于生命周期。** 同一文件可以既是 `reference` 又是 `ACTIVE`；也可以是 `reference` 但已 `ARCHIVED`。标签和状态分开读。
4. **没有为每个源文件另建说明文档。** 下表按目录与关键文件登记；测试夹具、schema 同类文件按组登记。只有无法由目录或自动检查表达的特殊约束，才在对应文件顶部或本文件“例外登记”中说明。

## 2. 用途标签

| 标签 | 含义 | 判断边界 |
|---|---|---|
| `authoritative` | 某项规则、数据结构或契约的**唯一正式定义**位置。 | 同一规则不得在第二处再定义；其它文件只能引用。 |
| `reference` | 导航、用户说明、设计提案、实现记录、证据映射或可复制模板；**不重复定义规则**。 | 可以解释“怎么用”，不能改写权威语义。 |
| `implementation` | 源码、测试、验证器、构建 / 迁移逻辑、CI 配置与测试夹具。 | 行为以代码和测试为准；文档只解释边界。 |
| `derived-local` | 生成文件、机器私有记录或本机配置。 | 必须明确是否入库、同步、打包；默认不入库、不打包。 |
| `outdated` | 已过时、被替代或仅供历史追溯的文件。 | 必须给出替代链接、保留理由与退出条件；默认不进入当前阅读路径。缺任一项不得标 `outdated`。 |

生命周期状态（`PROPOSED` / `ACTIVE` / `IMPLEMENTED` / `DROPPED` / `SUPERSEDED` / `ARCHIVED`）及转换规则只在 [document-lifecycle.md](document-lifecycle.md) 定义，本文件不重复。

## 3. 顶层目录与根文件

分发范围按 [`scripts/validate_kit.py`](../../scripts/validate_kit.py) 的 `REQUIRED` 列表判定：`发行包` 表示该路径在 `REQUIRED` 中并随发行包发布；`仅仓库` 表示不随发行包发布；`仅本机` 表示不入库、不打包。

| 路径 | 用途 | 生命周期 | 分发 | 唯一职责 / 权威位置 |
|---|---|---|---|---|
| [`AGENTS.md`](../../AGENTS.md) | `authoritative` | `ACTIVE` | 发行包 | 协作契约：角色权限、Git 纪律、证据标准、阅读口径。 |
| [`kit/orchestration.md`](../../kit/orchestration.md) | `authoritative` | `ACTIVE` | 发行包 | 核心编排：会话入口、任务状态机、锁、合并门 G1–G6、能力契约 C1–C4、验收程序。 |
| [`editions/local/`](../../editions/local/)（`master.md`、`execution.md`） | `authoritative` | `ACTIVE` | 发行包 | `local` 底座的命令、路径锁与启动 / 执行增量；不得定义另一套核心语义。 |
| [`editions/github/`](../../editions/github/)（`master.md`、`execution.md`、`issue-template.md`、`pr-template.md`、`active-task-registry.md`） | `authoritative` | `ACTIVE` | 发行包 | `github` 底座的 Issue / PR / 登记块与启动 / 执行增量；不得定义另一套核心语义。 |
| [`templates/`](../../templates/) | `reference` | `ACTIVE` | 发行包 | 可复制模板与起始结构（项目配置、计划、任务包、设计契约、审计、审查、能力、指标、架构、自我完善）。模板只提供结构，不是规则权威。 |
| [`scripts/`](../../scripts/) | `implementation` | `ACTIVE` | 发行包（例外见下） | CLI、验证器、构建、更新引擎与随包测试支撑。 |
| [`tests/`](../../tests/) | `implementation` | `ACTIVE` | 部分发行包 | 可重复验证；仓库专属夹具与读取 `docs/` 的测试不随包发布。 |
| [`docs/`](../../docs/) | 混合（见 §4） | `ACTIVE` | 仅仓库 | 本仓库的配置、设计目标、阶段状态、设计契约、用户手册与历史材料；`docs` 路径段在发行包中禁止出现。 |
| [`assets/`](../../assets/)（`agentorch-logo.svg`、`agentorch-mark.svg`） | `reference` | `ACTIVE` | 仅仓库 | GitHub 首页与文档展示用图形资产；不在 `REQUIRED` 中。 |
| [`release/README.md`](../../release/README.md) | `reference` | `ACTIVE` | 仅仓库 | 发布标准与发布清单；`release/` 目录与 `release/*.zip` 不随包发布。 |
| [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) | `implementation` | `ACTIVE` | 仅仓库 | 本仓库 CI（登记检查、测试、场景、指标、打包自检）；不随包发布。 |
| [`.local/README.md`](../../.local/README.md) | `reference` | `ACTIVE` | 仅仓库（仅此说明文件跟踪） | 本机私有目录说明；目录其余内容为 `derived-local`，不入库、不打包。 |
| [`.gitignore`](../../.gitignore)、[`.gitattributes`](../../.gitattributes) | `implementation` | `ACTIVE` | 仅仓库 | 仓库忽略规则与行尾 / 文本属性；不在 `REQUIRED` 中。 |
| [`VERSION.md`](../../VERSION.md) | `authoritative` | `ACTIVE` | 发行包 | 套件名、支持底座、Format 版本与 Release 版本记录的单一位置。 |
| [`LICENSE.md`](../../LICENSE.md) | `authoritative` | `ACTIVE` | 发行包 | 许可证文本；不得随意改动。 |

### 3.1 顶层目录的例外登记

- `scripts/build_release.py` 只在仓库中，不随包发布；它从 [`scripts/validate_kit.py`](../../scripts/validate_kit.py) 解析 `REQUIRED`，并导入 [`scripts/check_drift.py`](../../scripts/check_drift.py) 生成清单，不维护第二份文件列表。
- `scripts/verification_impact/` 与 `scripts/verification_planner/` 是 AVE 的只读影子工具，当前不在 `REQUIRED` 中，属于 `implementation` / `仅仓库`；其对外契约在 [`docs/design/verification-impact-c-contract.md`](../design/verification-impact-c-contract.md) 与 [`docs/design/verification-planner-b-contract.md`](../design/verification-planner-b-contract.md)。
- `scripts/verification_profile.py` 在 `REQUIRED` 中，随包发布；它只读取显式提供的证据，不自行读取宿主工具列表。
- `tests/fixtures/`、`tests/update_agentorch/test_report.py`、`tests/test_verification_impact.py`、`tests/test_verification_planner.py` 与 `tests/test_verification_profile.py` 属于 `implementation` / `仅仓库`；需要 `docs/` 夹具的测试不得加入 `REQUIRED`。

## 4. `docs/` 关键文档

`docs/` 全部 `仅仓库`。下表的“生命周期”记录当前文件声明或仓库事实；两者不一致时在 §9.3 登记，不在本任务中改写。

### 4.1 入口、配置与计划

| 路径 | 用途 | 生命周期 | 唯一职责 / 权威位置 |
|---|---|---|---|
| [`docs/README.md`](../README.md) | `reference` | `ACTIVE` | 完整用户手册、安装 / 升级 / 排错与文档导航。规则以 `AGENTS.md`、`kit/orchestration.md` 为准，本文件不弱化。 |
| [`docs/PROJECT.md`](../PROJECT.md) | `authoritative` | `ACTIVE` | 本项目适配配置：`edition` 唯一权威、允许 / 禁改路径、验证命令、并发与本地工作流。 |
| [`docs/PLAN.md`](../PLAN.md) | `authoritative` | `ACTIVE` | 设计目标权威：为什么做、做到什么程度、原则与边界。 |
| [`docs/implementation-plan.md`](../implementation-plan.md) | `authoritative` | `ACTIVE` | 阶段目标、任务顺序、依赖与当前状态的唯一落点。 |
| [`docs/charter.md`](../charter.md) | `reference` | `ACTIVE` | 项目章程与演进背景；记录早期形态与决策，不覆盖当前能力边界。 |
| [`docs/upgrade.md`](../upgrade.md) | `reference` | `ACTIVE` | 旧版本升级路径、报错原文、退出码与回退记录；标题仍以 1.3 为主，见 §9.3。 |
| [`docs/drift.md`](../drift.md) | `reference` | `ACTIVE` | 发行包清单与下游漂移诊断的设计口径、格式与边界；机器实现见 `scripts/check_drift.py`。 |
| [`docs/metrics.md`](../metrics.md) | `reference` | `ACTIVE` | 能力登记与交付度量的使用说明、基线与限制；指标口径权威在 [`templates/metrics.md`](../../templates/metrics.md)。 |

### 4.2 架构与契约

| 路径 | 用途 | 生命周期 | 唯一职责 / 权威位置 |
|---|---|---|---|
| [`docs/architecture/repository-map.md`](repository-map.md) | `authoritative` | `ACTIVE` | 本文件：目录 / 关键文件职责、用途标签、分发范围与首要入口的当前盘点。 |
| [`docs/architecture/document-lifecycle.md`](document-lifecycle.md) | `authoritative` | `ACTIVE` | 文件新增 / 修改 / 弃用 / 迁移 / 归档 / 删除规则，以及 `outdated` 的替代、保留与退出条件。 |
| [`docs/architecture/architecture-contract.md`](../architecture/architecture-contract.md) | `authoritative` | `ACTIVE` | Architecture Contract v1：模块职责、依赖方向、严重级别、豁免与未配置语义。 |
| [`docs/architecture/agentmgr-contract-rules.md`](../architecture/agentmgr-contract-rules.md) | `reference` | `ACTIVE` | AgentMgr 经验到 AGM-1…AGM-6 的规则映射；字段与枚举仍由架构契约与 schema 定义。 |
| [`docs/contracts/README.md`](../contracts/README.md) | `reference` | `ACTIVE` | schema / fixture 索引、零依赖验证方案、CI 边界与下游依赖。 |
| [`docs/contracts/decision-log.md`](../contracts/decision-log.md) | `reference` | `ACTIVE` | 契约决策记录：何时、由谁、基于什么证据冻结或改契约；不复制语义。 |
| [`docs/contracts/schemas/*.schema.json`](../contracts/schemas/) | `authoritative` | `ACTIVE` | 五份 schema：字段名、必填、类型、枚举、`const` 与模式的机器权威。 |
| [`docs/contracts/fixtures/valid/`](../contracts/fixtures/valid/) 与 [`docs/contracts/fixtures/invalid/`](../contracts/fixtures/invalid/) | `implementation` | `ACTIVE` | 契约验证与负向测试的合成样例；不随包发布。 |

### 4.3 设计文档

| 路径 | 用途 | 生命周期 | 唯一职责 / 权威位置 |
|---|---|---|---|
| [`docs/design/resource-locks.md`](../design/resource-locks.md) | `authoritative` | `ACTIVE` | 具名资源锁 v1 冻结契约；状态机与 G1–G6 仍只引用 `kit/orchestration.md`。 |
| [`docs/design/agent-role-discovery-and-model-selection.md`](../design/agent-role-discovery-and-model-selection.md) | `authoritative` | `ACTIVE` | Phase D 角色发现、Subagent 能力考核与动态模型选择的设计（#117 / PR #118 合并后冻结）；实现记录见 `scripts/agent_selection/`。 |
| [`docs/design/deepseek-harness-agent-role-tools.md`](../design/deepseek-harness-agent-role-tools.md) | `authoritative` | `ACTIVE` | DSH 角色工具适配的 `FROZEN` 设计；只冻结适配层，不表示运行时已验证。 |
| [`docs/design/next-phase-governance-and-agent-performance.md`](../design/next-phase-governance-and-agent-performance.md) | `authoritative` | `ACTIVE`（依据 #147 `VERIFIED`；文件头仍写 `PROPOSED`，见 §9.3） | 下一阶段治理、旧项目接入、ETR 与三角色评价的设计依据；阶段状态仍以 `docs/implementation-plan.md` 为准。 |
| [`docs/design/adaptive-verification-and-quality-gates.md`](../design/adaptive-verification-and-quality-gates.md) | `reference` | `PROPOSED` | AVE 父级提案；仍是提案，不定义现行门禁。 |
| [`docs/design/verification-planner-b-contract.md`](../design/verification-planner-b-contract.md) | `authoritative` | `ACTIVE` | AVE B 阶段只读影子 Gate Planner 的 `FROZEN` 子契约。 |
| [`docs/design/verification-impact-c-contract.md`](../design/verification-impact-c-contract.md) | `authoritative` | `ACTIVE` | AVE C 阶段只读影响面与证据影子的 `FROZEN` 子契约。 |
| [`docs/design/semver-proactive-release-policy.md`](../design/semver-proactive-release-policy.md) | `reference` | `PROPOSED` | 主动式 SemVer 发行节奏提案；不替代 `release/README.md` 或现行授权规则。 |
| [`docs/design/task-driven-agent-topology.md`](../design/task-driven-agent-topology.md) | `reference` | `PROPOSED` | 任务驱动 Agent 拓扑提案；不改变现行角色权限、审查门或 C1–C4。 |

### 4.4 测试场景

| 路径 | 用途 | 生命周期 | 唯一职责 / 权威位置 |
|---|---|---|---|
| [`docs/testing/delivery-regression-scenarios.md`](../testing/delivery-regression-scenarios.md) | `authoritative` | `ACTIVE` | 合成回归场景目录、正负组判定、场景矩阵、fixture 规则与未验证范围；场景 ID 与预期拦截门只在这里定义。 |

### 4.5 自我完善与更新

| 路径 | 用途 | 生命周期 | 唯一职责 / 权威位置 |
|---|---|---|---|
| [`docs/self-improvement/README.md`](../self-improvement/README.md) | `reference` | `ACTIVE` | 自我完善 / 安全更新契约的索引与单一权威表；语义在下列子文件与 schema。 |
| [`docs/self-improvement/sources-and-trust.md`](../self-improvement/sources-and-trust.md) | `authoritative` | `ACTIVE` | 来源注册表、信任模型、增量游标、启用 / 停用 / 移除语义。 |
| [`docs/self-improvement/lesson-lifecycle.md`](../self-improvement/lesson-lifecycle.md) | `authoritative` | `ACTIVE` | Lesson 四类内容、证据等级 E0–E4、生命周期、去重与冲突决策。 |
| [`docs/self-improvement/entrypoints-and-decisions.md`](../self-improvement/entrypoints-and-decisions.md) | `authoritative` | `ACTIVE` | 触发语 / 非触发、授权模型、停止条件与 `HD-*` 人工决策目录。 |
| [`docs/self-improvement/update-safety.md`](../self-improvement/update-safety.md) | `authoritative` | `ACTIVE` | 官方 Release 信任、逐文件分类、项目自有保护、备份 / 回滚与失败关闭。 |
| [`docs/self-improvement/threat-model.md`](../self-improvement/threat-model.md) | `authoritative` | `ACTIVE` | 威胁、缓解、负向测试与剩余风险的唯一权威。 |
| [`docs/self-improvement/self-improvement-loop.md`](../self-improvement/self-improvement-loop.md) | `reference` | `IMPLEMENTED` | 自我完善闭环实现记录（触发、恢复、规划、派发、审查、报告）；不定义契约。 |
| [`docs/self-improvement/architecture-checker.md`](../self-improvement/architecture-checker.md) | `reference` | `IMPLEMENTED` | Architecture Contract 检查器实现记录与能力边界。 |
| [`docs/self-improvement/lessons-implementation.md`](../self-improvement/lessons-implementation.md) | `reference` | `IMPLEMENTED` | Lesson 校验、状态机、指纹、去重与冲突的实现记录。 |
| [`docs/self-improvement/sources-implementation.md`](../self-improvement/sources-implementation.md) | `reference` | `IMPLEMENTED` | 来源注册表与采集适配器的实现记录。 |
| [`docs/self-improvement/update-engine-implementation.md`](../self-improvement/update-engine-implementation.md) | `reference` | `IMPLEMENTED` | 安全更新引擎实现记录；真实 Release 获取等未验证项按文件内标注。 |
| [`docs/self-improvement/cross-module-state-and-recovery.md`](../self-improvement/cross-module-state-and-recovery.md) | `reference` | `ACTIVE` | 共享状态 / 并发 / 恢复的增量验收检查点 R1–R6；不新增契约字段。 |
| [`docs/self-improvement/agentmgr-lessons.md`](../self-improvement/agentmgr-lessons.md) | `reference` | `ACTIVE` | AgentMgr 原始证据到泛化原则、规则、测试与限制的映射。 |
| [`docs/self-improvement/live-scan-2026-10-26.md`](../self-improvement/live-scan-2026-10-26.md) | `reference` | `ARCHIVED` | 首轮真实经验扫描的一次性脱敏报告；保留审计价值，不进入默认阅读路径。 |
| [`docs/self-improvement/sources.json`](../self-improvement/sources.json) | `authoritative` | `ACTIVE` | 当前来源注册表数据；字段与语义由 `source-registry.schema.json` 与 `sources-and-trust.md` 约束。 |
| [`docs/self-improvement-and-update-plan.md`](../self-improvement-and-update-plan.md) | `reference` | `IMPLEMENTED` | 自我完善与安全更新的原始设计与实施计划；阶段 #47–#53 已完成，文件头状态段落未同步，见 §9.3。 |

## 5. 核心文件职责（只登记，不复制内容）

| 文件 | 负责什么 | 不负责什么 |
|---|---|---|
| [`README.md`](../../README.md) | GitHub 首页：产品定位、核心能力、快速开始与完整上手指南入口。 | 不定义协作契约、状态机、门禁、配置字段或发布标准；不随发行包发布。 |
| [`docs/README.md`](../README.md) | 完整用户手册：安装、底座选择、命令、三条落地路径、配置、自检、发布、升级与治理导航。 | 不替代 `AGENTS.md` / `kit/orchestration.md` 的规则；不定义状态机、G1–G6、C1–C4。 |
| [`docs/PLAN.md`](../PLAN.md) | 设计目标、原则、范围与非目标、全局验收方向。 | 不写阶段顺序、任务状态、门禁定义或能力字段。 |
| [`docs/implementation-plan.md`](../implementation-plan.md) | 阶段目标、任务依赖、未完成工作、阻塞、下一步与状态表。 | 不写设计目标、状态机、G1–G6、字段或证据矩阵定义；历史详情由 Issue / PR 承载。 |
| [`docs/PROJECT.md`](../PROJECT.md) | 本项目适配：`edition` 唯一权威、允许 / 禁改路径、验证命令、并发与本地工作流。 | 不定义核心契约；不把下游项目特例写进上游规则。 |
| [`AGENTS.md`](../../AGENTS.md) | 与底座无关的协作契约：角色权限、阅读口径、Git 纪律、证据标准、安全边界。 | 不写具体载体命令；不重复 `kit/orchestration.md` 的状态机细节。 |
| [`kit/orchestration.md`](../../kit/orchestration.md) | 核心编排契约：会话入口、状态机、锁、合并门、能力探测与通用验收程序。 | 不写项目配置、阶段状态或个人环境事实；底座手册只能细化，不能另写一套。 |

## 6. 核心依赖方向

```text
AGENTS.md + kit/orchestration.md
        │  （核心语义，唯一权威）
        ├── editions/<edition>/       底座命令与载体细化
        ├── templates/                任务包 / 设计 / 审计 / 审查结构
        └── scripts/ + tests/         实现与可重复验证
                    │
docs/PROJECT.md ────┤  edition、路径、验证命令
docs/PLAN.md ───────┤  设计目标
docs/implementation-plan.md ── 阶段状态与依赖
                    │
docs/design/ + docs/contracts/schemas/
        │  （专项冻结契约与字段权威）
        ├── docs/self-improvement/    语义与实现记录
        └── docs/architecture/        架构契约与文件责任
                    │
docs/README.md ─────┘  面向人的导航与使用说明
```

- **单一权威**：一项规则只在一个位置定义；箭头表示“被引用 / 被实现”，不表示可以复制定义。
- **分发依赖**：`scripts/validate_kit.py` 的 `REQUIRED` 同时是结构必需文件与发行包清单；`scripts/build_release.py` 解析它，`scripts/check_drift.py` 生成 `MANIFEST.json`，`release/README.md` 记录发布标准。改动路径或分发范围必须同时检查这条链。
- **文档依赖**：`docs/PLAN.md` 决定方向，`docs/implementation-plan.md` 决定顺序与状态；二者冲突时以设计目标为准并停下对齐。`docs/architecture/repository-map.md` 与 `document-lifecycle.md` 只决定“文件放哪、是否权威、是否过期”，不改变上述优先级。

## 7. 首要阅读入口

| 读者 / 场景 | 先读 | 再读 |
|---|---|---|
| 人类第一次了解项目 | [`README.md`](../../README.md) | [`docs/README.md`](../README.md) |
| Agent 每次会话开始 | [`AGENTS.md`](../../AGENTS.md) | [`kit/orchestration.md`](../../kit/orchestration.md) 第一部分、第二部分；按需第三部分；当前底座 `editions/<edition>/master.md` |
| 派发 / 审查 / 合并 | [`kit/orchestration.md`](../../kit/orchestration.md) 第二部分 | 当前底座目录（[`editions/`](../../editions/)）下的 `execution.md` / `master.md`、[`templates/review-checklist.md`](../../templates/review-checklist.md) |
| 判断项目方向 | [`docs/PLAN.md`](../PLAN.md) | [`docs/implementation-plan.md`](../implementation-plan.md) |
| 调整项目配置 | [`docs/PROJECT.md`](../PROJECT.md) | [`templates/project.md`](../../templates/project.md) |
| 判断文件归属 / 是否过期 | 本文件 §2–§4 | [document-lifecycle.md](document-lifecycle.md) |
| 设计冻结或改契约 | [`templates/design.md`](../../templates/design.md)、[`templates/contract-decision-log.md`](../../templates/contract-decision-log.md) | 对应 `docs/design/` 契约与 `docs/contracts/schemas/` |
| 自我完善 / 更新套件 | [`docs/self-improvement/README.md`](../self-improvement/README.md) | [`docs/self-improvement/entrypoints-and-decisions.md`](../self-improvement/entrypoints-and-decisions.md)、[`docs/self-improvement/update-safety.md`](../self-improvement/update-safety.md) |

## 8. 允许的分发范围

| 范围 | 判定 | 包含 | 不包含 / 规则 |
|---|---|---|---|
| `发行包` | 路径在 `REQUIRED` 中 | `AGENTS.md`、`VERSION.md`、`LICENSE.md`、`kit/`、`editions/`、`templates/`、大部分 `scripts/` 与随包 `tests/`；构建时另加 `MANIFEST.json`。 | 包内文件集合 == `REQUIRED` + `MANIFEST.json`；不维护第二份清单。 |
| `仅仓库` | 不在 `REQUIRED` 中，或位于禁止路径段 | `README.md`、`docs/`、`assets/`、`release/`、`.github/`、`.local/README.md`、`.gitignore`、`.gitattributes`、`scripts/build_release.py`、AVE 影子工具及其仓库测试、仓库夹具。 | `docs`、`release`、`.orchestration`、`.worktrees`、`.workbuddy` 等路径段禁止进入发行包。 |
| `仅本机` | `.local/` 下除 `README.md` 外的内容，以及运行时状态 | 凭据、机器信息、发布扫描规则、scope 记录、任务库、缓存与 Worktree。 | 永不入库、永不上传、永不打包；读取与写入边界服从 `AGENTS.md` 与 `docs/PROJECT.md`。 |

下游项目拿到的是 `templates/project.md` 作为待填配置起点，不是本仓库的 `docs/PROJECT.md`；发行包不包含 `docs/`。发布前自检与包内文件集合以 [`release/README.md`](../../release/README.md) 与 [`scripts/build_release.py`](../../scripts/build_release.py) 为准。

## 9. 一次性计划、历史材料与过期候选

### 9.1 已完成的一次性计划 / 历史材料

| 文件 | 处置 | 依据与保留理由 |
|---|---|---|
| [`docs/self-improvement-and-update-plan.md`](../self-improvement-and-update-plan.md) | `reference` + `IMPLEMENTED`；不是当前字段 / 状态语义的权威。 | 计划阶段 #47–#53 已在 [`docs/implementation-plan.md`](../implementation-plan.md) §11 登记为完成；契约语义已拆分到 `docs/self-improvement/` 与 `docs/contracts/schemas/`。保留作设计与背景追溯，仍被 `README.md` 与阶段计划引用。 |
| [`docs/self-improvement/live-scan-2026-10-26.md`](../self-improvement/live-scan-2026-10-26.md) | `reference` + `ARCHIVED`；不进入默认阅读路径。 | 首轮真实扫描的一次性脱敏报告，保留审计价值；扫描状态与来源注册表在 [`docs/self-improvement/sources.json`](../self-improvement/sources.json) 与 [`sources-and-trust.md`](../self-improvement/sources-and-trust.md)。 |

### 9.2 已确认 `outdated`

**当前基线没有已确认的 `outdated` 文件。** 判定 `outdated` 需要同时具备：可定位的替代文件 / 决定、保留理由、退出条件、决定依据与最后核验日期；缺任一项时只能登记为候选，不能凭印象或文件年龄推断。已实现（`IMPLEMENTED`）也不等于过期；仍承担现行规则的文件保持 `ACTIVE`。

### 9.3 候选过期 / 已知不一致（本任务只登记，不改写状态）

| 文件 / 位置 | 观察 | 可定位证据 | 影响与建议 |
|---|---|---|---|
| [`docs/design/next-phase-governance-and-agent-performance.md`](../design/next-phase-governance-and-agent-performance.md) 文件头 | 文件仍写 `PROPOSED` 且要求“合并并 `VERIFIED` 前保持 `PROPOSED`”；但 #147 已被登记为已完成 / `VERIFIED`。 | [`docs/implementation-plan.md`](../implementation-plan.md) §1 的 #147 行（PR [#148](https://github.com/ctrlshiftaltdel/AgentOrch/pull/148) 合并 `3528e3ff5a8e868f92b8b2a36c523823cfe9ad6e`）；所有者在本 Issue 的说明 [comment 6544993213](https://github.com/ctrlshiftaltdel/AgentOrch/issues/150#issuecomment-6544993213)。 | 读者可能误以为设计尚未冻结。**不得**用本任务或评论直接改写设计冻结语义；建议由 Master / Owner 决定单独的“设计状态同步”任务，并保留本行作为差异记录。 |
| [`docs/self-improvement-and-update-plan.md`](../self-improvement-and-update-plan.md) 文件头 | 文件头仍称“当前没有任何检查器、更新脚本或运行时”，与 #47–#53 已完成的事实不一致。 | [`docs/implementation-plan.md`](../implementation-plan.md) §11 的 #47–#53 行；`scripts/self_improvement/`、`scripts/update_agentorch/` 与对应测试已存在。 | 文件整体仍有设计追溯价值，故不标 `outdated`。建议后续文档任务修正状态段落，或由所有者确认是否转为 `SUPERSEDED`；替代候选是 [`docs/self-improvement/README.md`](../self-improvement/README.md) 与 [`docs/contracts/README.md`](../contracts/README.md)，但替代关系尚未经任务载体批准。 |
| [`docs/README.md`](../README.md) §5 目录结构 | 目录树中 `kit/orchestration.md` 被重复列三次并分别标注“核心编排 / 通用验收程序 / 会话入口”；同时列出当前仓库不存在的 `prompts/`。 | 实际跟踪文件见仓库 `git ls-files`；`kit/orchestration.md` 已是合并后的单份文件，`prompts/` 已在 1.5 迁移中并入模板（见 [`docs/upgrade.md`](../upgrade.md) §12）。 | 属于导航残留，不是规则冲突。建议由后续导航清理任务（引用本文件与生命周期规范）修正；本任务只新增导航链接，不重排该节。 |
| [`docs/upgrade.md`](../upgrade.md) 标题 | 标题写“从旧版升级到 1.3（2026-10）”，正文已包含 1.4 的 `docs/PLAN.md` 迁移与当前更新入口等内容。 | 文件自身 §10.6 与 §13；[`docs/README.md`](../README.md) 第 10 节。 | 标题范围落后于内容，容易让读者误判适用版本。建议后续文档任务调整标题 / 适用范围；本任务不改写升级语义。 |

### 9.4 例外登记

- `docs/architecture/repository-map.md` 与 `docs/architecture/document-lifecycle.md` 是本任务新增的文件责任 / 生命周期权威；它们不改变任何现有规则文件，也不把 `docs/` 带入发行包。
- 设计文稿中的 `docs/orchestration.md` 是 **GOV-2 迁移目标**，当前尚未存在；当前核心契约仍是 [`kit/orchestration.md`](../../kit/orchestration.md)，在 #151 `VERIFIED` 前不得按目标路径读取。

## 10. 维护规则

1. **关键文件变化同步地图**：新增、迁移、删除、改变分发范围或改变权威归属时，在同一任务内更新本文件与被影响的链接；不要为单个源文件另建说明文档。
2. **一项规则一个权威位置**：发现同一规则出现第二处定义时，先停下登记缺陷，不在本文件“顺手”再写一份。
3. **过期先登记再处置**：候选过期只登记证据与建议；转为 `outdated` 必须满足 [document-lifecycle.md](document-lifecycle.md) 的替代链接、保留理由、退出条件与决定依据。
4. **不移动、不删除**：本任务只做盘点与导航；迁移 / 归档 / 删除必须另立任务，并更新 `REQUIRED`、构建、更新引擎与所有引用。
5. **验证**：改动后运行 `uv run python scripts/validate_kit.py`，并手工核对本文件新增的每个相对链接与仓库路径真实存在。
