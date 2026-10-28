# DeepSeek Harness 角色工具适配设计

- **状态：** `FROZEN`（设计已冻结；不代表运行时已实现，DSH 真实运行时验证仍待 #163 / #167 完成）
- **日期：** 2026-10-28
- **关联：** Epic [#160](https://github.com/ctrlshiftaltdel/AgentOrch/issues/160)、设计冻结 [#161](https://github.com/ctrlshiftaltdel/AgentOrch/issues/161)
- **阶段计划：** [#162](https://github.com/ctrlshiftaltdel/AgentOrch/issues/162)
- **前置约束：** AgentOrch 当前治理契约、Phase D 已交付能力，以及 #147/#149/#151/#153 等开放设计 / 任务的真实状态
- **设计边界：** 这里只冻结 DSH 适配层；不修改现有 Kit 状态机、权限模型、发行清单或 Release 流程

## 1. 结论摘要

AgentOrch 与 DeepSeek Harness（DSH）的集成应采用**独立的角色工具适配器**：DSH 负责会话、模型和工具运行时；AgentOrch 负责项目任务治理、角色边界、证据要求、状态判定及 local / github 底座分流。适配层不得重新实现一套编排状态机，也不得把现有 Kit 全量复制成另一套独立产品。

对外核心工具名固定为：

- **ao_master** — Master 角色接口
- **ao_execution** — Execution 角色接口
- **ao_reviewer** — Reviewer 角色接口

不采用泛化的 plan / run / gate 作为并列核心工具。工具名字用于清晰表达角色，不构成身份验证：调用者身份与工具可见范围必须由 DSH 经验证的 Agent scope / 配置绑定证明，不能由模型提交的 role 参数决定。

首版建议提供单独 DSH bundle，作为同一 AgentOrch Release 的第二个资产，与现有 Kit ZIP 使用相同版本 / Git tag；不将 DSH 包纳入 Kit 的 REQUIRED / MANIFEST 集合，不发布公共 npm 包，不让 CI 自动创建 Tag 或 Release。所有不具备真实执行证据的能力必须返回 UNKNOWN、UNSUPPORTED 或 NEEDS_HANDOFF，不能伪报成功。

## 2. 基线事实与待落地目标必须分开

### 2.1 当前主线已知事实

以 2026-10-28 当前主线可核实的信息为准：

- AgentOrch 当前已发布版本为 **v1.7.0 / Format 1.7**，Kit 包有 117 个 REQUIRED 文件，另加生成的 MANIFEST.json。
- Phase D 的 #120–#124 已完成，已有 per-terminal scope、Markdown 私有 store、Runtime / Agent 发现、角色匹配、模型数据源、基础反馈和 E2E；本项目应增量复用，不重建平行系统。
- AgentOrch 当前核心契约文件仍是 kit/orchestration.md，且在 scripts/validate_kit.py 的 REQUIRED 中。#151 所规划的迁移目标 docs/orchestration.md 不能在迁移完成前被描述为现行事实。
- 当前仓库的 docs/PROJECT.md 将 edition 设为 github。现有 scripts/orchestrator.py 是本地 CLI / 任务库工具，并明确不启动 Agent、不合并 Git 分支；github edition 下不能用它写本地 SQLite 来冒充 GitHub 任务载体。
- #147 / #148、#149、#151、#153、#154 等治理工作仍需以各自 Issue、PR、head SHA 和验证记录重新核验。本设计仅声明对它们的依赖，不将未来的契约路径或 agentorch/ 下游布局提前写成已交付功能。
- **DSH 官方 Release 快照（历史事实，带查询时间）：** 对 `deepseek-ai/deepseek-harness` 的 GitHub Releases API 查询发生在 **2026-10-28T07:37:16Z（UTC）**，当时返回的最新发布条目为 `dsh-v0.2.2-alpha.2`（published `2026-10-28T02:50:54Z`，`prerelease=true`），其下一条为 `dsh-v0.2.1-rc.4`（published `2026-10-28T02:49:58Z`，同样为预发布）。本执行实例于 2026-10-28T07:40:20Z 按同一 API 复核，前两条结果相同。
  - 这是**该时刻的历史快照**，不是「当前最新」的无时间断言：后续条目随时可能新增或变更，任何引用、调研或测试前都必须重新查询并记录新的时间戳与 tag。
  - 快照里出现预发布条目，只说明 DSH 存在可调研的 API 起点；**预发布条目不证明** AgentOrch 已支持、已兼容或已实测该版本，也不代表任何更高 DSH 版本兼容。支持的版本范围只能由 #163 / #167 在锁定版本上的实测证据给出。

参考：
- [AgentOrch Releases](https://github.com/ctrlshiftaltdel/AgentOrch/releases)
- [DeepSeek Harness Releases](https://github.com/DeepSeek-ai/DeepSeek-Harness/releases)
- [DSH Tools API README](https://github.com/DeepSeek-ai/DeepSeek-Harness/blob/master/packages/core/tools/README.md)
- [DSH Extension Cookbook](https://github.com/DeepSeek-ai/DeepSeek-Harness/blob/master/docs/cookbook/extension-cookbook.md)
- [DSH Plugin Manager README](https://github.com/DeepSeek-ai/DeepSeek-Harness/blob/master/packages/boot/plugin-manager/README.md)

### 2.2 当前不能当作事实的设计目标

以下内容需要相关 Issue 完成后才能宣称支持：

- 核心契约迁移到 docs/orchestration.md；
- 老项目统一采用 Host Root / AgentOrch Root 分离的 agentorch/ 内嵌布局；
- DSH 在目标发行版上的每 Agent 工具 scope、调用者身份、会话工作目录上下文和可执行的 subagent 调用能力；
- DSH bundle 的确切 engine / peer 版本范围、安装审批行为及跨平台可复现构建；
- 任意 GitHub edition 任务操作的自动化能力。

这些不是文档表述上的小差异：任何一项未核验都可能导致错误路由、越权调用或错误写入任务状态，因此必须按失败关闭处理。

## 3. 目标与非目标

### 3.1 目标

1. 用固定的三个工具入口映射 AO 的三个治理角色。
2. 通过 DSH 的真实 Agent scope / 调用身份限制工具可见性和可执行操作。
3. 在每次调用时确定当前项目、AgentOrch 根目录、edition 和允许操作范围，不缓存跨项目的根路径。
4. 复用现有 AgentOrch 状态、证据与角色选型契约；不发明新的状态机、任务库或 Reviewer 权限体系。
5. 对脚本完整性、包版本、路径、edition、权限、缺失后端和超时实行可验证的 fail-closed 策略。
6. 独立构建 DSH bundle、运行 DSH 专属测试，并作为同一 Release tag 的第二个资产发布。

### 3.2 非目标

- 不替代 DSH 的 Agent runtime、终端、文件系统、Git、模型调用、会话或 subagent 工具链。
- 不把所有功能塞入一个万能命令工具；不接受模型提供任意 shell 命令、任意 argv 或任意文件路径。
- 不新增第四种角色，不自动创建 / 授权 subagent，不自动修改用户 preset / profile。
- 不自动切换 Master 模型；Master 的选择 / 切换保留给用户。
- 不修改 AgentOrch 现有状态机、证据矩阵、合并门、C1–C8 / G1–G6、edition 选择语义或已有 Kit Release 的文件集合。
- 不因 DSH 工具接口存在，就宣称其底层具备真实 spawn / wait / collect、GitHub 写入或安全隔离能力。

## 4. 三个角色工具的唯一职责

### 4.1 ao_master

**职责：** 把目标、计划、任务依赖、任务载体状态、允许路径与验收证据映射到现有 AgentOrch 治理流程。Master 可检查状态、组织任务包、收集证据、执行独立审查与提出批准 / 合并动作，但每个动作必须经过既有门禁；实际派发或合并只有在后端能力真实存在时才可执行。

**禁止：** 绕过未满足依赖、缺失证据或过期 head；将 CI 绿灯当成逐条验收；替 Execution 修改实现文件来绕过任务登记；自动更换自身模型、Prompt、权限或工具配置；在未授权情况下执行不可逆动作。

### 4.2 ao_execution

**职责：** 读取当前已派发的单项任务包、依赖状态、允许 / 禁改路径、验证命令与证据要求；提交可定位的执行进展、Diff / head SHA、真实验证结果、剩余风险和交接记录。

**禁止：** 自批、自合、标记任务 VERIFIED、扩大允许路径、伪造命令结果 / head SHA、读取或写入其他 scope 的私有记录、执行任务包以外的任意命令。

### 4.3 ao_reviewer

**职责：** 以独立身份读取任务目标、实现 Diff、当前 head、测试结果和证据，输出带依据的缺陷与风险意见、逐条验收结论及需要复验的事项。它的意见是独立证据，不取代 Master 的最终判断。

**禁止：** 修改产品代码、替代 Execution 修复、代替 Master 批准 / 合并、对没有证据的事实给出已验证结论。Reviewer 必须满足现有底层模型独立性要求；仅不同 alias、温度或 Prompt 的同一模型不能算独立。

### 4.4 工具名不是授权凭证

实现不允许以下模式：把一个全局工具暴露给所有 DSH Agent，然后仅依赖系统提示词让模型“自觉”按角色使用；也不允许让模型通过参数传入 role=master 来获得 Master 权限。

首选方案是将每个工具注册在经目标 DSH 版本验证的 Agent-scoped tool registry / restriction 上下文中，并对调用入口做第二次 role-to-capability 校验。若目标版本无法证明调用者身份，或工具 scope 无法按 Agent 隔离，则该版本不支持本适配方案；必须阻塞在 API 验证 Issue #163，不允许退回全局暴露模式。

## 5. 适配层结构与边界

逻辑链路：

1. DSH 中被用户配置的指定 Agent 调用自己的 AO 角色工具。
2. 工具入口从 DSH 的可信调用上下文读取 Agent 身份与当前会话工作目录。
3. 角色守卫验证 tool ↔ Agent 映射和允许的操作集合。
4. 根解析器定位 Host Root / AgentOrch Root、读取项目配置并验证发行版本。
5. 完整性守卫把即将调用的 AgentOrch 脚本与 bundle 内可信 SHA-256 基线比较。
6. edition adapter 选择真实可用的 local CLI 或 github Issue / PR 后端。
7. 后端结果、证据来源与失败原因以结构化格式返回；未知 / 无法执行必须明确呈现，不得用自然语言包装成成功。

此结构是适配器架构，不是新的 Agent scheduler。真正的执行派发能力由 DSH 和被配置的 Agent Runtime 提供；AgentOrch 只能记录 / 路由有真实证据支持的动作。

## 6. 项目根、AgentOrch 根及 edition 解析

### 6.1 两个根目录

- **Host Root：** 下游宿主项目的根目录，包含宿主自身文件和宿主根 AGENTS.md。
- **AgentOrch Root：** AgentOrch 脚本、核心契约、模板和版本清单所在根目录。在目标隔离布局中，它是 Host Root/agentorch/。

两者不能混为一谈。适配器内部路径以 AgentOrch Root 为基准；任何授权宿主级操作必须显式使用 Host Root，且本适配层默认不修改宿主文件。

### 6.2 解析原则

- 优先使用目标 DSH 版本公开 API 提供的当前会话 / 项目工作目录；需在 #163 中验证。工作目录应按每次工具调用重新读取，不复用上一次调用或另一个会话缓存的根路径。
- 在每次调用中从可信工作目录向上查找有限范围内的明确标记，区分当前平铺安装布局与目标嵌套 agentorch/ 布局。
- 平铺布局必须同时满足当前版可验证的核心标记，例如 docs/PROJECT.md、scripts/orchestrator.py 与版本信息；嵌套布局必须在 agentorch/ 下满足同等标记，并将宿主根另行识别。
- 如果同时找到多个候选根、根标记互相矛盾、目录无法读取、路径跨越不被允许的边界，或无法确认当前项目属于哪个根，返回 ROOT_UNKNOWN / AMBIGUOUS_ROOT，不得猜测。
- 不因 process.cwd() 存在就认为它是用户项目；不得通过更改 cwd 解决根解析失败。
- 旧的平铺布局作为兼容路径仅在确切兼容测试通过后保留；新项目与旧项目的安装流程由各自文档区分。

### 6.3 edition 是唯一路由权威

读取已识别 AgentOrch Root 下 docs/PROJECT.md 中唯一的 edition 声明，合法值仍是 local 或 github。缺失、重复、类型 / 值非法、文件无法读取时停止，不做默认选择。

- **local：** 仅调用已有 local CLI 能真实支持的命令；必须先检查命令白名单、任务状态、允许路径与当前 scope。不能把调用 CLI 等同于真正启动另一个 Agent。
- **github：** 任务状态应写入 Issue / PR。只有当可用、已授权的 GitHub 后端和具体操作都在当前 Runtime 上被真实确认时才执行。否则返回 UNSUPPORTED 或 NEEDS_HANDOFF；绝不调用本地 SQLite 当作 GitHub edition 的后备写入路径。
- 不允许同一任务同时写 local SQLite 与 GitHub Issue / PR，避免出现双重状态源。

## 7. 执行安全与数据边界

### 7.1 命令与路径安全

- 不暴露 exec(command:string)、通用 shell 或模型控制的命令拼接接口。
- 对任何外部进程使用无 shell 的固定可执行文件 / argv 白名单调用；将 cwd 锁定为经验证的 AgentOrch Root，使用最小环境变量、有限超时和输出大小限制。
- 不默认继承无关密钥、用户环境变量或其他项目凭据；异常输出需进行秘密脱敏。
- 每个工具只允许访问其角色契约声明的操作集。路径解析和路径授权应在实际文件打开 / 命令执行前再次校验，以防路径穿越、符号链接绕过和 TOCTOU 风险。
- 失败、超时、缺少权限、依赖不完整或后端结果未知时，保持任务为未完成 / 原状态，不能假装状态转换成功。

### 7.2 可信脚本完整性基线

不能只读取下游项目自己的 MANIFEST.json 来证明脚本可信，因为下游可能同时修改脚本与清单。建议的实现约束：

1. DSH builder 以经验证的正式 AgentOrch Kit Release ZIP 为来源，校验发布版本、Format 版本及包内清单后，生成待调用脚本的 allowlist 与 SHA-256 预期值。
2. 将这些预期值和对应的源 Release 版本 / Format / 资产摘要封入 DSH bundle；它们不从每个下游项目的可变清单动态生成。
3. 工具执行前，比较目标 AgentOrch Root 下实际脚本字节与 bundle 的固定预期值；缺文件、额外的运行依赖、版本不匹配或哈希不一致均停止并报告。
4. 该校验检测的是脚本与可信发行基线的偏离，不代表对下游代码进行完整安全审计。若用户有意修改上游脚本，须走明确的人工批准 / 重新构建基线流程，而不是自动覆盖或自动接受。
5. 不修改下游文件来“修复”哈希不一致，也不丢弃用户改动。报告差异并交由用户决定。

哈希基线的信任源、资产下载 / SHA 复核方式以及实际 allowlist 将在 builder Issue #165 中进一步冻结和测试。

### 7.3 私有数据与 scope

- 角色发现与当前 Phase D 既有反馈记录继续使用现有的、按当前终端 / 会话隔离的 Markdown 存储；不得跨 scope 读取或混写，不另建数据库。
- ETR 计算与覆盖率属于尚未实现的开放任务 #155，不是当前已有能力。若 #155 经授权实现，其 ETR 数据必须复用上述同一 scope 隔离 Markdown store，保持 scope 隔离，不得另建数据库或跨 scope 汇总。
- 任何真实项目私有 Prompt、会话内容、访问令牌、API Key、完整敏感 Diff 或个人路径不得复制到公开 bundle / Issue / CI 日志。
- 如果当前 scope 的隔离性无法证明，则不得写入持久评价或状态记录。
- 下游隔离布局中的 agentorch/.local/ 应作为 AgentOrch 自有本机数据边界，并在 #153 落地后按其 ignore / 更新契约执行；不能假定目前所有下游项目已经采用该布局。

## 8. Bundle 与发布版本

### 8.1 独立 bundle

建议的源文件结构：

    release/dsh/
      README.md
      package/
        package.json
        cordis.patch.yml
        index.js
        lib/
        locale/
        skills/agentorch/SKILL.md
        LICENSE
      tests/
        package.test.mjs
        tools.test.mjs
        fixtures/

准确文件名和依赖须以 #163 的真实 DSH API 验证为准；以上是职责示意，不是声明文件已存在。

### 8.2 版本 / 资产规则

- 由独立的 scripts/build_dsh_release.py 构建 DSH 包；不把 DSH 源码或 .tgz 纳入现有 Kit ZIP 的 REQUIRED 列表。
- 同一 AgentOrch Release 下，Kit 资产与 DSH 资产共用 release version 和 Git tag，例如 AgentOrch-<version>.zip 与 AgentOrch-<version>-dsh.tgz。示例只说明命名形态，不代表当前已有该资产。
- 不创设一个独立的 -dsh 语义化版本线或另一个 Git tag；Release 版本 / Format 版本仍遵循当前 VERSION.md 与 release/README.md 的权威规则。
- 首版只作为 GitHub Release 第二资产发布，不发布公共 npm 包。
- builder 必须以固定排序、归档时间戳、文件权限和工具链，测试同一 commit / 版本构建结果的 SHA-256 可重现。不能预先宣称 tar / pnpm 在所有平台上均确定。
- release/*.tgz 加入 .gitignore，但必须保留 release/dsh/ 中的源文件、测试与 README。
- DSH bundle 必须包含受支持的 DSH engine / API 范围声明；范围最多覆盖已经实际验证的 DSH 发布版。不能因为“兼容 semver”就声明后续版本受支持。

### 8.3 安装与更新的边界

DSH 插件管理器的安装或 bundle 操作可能触发依赖安装 / 构建脚本，因此正式安装前的审批、权限与来源校验必须清楚呈现。AO 适配器不得偷偷安装自身、修改用户 profile 或覆盖其他插件。更新、卸载、失败恢复和回滚必须使用 DSH 正式支持的机制，并在 #167 的隔离 profile 上实测。

## 9. 测试与发布验收

### 9.1 自动化测试

常规 CI 使用确定性 fixture 和临时目录，不访问真实用户 profile、用户 .local、凭据、真实任务账本或付费模型 API。至少覆盖：

- 固定三工具名称、输入 / 输出 schema、错误码与敏感输出脱敏；
- 正确角色可用、错角色不可见 / 不可调用、身份缺失时拒绝；
- Root 正确解析、多个候选根、无标记、嵌套目标、路径穿越与软链接异常；
- local / github edition 选择，github 后端不可用时不能写 SQLite；
- 缺文件、哈希被修改、用户本地修改、过期版本、未支持 DSH 版本；
- 超时、异常、任务状态非法、依赖未满足、证据过期、状态提交失败；
- bundle 文件集合、无私密数据、可重复构建 SHA-256、安装 / 更新 / 回滚；
- 现有 Kit 的完整验证和发行自检均保留原强度。

### 9.2 真正的 DSH 验证

Node 单测、mock tool registry 或 YAML 结构检查不能证明真实 DSH 能加载 bundle，也不能证明 per-agent 工具隔离。至少要在临时测试 profile / 确切 DSH 版本上实际执行：

1. 安装或加载 bundle；
2. 检查三个指定角色看到的工具清单；
3. 分别调用对应工具与跨角色的负向调用；
4. 验证调用上下文中的工作目录与身份；
5. 验证结果和任务状态是否写入正确载体；
6. 完整清理临时 profile，并记录版本 / 命令 / 退出码 / 观察结果。

任何未在本轮真实执行的 Desktop、平台、profile / engine 版本须明确为 NOT_RUN 或 UNKNOWN。不得用 stub 结果代替真实运行时证明。

### 9.3 发布权限与验收

- CI 只验证和构建测试资产，不自动创建 Tag / Release，不使用用户凭据，不上传正式资产。
- Master 在当前主分支 head、任务证据门和发布授权均通过后，人工创建同一 Tag 的 Release，并上传 Kit ZIP 与 DSH .tgz 两个资产。
- 发布后分别下载资产并复核 SHA-256、安装测试与包内文件集合；更新 release/README.md 的已验证事实、未验证平台和兼容范围。
- 发行验收不能以所有 Issue 已关闭为唯一标准。若真实 DSH 安装或角色隔离阻塞，必须阻止声称“已支持”。

## 10. 阶段依赖与执行顺序

| Issue | 交付 | 依赖 / 门禁（硬依赖须 VERIFIED；路径顺序另见 §10.1） |
|---|---|---|
| [#161](https://github.com/ctrlshiftaltdel/AgentOrch/issues/161) | 本设计冻结 | Epic #160；设计 PR 审查、合并并 VERIFIED |
| [#162](https://github.com/ctrlshiftaltdel/AgentOrch/issues/162) | 登记唯一阶段计划 | **硬依赖 #161 与 #149**；#149 对 `docs/implementation-plan.md` 的登记先完成，才轮到 #162 编辑同一文件 |
| [#163](https://github.com/ctrlshiftaltdel/AgentOrch/issues/163) | DSH API、角色 scope、身份 / 工作目录上下文与兼容范围实测 | #161 完成 |
| [#164](https://github.com/ctrlshiftaltdel/AgentOrch/issues/164) | 三个角色工具和安全适配器 | #161、#162、#163 完成；涉及根布局时遵守 #151/#153 的真实状态 |
| [#165](https://github.com/ctrlshiftaltdel/AgentOrch/issues/165) | 独立、可信、可复现的 DSH bundle builder | #161、#162、#163 完成 |
| [#166](https://github.com/ctrlshiftaltdel/AgentOrch/issues/166) | DSH CI、Release 双资产流程与文档 | **硬依赖 #161、#163、#165 完成并 VERIFIED** |
| [#167](https://github.com/ctrlshiftaltdel/AgentOrch/issues/167) | 隔离、迁移、安装、回滚与实际 DSH E2E 验收 | #162–#166 完成；治理依赖达到可验证状态 |

### 10.1 计划文件路径顺序与治理依赖

`docs/implementation-plan.md` 是阶段顺序 / 状态的唯一权威，因此对它的编辑必须串行。以下四项是当前确认的近期路径顺序，不是穷尽的后续任务清单：

1. **#149** — 先登记治理与 Agent 表现评价阶段（#149 自身以 #147 / PR #148 为设计门禁，并按自己的验收标准完成）。
2. **#162** — 再登记 DSH 角色工具阶段；硬依赖 #161 与 #149，必须在 #149 完成同一文件的登记之后执行。
3. **#151** — 随后把核心契约迁移到 `docs/orchestration.md`；迁移时**必须保留 DSH 阶段登记段**（#151 自身另有 #150、#169 等实现前置，按其 Issue 核对）。
4. **#152** — 最后精简计划；精简**必须保留 DSH 阶段登记与依赖**，不得以「清理过期规划」为由删除。

即计划文件的路径序列是 **#149 → #162 → #151 → #152**。其中 #162 与 #151 / #152 是同一文件的路径顺序，不是可并行或可倒序的独立任务；后续任务都从最新主分支重验。此后任何获授权的相关编辑——包括 #158 的最终状态更新，以及仅在单独授权时才发生的 #153 编辑——仍须从最新 main 重验并串行执行，在共享路径上与其他任务协调；本段不新增硬依赖，也不表示上述任一任务已完成。

治理侧的其它已核验顺序（不改变本阶段 DAG，但会与本阶段任务路径相交）：

- **#152 → #153**：#153 依赖 #150–#152，且与 #152 共享 `docs/README.md`，必须先完成计划 / 导航清理，再串行加入旧项目安装说明。
- **#153、#154 不是 #162 的登记前置**：#153 是 #164 / #167 涉及目标 `agentorch/` 布局时的实现前置，#154 是后续文档一致性门禁；#162 只按 #161 与 #149 判定，不得因 #153 / #154 未完成而阻塞登记，也不得把二者写成已完成。
- **#158 等待 #73 VERIFIED 与设计门禁 #147 / PR #148**：#73 与 #158 修改同一场景 runner / 场景注册与文档路径，必须先完成场景拆分，再做全阶段回归；按 #158 的依赖，设计门禁 #147 / PR #148、阶段计划 #149、#73 VERIFIED 以及 #153–#157 均须在其进入 READY 前完成并 VERIFIED。以上均为待完成前置，本设计不把它们写成已完成。

路径冲突必须按当前 AgentOrch 的任务锁 / 依赖政策协调。尤其不得在多个 Issue 中并发修改 release/README.md、build_release / validate_kit 相关路径或同一份 DSH package 入口。Issue 创建只代表任务登记；所有实现任务在其依赖及设计 / 计划门禁满足前都不能进入 READY，本表不把任何未验证任务写成已完成。

## 11. 风险、未知项与冻结条件

| 风险 / 未知项 | 当前处置 |
|---|---|
| DSH 版本快速变化，快照时点的官方最新条目仍为预发布版（见 §2.1，查询时间 2026-10-28T07:37:16Z） | 锁定具体测试版并重新查询 tag；#163 实测完成前不宣称兼容范围 |
| per-agent 工具隔离或调用者身份 API 不足 | 阻塞实现，不退回全局工具暴露 |
| 不能获取可信会话工作目录 | 不猜 process.cwd()；无法绑定项目根则返回 ROOT_UNKNOWN |
| 只依赖下游 MANIFEST.json | 不接受；使用 bundle 中由可信发行资产建立的 SHA 基线 |
| GitHub edition 缺少执行后端 | 只读 / NEEDS_HANDOFF，不写本地 SQLite |
| #151 / #153 仍在进行或尚未 VERIFIED | 仅支持已实测布局；目标布局明确标注为待实现 |
| 安装脚本会执行本地构建 | 在独立 profile 先验证来源和脚本风险，不静默自动安装 |
| 同一工具被错误角色调用 | scope 限制与角色守卫双层拒绝，并有负向实测 |

本设计只有在以下问题明确并经过独立审查后才能从 PROPOSED 变为冻结状态：

- 三个角色工具的 API 名称和职责边界固定；
- DSH 版本 / Agent identity / per-agent tool scope / working-directory API 的证据与未知项被准确记录；
- Root / edition / trusted hash / update / release 的安全模型可测试；
- #162–#167 的依赖、允许路径、禁改路径、验收证据与回归门齐备。

## 12. 文档影响

- 本设计文档：设计已冻结（`FROZEN`）；不代表 DSH runtime 已实现、已安装或兼容，真实 API、scope、identity、工作目录、安装及 E2E 验证仍由 #163 / #167 把关。
- docs/implementation-plan.md：由 #162 更新；本设计 PR 不修改。
- README.md / docs/README.md / release/README.md：待 #166 依据已实现行为与真实测试结果更新。
- scripts/validate_kit.py、scripts/build_release.py、VERSION.md：本设计 PR 不修改。
- docs/PLAN.md、AGENTS.md、kit/orchestration.md：本设计 PR 不修改。
