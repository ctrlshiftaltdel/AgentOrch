# “进行自我完善”闭环（#50 实现记录）

> **定位**：本文件说明入口与闭环**怎么做**：触发边界、恢复、规划、派发、审查和报告。
> 任务状态机、路径 / 资源锁、证据矩阵与合并门 G1–G6 **只在** [`docs/orchestration.md`](../orchestration.md) 定义；字段名、取值与判定规则只在其中 4.1 节定义。本文件只引用章节，不复制、不改写。
> **前置契约**：触发语与人工决策目录 [`entrypoints-and-decisions.md`](entrypoints-and-decisions.md)，来源注册表与信任模型 [`sources-and-trust.md`](sources-and-trust.md)，Lesson 生命周期与去重 [`lesson-lifecycle.md`](lesson-lifecycle.md)。本任务没有修改它们的语义。
> **包集成**：发行包 `REQUIRED` 清单、`scripts/build_release.py`、CI 与 `docs/README.md` 计数由 #52 统一接入；运行脚本、模板与随包测试已进入 `REQUIRED`，`docs/` 除核心契约 `docs/orchestration.md` 外仍不随包发布。包内操作说明见 [`templates/self-improvement/README.md`](../../templates/self-improvement/README.md)。

## 1. 触发与不触发

### 1.1 进入闭环的表达

以下表达（大小写、空格、标点无关）在**有明确执行意图**时进入本闭环：

- `进行自我完善` / `自我完善`（单独的名词短语先问一次，不直接开工）
- `根据其他 AgentOrch 项目的经验改进本项目`
- `总结近期开发教训并推进改进`

`scripts/self_improvement/planning.py` 的 `route_request()` 是这条规则的实现：它返回 `SELF_IMPROVEMENT`、`UPDATE_AGENTORCH`、`EXPLAIN_ONLY` 或 `UNRELATED`，只有前者的 `starts_workflow` 为真才继续。

### 1.2 不触发

- 解释型提问（“自我完善机制是什么”“自我完善的原理是什么”“能介绍一下流程吗”）只回答，不启动流程，也不创建任务。
- 普通工作指令（“更新一下文档”“更新依赖”“更新这个 Issue”）不触发本闭环。
- `更新 AgentOrch` / `升级 AgentOrch` / `把项目中的 AgentOrch 更新到最新版` 属于**更新引擎**，不在本闭环；仅询问版本号只回答。按 [`entrypoints-and-decisions.md`](entrypoints-and-decisions.md) §1.2，歧义时先问一次“是更新套件本身还是更新项目内容”，不得默认执行。
- 与当前项目无关的讨论只回答，不落地改动。

### 1.3 边界与限制

路由是**确定性的短语 / 动词表**，不是语言模型。它让误触发可以被测试发现，但不能覆盖所有自然语言表述；真实会话中的措辞稳定性仍是未验证假设，见 §8。

触发语**不等于授权**：它只表示“启动完整工作流”，不表示批准任何具体改动，也不改变 `docs/PROJECT.md` 的 `edition`。

## 2. 闭环步骤

每一步都留下可定位证据；没有证据的步骤按未完成处理，不得写成已完成。

### 2.1 恢复真实状态

- 读 `docs/PROJECT.md` 的 `edition`，按 `editions/<edition>/` 的提示词操作。
- 运行 `git status --short`、`git branch --show-current`、`git log -1 --oneline`，记录当前完整 head SHA。
- 按底座恢复任务载体：`local` 用 `python scripts/orchestrator.py task list`；`github` 用 `gh issue list --state open --limit 100`、`gh pr list --state open --limit 100`，并读 `ORCH-TASK-REGISTRY` 登记块与开放 PR 的 changed files。
- 缺少 head SHA、分支或 edition 不一致时停止（`BLOCKED`），不从聊天记忆补齐。

### 2.2 读取来源增量

按 [`sources-and-trust.md`](sources-and-trust.md) 加载注册表，只读采集；空注册表 → “无来源可扫描”，配置非法 → 失败关闭。采集与窗口语义由 #49 的 `sources` / `collect` / `pipeline` 实现，本闭环不新增来源、不改游标语义。

### 2.3 验证、去重与覆盖检查

- 只有 `GENERAL_LESSON` + `GENERALIZED` + 无 `OPEN` 冲突的 Lesson 允许生成任务（[`lesson-lifecycle.md`](lesson-lifecycle.md) §7）。
- `UNVERIFIED_ASSUMPTION`、重复 Lesson（`duplicate_of`）、已被现有契约 / 代码 / 测试覆盖的 Lesson 不生成新任务；只记录证据或建议补测试。
- `OPEN` 冲突（`CONTRADICTS` / `SUPERSEDES` / `OVERLAPS`）进入 `HD-04`；不得自动选边。
- 覆盖检查要写清对照对象（`docs/orchestration.md` 章节、源码路径、测试名），不能只写“已覆盖”。

### 2.4 成本收益与方案

每个候选至少给出两个选项（不做 / 缩小范围 / 完整改），并写目标、非目标、根因、收益、成本、风险、回滚方式和允许路径。方案落在 [`proposal.md`](../../templates/self-improvement/proposal.md)。

### 2.5 生成任务包

任务包必须含：单一目标、依赖、允许 / 禁改路径、逐条验收（每条带验证命令）、`negative_cases`、`migration_cases`、`integration_paths`、`unverified_assumptions`、验证命令与证据矩阵占位。字段名与判定规则见 `docs/orchestration.md` 4.1；模板见 [`task-packet.md`](../../templates/self-improvement/task-packet.md)。

实现里由 `scripts/self_improvement/planning.py` 的 `plan_tasks()` 把关：缺字段、缺 AC 验证、裸 `N/A`、依赖未 `VERIFIED`、允许路径过宽都会得到 `BLOCKED` / `NO_TASK`，而不是被自动补全。

### 2.6 按锁与依赖派发

- 依赖必须先 `VERIFIED`；路径 / 资源锁按 `docs/orchestration.md` §3 取得（`local` 原子路径锁，`github` 登记块 + 开放 PR 冲突检查）。
- `local` 只写任务库（`task add` / `ready` / `dispatch`）；`github` 只写 Issue / PR。**不双写**，也不把本地 CLI 当成 GitHub 调度。
- 同一时刻只有一个 Master 派发；没有真实 spawn / wait / collect（C1–C4）时降级为人工交接，不声称已派发。

### 2.7 独立审查与当前 head 验证

- Master 按 G1–G6 逐门审查，不采信执行者自述，不把 CI 绿灯当作逐条验收。
- 任何 head 变化使既有批准失效；必须在新 head 上重新运行配置的检查并取证。
- 执行者不自批、不自合、不关闭 Issue、不声明 `VERIFIED`。

### 2.8 闭环报告

报告模板见 [`closure-report.md`](../../templates/self-improvement/closure-report.md)。必须包含：触发与 edition、head SHA 与工作区、来源 / 游标 / 窗口 / 过滤计数、逐来源扫描结论、候选与采纳、覆盖检查、生成的任务与依赖、阻塞、`HD-*` 决策与选项、未执行项（`NOT_RUN` / `BLOCKED` / `UNKNOWN`）、证据链接与残余风险。

`scripts/self_improvement/orchestration.py` 的 `run_loop()` 生成该报告：它把 `scan` 的结论分成 `NO_SOURCES` / `NOT_RUN` / `INCOMPLETE` / `NO_NEW_CANDIDATES` / `CANDIDATES_FOUND`，未完成扫描不会被写成“无新候选”；未传入结果的验证命令一律标 `NOT_RUN`。

## 3. 无新证据 / 不完整扫描

| 观察 | 报告结论 | 是否创建任务 |
|---|---|---|
| 注册表为空 | 无来源可扫描 | 否 |
| 扫描达到范围且没有候选 | 无新候选（附窗口、游标、过滤计数） | 否 |
| 只有 `UNVERIFIED_ASSUMPTION` | 候选记录，不采纳 | 否 |
| 任一来源 `BLOCKED` / `UNKNOWN` / `NOT_RUN` 且没有来源达到 `OK` / `PARTIAL` | 未完成扫描，列出原因与未覆盖范围 | 否 |
| 有候选但全部不合格（重复 / 已覆盖 / 缺证据） | 无可执行候选 | 否 |

## 4. 高风险与人工决策门

以下任一情况在真实 Git 证据支持时进入 `NEEDS_HUMAN_DECISION`，决策 ID 只引用 [`entrypoints-and-decisions.md`](entrypoints-and-decisions.md) §5：

- 规划器**识别为**改变权限 / 安全边界、锁 / 状态机、数据迁移、外部依赖、隐私、破坏兼容或不可逆动作的变更 → `HD-05`（契约 / schema 级改为 `HD-13`）。
- 任务目标 / 允许范围本身声明破坏性或不可逆动作（例如「删除全部用户数据」），而当前 diff 无法证明它时同样 `HD-05`；这条只看破坏性措辞，空 diff 不能作为「无风险」的反证。
- 权限 / 安全 / 迁移等一般关键词只是线索，不单独升级：只有当**允许范围本身命中高风险路径**（authority surface、受保护执行面、安全命名的文件名、schema）并同时出现对应关键词时，空 diff 才升级 `HD-05` / `HD-13`。目标写「调整用户权限」、允许 `a.md` 的空提交仍是 `NEEDS_EVIDENCE`，且**没有** decision ID。
- Lesson 冲突无法靠证据解除 → `HD-04`。
- 把 Lesson 提升为通用规则且影响上述边界 → `HD-03`。
- 来源访问授权问题 → `HD-01` / `HD-02`。

**识别能力有限，未命中不等于安全**：`planning.py` 使用固定的路径前缀表和有限关键词 / 文件名正则，不是完整的语义分析。只有命中这些线索时才升级 `HD-05` / `HD-13`；未命中的变更仍然停在 `NEEDS_EVIDENCE` / `BLOCKED`，绝不自动放行。因此「所有权限变化都会 `HD-05`」是错误描述。具体边界：

- 只有 `AGENTS.md`、`docs/orchestration.md`、`kit/orchestration.md`、`editions/`、`docs/contracts/`、`docs/design/` 这类 authority surface 上的权限 / 安全措辞变化才映射到 `HD-05`；
- 普通文档（例如 `docs/help.md`）里出现「permission」等词不自动判为 `HD-05`，而是作为内容变化进入 `NEEDS_EVIDENCE` / `BLOCKED`，等待审查；
- 命中受保护执行面（`scripts/orchestrator.py`、`.orchestration/`、`.github/workflows/`、`release/`）、schema、可执行位变化、不可逆操作的变更进入 `HD-05` / `HD-13`。

`planning.py` 的 `assess_risk()` 只用仓库自己解析的 Git 证据，并把调用者提供的 `base_sha` / `head_sha` 参数与 `git_evidence` 交叉校验。**这些参数只是定位符和一致性检查，不是权威任务载体认证**：真实适配器未接入，任何调用者都可以传相同的字符串。规划器验证两个提交存在、base 是 head 的祖先、`HEAD` 与 head 一致、工作树干净且不是浅克隆，然后用 Git 生成完整 raw / numstat diff。它还会：

- 屏蔽 Git replace refs，并覆盖 `diff.ignoreSubmodules`、`core.fileMode`、`core.symlinks`、`core.ignoreCase`、external diff / textconv / 系统与全局配置等可隐藏 changed paths 的配置与环境；
- **在任何 Git 命令之前**用纯文件读取检查仓库本地配置与 attributes：若定义了 `filter.<driver>.clean` / `.process`、`include` / `includeIf`，或 attributes 声明了 `filter=`，则无法在不执行仓库命令的情况下隔离，直接失败关闭并标明未实现；同样的检查覆盖工作树内的**子模块 / 嵌套仓库**（父仓库的 `git status` 会进入已初始化子模块并运行它自己的 clean filter），以及会让只读命令发起 fetch / remote helper 的 **promisor / partial-clone remote、`ext::` transport、`objects/info/http-alternates`**。`core.fsmonitor` 与 hooks 通过 `-c core.fsmonitor=false` / `-c core.hooksPath=<空>` 显式禁用，lazy fetch 通过 `GIT_NO_LAZY_FETCH=1` 关闭；每条 Git 命令（包括公开的 diff 摘要函数）启动前都重复该预检；
- 拒绝带 assume-unchanged / skip-worktree 的索引、未提交 / 未跟踪工作树、以及落在 `allowed_paths` 内的 ignored 文件；
- 记录 Git 快照，并在登记 / 交付前复查；快照变化即阻塞。

调用者提供的 Diff、`changed_paths`、40 位 SHA 字符串、`low_risk` / `approved` 布尔值、spec 内 `review` 或 `authorization` 字段都不是证据；伪造或截断的 Diff 不能缩小真实 changed paths。`allowed_paths` 中的绝对路径、反斜杠、通配符和任何 `..` 段在任何证据之前直接拒绝，不能被其他合法路径或 Diff 冲掉。

空 diff 不能证明拟实施目标、允许范围或授权安全，因此**没有自动 `LOW`**：空 diff / 空 tree、纯空白、Markdown 代码块 / 命令、普通文档、代码和治理面一样，都不凭后缀或空白自动放行。任何非空内容变化都保持 `NEEDS_EVIDENCE` / `BLOCKED`，并写出结构化 `required_evidence` 与 `next_action`。因此本切片的规划阶段不会产出 `READY`（`LOW` 保留给接入后的取证路径）：任务包完整性、依赖与 `required_evidence` 照常记录，登记必须等权威载体适配器与独立审查接入。实际行为与文档逐项对齐：

| 输入 | 实际结果 |
|---|---|
| 空 diff / 空 tree，且目标与允许范围无可识别高风险线索 | `NEEDS_EVIDENCE` → `BLOCKED`（不产出 `READY`） |
| 空 diff / 空 tree，目标声明破坏性或不可逆动作（例如「删除全部用户数据」） | `HIGH` → `NEEDS_HUMAN_DECISION` / `HD-05` |
| 空 diff / 空 tree，目标只提到权限 / 安全 / 迁移等一般关键词，允许范围未命中高风险路径（例如「调整用户权限」+ `a.md`） | `NEEDS_EVIDENCE` → `BLOCKED`，内容线索待审，**无 decision ID** |
| 空 diff / 空 tree，允许范围命中高风险路径并伴随对应关键词 | `HIGH` → `HD-05`（契约 / schema 级 `HD-13`） |
| 普通文档（非 authority surface）文本变化，即使含 permission / auth 等词 | `NEEDS_EVIDENCE` → `BLOCKED`；不自动升级 `HD-05` |
| authority surface 上命中权限 / 安全 / 不可逆关键词 | `HIGH` → `HD-05`（契约 / schema 级 `HD-13`） |
| 非空代码 / 文档 / 治理面变化，未命中高风险线索 | `NEEDS_EVIDENCE` → `BLOCKED`，等待真实独立审查 |
| Git 证据缺失、伪造、不可验证、HEAD 不符、dirty / untracked / hidden-index、binary / rename / symlink / submodule / deletion | `NEEDS_EVIDENCE` → `BLOCKED`，写出 `required_evidence` |
| 仓库（含子模块 / 嵌套仓库）本地配置定义 clean / process filter 或 include / includeIf，或声明 promisor / partial-clone remote、`ext::` transport、HTTP alternate | 任何 Git 命令前失败关闭：`NEEDS_EVIDENCE` / `BLOCKED`，明确标注未实现（不执行仓库命令、零 Git 进程） |

**当前切片没有接入权威任务载体适配器或真实独立审查**：`state.task` 的 task id 与调用者传入的 `base_sha` / `head_sha` 都只是定位符和一致性交叉校验，不是认证；spec / `state.task` / 调用者提供的 review、authorization 或自算摘要都不会触发 READY。`review_status` 明确为 `NOT_WIRED`。未来路径必须由独立适配器从 local SQLite / GitHub 认证 PR Review / Owner 权限取证，并绑定 task id、目标与 allowed-path hash、base/head、完整 diff、reviewer 身份/角色/独立性与有效期/撤销；在这条链路实现前，普通错字也只能停在 `NEEDS_EVIDENCE`，不能声称已自动推进。被识别的治理面及权限 / 安全 / 锁 / 状态机 / 数据 / 外部 / 不可逆变化升级 `NEEDS_HUMAN_DECISION`；未被识别的同类变化不会被放行，只会停在 `NEEDS_EVIDENCE` / `BLOCKED`。二进制、rename / copy / type change、symlink、submodule、删除、dirty / untracked / hidden-index 工作树、缺失或不可验证的提交都显式阻塞。两种任务载体 payload 记录 Git base/head、diff SHA-256、changed paths、review status 与 next_action，但记录的是待取证状态，不是授权。

**常规低风险工作也不得绕过 Master 审查、证据矩阵或合并门。**

## 5. 两个底座

| 环节 | `local` | `github` |
|---|---|---|
| 恢复状态 | `task list` + Git | `gh issue/pr list` + 登记块 + Git |
| 任务载体 | `.orchestration/state.sqlite3`（只经 `orchestrator.py`） | Issue 正文 + 标签 + `ORCH-TASK-REGISTRY` |
| 派发锁 | `task dispatch` 原子路径锁 | 登记块 + 开放 PR 同文件检查（**不是**原子锁） |
| 实现可见性 | 分支 / Worktree | Draft PR（`PR_OPEN`） |
| 证据 | 本地命令输出 + head SHA | CI + PR 评论 + head SHA |
| 闭环报告位置 | 任务记录 / 本地报告文件 | Issue / PR 评论 |

两边的**门禁语义相同**：同一套 4.1 字段、同一组 G1–G6、同一套人工决策门。差异只在写入位置和取回方式；`local` 不得假装有 GitHub 自动化，`github` 不得临时改用本地任务库。

`orchestration.py` 只接受一个与 `edition` 匹配的 carrier；carrier 不匹配直接报错，不会出现一次运行写两个载体。

## 6. 实现与测试

| 路径 | 职责 |
|---|---|
| [`scripts/self_improvement/planning.py`](../../scripts/self_improvement/planning.py) | 触发路由、风险分类、提案门禁、任务包结构校验与渲染 |
| [`scripts/self_improvement/orchestration.py`](../../scripts/self_improvement/orchestration.py) | 一次闭环：恢复校验 → 扫描结论 → 规划 → 单一 carrier 登记 → 闭环报告；两个内置 carrier 为 dry-run（`executed: false`） |
| [`tests/self_improvement/test_routing.py`](../../tests/self_improvement/test_routing.py) | 触发 / 同义表述 / 普通提问 / `更新 AgentOrch` 的路由 |
| [`tests/self_improvement/test_planning.py`](../../tests/self_improvement/test_planning.py) | 无证据、重复、冲突、已覆盖、高风险、缺字段与完整任务包的正负用例 |
| [`tests/self_improvement/test_orchestration.py`](../../tests/self_improvement/test_orchestration.py) | 无新候选 / 未完成扫描 / 双 edition 门禁一致 / 合成端到端（真实 #49 管线 + fake carrier） |

测试不联网、不创建真实 Issue / PR、不运行真实验证命令、不合并；`FakeRunner` 与合成 lesson / scan 数据全部由 `tests/self_improvement/support.py` 与 `loop_support.py` 提供。

## 7. 明确不做

- 不自动采纳 Lesson、不修改 `AGENTS.md` / `docs/orchestration.md` / `editions/` 的权威语义来“吸收教训”。
- 不自动创建 Issue / 任务（实现返回的是可复制 payload 与门禁结果，登记由 Master 在真实载体上执行）。
- 不自动批准、不自动合并、不关闭 Issue、不标 `VERIFIED`。
- 不新增状态、锁、门或证据字段；不修改 #49 的 `sources` / `collect` / `lessons` / `pipeline` 语义。
- 不判断措辞背后的真实意图；模型表述差异留给真实会话验证。

## 8. 未验证假设与限制

1. 自然语言触发在真实会话中的稳定性未被证明；合成测试只验证路由表与报告，不声称覆盖所有说法。
2. `local` 与 `github` 的提示词在同一版本内保持语义一致；需要逐 edition 复核。
3. 真实 Issue 创建权限、速率限制与 `gh` 认证未探测；无权限时必须停止并报告，不假装创建成功。
4. 教训管线能否持续提供足够增量证据未验证；没有证据时闭环降级为“无新候选”，不做猜测。
5. 内置 carrier 是 dry-run：它们不执行命令、不分配真实任务号 / Issue 号；真实登记仍由当前底座的 Master 完成。
