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

以下任一情况必须进入 `NEEDS_HUMAN_DECISION`，决策 ID 只引用 [`entrypoints-and-decisions.md`](entrypoints-and-decisions.md) §5：

- 权限 / 安全边界、锁 / 状态机、数据迁移、外部依赖、隐私、破坏兼容、不可逆动作 → `HD-05`（契约 / schema 级改为 `HD-13`）。
- Lesson 冲突无法靠证据解除 → `HD-04`。
- 把 Lesson 提升为通用规则且影响上述边界 → `HD-03`。
- 来源访问授权问题 → `HD-01` / `HD-02`。

`planning.py` 的 `assess_risk()` 对允许路径与 Lesson 文本做保守判定；命中即返回 `NEEDS_HUMAN_DECISION`，**不会**因为调用方传了“已批准”标记而自动放行。决策后重新规划仍是普通任务，不是闭环自动解锁。

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
