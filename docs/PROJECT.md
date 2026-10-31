# docs/PROJECT.md — Per-Project Configuration

> 本文件是本规则包仓库自身的适配层（套件演进项目）。其他项目复用本套件时，应复制本包并按各自情况重写本文件。

## 项目身份

- 项目名称：`Agent-Orchestration Starter Kit（离线单机版）`
- 一句话目标：维护一套可被任意项目复用的单机 Agent 编排规则包：任务状态、路径锁、角色契约与启动/执行/审查方法论。
- 现有项目 / 新项目：现有项目（本仓库即规则包本体）
- 本地主分支：`main`
- 语言 / 框架 / 运行方式：Python 3 标准库（scripts/）；Markdown 规则与提示词文档；无运行时服务。
- 重要设计 / 需求文件：`docs/charter.md`（演进章程）、`AGENTS.md`（权威契约）

## 范围与安全

- 允许改动路径：`AGENTS.md`、`LICENSE.md`、`VERSION.md`、`docs/orchestration.md`、`editions/`、`templates/`、`docs/`、`README.md`（docs 内）、`scripts/validate_kit.py`、`scripts/build_release.py`、`scripts/check_drift.py`、`scripts/report_metrics.py`、`scripts/run_delivery_scenarios.py`、`tests/`、`.github/`、`release/README.md`（`release/*.zip` 除外）
- 永远禁止改动路径 / 文件：`LICENSE.md`。`.orchestration/` 不是可提交源码：禁止手工编辑、替换或删除其中的状态库、配置与备份；禁止绕过 `scripts/orchestrator.py` 写任务状态；禁止把该目录提交或打进发行包。唯一受控例外是下方「维护写入边界」。未经授权不得改动 `scripts/orchestrator.py` 的状态机与锁语义。
- 授权记录（2026-10-12）：授权人=项目所有者；授权范围=该轮质量修复；限缩说明=本次授权仅覆盖当轮，不改变后续任务的默认边界。
- 秘密与隐私文件模式：`.env*`、`*token*`、`*secret*`、个人绝对路径
- 破坏性操作和迁移政策：禁止对 `.orchestration/state.sqlite3` 做手工 DELETE/UPDATE 绕过 CLI；状态变更只经 `orchestrator.py`
- 维护写入边界（2026-10-30 项目所有者批准，记录于 Issue #204；仅当本段已在合并目标 `main` 上时生效）：只允许既有命令 `python scripts/orchestrator.py backup`。前提是 `.orchestration/state.sqlite3` 已存在；缺库必须拒绝，不得 `init`，不得留下伪成功文件。效果只是调用该既有命令，在 `.orchestration/backups/` 写入一致性副本。该命令以秒为粒度命名；同秒同名文件已存在时，现有实现会覆盖该文件内容。这种覆盖是该命令的既有效果，不是另授的替换权或清理权，本边界也不声称实现已防止覆盖、并发碰撞或时钟回拨。不授权手工 DML、替换或删除源库、清理旧备份、初始化缺失库，或把 `github` 项目双写成 `local` 调度。副本不提交。不授权手工删除既有备份。源库逻辑表与行不得因该命令改变。
- 新依赖政策：禁止引入任何第三方依赖（保持纯标准库，保证可移植）
- 需要人工批准的变化：角色权限模型改动、AGENTS.md 契约语义变化、破坏 CLI 兼容性的改动

## 验证命令

每条命令已在本机真实探测（Python 路径按机器调整；文档中以 `python` 指代）。

- 快速测试：`python scripts/validate_kit.py`
- 完整回归：`python -m unittest discover -s tests`
- 静态检查 / 类型检查：`python -m py_compile scripts/orchestrator.py scripts/validate_kit.py`（零依赖语法检查；不引入第三方依赖）
- 构建 / 打包：不适用（无构建产物）
- 手工 / UI 冒烟检查：按新提示词走一遍空目录启动冒烟
- 合并后必须执行：快速测试 + 完整回归 + `python scripts/orchestrator.py backup` + 静态检查。`backup` 只按「维护写入边界」执行；该边界未出现在合并目标 `main` 上时，不得用待合并文本给当次合并授权。
- 合并后验证 profile（2026-10-31 项目所有者授权，Issue #188）：每个 HEAD 的 CI 全套仍必须真实通过；默认集合仍是快速测试 + 完整回归 + `backup` + 静态检查。唯一授权减项仅当 `verification_profile` 精确等于 `risk_pilot` 且调用者用 `python scripts/verification_profile.py --evidence <json>` 显式证明变更为 L0 时生效：此时合并后集合可以不含 `python -m unittest discover -s tests`，但仍必须含 `python scripts/validate_kit.py`、`python -m py_compile scripts/orchestrator.py scripts/validate_kit.py` 与 `python scripts/orchestrator.py backup`。
- 上述减项的关闭条件：证明缺失或不可判定、`executable_doc` / `policy` / `contract` / `uncertainty_l3` 任一为真，或任一路径命中 `scripts/orchestrator.py`、`docs/orchestration.md`、`kit/`、`.github/workflows/`、`docs/PROJECT.md`、`docs/PLAN.md`、`AGENTS.md`、`editions/`、`scripts/`、`release/`、`templates/`、`VERSION.md` 时，仍执行完整回归。L1 / L2 / L3、发布、安全、权限、身份、迁移、锁与状态机相关检查一律不减。
- 回退：把 `verification_profile` 改回 `strict`（或删除该键）即恢复唯一的默认完整集合，不需要改代码。

## 运行配置

本文件的 `edition` 键是底座选择的**唯一权威来源**；`.orchestration/config.json` 只描述本地 CLI 运行参数（`retry_cap`、`local_dir`），不参与底座选择，也不存在“缺省底座”。

- `edition`：`github`
- `verification_profile`：`risk_pilot`
- 上面 `verification_profile` 的回退：改回 `strict` 或删除该键（2026-10-31 起，Issue #188）。
- （可选）`architecture_contract_path`：架构契约文件路径，默认 `docs/architecture/contract.json`。**不配置等价于 `OFF`（只读诊断，不阻断）**；配置了但文件缺失属于配置错误，检查器失败关闭。检查器与能力边界见 `docs/self-improvement/architecture-checker.md`。
- 本地 CLI 边界：`scripts/orchestrator.py` 只维护 `local` 任务库。声明 `github` 时，`task add` / `task ready` / `task dispatch` / `task status` / `task workspace` / `task amend` / `task unlock` 会拒绝执行并给出下一步指引；`init`、`backup`、`config show`、`task list`、`task show` 仍可用。`edition` 缺失、为空、非法字符串、类型错误或文件不可读时同样拒绝调度，并报出实际值与修复位置（本文件 + 键名）。完整矩阵见 `README.md` 6.2。
- 并发策略：**按机器余量动态决定，不设固定上限**。派发前观察 CPU / 内存 / 磁盘 / 测试资源争用，有余量即可并行；同一 worktree 同一时刻只允许一个测试进程。把「本机实测可承载并发」作为参考记录，而不是规则常量。
- 本地私有目录：`.local/`（`.env`、机器信息、发布扫描规则），永不入库、永不打包。

## 底座切换记录

- 2026-10-22：`local` → `github`（一次性、单向；切换后只写 Issue / PR，不双写）。
  - 切换点：`main` @ `60252f2805cfd9a7f04e99732711467a79c2ac3f`。
  - 决定与执行：项目所有者在 Issue #4 决定；Master 执行切换，具体改动由 Issue #4 的任务分支 `task/4-edition-authority` 的 PR 落地。
  - 本地任务库归档：`.orchestration/state.sqlite3` 转为**只读归档**，不再作为活动任务状态写入，且不得手工修改（T-701…T-707 共 7 个任务全部 `VERIFIED`，无活动锁）；切换点之后新建或推进的任务状态只记在 Issue / PR 上。一致性副本只按「维护写入边界」写入 `.orchestration/backups/`，不改变归档库的活动状态语义。
  - 归档备份：`.orchestration/backups/state-20261022-173524.sqlite3`；`python scripts/orchestrator.py backup` 仍可用于本地库维护。该用途受「维护写入边界」约束，不另开写入权。
  - 回退：本套件不提供自动回退；如需回退由项目所有者单独立项。

## 本地工作流

- 分支命名：`task/<ID>-<short-name>`
- Worktree 根目录：`.worktrees/`
- 工作目录要求：一个执行实例一个 Worktree；不共享可写工作区。
- 默认审查修复上限：`3`（只有用户显式修改此值才可改变）
- 本地证据保留方式：`.orchestration/`；日志中需脱敏。
- 备份策略：每阶段结束与每日收尾运行 `python scripts/orchestrator.py backup`，备份保留最近 5 份。命令边界见「维护写入边界」；「保留最近 5 份」不是删除或清理授权。

## 通用性要求（本包特有约束）

本仓库的所有 `prompts/`、`templates/` 与规则文档是**可复用产出**：内容必须保持项目无关（不引用本仓库特有的路径、任务 ID 或某次演进决策）；演进决策与背景只写入 `docs/charter.md` 与任务记录。示例中的命令用 `python` 指代解释器路径。

## 新项目启动产物

最少应建立：项目章程 / 范围与非目标、关键假设、架构决策记录（若适用）、目录结构、首个可运行垂直切片、验证方式和后续任务路线。按项目规模调整；不要求为了模板完整而过度文档化。

计划分两层：本仓库的 `docs/PLAN.md` 是**设计目标权威**（为什么做、做到什么程度、原则与边界；1.4 起从根目录迁入），`templates/plan.md` 只用于初始化复制；阶段目标、任务顺序与状态在 [`docs/implementation-plan.md`](implementation-plan.md)，它是阶段计划权威。两者冲突时以 `docs/PLAN.md` 的设计目标为准，停下对齐，不得各写一套。
