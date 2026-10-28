# Agent-Orchestration — 通用软件项目的启动与推进工具

> 本文件是入口。协作契约与安全规则以 [AGENTS.md](../AGENTS.md) 和 [kit/orchestration.md](../kit/orchestration.md) 为准；本文件不删除、不弱化其中任何规则。

## 1. 一句话定位

**把「需求 → 任务 → 执行 → 独立审查 → 合并 → 验证」固化成一条可执行、可核验的流程。**

它由两部分组成：一份与任务载体解耦的核心契约，以及两个可互换的底座（`local` / `github`）。核心契约规定谁可以做什么、任务如何流转、合并前必须过哪些门、什么才算证据；底座只决定任务状态写到哪里、证据从哪里取。配套的 Python 脚本负责登记任务、占用路径锁、做结构自检与打包，全部只用标准库。

目标是成为**通用软件项目的启动与推进工具**：因此自检覆盖必需文件、底座结构、治理概念与相对链接，配置允许按项目调整范围、验证命令、重试上限与私有目录，而不是把某一类项目的假设写死。

它主要解决四类问题：

- **多 Agent 协作失序**：不清楚谁在做哪个任务、改了哪些文件、两个任务是否改到同一路径。
- **状态丢失**：换会话、换机器或上下文被截断后，只能靠聊天记忆猜进度。
- **自批自合**：执行者自己宣布完成、自己批准、自己合并，审查门形同虚设。
- **证据不可核验**：只说“已完成”，不给出当前 head SHA 上的命令、输出与退出码；或用旧结论、CI 绿灯代替逐条验收。

**适用：**

- 一位操作者或一个小团队，用多个 Agent / 多个会话来推进一个软件项目。
- 希望每个任务都有明确的目标、允许路径、验收标准、依赖与证据。
- 愿意使用 Git 分支 / Worktree，并接受“执行者不自批、合并由 Master 决策”的约束。
- 需要一条从空目录启动、接入已有仓库、或从单机协作升级到平台协作的清晰路径。

**不适用：**

- 需要完整多人权限体系（角色管理、审批链、最小权限、账号隔离）的组织。
- 需要托管服务、SaaS 控制台、跨组织审计或合规认证的场景。
- 希望工具直接启动模型、写代码、跑测试或自动合并——本工具只管理治理状态，不替代 Agent 运行时与 Git 操作。
- 只需要一个看板或待办清单、不需要证据链与合并门的小项目。

## 快速上手（5 分钟）

前提：Git 与 [uv](https://docs.astral.sh/uv/)（本项目统一用 `uv run python` 执行脚本）；脚本只用标准库，**无第三方依赖**；`local` 底座完全离线可用。

**用法一句话：把下面的话术原样复制给你的 Agent（WorkBuddy、Claude Code、Cursor… 都行），它会自己识别要做什么、执行对应命令，并把输出与退出码带回来给你。** 命令不需要你手打；想知道 Agent 到底会跑什么，见第 3 节的「话术 → 命令」对照表。Agent 第一次接入本项目时，先让它读 [kit/orchestration.md](../kit/orchestration.md) 与 [AGENTS.md](../AGENTS.md)——那是它的行为契约，话术只负责"点菜"。

**先判断你处在哪种情况**：

- **情况 A · 新建项目**（空目录 / 刚起步的仓库）：套件文件直接装进项目文件夹即可，按下面第 1–4 步走。
- **情况 B · 旧项目**（已有代码、已有 README / LICENSE / docs / scripts 等的仓库）：**不要把发行包直接解压覆盖进项目根**——那可能盖掉你项目的重要文件。改用下面第 1 步 B 的话术，让 Agent 先读套件方法、盘点冲突、经你确认后引入；完整步骤见第 4 节路径二。

**第 1 步 A · 装进新项目**（先从本仓库 Releases 下载最新的正式发行包解压备好；正式 Release 资产名形如 `AgentOrch-1.7.0.zip`，包内顶层目录同名；日期构建则是 `agentorch-YYYYMMDD.zip`。细节见 3.1）：

> 从 AgentOrch 仓库的 Releases 下载最新的发行包并解压，把包内顶层目录（如 AgentOrch-1.7.0/ 或 agentorch-YYYYMMDD/）的全部内容装进我的项目根（保留 scripts/、editions/、templates/ 的相对路径）。装完列出顶层目录给我确认。

**第 1 步 B · 引入旧项目**（旧项目专用，代替第 1 步 A；发行包解压到项目外的临时目录，细节见第 4 节路径二）：

> 这是把 Agent-Orchestration 套件引入我的旧项目，分四步做，全程不许覆盖我项目的现有文件：
> 1. 先读方法：读 kit/orchestration.md 的会话入口、AGENTS.md、editions/local/master.md 的「已有项目接入」章节，给我用三句话概括这套流程的角色分工与合并门。
> 2. 解压到项目外的临时目录，然后对照发行包文件清单（AGENTS.md、LICENSE.md、VERSION.md、docs/、kit/、editions/、templates/、scripts/、tests/）逐个检查我的项目里哪些位置会重名或被覆盖，输出冲突清单给我。
> 3. 经我确认后引入：无冲突的文件直接复制到位；与我已有文件重名的（尤其 AGENTS.md、LICENSE.md 和 docs/、scripts/、tests/ 里的同名文件），先给我看两边内容，按我决定的合并方式写入；套件的 scripts/ 与 tests/ 不得混进我项目自有的同名目录下已有的文件。
> 4. 引入完成后列出项目根的最新状态给我复核。

**第 2 步 · 选底座并初始化**：

> 编辑 docs/PROJECT.md：这是待填模板，把所有 FILL_IN 换成实际内容，edition 填 local。然后运行初始化，生成 docs/PLAN.md，把 config show 和 task list 的完整输出给我看。

**第 3 步 · 登记并派发第一条任务**：

> 登记 ID 为 TASK-0001 的示例任务：标题"建立项目骨架"，可改路径 src/，验收两条——最小入口可运行；验证命令退出码为 0。然后把它置为 READY 再 DISPATCH，把生成的任务包完整展示给我。提醒：DISPATCH 只登记状态并占用路径锁，不会启动任何 Agent，任务包要交给真实的执行会话去做。

**第 4 步 · 自检与收口**：

> 运行结构自检并告诉我退出码；然后把示例任务置为 CANCELED（理由写"冒烟结束"）以释放路径锁。示例任务以后要走完整流程时：执行会话完成任务包后，由 Master 审查并置为 VERIFIED——执行者不自批、不自合，合并由 Master 决策（完整规则见 AGENTS.md）。

**我想…**（按需跳转，不必通读）：

| 我想… | 去哪 |
| --- | --- |
| 知道它是什么、解决什么问题、适不适合我 | 第 1 节 |
| 选 `local` 还是 `github`，什么时候迁移 | 第 2 节 |
| 看话术对应哪些命令、每条命令的完整输出 | 第 3 节 |
| 空目录从零开始 / 接入已有仓库 / 迁到 GitHub | 第 4 节 |
| 看目录里每个文件是干什么的 | 第 5 节 |
| 配置底座、重试上限、资源锁 TTL、私有目录、并发 | 第 6 节 |
| 跑测试、用 CI、跑合成回归场景 | 第 7 节 |
| 打包发布、给下游做漂移诊断 | 第 8 节 |
| 看版本兼容与从旧版升级 | 第 9、10 节 |
| 冻结设计契约、做阶段收口审计 | 第 11 节 |
| 了解角色发现、Subagent 能力考核、模型动态选型及终端私有记录 | [角色发现与模型选择设计](design/agent-role-discovery-and-model-selection.md) |
| 找各治理文档入口 | 第 12 节 |

## 2. 两种底座怎么选

两个底座共用同一份核心契约，角色权限、审查门、禁改路径、证据标准完全一致，**只有任务载体与证据来源不同**。底座由 [docs/PROJECT.md](PROJECT.md) 的 `edition` 决定，取值只能是 `local` 或 `github`；缺失或非法时必须停止调度并报告，不得猜测或按默认值继续。本地 CLI 只维护 `local` 任务库：声明 `github` 时调度命令会被明确拒绝并给出下一步指引，而不是静默按 `local` 执行（支持矩阵见 6.2）。

| 维度 | `local`（[说明](../editions/local/master.md)） | `github`（[说明](../editions/github/master.md)） |
| --- | --- | --- |
| 形态 | 离线单机，不依赖托管平台 | 托管平台，Issue + Draft PR + CI |
| 任务载体 | SQLite 任务库 `.orchestration/state.sqlite3` | 每个任务一个 Issue，正文五节 |
| 锁 / 防冲突 | `task dispatch` 原子路径锁；可选具名资源锁（同一数据库文件 + 单机范围内原子） | Issue 内 `ORCH-TASK-REGISTRY` 登记块 + 开放 PR 同文件检查；具名资源只能协作登记，不具原子性 |
| 主要证据 | 本地命令输出、退出码、head SHA | CI 结果、PR 评论、head SHA |
| 协作范围 | 一位操作者、一台机器 | 多人、多地点，可异地接手 |
| 外部依赖 | Python 3 + Git，无需联网 | GitHub 仓库、平台权限、CI 工作流 |
| 适合 | 完全离线、单机、规模较小、内部项目 | 协作频繁、规模增长、需要公开可核验证据 |
| 主要代价 | 状态只在本机，无平台强制门 | 依赖平台可用性，公开内容必须脱敏 |

**选择判据：**

- 只有一台机器、可以完全离线、任务数量有限、审查记录只需内部可见：选 `local`。
- 需要异地接手、多人同时推进、需要平台强制的分支保护与必需检查、审查证据要对第三方可见：选 `github`。
- 拿不准时先 `local`；等协作需求明确后再迁移，不要一开始就为想象中的规模付出平台成本。

**什么时候该迁移：**

- 项目变大，任务数量、并行度或参与人数超出单机人工协调能力。
- 需要异地接手，或状态不能只留在某一台机器上。
- 需要公开、可独立核验的过程证据，或需要平台强制的合并门。

> 从 `local` 迁移到 `github` 是**授权决策**，不是偏离：多地协同开发时 GitHub 的优势明显，项目逐步变大后 `github` 底座更合适。

“先 `local` 后 `github`”是常见路径。迁移必须由项目所有者批准，**单向执行、一次性完成、禁止双写**：同一任务不能同时在任务库和 Issue 中维护状态。四步概览见 [kit/orchestration.md 第 6 节](../kit/orchestration.md#6-底座切换)，落地路径见本文第 4 节。

并发不设固定数量：**按当前机器性能动态判断**，只要任务可并行且 CPU / 内存 / 磁盘 / 测试资源仍有余量，就可以并行；同一 Worktree 同一时刻只允许一个测试进程。

## 3. 上手命令详解（local）

这是「快速上手」的完整版：话术对应哪些命令、每条命令的真实输出、退出码与逐条解释。所有命令都通过 `uv run python` 执行（uv 会自动选择合适的解释器）。

### 3.0 话术 → 命令对照表

快速上手的每句话术对应以下命令。**命令不需要用户手打**——列在这里是为了透明与审计：Agent 说它做了什么，你可以对照这张表核实；证据列是 Agent 执行后应带回的最小证据。

| 话术（对 Agent 说） | Agent 执行的命令 | 预期证据 |
| --- | --- | --- |
| "把套件装进我的项目…" | 从 Releases 下载发行包并解压，把包内顶层目录（`AgentOrch-<X.Y>/` 或 `agentorch-YYYYMMDD/`）内容复制到项目根 | 顶层目录清单：`scripts/`、`editions/`、`templates/`、`docs/PROJECT.md` 等 |
| "编辑 docs/PROJECT.md…然后运行初始化" | 编辑 `docs/PROJECT.md`；`uv run python scripts/orchestrator.py init` | 退出码 0；输出含 `Created docs/PLAN.md from <项目根>\templates\plan.md`；`.orchestration/state.sqlite3` 出现 |
| "把 config show 和 task list 的完整输出给我看" | `uv run python scripts/orchestrator.py config show`、`task list` | 退出码 0；`edition = local`；任务库为空 |
| "登记 ID 为 TASK-0001 的示例任务…" | `uv run python scripts/orchestrator.py task add --id TASK-0001 --title "建立项目骨架" --paths src/ --acceptance "最小入口可运行" "验证命令退出码为 0"` | 输出 `TASK-0001: created in PLANNED` |
| "置为 READY 再 DISPATCH" | `uv run python scripts/orchestrator.py task ready TASK-0001`、`task dispatch TASK-0001` | 退出码 0；`DISPATCHED; locks acquired for src`；任务包生成并展示 |
| "运行结构自检并告诉我退出码" | `uv run python scripts/validate_kit.py` | 退出码 0；`Kit validation passed (124 required files checked; …)` |
| "把示例任务置为 CANCELED" | `uv run python scripts/orchestrator.py task status TASK-0001 CANCELED --note "冒烟结束"` | 退出码 0；路径锁释放 |

### 3.1 放入套件

**直接使用发行版，不要 clone 本仓库。** 本仓库是套件自我开发与完善的现场——阶段计划、任务记录、审计与设计过程材料都在里面，不面向使用者；面向使用者的只有 Releases 里的发行包。发行包只含可复用产出（核心契约、两套底座、模板、脚本与自检），并经隐私自检：无个人路径、无凭据痕迹、无任务记录与过程残留。

```bash
# 从 Releases 下载正式发行包（如 AgentOrch-1.7.0.zip；日期构建为 agentorch-YYYYMMDD.zip）后解压，
# 把包内顶层目录内容复制进你的项目根；zip 名与顶层目录名相同
unzip /path/to/AgentOrch-1.7.0.zip -d /tmp/agentorch-kit
cp -r /tmp/agentorch-kit/AgentOrch-1.7.0/. your-project/ && cd your-project
```

随后编辑 `docs/PROJECT.md`，至少填好 `edition: local`、允许 / 禁改路径和验证命令；发行包里的该文件是带 `FILL_IN` 的待填模板。

**旧项目注意**：本节命令适用于新建项目；已有代码的仓库**不要**在项目根直接解压覆盖（可能盖掉你自己的 README / LICENSE / docs / scripts），改走第 4 节路径二的"先读方法、临时解压、冲突盘点、不覆盖引入"流程。

### 3.2 最小命令序列

```bash
uv run python scripts/orchestrator.py init
uv run python scripts/orchestrator.py config show
uv run python scripts/orchestrator.py task add --id TASK-0001 --title "建立项目骨架" --paths README.md src/ --acceptance "有最小可运行入口" "测试命令退出码为 0"
uv run python scripts/orchestrator.py task ready TASK-0001
uv run python scripts/orchestrator.py task dispatch TASK-0001
uv run python scripts/orchestrator.py task list
uv run python scripts/validate_kit.py
uv run python -m unittest discover -s tests
```

`--paths`、`--depends`、`--acceptance`、`--resources` 都是多值选项：一次给多个值（如上例）或重复写同一个选项（如 `--acceptance "AC-1" --acceptance "AC-2"`）都会合并保留；`--acceptance` 保持给定顺序，`--paths` / `--depends` 仍排序去重。

关键输出（为便于阅读，绝对路径以 `<项目根>` 代替，分隔符随平台变化、示例为 Windows 写法；路径末尾的 `/` 会被规范化为 `src`）：

```text
.
├── AGENTS.md                          # 核心契约：角色、权限、Git 纪律、安全（唯一权威，人打开）
├── LICENSE.md                         # 许可证
├── VERSION.md                         # 套件名、Format version 与 Release 版本
├── kit/
│   └── orchestration.md               # Agent 唯一权威：会话入口 + 状态机/合并门 + 验收程序（原三份合并）
├── editions/
│   ├── local/                         # master.md（Master 手册）+ execution.md（执行提示词）
│   └── github/                        # 同上 + issue/pr/active-task-registry 三个载体模板
├── templates/                         # 全部小写、可复制模板（任务包、设计契约、审计、清单…）
│   └── self-improvement/              # 提案 / 任务包 / 闭环报告 / 注册表示例 / 包内操作说明
├── docs/                              # 项目自有文档（不随包发布；人打开的用大写）
│   ├── README.md                      # 本文件（GitHub 仓库首页仍会渲染它）
│   ├── PROJECT.md                     # 项目配置：edition、允许/禁改路径、验证命令
│   ├── PLAN.md                        # 设计目标权威（init 从 templates/plan.md 生成）
│   ├── implementation-plan.md         # 阶段计划（状态唯一落点）
│   ├── charter.md / upgrade.md / drift.md / metrics.md
│   ├── self-improvement/              # 自我完善与更新安全的仓库文档（不随包）
│   ├── design/resource-locks.md       # 冻结契约
│   ├── testing/delivery-regression-scenarios.md
│   └── images/                        # 设计配图（按需查看、图文双写）
├── scripts/                           # orchestrator / validate_kit / check_drift / report_metrics / run_delivery_scenarios
│   ├── self_improvement/              # 来源注册表、采集、Lesson、规划与闭环
│   └── update_agentorch/              # 安全更新引擎与「更新 AgentOrch」入口
├── tests/                             # 随包自检用单元测试（docs/ 专属用例保留为 repository-only）
└── release/                           # 构建产物（gitignore）与发布标准 README.md
```

说明：

- `init` 创建 `.orchestration/state.sqlite3` 与 `.orchestration/config.json`，并把 `.orchestration/` 写入当前克隆的 `.git/info/exclude`（若存在 Git），不会覆盖项目的 `.gitignore`。
- `init` 还会在 `docs/PLAN.md` 缺失且不存在 1.3 之前的根 `PLAN.md` 时，创建 `docs/` 并从 `templates/plan.md` **逐字节复制**一份到 `docs/PLAN.md`；发现旧根 `PLAN.md` 时不自动迁移，只提示 `git mv PLAN.md docs/PLAN.md`（见 10.6）；`docs/PLAN.md` 已存在时保持原样、只打印提示，重复执行不产生差异。模板缺失时给出提示但 `init` 仍以退出码 0 结束（其余初始化照常完成）；写入失败（例如目录只读）会报 `ERROR` 并以退出码 1 结束，不会假装成功。
- **计划分两层**：`docs/PLAN.md` 是**设计目标权威**（为什么做、做到什么程度、原则与边界），任务方向必须与它一致；阶段目标、任务顺序与状态写在 `docs/` 的阶段计划里，需要时把 `templates/implementation-plan.md` 复制到 `docs/`（`init` 不创建阶段计划）。两者冲突时以 `docs/PLAN.md` 的设计目标为准，停下对齐，不得各写一套。`templates/plan.md` 只用于初始化复制，不要直接把它当项目计划。**设计文档可配图**：图放 `docs/images/`、正文相对引用；Agent 只按文中指引看图、不批量读取；设计意图必须文字自足（规则见 [AGENTS.md](../AGENTS.md) 第 8 条）。
- `dispatch` 只登记状态并占用路径锁，**不会启动 Agent**。拿到锁后，把自包含任务包交给真实执行实例；没有真实 spawn 能力时，由操作者另开会话执行。
- 单元测试会大量打印临时任务库日志，属正常现象；退出码为 0 即通过。
- 冒烟完成后可取消这条示例任务并释放锁：`uv run python scripts/orchestrator.py task status TASK-0001 CANCELED --note "快速开始冒烟结束"`。

## 4. 三条落地路径

本节命令同样可以用话术交给 Agent 执行——对照关系同 3.0；Agent 不会替你做授权决策（底座迁移、发布），那些仍由你拍板。

### 路径一：空目录启动新项目

目标：从一句想法走到项目章程、首批任务与第一个可运行切片。

1. 按第 3 节把套件放入空目录，`docs/PROJECT.md` 填 `edition: local`。
2. 运行 `uv run python scripts/orchestrator.py init` 与 `uv run python scripts/orchestrator.py task list`：`init` 会从 `templates/plan.md` 复制出 `docs/PLAN.md`，先在这份**设计目标**文件里写清问题、目标、原则、边界与全局验收，再确认任务库可用且为空。需要阶段计划时把 `templates/implementation-plan.md` 复制到 `docs/`，在那里写阶段目标、任务顺序与状态。
3. 在 Master Agent 会话中加载 [editions/local/master.md](../editions/local/master.md)，按提示词澄清目标、产出章程、拆出 1–3 个最小任务。
4. 若想法本身还很模糊，先走 [templates/project-charter.md](../templates/project-charter.md) 的章程引导流程，再进入任务拆解。

### 路径二：接入已有仓库

目标：在不破坏既有规则、**不覆盖旧项目任何重要文件**的前提下接入套件，恢复真实进度，再继续推进。对 Agent 说的话术见快速上手「第 1 步 B」，本节是它背后的完整步骤：

1. **Agent 先读方法，再动手**：读 [kit/orchestration.md](../kit/orchestration.md) 的会话入口、[AGENTS.md](../AGENTS.md) 与 [editions/local/master.md](../editions/local/master.md)（github 底座为 [editions/github/master.md](../editions/github/master.md)）的「已有项目接入」章节，理解角色分工、合并门与证据要求后，才允许改你的项目。
2. **发行包解压到项目外的临时目录**，不进项目根——后续所有复制都以"逐文件决策"方式进行。
3. **冲突盘点**：对照发行包文件清单（`AGENTS.md`、`LICENSE.md`、`VERSION.md`、`docs/`、`kit/`、`editions/`、`templates/`、`scripts/`、`tests/`）与项目现有文件，输出"会重名 / 会覆盖"清单给所有者确认；已有 `AGENTS.md` 的项目按"人工合并、不盲目覆盖"处理。
4. **不覆盖引入**：无冲突文件直接复制；重名文件逐个展示两边内容，由所有者决定合并方式后写入；套件的 `scripts/`、`tests/` 不得污染项目自有同名目录下的既有文件。
5. **填写 `docs/PROJECT.md`**：底座、主分支、允许 / 禁改路径（按旧项目真实关键目录从严设定）、验证命令（对齐旧项目真实的测试方式）、并发策略。
6. **初始化并自检**：

   ```bash
   uv run python scripts/orchestrator.py init
   uv run python scripts/orchestrator.py task list
   uv run python scripts/validate_kit.py
   ```

7. 在 Master Agent 会话中加载 [editions/local/master.md](../editions/local/master.md)。该提示词会要求先读 Git 状态与任务库，再拆任务与派发。

### 路径三：从 `local` 迁移到 `github`

目标：把任务状态从本机任务库迁到 Issue / PR / CI，切换后只保留一个事实来源。

入口：[kit/orchestration.md 第 6 节「底座切换」](../kit/orchestration.md#6-底座切换)。四步概览：

1. **冻结写入并导出 Issue**：停止 `local` 派发与状态变更，记录切换点的主分支 SHA 与任务库备份位置；每个未完成任务按 [ISSUE-TEMPLATE.md](../editions/github/issue-template.md) 的五节导出一个 Issue，已完成任务只归档摘要。
2. **建立平台保护与 CI**：配置默认分支保护、必需检查与 PR 模板，使合并门由平台强制执行。
3. **登记块接管锁**：开工前在 Issue 写 `ORCH-TASK-REGISTRY` 登记块，并检查所有开放 PR 的变更文件是否与 `locked_paths` 相交；两者都通过才算取得锁。
4. **切换点一次性完成**：状态映射、证据迁移、合并后主分支重验一次做完；迁移后所有状态只写入 Issue / PR，任务库转为只读归档。

迁移完成后，按 `github` 底座的启动提示词继续：[master.md 的“新项目启动”章节](../editions/github/master.md) 或 [master.md 的“已有项目接入”章节](../editions/github/master.md)。迁移是单向的，本套件不提供自动回退；确需回退时由项目所有者单独立项。

## 5. 目录结构

```text
.
├── AGENTS.md                          # 核心契约：角色、权限、Git 纪律、安全（唯一权威）
├── kit/orchestration.md             # 核心编排：状态机、G1–G6、C1–C4、底座切换
├── kit/orchestration.md  # 通用验收程序
├── kit/orchestration.md                 # 会话入口：判定底座、恢复状态、自动路由
├── docs/PROJECT.md           # 每个项目自己的配置（edition 在这里）
├── README.md                          # 本文件：入口与使用说明
├── VERSION.md                        # 套件名、Format version 与 Release 版本
├── LICENSE.md
├── editions/
│   ├── local/                         # local 增量：SQLite 命令、路径锁、启动提示词
│   └── github/                        # github 增量：Issue / PR 模板、登记块、启动提示词
├── docs/
│   ├── PLAN.md                        # 设计目标权威：为什么做、做到什么程度、边界（init 从模板生成后由所有者填写；1.4 起位于 docs/）
│   ├── images/                        # 设计配图（正文中相对引用；Agent 按文中指引按需查看，不批量读取）
│   └── …                              # 阶段计划、UPGRADE 等项目自有文档（不随包发布）
├── prompts/                           # 两底座共享提示词（章程、Reviewer）
├── templates/                         # 任务包、设计契约与决策日志、阶段收口审计、审查清单等可复制模板
├── scripts/
│   ├── orchestrator.py                # local 任务库与原子路径锁 CLI
│   ├── report_metrics.py              # 只读指标报告：任务库事件 → 七项指标（人类摘要 / JSON）
│   ├── validate_kit.py                # 结构 / 概念 / 链接自检，同时是发布清单
│   ├── check_drift.py                 # 生成 MANIFEST.json + 下游只读漂移诊断
│   └── build_release.py               # 发布构建（仅仓库内，不随包发布）
├── tests/                             # orchestrator 的单元测试
├── .github/workflows/ci.yml           # PR / main 推送时跑登记检查与发布打包自检（github 底座 G4 证据；仅本仓库，不随包发布）
├── release/README.md                  # 发布标准与发布清单（产物不入库）
├── docs/                              # 本仓库自己的章程、阶段计划与决策记录（不随包发布）
└── .local/                            # 本机私有文件（除说明文件外不入库）
```

**核心只有一份，底座只做增量。** `AGENTS.md` 与 `kit/orchestration.md` 定义与底座无关的契约；`editions/local/` 与 `editions/github/` 只能细化命令、模板和载体操作，不得复制或改写出另一套核心条款。底座手册与核心契约冲突时，以核心契约为准。

## 6. 配置

### 6.1 项目配置：`docs/PROJECT.md`

每个项目必须填写这份文件，它决定项目怎么被调度：

| 字段 | 作用 |
| --- | --- |
| `edition` | 底座的**唯一权威来源**，只能 `local` 或 `github`，且只写取值、不加说明文字；缺失、为空、非法字符串或类型错误时阻断调度，并报出实际值与修复位置（文件 + 键名） |
| 允许改动路径 / 永远禁止改动路径 | 任务默认范围与硬边界；任务只能进一步收窄 |
| 验证命令 | 审查门 G4 要求的测试、静态检查、构建命令；必须可真实执行 |
| 并发策略 | 不写固定数字，按机器余量动态判断 |
| 本地工作流 | 分支命名、Worktree 根目录、审查修复上限、备份节奏 |
| 风险与人工批准 | 数据删除 / 迁移、权限或安全变化、外部服务、不可逆动作 |

分发包里的这份文件是待填模板；也可以参考 [templates/project.md](../templates/project.md)。

### 6.2 运行时配置：`.orchestration/config.json`

由 `uv run python scripts/orchestrator.py init` 创建，**只描述本地 CLI 的运行参数**；它不决定底座，也不存在“缺省底座”。底座只看 6.1 的 `edition`。

| 键 | 允许值 / 默认 | 作用 |
| --- | --- | --- |
| `retry_cap` | 整数 ≥ 1，默认 `3` | 审查—修复往返上限；达到上限仍未通过则转 `BLOCKED` |
| `local_dir` | 非空字符串，默认 `.local` | 本机私有目录的位置 |
| `resource_lease_ttl` | 整数 1–604800（秒），**无默认** | 具名资源锁的租约时长；任务声明了资源但此项缺失 / 非法时**拒绝派发** |
| `resource_reclaim_grace` | 整数 0–86400（秒），**无默认** | 过期锁的回收冷却期；`task reclaim` 在冷却期内一律拒绝 |

**非法值的处理分两类，不要混淆**：

- `retry_cap` / `local_dir`：文件缺失、键缺失、类型错误、取值非法或 JSON 损坏时**静默回落到内置默认**，既有本地库行为不变。
- `edition`：只在 `docs/PROJECT.md` 中读取。缺失、为空、非法字符串（如 `GITHUB`、`cloud`）、类型错误（如 `1`、`true`）、重复声明或文件不可读时**阻断调度**，诊断给出实际值与修复位置（文件 + 键名）。`config.json` 里残留的 `edition` 键只作为历史记录，`config show` 会标注 `legacy key is ignored`，不参与路由。
- `resource_lease_ttl` / `resource_reclaim_grace`：**不回落默认值**。只有声明了资源的任务才读取它们，缺失或非法时派发失败并指名该键；未声明资源的任务完全不读这两项，行为与之前一致。

查看每个键的生效值与来源：

```bash
uv run python scripts/orchestrator.py config show
```

`source` 为 `config.json` 表示来自运行时文件，为 `default` 表示回落到内置默认；`edition` 的来源固定是 `docs/PROJECT.md`。示例（合法覆盖 + 合法 `local` 声明）：

```text
Config file: <项目根>\.orchestration\config.json
  retry_cap  = 5          (source: config.json)
  local_dir  = .private   (source: config.json)
  edition    = local      (source: docs/PROJECT.md)
Edition is selected only by `edition` in <项目根>\docs/PROJECT.md; config.json holds local CLI runtime parameters (retry_cap, local_dir) and never selects an edition.
Edit .orchestration/config.json to change retry_cap or local_dir; invalid values there fall back to defaults.
```

`edition` 缺失或非法时，`config show` 仍打印运行参数，同时把诊断写到 stderr 并以退出码 1 结束。

#### 底座与命令支持矩阵

| 命令 | `local` | `github` | `edition` 缺失 / 非法 |
| --- | --- | --- | --- |
| `init` | 可用 | 可用（只建本地库，不选底座） | 可用 |
| `backup` | 可用 | 可用 | 可用 |
| `config show` | 可用，退出码 0 | 可用，退出码 0 | 打印诊断，退出码 1 |
| `task list` / `task show` / `task resources` | 可用 | 可用（只读归档；`task resources` 只列出已有记录，不做过期迁移） | 可用（只读） |
| `task add` / `ready` / `dispatch` / `status` / `workspace` / `amend` / `unlock` | 可用 | 拒绝，并给出 Issue / PR 指引 | 拒绝，并给出实际值与修复位置 |
| `task renew` / `task reclaim` / `task reconcile` | 可用 | 拒绝，并给出 Issue / PR 指引 | 拒绝，并给出实际值与修复位置 |

`github` 底座的调度由 Issue / PR 承载：新建任务按 [editions/github/issue-template.md](../editions/github/issue-template.md) 写 Issue，执行按 [editions/github/execution.md](../editions/github/execution.md) 推进。本地 CLI **不**声称支持 github 调度，也不会把状态写进本地库。

### 6.3 私有目录：`.local/`

隐私文件统一放在 `.local/`，**除目录说明外永不入库、永不打包**：

- `.env` 等环境变量与凭据；
- 机器信息（CPU、内存、磁盘、实测可承载并发、解释器路径等本机事实）；
- 发布扫描规则（发布构建使用的额外禁用词）。

`.gitignore` 用 `.local/*` 忽略全部内容，只保留 `.local/README.md` 作为目录说明；该说明文件本身不得写任何个人信息、凭据或主机事实。分发包按 `validate_kit.py` 的文件清单打包，`.local/` 不在清单内，因此不会被发布。若修改 `local_dir`，必须同步更新 `.gitignore`，否则私有文件可能被提交。

### 6.4 并发策略

不设固定并发数量。派发前观察 CPU、内存、磁盘与测试资源争用：只要任务之间依赖已满足、路径锁不冲突，且机器仍有余量，就可以并行；资源紧张时串行。同一 Worktree 同一时刻只允许一个测试进程，也不要让两个执行实例共享可写工作区。“本机实测可承载并发”只作为记录，不作为规则常量。

### 6.5 具名资源锁（可选，语义见 `docs/design/resource-locks.md`）

路径锁只能回答“两个任务是否改同一批文件”。测试数据库、共享构建机、端到端测试槽这类**不落在文件上**的独占需求，用可选具名资源锁表达：任务声明显式资源集合，派发时与路径锁一起判定，两者都通过才可派发。语义（冲突矩阵、原子集合获取、租约与恢复、底座边界、故障场景）**只在仓库中的 `docs/design/resource-locks.md` 定义**（该目录不随发行包发布），本节只列命令。

```bash
uv run python scripts/orchestrator.py task add --id TASK-0002 --title "跑集成测试" --paths tests/ --acceptance "集成测试通过" --resources db:test/mysql-main:exclusive svc:build/win-01:shared:600
uv run python scripts/orchestrator.py task dispatch TASK-0002 --holder "$ORCH_HOLDER" --evidence "issue #12 comment"
uv run python scripts/orchestrator.py task resources      # 资源 / 模式 / 状态 / 租约 / 持有者 / 阻塞效果
uv run python scripts/orchestrator.py task show TASK-0002 # 本任务的资源锁与最近阻塞原因
```

- **不声明就不参与**：不带 `--resources` 的任务与之前完全一致，行为、报错文案、锁表都不变。
- **声明了就整批取得**：一次派发是一笔事务，任一资源冲突则整批失败，不会留下半套锁；失败返回会给出冲突资源、占用方任务与持有者、占用方模式与租约，以及可执行的下一步。
- **只有 `VERIFIED` / `CANCELED` 自动释放**：`FAILED`、`BLOCKED`、需人工决策与状态未知都保持占用；租约到期只是转 `EXPIRED`，**仍然参与冲突判定**，不会被当成空闲。
- **人工动作必须留痕**：`task unlock <ID> --resource <id> --reason <text> --force` 需要理由，非终局任务还需 `--authorized-by`；`task reclaim` 需要过期、冷却期已过和证据引用；两者都会写审计事件。强制解锁后原任务再续租会得到 `LOCK_NOT_HELD`。
- **恢复不靠记忆**：`task reconcile` 只读锁表与审计事件重建事实，缺事件、持有者不可定位、任务需人工决策的记录进入隔离（`QUARANTINED`），既不当有效持有，也不当空闲。
- **配置**：声明资源前在 `.orchestration/config.json` 显式写入 `resource_lease_ttl` 与 `resource_reclaim_grace`（见 6.2），没有内置默认值。
- **底座边界**：`local` 的原子性只覆盖“同一数据库文件 + 单机（或受支持的一致性共享文件系统）”，不覆盖跨主机或网络文件系统；`github` 只有 Issue 登记块里的协作性占用（可选 `locked_resources`），检查与写入之间存在 TOCTOU 窗口，**不得表述为“原子锁”或“并发已保证”**。详见契约第 7 节。

## 7. 自检

套件自带两层自检：结构自检保证文件与文档没有腐化，单元测试保证任务库与锁的行为不变。

```bash
uv run python scripts/validate_kit.py
uv run python scripts/validate_kit.py --project-ready
uv run python -m unittest discover -s tests
uv run python -m py_compile scripts/orchestrator.py scripts/validate_kit.py scripts/report_metrics.py scripts/check_drift.py scripts/build_release.py scripts/run_delivery_scenarios.py scripts/update_agentorch/*.py scripts/self_improvement/*.py
uv run python scripts/run_delivery_scenarios.py --json
uv run python scripts/report_metrics.py --gold-sample --since 2026-01-01 --until 2026-02-01 --json
```

`validate_kit.py` 检查：

- **124 项必需文件**是否齐全（`REQUIRED` 列表同时是发布包清单，只有一份来源）。
- **合约模板是否仍问对问题**：`templates/design.md`、`templates/milestone-audit.md`、`templates/contract-decision-log.md` 的必备章节缺一即失败，模板内的相对链接断链也失败（章节缺失会被静默当成“不需要回答”，因此单列一项检查）。
- **能力登记与指标模板是否仍问对问题**：`templates/runtime-capability.md` 的身份 / 能力矩阵 / 扩展边界 / `UNKNOWN` 与重测 / 权限范围 / 证据与限制，以及 `templates/metrics.md` 的阅读规则 / 公共定义 / 指标定义 / 缺失与覆盖率 / 报告契约 / 金样本 / 限制，缺章节同样失败。
- **发行包清单是否自洽**：根目录存在 `MANIFEST.json`（发行包内）时，校验其结构、分类、路径可解析与 `REQUIRED` 集合一致；SHA-256 与大小在纯净包与上游 checkout 严格比较，在下游项目只校验结构（见下方“三种树”）。仓库根目录没有该文件，检查自动跳过，不误报。
- **两底座结构是否一致**：`editions/local` 与 `editions/github` 共享 2 个文件（`master.md`、`execution.md`），`github` 额外提供 3 个载体模板（`issue-template.md`、`pr-template.md`、`active-task-registry.md`）；任一侧缺失或出现多余文件都会失败。
- **治理概念是否真实存在**：核心编排文档必须出现 `PR_OPEN`、合并门 G1–G6、运行时能力契约 C1–C4；文档整体必须出现主要状态名。
- **`edition` 是否合法**：`docs/PROJECT.md` 必须有该键，且值为 `local` 或 `github`。
- **相对链接是否可解析**：扫描所有 Markdown 链接，断链即失败（外部 URL 与锚点除外）。
- **可选架构契约检查器是否随包发布**：`scripts/architecture/` 与 `templates/architecture/` 进入 `REQUIRED`（发行包清单），缺失即失败。检查器本身不猜模块：没有 `docs/architecture/contract.json` 时等价 `OFF`，只输出只读盘点并以 0 退出；配置契约后才按 `ERROR` / `WARNING` / `UNKNOWN` / `NOT_RUN` 报告并绑定 head SHA。用法见 [`templates/architecture/README.md`](../templates/architecture/README.md)。

**三种树，三种严格度**：`validate_kit.py` 按树自动判定，汇总行会打印 `tree mode package|downstream|upstream`。

| 树 | 判定依据 | 仓库文档检查 | `MANIFEST.json` 检查 |
| --- | --- | --- | --- |
| 纯净包（`package`） | 没有 `docs/` 目录 | 跳过（包内不带仓库文档） | 结构 + 分类 + 路径 + `REQUIRED` 集合 + **SHA-256 / 大小** |
| 下游项目（`downstream`） | 有 `docs/`，没有 `docs/upgrade.md` | 只要求 `docs/PROJECT.md` 与 `docs/PLAN.md`（后者仍需“设计目标”章节） | 结构 + 分类 + 路径 + `REQUIRED` 集合 + 每个列出文件存在；**不比较哈希** |
| 上游 checkout（`upstream`） | 有 `docs/upgrade.md` | 全部 `REQUIRED_DOCS` | 结构 + 分类 + 路径 + `REQUIRED` 集合 + **SHA-256 / 大小** |

- **`docs/upgrade.md` 是上游标记**：它只存在于本仓库，不随包发布。下游项目**不要**创建同名文件——一旦出现，`validate_kit.py` 会按上游 checkout 要求全部仓库文档，并把本地修改当哈希漂移报错。
- **下游放宽的只有哈希**：清单结构非法、分类错误、路径非法、`REQUIRED` 少列 / 多列、清单列出的文件缺失，在三种树下都是失败。下游项目保留官方 `MANIFEST.json` 当基线即可，自己的修改由 `check_drift.py` 报告（见 8.2）。
- **纯净包与上游不降级**：包内或本仓库 checkout 里改动核心文件，`validate_kit.py` 仍以退出码 1 失败。

`--project-ready` 的含义：在项目配置填完之后追加检查，只要 `docs/PROJECT.md` 里仍有 `FILL_IN` 占位符就以退出码 1 失败。发布包里的 `docs/PROJECT.md` 故意保持待填模板，因此**发布自检中该命令预期失败**；若它通过了，反而说明模板没有被正确替换。

`unittest` 覆盖状态机、依赖、路径锁、重试上限、head SHA 与 merge SHA、`task add` 多值选项的重复出现与单次多值合并、edition 的权威来源与 fail-closed 行为、计划层拆分（设计目标模板不含任务块、阶段计划模板含任务块、必需文件数与 README 一致），以及发行包清单与漂移诊断（二次构建确定性、篡改 / 缺失核心文件、额外扩展、上游目录内的未知文件、无 manifest 旧项目、JSON 字段与退出码、诊断过程不改动目标树、构建 / 缓存目录被跳过）、三种树下的校验模式（纯净包 / 下游 / 上游的文档集合与清单严格度、下游本地修改不触发哈希失败而 `check_drift` 仍报 `modified`）、shipped 文件不出现已改名路径、契约模板结构（缺章节 / 缺模板文件 / 模板内断链都必须失败，模板不复制任务生命周期）、能力登记与指标模板章节、指标报告本身（金样本数值、空输入 `no data`、缺事件记 `missing`、未采集记 `unknown`、JSON 字段与顺序稳定、不读事件备注），以及合成回归场景运行器（单场景异常不吞掉后续、退出码三态、场景表与注册一致）、最终验收场景集（自我完善 / 安全更新 / 依赖方向 / 指标诚实性真实执行并清理沙箱）与 Phase D 角色选择端到端组合（S1 scope / 记录 → S2 发现与匹配 → S3 可选模型数据 → S4 反馈；C1–C4 门、缺角色、同模型 Reviewer、模态、过期数据、scope 隔离与写入失败）；当前 1047 个用例。`py_compile` 只做零依赖语法检查，不引入任何第三方工具。

### 7.1 CI（`github` 底座）

仓库自带一条 GitHub Actions 工作流 `.github/workflows/ci.yml`（仅本仓库使用，不随发行包发布），把登记检查与发布打包自检放到平台上执行：

- **触发**：`pull_request` 目标 `main`、`push` 到 `main`、以及手动 `workflow_dispatch`。
- **环境（三个操作系统）**：
  - `checks`：`ubuntu-latest`，Python 矩阵 `3.12` / `3.13`；
  - `checks-macos`：`macos-latest`（真实 macOS runner），Python `3.13`；
  - `checks-windows`：`windows-latest`（真实 Windows runner，默认 shell 是 pwsh），Python `3.13`。

  三个 job 共用同一份步骤定义（workflow 内的 YAML anchor），所以不存在"某个平台少跑一步"的漂移；任一 job 的任一档失败即整体失败（矩阵 `fail-fast: false`，其余档继续跑并各自留证）。macOS / Windows 只跑 `3.13`，是**为了验证平台，而不是扩大 Python 版本承诺**：`3.12` / `3.13` 的版本覆盖仍由 Linux 矩阵承担。
- **步骤**：按顺序执行且不跳过失败步骤 —— `uv run python scripts/validate_kit.py`、`uv run python scripts/architecture/check.py --project . --json`（无契约项目应为只读诊断并退出 0）、`uv run python -m unittest discover -s tests`、`uv run python scripts/run_delivery_scenarios.py --json --output "${{ runner.temp }}/delivery-scenarios.json"`、`uv run python scripts/report_metrics.py --gold-sample --since 2026-01-01 --until 2026-02-01 --json`、跨平台语法检查、`uv run python scripts/build_release.py`。
  - **报告路径用 `${{ runner.temp }}`**：不依赖 shell 的 `$RUNNER_TEMP`，同一行在 bash 与 pwsh 下都能解析；报告只写到 runner 临时目录，不进仓库。
  - **语法检查不依赖 shell 通配符**：核心脚本用显式文件列表，`scripts/update_agentorch/*.py` 与 `scripts/self_improvement/*.py` 由 Python `glob` 展开。Windows runner 的默认 shell 是 pwsh，不会像 bash 那样展开 `*.py`，原写法会让这两个目录在 Windows 上被静默跳过。
  - **统一 UTF-8**：工作流级设置 `PYTHONUTF8=1` 与 `PYTHONIOENCODING=utf-8`。不设置时 Windows runner 的 Python 子进程会沿用旧代码页，套件里的中文诊断信息会在写出或读回时失败，而同样的命令在 Linux 上是通过的。这是 runner 环境设置，不改变产品行为。
- **打包自检**：最后一步是发布构建本身。它失败即 job 失败 —— 没有 `continue-on-error`、没有 `|| true`、没有跳过开关，因此"少打包一个文件、包里断链、清单哈希对不上、包内出现禁止路径、包内场景或指标回归"这类回归会在 PR 上直接变红，而不是等 Master 手工构建才发现。
  - **每个平台都跑的原因**：自检会在解压目录里执行随包发布的 `validate_kit.py` 与 `unittest`（a–l 中的 b、d），即真正检验"这一平台 / Python 版本拿到的包是否可用"；三个 job 各自证明一次，无 `if:` 条件，也就不会出现"某档静默跳过打包步骤"。
  - **行尾与可复现性**：仓库 `.gitattributes` 统一 `eol=lf`，打包前还会把文本文件归一为 LF，自检 f 断言包内文本不含 CRLF，因此 Windows 检出无法把 CRLF 带进发行包；场景沙箱里的 git 仓库也把 `core.autocrlf` / `core.eol` 固定为 LF 行为，不依赖 runner 的全局 git 配置。
  - **产物处理**：zip 写到 runner 的 `release/`（`.gitignore` 已忽略 `release/*.zip`），不提交、不上传 artifact、不使用任何密钥。
  - **覆盖边界**：CI 只证明"包能在该平台构建且内置自检 a–l 通过"，**不证明发布本身** —— 不校验发布命名（`--release-version` 资产名或日期构建名）、不生成 GitHub Release、不核对发布记录。发布仍需按 `release/README.md` 第 4 节由 Master 执行并留证。
- **仍未覆盖（继续标 `UNKNOWN`）**：真实安装器与用户桌面环境、Windows 交互式 PowerShell / Git Bash 会话中的手工命令、macOS 大小写不敏感卷上的极端路径场景、以及下游项目的平台承诺。CI 只覆盖 GitHub-hosted 的 `ubuntu-latest` / `macos-latest` / `windows-latest`，不外推到其他发行版或自托管环境。
- **边界**：`permissions: contents: read`，不使用 `pull_request_target`，不引入第三方依赖、密钥或 `pip install`；新增的 runner 用量（`macos-latest` / `windows-latest`）已由项目所有者授权。

**证据地位**：`github` 底座下，G4 以**当前 head SHA** 上的 CI 运行全绿为准 —— 本地四条命令（含打包自检）仍是可复现的补充证据，不能替代 CI；反过来，CI 绿灯也只覆盖这四条命令，不能代替 G1 的逐条验收，也不能代替对发布标准的逐条审查。head 变化后旧 head 的绿灯失效，必须在新 head 上重新运行。

**查看方式**：

- PR 页面的 **Checks** 区域（或提交旁的绿勾 / 红叉）展开即见各矩阵任务与失败步骤日志；
- 命令行：`gh pr checks <PR 编号>` 查看当前 head 的检查状态，`gh pr checks <PR 编号> --watch` 等待结果；
- Actions 页面（`https://github.com/<owner>/<repo>/actions/workflows/ci.yml`）可查历史运行记录；写证据矩阵时记录运行 URL 与 head SHA。

发布前的完整自检（构建内置、无跳过开关）见 `release/README.md`。

### 7.2 能力登记与交付度量

两件事分开做，互相不能替代：**能力登记**说明“这个运行时被观察到能做什么”，**交付度量**说明“这段时间的流程数字是多少”。详细说明见 `docs/metrics.md`（本仓库文档，不随包发布）。

```bash
# 固定合成样本：成功 / 失败 / 取消 / 阻塞 / 重试 / 缺事件都覆盖
uv run python scripts/report_metrics.py --gold-sample --since 2026-01-01 --until 2026-02-01

# 真实 local 任务库（只读打开；不会写状态、不释放锁）
uv run python scripts/report_metrics.py .orchestration/state.sqlite3 --json
```

- **能力登记**按 [templates/runtime-capability.md](../templates/runtime-capability.md) 填写：运行时名称与精确版本、底座、执行模式、探测时间（UTC）、C1–C4 逐条结论、更细的边界（spawn 句柄、隔离、wait-collect、终局分类、取消、文件 / 命令 / CI 权限、工具与测试环境、证据、限制）。未探测、证据失效或运行时升级一律写 `UNKNOWN`，先降级再重测。**记录只说明观察到的事实，不授予权限，也不替代 G1–G6。**
- **指标口径**只在 [templates/metrics.md](../templates/metrics.md) 定义：七项指标各自写明起止事件、时区（UTC）、排除项与缺失处理；报告只读任务库事件与证据引用，不读凭据、提示词或消息正文。
- **读数纪律**：任何统计值都要带样本量、窗口与缺失率；空数据输出 `no data`（coverage 0），缺事件记 `missing`，来源未采集记 `unknown` —— 三者都不等于 0。指标不用于没有上下文的 Agent 排名。
- **未完成的采集项**：`local` 任务库目前只记录状态事件与资源冲突；检查结果、路径冲突、回归与验收行尚未写入，因此对应指标显示 `unknown`。这是原型，不宣称已自动采集。

### 7.3 合成回归场景（重大变更必跑）

文档要求“该失败的要失败”，`scripts/run_delivery_scenarios.py` 用合成 fixture 证明这一点：A 治理/授权、B 迁移/版本、C 跨模块闭环、D 底座与证据失效四组，每组都有正向控制组与负向注入组。场景矩阵（前置 / 注入缺陷 / 预期拦截门 / 证据 / 清理）只在 `docs/testing/delivery-regression-scenarios.md`（本仓库文档，不随包发布）；`kit/orchestration.md` I 段把它列为套件重大变更的验收步骤。

```bash
# 全量运行（人类摘要写到 stdout；--json 时 stdout 只有报告，摘要转 stderr）
uv run python scripts/run_delivery_scenarios.py
uv run python scripts/run_delivery_scenarios.py --json --output "$TEMP/delivery-report.json"
```

- **退出码三态**：`0` 全部场景通过；`1` 至少一个场景失败或异常（不当作通过）；`2` 运行器自身未能开跑（模块缺失、`--only` 未知 ID、输出不可写）。
- **正负组口径**：负向场景“通过”表示拦截门真的触发了（缺陷被拦下），不是任务真的成功了；`totals.negative_passed == totals.negative` 且至少一个正向控制组 `PASS` 才算有效证据。
- **单场景不结束运行**：任何场景抛出非断言异常都会记为 `ERROR` 并保留堆栈，其余场景继续执行；这是运行器自身被测试的行为。
- **只读仓库**：fixture 在一次性临时目录里生成，运行器不读写仓库文件（`--output` 指定的报告路径除外，建议写到仓库外）；无网络、无凭据、无外部模型。
- **边界**：场景只覆盖套件自身的门禁语义；`github` 底座的 Issue / PR / 登记块 / CI 端到端无法离线验证，未实际运行即标 `BLOCKED`，不得用 `local` 结果替代；也不得据此宣称对任何外部产品项目做过真实数据或自动化验证。



## 8. 发布与下游漂移诊断

### 8.1 构建发行包

- **构建（两种命名，互不组合）**：日期构建用 `uv run python scripts/build_release.py`（今天）或 `--date YYYYMMDD`，产出 `release/agentorch-{YYYYMMDD}.zip`，顶层目录 `agentorch-{YYYYMMDD}/`；正式 Release 用 `--release-version 1.7.0`，产出 `release/AgentOrch-1.7.0.zip`，顶层目录 `AgentOrch-1.7.0/`。zip 名与顶层目录名始终相同；两个命名参数同时给出会直接报错。无论用哪种命名，包内文件集合 == `validate_kit.py` 的 `REQUIRED` 列表 + 构建时生成的 `MANIFEST.json`，`MANIFEST.json` 的 `format_version` 只由 `VERSION.md` 的 `Format version:` 行决定（当前 `1.7`）。
- **Release 版本 ≠ Format 版本**：`AgentOrch-1.7.0.zip` 是**发行号**（tag `v1.7.0`），`Format version` 是格式契约版本；当前 Release 为 `1.7.0`、Format 为 `1.7`，两者 major.minor 相同但仍是两条序列（1.6.x 时代 Release `1.6.3` 与 Format `1.6` 已说明这一点）。`MANIFEST.json` 的 `format_version` 由 `Format version:` 行生成（`1.7`），`Release version` 行只作记录、不参与 `check_drift.py` 的解析。更新引擎的 `VERSION_MATCH` 比较 tag 的 major.minor 与 `format_version`，因此 `v1.7.0` 匹配 Format `1.7`。
- **机器可读清单**：`MANIFEST.json` 记录 `schema_version`（清单格式版本）、`suite`、`format_version`（套件格式版本）与 `files[]`；每个条目含 `path`、`category`、`sha256`、`size`。清单**不含时间戳**、条目按路径排序，分类规则只有一处实现（`scripts/check_drift.py`，构建脚本导入它），因此同一工作树两次构建得到完全相同的清单与 zip SHA256。
- **产物不入库、自检强制**：`release/*.zip` 已被忽略；构建后自动执行 a–l 自检，失败会删除未通过自检的产物，没有跳过开关。自检 a 现在校验「包内文件集合 == `REQUIRED` + `MANIFEST.json`」、清单中每条路径可解析、SHA-256 与大小和实际文件一致、分类合法且清单路径与 `REQUIRED` 完全一致。
- **可复现**：归档时间戳固定、文本文件按 LF 归一后打包，清单哈希同样按归一后的字节计算；同一仓库状态重建应得到同一 SHA256，Windows 上 `core.autocrlf=true` 的检出也不会改变结果。

命名、包内容、自检清单、发布流程与发布清单以 `release/README.md` 为准；该文件与本节一致（包内容 == `REQUIRED` + `MANIFEST.json`，自检 a 同时校验文件集合与清单条目）。该自检现由 CI 在每个 PR 上执行（见 7.1 节），因此打包回归不再依赖手工构建才发现。

### 8.2 下游只读诊断：`check_drift.py`

项目从发行包复制出来后，用下面这条命令判断「我用的哪个版本、核心契约是否被改过」，**只报告、不覆盖**：

```bash
uv run python scripts/check_drift.py <项目根>          # 人类可读摘要，stdout
uv run python scripts/check_drift.py <项目根> --json   # stdout 只有 JSON；摘要转到 stderr
```

输出把文件分成五类，同一路径只归一类：

| 类别 | 含义 | 命中时算 drift？ |
| --- | --- | --- |
| `core` | 根目录契约与入口（`AGENTS.md`、`kit/orchestration.md`、`scripts/orchestrator.py` 等） | 缺失或被改动即 drift |
| `edition` | `editions/<edition>/` 下的底座增量 | 缺失或被改动即 drift |
| `template` | `templates/` 下的模板 | 缺失或被改动即 drift |
| `project-profile` | 每项目文件（`docs/PROJECT.md`、`PLAN.md`（旧根位置）、`docs/PLAN.md`（1.4 起）、`DESIGN.md`）；安装后本来就该被填写 | 否，单独列出 |
| `project-extension` | 清单之外的本地文件，且不在上游目录里（项目源码、`docs/`、自建目录……） | 否，单独列出，永不被覆盖 |
| `unknown` | 清单之外、却落在上游目录（`editions/`、`templates/`、`scripts/`、`prompts/`、`tests/`）内的文件，可能是旧版本残留或本地改写的套件路径 | 是，单独列出 |

`project-extension` 与 `unknown` 的差别是「归属」：前者是项目自己的地盘，后者是套件的地盘出现了清单不认识的文件；两者都会逐条列出，既不误报成应覆盖的通用核心，也不被静默忽略。

- **与 `validate_kit.py` 的分工**：`validate_kit.py` 在纯净包与上游 checkout 上做哈希严格校验，在下游树上只校验清单契约；**下游的本地修改只由本工具报告**（`modified` / `missing` / `unknown`）。因此下游看到 `check_drift` 退出码 2 是预期诊断结果，不是 `validate_kit` 失败。
- **退出码**：`0` clean；`2` 发现 drift（缺失、改动、`unknown`，或清单 schema 超出本工具支持范围）；`1` 输入无效或工具错误（项目根不存在、`MANIFEST.json` 非法 JSON / 结构不合法）。`--json` 的报告结构带 `report_schema_version`，字段固定，失败时同样输出稳定结构（多一个 `error` 字段）。
- **只读**：诊断只读取文件与目录、绝不写入 / 重命名 / 删除 / 覆盖目标项目；没有 `--fix`、`--sync` 一类开关，也不做自动合并或迁移。
- **不扫描的目录**：`.git`、`.orchestration`、`.local`、`.worktrees`、`.workbuddy`、`__pycache__`、`release`、`node_modules`、`.venv`、`.idea`、`.vscode`，以及构建 / 缓存目录 `target`、`.pytest_cache`、`.codegraph`、`.opencode`。这些目录下的文件不会进入任何分类，也不会被误报成 `extension` / `unknown`。
- **旧项目（无 `MANIFEST.json`）**：报告 `status: version-unknown`、退出码 `2`，提示「版本未知 / 需基线登记」并说明如何登记基线，**不猜版本**，也不阻止用户继续读取项目。
- **版本跨度**：报告 manifest 的 `schema_version` 是否在本工具支持范围内，并原样给出 `suite` / `format_version`，便于与上游发布记录对照。

哈希差异只表示文件字节不同，不表示契约冲突；工具只报告差异与建议，是否对齐由项目所有者决定。

## 9. 版本与兼容

- [VERSION.md](../VERSION.md) 记录套件名、支持底座、**Format version**（当前 `1.7`）与**对外 Release 版本**（当前 `1.7.0`，仅作记录）；格式变更时同步更新模板与校验清单。
- **Format 版本与 Release 版本是两条序列，不要混读**：当前发布是 Release `1.7.0`（tag `v1.7.0`、资产 `AgentOrch-1.7.0.zip`），Format 为 `1.7`。`1.7.0` 是 **Phase D 的功能发行**，同时把 `Format version` 从 `1.6` 升到 `1.7`：更新引擎的 `VERSION_MATCH` 要求 tag 的 major.minor 等于清单 `format_version`，`v1.7.0` 必须配 Format `1.7` 才会被接受；包内 118 个文件（117 `REQUIRED` + `MANIFEST.json`）。上一个正式发布是 Release `1.6.3`（tag `v1.6.3`、资产 `AgentOrch-1.6.3.zip`），它是 `1.6.2` 的补丁发行，包含 #99–#104 的路径冲突记账、`task amend --paths`、`check_scope.py` 与文档口径修复，Format 仍为 `1.6`；再往前是 Release `1.6.2`（tag `v1.6.2`、资产 `AgentOrch-1.6.2.zip`），它是 `1.6.1` 的补丁发行（#93 重复多值选项修复）；更早是 Release `1.6.1`（tag `v1.6.1`、资产 `AgentOrch-1.6.1.zip`），它是 `1.6.0` 的补丁发行（#85 下游包修复）；最早是 Release `1.6`（tag `v1.6`、资产 `AgentOrch-1.6.zip`），它同时把 `Format version` 从 `1.5` 升到 `1.6`（新增自我完善 / 安全更新资产，见 `release/README.md` §5.2）。`MANIFEST.json` 的 `format_version` 只由 `Format version:` 一行生成（`1.7`），`Release version` 行不参与解析；下游判断兼容性看 `format_version`。
- **1.7.0 相对 1.6.3 的功能内容（Phase D，Epic #119）**：新增每终端独立 scope 与 Markdown 私有记录（S1 #120，`scripts/agent_selection/scope.py` / `local_store.py`）；Runtime adapter、Agent 发现与角色匹配（S2 #121，`runtime_adapter.py` / `discovery.py` / `roles.py`，C1–C4 门先于排序、Reviewer 独立性按底层模型 ID 判定）；可选模型数据源与角色专项动态选型（S3 #122，`model_selection.py` / `model_sources/`，不可用或过期时降级为 `BLOCKED` / `UNKNOWN` / `STALE`）；本机项目反馈与可解释校准（S4 #123，`feedback.py`，只在当前 scope 内读取、不自动改配置）；会话入口接线与 E1–E13 端到端合成回归（S5 #124）。回归场景从 58 增至 71（A–E 组，13 正向 / 58 负向），全量测试 1047 个。包内文件集合从 93 个（92 `REQUIRED` + `MANIFEST.json`）增至 118 个（117 + 1），新增的 25 个 `REQUIRED` 文件全部是 Phase D 资产与随包测试；无移动 / 改名 / 删除，`MANIFEST.json` schema 1 不变，Format 从 1.6 升到 1.7 只为与 `v1.7.0` tag 对齐。真实 `v1.6.3 → 1.7.0` 升级已于 2026-10-27 在隔离项目中实测为 `APPLIED`（Format 1.6 → 1.7、`MANIFEST.json` files 92 → 117、117/117 包内文件与目标包逐字节一致、项目自有文件 SHA256 不变、备份 `verified=true`、head 前后不变，见 `docs/upgrade.md` §14.6）。
- **1.6.3 相对 1.6.2 的补丁内容**：`task dispatch` 路径锁冲突拒绝时写入 `CONFLICT_RECORDED`（`data.kind=path`）且不留锁，`task ready` 预检拒绝另记 `PATH_PREFLIGHT_REFUSED` 供审计、不计入 M4（#99）；`task amend --paths` 可在 `BLOCKED` 任务上原子重写路径与锁（#100）；新增只读 `scripts/check_scope.py` 作为 G2 证据，退出码 0 范围内 / 2 越界 / 1 无法判定（#101）；会话必读集合、G6 风险深度与 M4 资源冲突来源的文档口径修正（#102 / #103 / #104）。包内文件集合从 91 个（90 `REQUIRED` + `MANIFEST.json`）增至 93 个（92 + 1），新增的 2 个 `REQUIRED` 文件是 `scripts/check_scope.py` 与 `tests/test_check_scope.py`；无移动 / 改名 / 删除，`MANIFEST.json` schema 1 与 `format_version` 均不变，仍是 Format 1.6。真实 `v1.6.2 → v1.6.3` 升级已于 2026-10-27 在隔离项目中实测为 `APPLIED`（10 项包校验全 `PASS`、Format 1.6 → 1.6、`MANIFEST.json` files 90 → 92、92/92 包内文件与目标包逐字节一致、项目自有文件 SHA256 不变、备份 `verified=true`、head 前后不变；`docs/upgrade.md` §14.5）。
- **1.6.2 相对 1.6.1 的补丁内容**：修复 `task add` 重复多值选项静默丢值——`--acceptance` / `--paths` / `--depends` / `--resources` 原先用 `nargs='+'` + `store`，重复写同名选项时只有最后一次生效；现在重复出现与单次多值都会合并保留全部取值。包内文件集合（91 个 = 90 `REQUIRED` + `MANIFEST.json`）、`MANIFEST.json` schema 1 与 `format_version` 均不变；真实 `v1.6.1 → v1.6.2` 升级仍为 `NOT_RUN`，发布后验证。
- **1.6.1 相对 1.6.0 的补丁内容**：修复下游项目已有 `docs/` 时 1.6 包无法通过 `validate_kit` 与包内 `unittest` 的问题（下游模式只要求 `docs/PROJECT.md` 与 `docs/PLAN.md`，不比较清单哈希；同时修正大小写路径残留）。包内文件集合（91 个 = 90 `REQUIRED` + `MANIFEST.json`）、`MANIFEST.json` schema 1 与 `format_version` 均不变；真实 `v1.6.0 → v1.6.1` 升级仍为 `NOT_RUN`，发布后验证。
- **1.6 相对 1.5 的格式变化**：新增 `scripts/self_improvement/`、`scripts/update_agentorch/`、`templates/self-improvement/` 及随包测试进入 `REQUIRED`（包内 34 → 91 个文件）；无文件移动、改名或删除，`MANIFEST.json` schema 1 不变。已安装 1.3 及以后、有 `MANIFEST.json` 的项目可用「更新 AgentOrch」入口准备升级；真实 v1.5 → 1.6 升级已由 #71 验证（`APPLIED`，`docs/upgrade.md` §14.2）；以 1.6 为安装基线的升级与真实 1.3 / 1.4 仍为 `NOT_RUN`。
- `1.3` 相对 `1.2` 的变更要点：
  - **资源锁的载体字段**：具名资源锁把"不落在文件上"的独占需求写成任务字段——通用任务包新增可选 `declared_resources`（`<resource_id>:<mode>[:<ttl>]`，逗号分隔）；`github` 登记块新增 `locked_resources` 与 `resource_lease_until`（后者仅为记账值，平台不会因其过期而拒绝写入）；`local` 任务库新增 `resources_json` 列。字段格式属载体契约，故 `Format version` 从 `1.2` 升到 `1.3`；语义仍只在 `docs/design/resource-locks.md` 定义（该目录不随包发布）。
  - **发布清单与漂移工具进包**：包内新增 `MANIFEST.json`，分类与清单生成只有一处实现（`scripts/check_drift.py`，构建脚本导入它），`check_drift.py` 同时随包提供下游只读漂移诊断（见 8.1 / 8.2 节）。
  - **CI 打包自检**：`.github/workflows/ci.yml` 在每个 PR 与 `main` 推送的两个 Python 版本上都执行 `uv run python scripts/build_release.py`，失败即红（见 7.1 节）；`py_compile` 同时覆盖 `scripts/check_drift.py`。
- `1.2` 相对 `1.1` 的变更要点：
  - **发行包版本清单**：包内新增机器可读的 `MANIFEST.json`（套件版本、清单 schema 版本、逐文件分类与 SHA-256），构建可复现，清单无时间戳（见 8.1 节）。
  - **下游只读漂移诊断**：新增 `scripts/check_drift.py`，报告缺失、被改动的核心文件、上游目录内的未知文件、无清单旧项目与版本跨度，只诊断不覆盖，退出码区分 clean / drift / 工具错误（见 8.2 节）。
- `1.1` 相对 `1.0` 的变更要点：
  - **双底座**：`Edition: offline-single-user` 改为 `Editions: local, github`，同一套核心契约下分 `local`（纯本地）与 `github`（多人协作）两种底座，选择判据见第 2 节。
  - **`.local` 隐私层**：机器信息、凭据与发布扫描规则统一放 `.local/`，除目录说明外永不入库、永不打包（见 6.3 节）。
  - **按机器余量的并发策略**：不再固定并发数量，派发前按 CPU、内存、磁盘与测试资源余量决定并行度（见 6.4 节）。
  - **配置自省**：`uv run python scripts/orchestrator.py config show` 可查看每个配置键的生效值与来源，便于定位缺省值与项目覆盖。
- 纯 **Python 3 标准库**，**零第三方依赖**；不需要数据库服务、包管理器或构建步骤。
- 已在 **Windows + Git Bash** 环境用 Python 3 验证；四条检查（三条登记检查 + 发布打包自检）另由 CI 在 `ubuntu-latest`（Python 3.12 / 3.13）、`macos-latest`（3.13）与 `windows-latest`（3.13）上运行（见 7.1）。真实安装器与用户桌面环境、其他 shell 会话与其余命令**未验证**，命令中的引号与路径写法可能需要调整。
- `orchestrator.py` 只管理本地任务状态与路径锁，不启动模型、不写代码、不运行项目测试、不自动合并 Git 分支；这些动作由 Agent 按提示词执行，合并由 Master 决策。
- 若运行时无法真实完成 spawn / wait / collect（C1–C4 未全部通过），必须降级为多会话人工交接，不得声称已派发。

上面每条版本变更对应的**旧项目迁移动作**见第 10 节：`edition` 旧格式行、旧 `PLAN.md` 台账、资源锁 TTL、旧包无 `MANIFEST.json`；1.4 新增第 10.6 节（根 `PLAN.md` 迁入 `docs/PLAN.md` 与图片规范）。

## 10. 从旧版升级（1.1 → 1.7，含安全更新入口）

已经装了 1.1 / 1.2 的项目升到 1.3 时，有四条会“先撞错、再猜原因”。下面每条按**变了什么 / 怎么改 / 怎么自检**三段写，命令可直接复制；四条互相独立，命中几条改几条。完整分步清单、报错原文、退出码与逐条回退见仓库文档 `docs/upgrade.md`（`docs/` 不随发行包发布，因此本节不依赖它：包内用户照本节即可完成四条迁移）。

**没有自动迁移**：套件不提供迁移脚本，`init` 也不会替你拆分计划或改写配置。动手前先留证据：

```bash
uv run python scripts/orchestrator.py backup    # 备份任务库，退出码 0
git rev-parse HEAD                       # 记录升级前 head，回退时要用
```

### 10.1 `edition` 旧格式行

- **变了什么**：`edition` 只在 `docs/PROJECT.md` 里读，且**只认裸取值** `local` / `github`。多取值（`local / github`）、带说明文字（`local（默认）`）、重复声明、没写成列表项（`edition: local` 或放在表格里）都会**阻断调度**，不再回落到 `local`；`.orchestration/config.json` 里的 `edition` 不再生效，只作为历史记录显示。
- **怎么改**：一行、以 `-` 开头、冒号后只写取值。

  ```diff
  - `edition`: local（单人本地；需要多人协作时改为 github）
  + `edition`: local
  ```

- **怎么自检**：`uv run python scripts/orchestrator.py config show` 中 `edition` 一行应为 `local`（或 `github`）、`source` 为 `docs/PROJECT.md`，退出码 `0`。旧格式的报错长这样（都以退出码 `1` 结束）：

  ```text
  ERROR: docs/PROJECT.md: key `edition` has unsupported value 'local（单人本地；需要多人协作时改为 github）' in <项目根>\docs/PROJECT.md; allowed values are local, github (write the value alone, with no extra text)
  ERROR: docs/PROJECT.md: key `edition` is declared 2 times (lines 2, 4) in <项目根>\docs/PROJECT.md; keep exactly one declaration
  ERROR: docs/PROJECT.md: missing required key `edition` in <项目根>\docs/PROJECT.md; add one line "- `edition`: local" or "- `edition`: github"
  ```

  `config show` 的表格打到 stdout、报错打到 stderr，所以别只看屏幕——看退出码。

### 10.2 旧 `PLAN.md` 台账拆成两层

- **变了什么**：根 `PLAN.md` 只承载**设计目标**；阶段目标、任务顺序、任务块与状态搬到 `docs/` 的阶段计划（口径见 [AGENTS.md](../AGENTS.md) 第 8 条，本节不重述）。旧项目把两层混在一个文件里，升级后“方向”与“进度”读不出唯一答案。
- **怎么改**（手工；`init` 发现根 `PLAN.md` 已存在时只打印 `PLAN.md already present, kept unchanged`，**不会**自动改写或拆分）：

  ```bash
  mkdir -p docs
  cp templates/implementation-plan.md docs/IMPLEMENTATION-PLAN.md
  ```

  然后把阶段划分、依赖、任务块与状态表搬进 `docs/IMPLEMENTATION-PLAN.md`（状态变化的唯一落点就是它的「当前状态」表）；根 `PLAN.md` 只留设计目标六类内容（问题与目标用户 / 设计目标 / 设计原则 / 范围与非目标 / 全局约束 / 可度量的全局验收），并在顶部留一句指向阶段计划。

- **怎么自检**：

  ```bash
  grep -n '^|' PLAN.md || echo "no table rows: PLAN.md carries no task status table"
  uv run python scripts/validate_kit.py
  uv run python -m unittest discover -s tests
  ```

  根 `PLAN.md` 不该再有任务状态表；后两条各以退出码 `0` 结束。

### 10.3 资源锁：先配 `resource_lease_ttl`

- **变了什么**：任务用 `--resources` 声明了具名资源、而声明里没带 `:<ttl>` 时，`task dispatch` 会去 `.orchestration/config.json` 读 `resource_lease_ttl`。这个键**没有内置默认值**，没配就拒绝派发（写锁之前就失败，不会留下半套锁）。`task renew` 总是读它；`task reclaim` 总是读 `resource_reclaim_grace`。
- **怎么改**：在 `.orchestration/config.json` 加两个**整数**键（字符串 `"900"` 不合法）：

  ```json
  {
    "retry_cap": 3,
    "local_dir": ".local",
    "resource_lease_ttl": 900,
    "resource_reclaim_grace": 300
  }
  ```

  `resource_lease_ttl` 取值 1–604800 秒（示例 900 = 15 分钟）；`resource_reclaim_grace` 取值 0–86400 秒（示例 300 = 过期后 5 分钟冷却）。**没声明资源的任务完全不读这两项**，不配也不影响它们。

- **怎么自检**：

  ```bash
  uv run python scripts/orchestrator.py task dispatch TASK-0001 --holder runner:local-01 --evidence "upgrade smoke"
  uv run python scripts/orchestrator.py task resources
  ```

  没配时报：

  ```text
  ERROR: RESOURCE_CONFIG_INVALID: resource lock configuration is missing or illegal: resource_lease_ttl must be an integer between 1 and 604800 in <项目根>\.orchestration\config.json; add it to .orchestration/config.json (a lease without an explicit ttl or cool-down is refused)
  ```

  配好后派发退出码 `0` 并打印 `resource locks GRANTED`，`task resources` 里该资源为 `ACTIVE`。`github` 底座不需要配：本地 CLI 在 `github` 下拒绝派发，资源占用只以 Issue 登记块协作记录。

### 10.4 旧包没有 `MANIFEST.json`

- **变了什么**：1.2 起发行包内带机器可读的 `MANIFEST.json`。旧包没有它时 `check_drift.py` 报 `status: version-unknown`、**退出码 2**——含义是“版本未知 / 需基线登记”，不是崩溃，也不阻断继续使用项目。
- **怎么改**：把当初安装时那份发行包里的 `MANIFEST.json` 放到项目根即可。找不到原包时，在与安装版本一致的套件仓库 checkout 里重建一次，再从产物里取清单：

  若安装来源是正式 Release（如 `AgentOrch-1.6.zip`），用与安装版本一致的 checkout 加 `--release-version <X.Y>` 重建；日期构建仍用 `--date`。两种模式的 zip 名与顶层目录名相同：

  ```bash
  # 正式 Release 重建（示例：1.6）
  uv run python scripts/build_release.py --release-version <X.Y> --output-dir <输出目录>
  uv run python -m zipfile -e <输出目录>/AgentOrch-<X.Y>.zip <解压目录>
  cp <解压目录>/AgentOrch-<X.Y>/MANIFEST.json .

  # 日期构建（默认模式）
  uv run python scripts/build_release.py --date <YYYYMMDD> --output-dir <输出目录>
  uv run python -m zipfile -e <输出目录>/agentorch-<YYYYMMDD>.zip <解压目录>
  cp <解压目录>/agentorch-<YYYYMMDD>/MANIFEST.json .
  ```

  只登记基线，**不改、不覆盖**任何项目文件。两个注意点：`build_release.py` 不随发行包发布（包内 `scripts/` 只有 `check_drift.py`、`orchestrator.py`、`report_metrics.py`、`validate_kit.py`），所以只能在仓库 checkout 上重建；也别拿**新版**清单套**旧**树，否则每个上游文件都会被报成 `modified`。

- **怎么自检**：

  ```bash
  uv run python scripts/check_drift.py .
  ```

  ```text
  status: clean (exit 0)
  manifest: schema 1 (supported: yes), suite 'Agent-Orchestration Starter Kit', format 1.3
  checked: 33 manifest file(s); missing 0, modified 0, unknown 0, project-profile 2, extensions 0
  ```

  `project-profile` 条目（`docs/PROJECT.md`、`PLAN.md`）**正常**，它们本来就该被项目填写。诊断本身只读：没有 `--fix` / `--sync`，不创建、不删除、不覆盖目标项目里的任何文件。

### 10.5 升级后一次性自检

```bash
uv run python scripts/orchestrator.py config show    # 0：edition 不是 (invalid)
uv run python scripts/validate_kit.py                # 0
uv run python -m unittest discover -s tests          # 0
uv run python scripts/check_drift.py .               # 0：已登记基线
```

`check_drift.py` 在**未登记基线**的树（包括本仓库的裸 checkout）上按设计返回 `2`，这是“需基线登记”的正确表现，不是失败。

### 10.6 根 `PLAN.md` 迁入 `docs/PLAN.md` 与图片规范（1.4）

- **变了什么**：设计目标权威从根 `PLAN.md` 移到 `docs/PLAN.md`（放 `docs/` 便于同目录配图：图放 `docs/images/`，正文相对引用）；`init` 改为在 `docs/PLAN.md` 缺失且**无**根 `PLAN.md` 时自动创建；存在根 `PLAN.md`（1.3 及更早）时 `init` 不迁移、`validate_kit` 直接报错——根与 docs 并存等于两个设计权威。
- **怎么改**（手工两条命令；`init` 不会替你动旧文件）：

  ```bash
  mkdir docs
  git mv PLAN.md docs/PLAN.md
  ```

  然后把文内指向仓库根文件的相对链接补上 `../`（如 `[AGENTS.md](../AGENTS.md)` → `[AGENTS.md](../AGENTS.md)`）；`uv run python scripts/validate_kit.py` 会逐个指出断链。
- **怎么自检**：

  ```bash
  uv run python scripts/validate_kit.py                     # 0：不再报 legacy root PLAN.md
  uv run python scripts/orchestrator.py init                # 输出 kept unchanged: <项目根>\docs\PLAN.md
  ```

- **图片规则**（写入 [AGENTS.md](../AGENTS.md) 第 8 条，本节只提醒）：图放 `docs/images/`、正文相对引用；**Agent 只在正文明确指向某图时才打开它，禁止批量读取 `docs/images/`**；**图文双写**——设计意图必须文字自足，每个图引用都要有相邻文字说明；`docs/images/` 随公开仓库公开、不随发行包发布，截图先脱敏。
- **不要这样做**：把根 `PLAN.md` 重命名成别的名字留在根目录；或迁移后仍保留一份根副本。

### 10.7 “更新 AgentOrch”安全入口（当前 Format）

已安装 1.3 / 1.4 / 1.5（有 `MANIFEST.json`）的项目可以用一条入口命令准备升级到当前 1.7.0，不必手工比对上游文件；已安装 1.7.0 的项目无需再升级。已安装 1.6.0 / 1.6.1 / 1.6.2 / 1.6.3 的项目也可以用同一条入口准备升级到 1.7.0：入口按 `VERSION_MATCH` 接受 tag `v1.7.0`（major.minor = 1.7），再比较 target release `1.7.0` 与已安装 `format_version`（`1.6` 记作 `1.6.0`）判定可升级。**真实 `1.6.3` → `1.7.0` 升级为 `PASS`**（2026-10-27 隔离升级 `APPLIED`：`PLAN` 的 10 项包校验全 `PASS`、Format 1.6 → 1.7、`MANIFEST.json` files 92 → 117、117/117 包内文件与目标包逐字节一致、项目自有文件 SHA256 不变、备份 `verified=true`、head 前后不变；见 `docs/upgrade.md` §14.6）；此前的 `1.6.2` → `1.6.3` 升级已于 2026-10-27 在隔离项目中实测为 `APPLIED`（`PLAN` 的 10 项包校验全 `PASS`、Format 1.6 → 1.6、`MANIFEST.json` files 90 → 92、92/92 包内文件与目标包逐字节一致、项目自有文件 SHA256 不变、备份 `verified=true`、head 前后不变；见 `docs/upgrade.md` §14.5），`1.6.0` / `1.6.1` → `1.6.2` 与更早的补丁升级仍为 `NOT_RUN`。“入口可判定”不等于“升级已验证”，一次升级成功也不代表其他旧版本 / 平台已验证：

```bash
uv run python -m scripts.update_agentorch.entry --project . \
  --request "更新 AgentOrch" --mode PLAN
```

- 入口先识别 `edition`、当前 Format / 版本、工作区状态与已安装基线，再调用安全更新引擎；默认 `PLAN` 只读，`APPLY` 只在隔离分支 / Worktree 准备变更，不提交、不合并。
- `local` 只返回任务包 + 分支交付，不写任务库；`github` 只返回 Issue / Draft PR payload，不调用 `gh`。两者门禁相同。
- 冲突、上游删除 / 重命名、无基线、未提交改动、网络不可用都默认停止并报告；1.3 的根文件改名按 `HD-09` 交人工决策，不自动删除。
- 1.1 / 1.2 没有 `MANIFEST.json`，先按 10.4 登记基线；不支持的情况标 `NOT_RUN` / `UNKNOWN`。完整矩阵、回退与未实测范围见仓库文档 `docs/upgrade.md` 第 13 节（`docs/` 不随包发布）。

## 11. 设计冻结与阶段收口

三个模板——[templates/design.md](../templates/design.md)、[templates/contract-decision-log.md](../templates/contract-decision-log.md)、[templates/milestone-audit.md](../templates/milestone-audit.md)——解决的都不是“写文档”，而是**让结论可以回到任务、证据与 head SHA**。它们只提供结构与检查口径，不证明结论正确：某个问题是否 `BLOCKING`，仍以项目章程、已冻结契约与真实证据为准。

| 模板 | 什么时候用 | 用完得到什么 |
| --- | --- | --- |
| [templates/design.md](../templates/design.md) | **设计冻结前**：契约要跨模块、要冻结 Schema / API / 事件形状、涉及权限或存量数据、或改动代价高 | 一份冻结契约：范围 / 非目标、Schema 与接口、状态转换、不变量、失败语义、兼容与迁移、权限 / 信任边界、验证策略、冻结项、待决项、版本与变更策略 |
| [templates/contract-decision-log.md](../templates/contract-decision-log.md) | **每次冻结、解冻、改契约时**（随时追加，不只收口时） | 一条可追溯的决策记录：ID、日期、决定人、影响任务、变更前后、理由、状态（`OPEN` / `FROZEN` / `REVISITABLE` / `SUPERSEDED`）、证据链接 |
| [templates/milestone-audit.md](../templates/milestone-audit.md) | **阶段收口时**：里程碑准备宣布完成、冻结契约发生变更、或需要交接 | 十类检查的逐条结论与分类计数，能回答“这个阶段到底能不能收口” |

要点：

- **冻结不等于永远不可改**。“冻结”只是说下游任务不得自行改写契约；要改就新建一条决策记录，写明受影响任务与理由，再同步更新设计契约。`DESIGN.md` 与决策日志对同一条契约给出不同说法即视为缺陷。
- **局部验收代替不了阶段审计**。任务级审查按 [templates/review-checklist.md](../templates/review-checklist.md) 逐条判定，只证明该任务自己达标；阶段级是否成立要用 `MILESTONE-AUDIT.md`。审计结论只用四类：`BLOCKING`、`NON_BLOCKING_HARDENING`、`OPTIONAL`、`NEEDS_HUMAN_DECISION`（核实无问题记 `PASS`）。
- **范围证据可复现**：审查前跑 `uv run python scripts/check_scope.py --task <ID>`（只读本地任务库；缺省取任务 `approved_sha`，再退 `head_sha`，也可显式 `--base <40-hex>`），输出 base SHA 与越界路径，退出码 `0` 范围内 / `2` 越界 / `1` 无法判定。
- **未验证不算通过**。`NOT_RUN` / `BLOCKED` / 没有证据的项不得计入通过；只要存在 `BLOCKING` 或 `NEEDS_HUMAN_DECISION`，阶段就不得标记完成。
- **轻量模式**：小项目保留全部标题，不适用项写 `N/A: <理由>`（光写 `N/A` 不算），不强制产出冗长文档；模板也不复制状态机与 G1–G6 定义，需要时引用 [kit/orchestration.md](../kit/orchestration.md)。
- `validate_kit.py` 会校验这三个模板的必备章节与相对链接；章节被删除会失败，而不是被当成“这项不需要回答”。

## 12. 治理文档导航

| 文档 | 什么时候读 | 读完后做什么 |
| --- | --- | --- |
| [AGENTS.md](../AGENTS.md) | 每次接手项目、写代码或做审查之前 | 确认角色权限、Git 纪律、证据标准与安全边界 |
| [kit/orchestration.md](../kit/orchestration.md) | 派发任务、改状态、审查合并、迁移底座之前 | 对照状态机、G1–G6、C1–C4 执行 |
| [kit/orchestration.md](../kit/orchestration.md) | 每次会话开始时 | 判定底座、恢复真实状态、路由到对应流程 |
| [docs/PROJECT.md](PROJECT.md) | 初始化项目或调整范围 / 验证命令时 | 填好 `edition`、路径边界、验证命令与并发策略 |
| [editions/local/master.md](../editions/local/master.md) / [editions/github/master.md](../editions/github/master.md) | 当前底座的日常操作 | 按底座手册创建、派发、审查、合并任务 |
| [editions/github/issue-template.md](../editions/github/issue-template.md) | 在 `github` 底座创建任务单时 | 按五节写 Issue，缺一节不得进入 `READY` |
| [templates/project-charter.md](../templates/project-charter.md) | 想法模糊、涉及新方向 / 多文件 / 新依赖 / 不可逆操作时 | 先澄清并签署项目章程，再拆任务 |
| [templates/design.md](../templates/design.md) / [templates/contract-decision-log.md](../templates/contract-decision-log.md) | 冻结或修改跨模块契约之前 | 填冻结契约、登记决策（含影响任务与证据），再让下游任务依赖 |
| [templates/milestone-audit.md](../templates/milestone-audit.md) | 阶段收口、契约变更或交接之前 | 逐类给结论并分类，`BLOCKING` / `NEEDS_HUMAN_DECISION` 未清不得宣布完成 |
| [templates/runtime-capability.md](../templates/runtime-capability.md) | 换运行时 / 版本 / 底座 / 执行模式后，或要向子代理派发之前 | 一份可追溯的能力记录：C1–C4 与更细边界的逐条结论、探测时间、证据与限制；未探测项为 `UNKNOWN` |
| [templates/metrics.md](../templates/metrics.md) / [`scripts/report_metrics.py`](../scripts/report_metrics.py) | 解释交付瓶颈、阶段收口要数字、或声明“哪些指标还算不出来”时 | 七项指标的口径与只读报告（带样本量、窗口、缺失率），空数据输出 `no data` |
| `release/README.md` | 准备发布分发包之前 | 按发布标准构建、自检、登记清单 |
