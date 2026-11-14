# 自主交付执行器：只准备、不合并的安全切片

> 状态：`ACTIVE`，**仅仓库**，不在 `scripts/validate_kit.py` 的 `REQUIRED` 清单中，不随发行包发布。它不替代 `kit/orchestration.md` 的状态机、G1–G6 合并门、Issue 登记块、路径锁或资源锁。
>
> 入口：`scripts/autonomous_delivery.py`。当前交付的是**证据采集与人工接管切片**：对已授权的任务 worktree 真实执行、验证、独立审查，最后停在 `PREPARED` 等人工/Master 审阅与合并。**runner 自身不提供合并写路径**；这不等同于整机安全隔离，见 §1 与 §7。

## 1. 能做什么、不能做什么

能做：

- 读真实载体：GitHub Issue（`--issue`）作为任务来源；Issue 的「可改路径」段落收窄为允许路径，正文摘要（sha256）在运行前重新比对。
- 读项目自身配置：`docs/PROJECT.md` 的 `edition` 必须是 `local` 或 `github`；声明必须是**单行**的列表项，首个词是 `edition`（反引号或 Markdown 强调也算同一词，包括 `**`edition`**` 这类嵌套组合），并以 `:` 或 `：` 带值。只有 `edition` / `` `edition` `` 后接冒号的形式可解析；粗体、嵌套、非冒号等写法仍算一次声明但会被拒绝。值必须整行精确匹配（`github/invalid`、`???`、空值、带尾随文字都算非法）；**换行不能把值带到下一行**。缺失、非法、重复或无法解析的声明数量不为 1 时在派发前拒绝。**每次副作用边界重新读取配置时都会重新验证 edition**：执行/验证/审查/恢复/重试前、每条验证命令前、`git add` 与 `git commit` 之间（读 index）以及提交前后，新增的非法、重复、空值或嵌套歧义声明立即失败关闭，不复用首次检查。carrier 的 `gh` 查询返回后，以及其后派发校验中的**每次连续 Git 调用返回后**，都会在下一个 Git、锁、journal 或 Agent 之前重新证明启动时的 edition 与授权（不是只在序列开始前证明一次）。处理任务期间，runner **同步等待的每一次 Git 调用返回后**也同样重证，因此 `_changes` 里连续三条 `git diff` / `git diff --cached` / `git ls-files` 不会在第一条被内部程序改写 edition 之后继续执行；`core.fsmonitor` hook 与 clean filter 一样属于这类进程内程序。任务的允许路径必须落在「允许改动路径」内，且不得与「禁止改动路径」互为祖先/后代。缺失或非法配置在派发前拒绝。**edition 与授权是共享状态，失败即停整个运行**：任何一次重证发现它们失效、被切换或撤销，都在观察到它的任务上记录一次 `BLOCKED` 后停止整个运行——不再启动下一个任务的 `_root`/`rev-parse`，也不再启动任何 Git、Agent 或 journal 写入，汇总前的批准复核同样不运行；`git add` 自身失败时（clean filter 可在 add 进程内改写配置后让 add 失败）先重证再让失败继续。同一笔写入会把仍 live 的批准一并 `BLOCKED`，因为重证它们需要停止后不允许的 Git 读取与 profile 查询。
- 启动真实 OpenCode 会话执行（`opencode run --standalone --format json`），不传 `--auto`，不复用会话。
- 用计划里的命令验证真实 diff；退出码、head 与 diff 指纹都会记录，head 或 diff 在验证/审查期间变动即失败。
- 用**另一个**新会话、以声明的 Agent 做审查；runner 把 Git 生成的 base..head 完整 diff（文本、受大小上限约束）交给审查者，结论必须来自该会话自身的 transcript，并绑定 base/head、diff 指纹、任务的完整摘要（描述、AC、路径、验证命令、Agent、载体）、模式与每条验收标准原文。
- 失败与局部阻塞：某个任务失败不阻止无依赖、无路径冲突的任务继续。`--retry` 只对**尚未提交**的失败尝试（执行阶段）开放；一旦产生过提交，恢复与重试都失败关闭，见 §4.3。

不能做 / 不宣称：

- **不能自动合并。** 计划中含 `merge`/`merge_into` 或 CLI 带 `--merge-into` 时，在启动任何 Agent 之前就拒绝运行。原因是本切片没有权威任务锁、原子恢复与可核验的独立授权绑定，无法安全地完成合并与合并后验证。合并仍由 Master 按 PR + 当前 head CI 执行。
- **不写权威载体。** journal 是本地私有记录，不是 GitHub Issue、本地任务库、`ORCH-TASK-REGISTRY`、锁或证据矩阵的替代；它可被复制或重算，因此使用时会从 Git 与会话 transcript 重新取证；跨进程读取的旧批准仅用于身份/范围诊断，不构成可信来源。
- **不证明执行产物。** 当前 OpenCode 没有可独立核验的执行产物绑定：Git trailer、提交与 journal 都由同一用户可写，无法证明“某个执行会话产出了这个 head/diff”。因此它不做这种声明；一旦执行产生提交，跨进程的恢复、重试与批准重证都失败关闭（§4.3），不会把可写字段当证明。
- **不是沙箱，也不作绝对保证。** OpenCode V2 可能允许 shell、外部目录、网络与凭据访问；`verify` 的 argv 是受信输入，它可以主动对目标仓库执行 `git merge`、`reset` 或任何外部写操作；Agent/MCP/插件也可以写出 worktree。runner 只约束自己发起的写操作，不能阻止这些通道。
- **不证明 OS 隔离或模型独立。** 审查门按 OpenCode V2 的“最后匹配”语义检查 `edit`/`shell` 的动作通配与作用域 allow；MCP/插件、未知工具与操作系统权限没有枚举，因此只记录为“工具门通过、未知通道未证”，不声称只读或独立。
- **看不到 worktree 之外的副作用。** 路径检查只覆盖 Git 能观察到的变更；被忽略文件、外部写入与执行过程中披露的数据无法回滚。
- **依赖任务不会执行。** 依赖只有达到集成状态（`VERIFIED`）才算满足；本切片不产生该状态，所以依赖任务保持 `PENDING`，不会在缺少依赖变更的基线上开工。

## 2. 计划格式

```json
{
  "merge": false,
  "executor_agent": "build",
  "reviewer_agent": "scout",
  "carrier": {"kind": "issue", "repo": "owner/name", "number": 230, "digest": "<sha256 of body>"},
  "tasks": [
    {
      "id": "one",
      "worktree": "/abs/path/to/task-worktree",
      "prompt": "task description",
      "paths": ["allowed/relative/path", "another.md"],
      "acceptance": ["first criterion", "second criterion"],
      "verify": [["python3", "-m", "unittest", "tests.test_x"]],
      "depends": []
    }
  ]
}
```

约定：

- `merge` 只能为 `false` 或省略；出现 `merge_into` 即拒绝。
- `worktree` 必须已存在、是同一仓库的 Git worktree 根、分支不是 `main` / `master`、干净，且 head **正好**等于目标基线。落后、领先或有未提交改动时拒绝，且**绝不** `reset`、`stash` 或删除；ignored 文件也不会被动。
- `paths` 只接受跨平台无歧义的相对路径：拒绝反斜杠、绝对路径、盘符、`..`、空段、`:`、空白、通配符、`.git` 与 `.orchestration`；计划路径与项目禁改路径重叠时整体拒绝。
- `acceptance` 必填；审查结论必须逐条引用原文并给出 `PASS` 与证据，任一条不是 `PASS` 就不批准。
- `verify` 是 argv 列表的列表，不经 shell。它是受信输入，以调用者权限运行；命令文本只以 `argv_sha256` 与程序名写入 journal。
- `carrier` 可选；给了就会在运行前重新读取 Issue 并比对 body 摘要，载体被改过即失败关闭。
- 计划写入 journal 后其哈希固定；改计划后必须换 journal，或人工处理旧 journal。

Issue 模式（`--issue`）等价于自动生成单任务计划：`id` 为 `issue-<n>`，允许路径取自「可改路径」段落中的反引号 token，`--allow` 只能**收窄**；`--verify` 与 `--acceptance` 必须显式给出。

## 3. 运行

```bash
# 计划文件（prepare-only；退出码 0 表示全部任务已 PREPARED）
python3 scripts/autonomous_delivery.py --repo <仓库worktree> --plan plan.json \
  --state ~/.cache/agentorch/delivery-state.json --execute

# 真实 Issue 作为任务来源，收窄到一段路径
python3 scripts/autonomous_delivery.py --repo <仓库worktree> --issue 230 \
  --issue-repo <owner/name> --worktree <任务worktree> --allow docs/example.md \
  --verify '["python3","scripts/validate_kit.py"]' \
  --acceptance 'the paragraph matches the owner decision' \
  --state ~/.cache/agentorch/delivery-state.json --execute

# 中断后：先看 journal，再决定 resume / retry
... --execute --resume
... --execute --retry one
```

退出码：`0` 仅当计划内所有任务都达到 `PREPARED`（可供人工合并；不代表已合并或已验收）；`1` 表示运行结束但存在未批准/阻塞任务；`2` 表示输入、权限或前置检查失败（未开始执行）。

## 4. 审查与恢复门禁

1. **不可变绑定**：审查结论绑定审查时的 base/head、worktree diff 指纹、runner 展示的完整 diff 指纹、`mode=prepare-only`、允许路径**原文**、每条 AC **原文**，以及任务摘要（描述、验证命令、执行/审查 Agent、载体）。journal/plan 被改写后旧 transcript 不再匹配，任务不能继续被信任。二进制、超过大小上限、或命中项目隐私模式的变更在审查前 `BLOCKED`。
2. **transcript 为准**：流式输出与 `session export` 得到的 transcript 必须一致；journal 里记录的结论只用于比对，不能单独作为证据。
   - 项目授权与 `edition` 在每个写操作前后都重新读取 `docs/PROJECT.md`；即使允许前缀更宽，实际变更路径命中禁改路径（含子文件）也会在提交前被拒绝；期间新增的非法、重复、空值或嵌套歧义 `edition` 声明同样立即失败关闭。共享重证（execute/verify/review/resume/retry/批准）要读 `git status`、`git diff`、`git ls-files` 等 Git 事实，这些调用本身也是外部进程，可以在内部运行 clean filter；因此这些调用返回后、在启动 Agent 或下一条命令之前，配置（`edition` 与授权）会再读一次，失效即 `BLOCKED`，不产生后续副作用。
   - `git add` 与 `git commit` 是两个进程：`add` 返回后、`commit` 启动前，runner 从 **index**（即将被提交的内容）重读 `docs/PROJECT.md` 的 `edition`、重证项目授权与即将提交的路径集。真实 Git clean filter 在 `add` 期间改写暂存内容、或工作区在暂存后发生变化，都在产生历史之前 `BLOCKED`；此时 runner 不 reset，暂存内容留给人工处理。index 重证本身同样通过 Git 进程读取，clean filter 可在其中改写项目配置；因此该重证的 Git 调用结束后、`commit` 启动前还会再次读取配置与授权，失效即 `BLOCKED`，不产生历史。`git add` 自身返回非零（例如 required clean filter 在 add 内失败）时同样先重证配置与授权：失败分支不能把改写过的 edition 当成“本任务自己的问题”而让下一个任务继续启动 Git。
   - **`git commit` 阶段不启动无法证明为惰性的提交**：porcelain `git commit` 会在进程内部刷新 index 时运行 clean filter 与配置的 `core.fsmonitor` 程序，随后运行 pre-commit / prepare-commit-msg / commit-msg 等 hook；它们都在 index 重证之后运行。真实 clean filter 或 fsmonitor hook 可以把 `local` 改成同样有效的 `github`，hook 随后执行，而提交已经存在——事后的 `BLOCKED` 不能抵消。`git commit --no-verify --no-status` 仍会运行 clean filter，Git 没有能可靠隔离它的开关；`--no-gpg-sign` 跳过签名程序、`-m` 跳过编辑器，但都不能关闭 filter、fsmonitor 或 hook。因此启动 `git commit` 前会先证明本次提交不会运行任何 clean/process filter、`core.fsmonitor` 辅助程序或 commit hook；证明不了（存在任一者）就不启动 `git commit`，直接 `BLOCKED` 并保留暂存内容交人工提交。拒绝原因只写配置**键**（如 `filter.foo.clean`、`core.fsmonitor`），不写配置值——值是任意命令行，可能含秘密，且会被写入 journal 与 CLI 摘要。键名只用 `git config --null --name-only --get-regexp` 读取（NUL 分隔）：配置值可以含换行，按物理行切分输出会把值的续行当成另一个键并写进拒绝原因；`core.fsmonitor` 的无值写法（`[core]` 后紧跟 `fsmonitor`）是 Git 的隐式 true，`--get` 只打印空行，仍按启用拒绝，只有显式 false 拼写才算关闭。这是**未实现能力**，不是独立 writer 竞态，也不能靠再读一次配置关闭。
3. **恢复先重证，再失败关闭**：`--resume` 处理 `RUNNING` 与 `UNKNOWN`。在运行任何验证命令前，先核验：同一 Git 仓库身份（common dir）、任务包与项目授权（含每个**实际**变更路径）、执行会话的 Agent/目录/终局、审查 Agent 的工具级门禁、worktree head 与指纹。**执行会话的元数据只是身份检查，不能证明它产出了当前 head/diff**；OpenCode 没有可独立核验的执行产物绑定，Git 历史与 journal 都可写，因此手工提交、可伪造的 trailer 和 journal 字段都不构成证明。只要中断前已经产生提交（`verify` / `review` 阶段），`--resume` 在完成上述可证检查后一律 `BLOCKED`，要求人工检查 worktree 并重做任务；只有尚未提交的执行阶段（无 head 可信任）才会重新派发执行。**跨进程的 `PREPARED` 批准同样失败关闭**：从 journal 读取的旧批准先做同样的非执行检查，然后一律 `BLOCKED`；Git、journal、执行会话元数据与可篡改 transcript 的相互一致既不证明执行来源，也不证明独立审查。拒绝发生在任何验证命令、Agent 启动与目标写入之前；journal 只记录 `BLOCKED` 与原因，不产生验证副作用。**本进程内仍然有效的批准也不是永久有效**：汇总前会逐个重新核验其 head、diff 指纹与 worktree 干净状态；后续任务的受信验证命令若改动了已批准任务，该批准立即 `BLOCKED`，且不会为复核而重跑任何受信命令。这些 Git 事实检查本身也是外部读取，因此**每个批准在 Git 事实之后、以及全部批准检查完后、保存 journal 之前**都会再次重证项目配置（`edition` 与授权），并重新查询 reviewer profile：只要当前配置不再成立、或 profile 不再可证只读，**所有**仍被本进程视为有效的批准一律 `BLOCKED`，而不是只阻断最后检查的那一个。若更早的步骤已经因共享 edition/授权失败停止整个运行，该复核不运行（它需要 Git 读取与 profile 查询）；停止时的同一笔写入已把仍 live 的批准改为 `BLOCKED`。这是收窄窗口，不是消除并发写入：两次读取之间配置仍可被同一用户改写，见 §7。
4. **审查门**：`--reviewer-agent` 的 profile 按规则顺序与 action 通配（如 `s*`）取最终匹配；`edit`、`shell` 必须最终为 `deny`，且不能存在无法证明范围的作用域 `allow`（例如先 `deny shell:*` 后 `allow s*:*`）。**每次** execute/verify/review/resume/retry/批准重证前的副作用边界都重新查询 profile，不按 worktree 缓存；查询失败或结果不再只读时在该命令或 Agent 启动前 `BLOCKED`。profile 属于同一道门，因此**每条验证命令之前都重新查询**（不只读项目配置），且**在导出执行会话之后、启动审查会话之前再查询一次**：导出本身是外部调用，期间被放宽的 profile 不能授权随后启动的审查 Agent。
5. **重试不破坏数据**：`--retry` 只在没有未提交改动时开放；执行阶段要求 head 等于基线，验证/审查阶段要求 head 等于失败尝试的 head。`--retry` 与 `--resume` 共用同一套重证（身份、实际变更路径、授权、reviewer profile、执行会话元数据），并在任何命令或 Agent 启动前停止。已提交的验证/审查尝试不能被重试：原因同 §4.3，无法证明该 head 由执行会话产出。它不 stash、不 reset；有提交或未提交内容时要求人工处理。
6. **合并检查点不存在**：journal 中出现 `MERGED`/`VERIFIED` 或 `merge`/`post_verify` 阶段时直接 `BLOCKED`，因为本切片不产生也不信任这类状态。
7. **并发**：按目标仓库、每个任务 worktree 与 journal 分别取得跨进程锁；仓库/worktree 锁位于仓库 Git common dir 下，journal 锁位于 journal 自身目录且不随 `TMPDIR` 变化，因此同一 journal 指向两个仓库也互斥。锁身份使用规范化后的真实路径：journal 的父目录是符号链接时，`real/journal.json` 与 `alias/journal.json` 仍落到同一个锁文件，不能借别名绕过互斥。它不能阻止其他工具或人直接写仓库。
8. **命令进程树**：验证命令在独立进程组中运行，超时会杀掉整个进程组；但 `setsid` 逃逸的子进程或 Windows 上的子孙进程无法保证被捕获。超时后任务停在 `BLOCKED`，不允许自动重试，需人工确认主机状态。

## 5. 测试套件清单

| 文件 | 覆盖 |
|---|---|
| `tests/test_autonomous_delivery.py` | 真实 Git 夹具：prepare-only 批准与目标不变；自动合并被拒绝且不启动 Agent；审查会话/身份/目录/transcript/AC 原文/路径集伪造；恢复先重证再失败关闭（已提交尝试不可恢复；未提交执行可重新派发；跨仓库 worktree、错误 Agent、未证只读 profile、可伪造 trailer、手工提交）；完整伪造 journal + 重放会话 + 可篡改 transcript 的跨进程 `PREPARED` 批准在验证命令前失败关闭（API 与 CLI，CLI 退出码非 0）；同一运行内 reviewer profile 变化后重新查询并失败关闭（含导出后启动审查前、每条验证命令之前，以及汇总前对已批准任务，均按调用次数或调用发生位置断言）；重试共用实际路径/完整 session 身份/profile 重证；不执行 `reset`/`stash` 且保留 ignored 数据；禁改子路径与实际变更路径；edition 缺失/非法/重复/前缀歧义/跨行/非冒号/嵌套强调与反引号（含相反顺序），以及运行中途（profile 查询、Git 事实读取中的 clean filter、执行提交前、验证后）新增的非法/重复/空值/嵌套歧义声明立即失败关闭；`git add` 与 `git commit` 之间对 index 的重证（真实 Git clean filter 在暂存期间注入重复 edition 时 `BLOCKED`、pre-commit hook 从未运行、暂存内容保留；index 重证自身的 Git 调用内被 clean filter 改写配置时同样 `BLOCKED` 且不产生提交；撤销授权与越界暂存路径同样拒绝）；**commit 阶段惰性证明**（真实 clean filter、pre-commit hook 与 `core.fsmonitor` 配置存在时 `git commit` 从不启动、hook/filter/fsmonitor 标记不出现、无新提交、暂存内容保留，并用同 worktree 的直连 `git commit` 正控证明 filter/hook 与 fsmonitor 攻击真实存在；存在任一者时按未实现能力拒绝而非 writer 竞态；filter 值中的虚构 token 不出现在 journal、拒绝原因与 CLI 摘要，多行值（第二行是秘密）的续行不会被当成另一个配置键）；**连续 Git 调用之间重证**（真实 `core.fsmonitor` 在 `_changes` 的第一条 `git diff` 内切换 edition 后，`git diff --cached` 与 `git ls-files` 都不再启动；同一场景在两个任务下停止整个运行——第二个任务不出现 `rev-parse`、不启动 Agent、保持 `PENDING`，切换后只有一笔 journal 写入；required clean filter 让 `git add` 失败并把 edition 改写成 `github` 时同样停止整个运行）；**无值 `core.fsmonitor`**（linked worktree 的 `config.worktree` 里写无值键，Git 视为隐式 true：不启动 `git commit`、不产生历史、暂存内容保留）；**carrier 返回后的连续重证**（真实 clean filter 在 `gh` 返回后的后续 `git status` 中切换 edition：`gh` 之后的重证与每次连续 Git 调用后的重证在下一个 Git/锁/journal/Agent 前失败关闭）；共享重证里的 `git status` 内 clean filter 使 edition 失效时执行 Agent 不启动；汇总前重证本进程批准任务的 head/diff/干净状态，Git 事实之后再次重证配置/授权、重新查询 profile 并使**所有** live 批准 `BLOCKED`（后续受信命令改动即阻断，且不重跑该命令）；跨进程锁（不同 `TMPDIR`、journal 父目录符号链接别名）；验证命令超时杀掉整个进程树；journal 伪造与合并状态拒绝；argv 凭据不外泄。 |
| `tests/test_opencode_probe.py` | 单次探针事件流的保守分类，不构成交付成功。 |
| `scripts/opencode_probe.py` | 探针事件流分类辅助；不被执行器用来判定任务成功。 |

运行：`python3 -m unittest tests.test_autonomous_delivery tests.test_opencode_probe`。这些测试不启动真实模型；真实运行摘要见 §6。

## 6. 实测摘要（2026-11-14，本机 OpenCode v2.2.7）

真实调用后的摘要，未包含会话标识或个人路径；原始 journal 只在本机受保护目录，不入库。

- **较早候选版本**上，在 AgentOrch 本仓库（`github` edition）对一个文档任务做过 prepare-only 运行：真实执行与审查会话均为 `succeeded`、目录为该任务 worktree、Agent 不同；验证命令退出 0；审查 transcript 中全部验收标准 `PASS`、结论绑定该 commit 与 diff 指纹；最终 `PREPARED`，未改动目标分支与 `main`。该运行的 head **没有被记录**，无法定位到具体提交；相关记录最早出现在提交 `17ca355d781eb89c711a514e4ac2a8747fd1699e`（2026-11-14）。它是历史证据，**不能代表当前候选 head 的完整实现**。
- 同一任务的另一次重跑（同样是较早候选，head 未记录；该记录由提交 `17ca355` 引入）：执行、提交与验证（退出 0）完成，但审查会话在 provider 侧返回 401（`User not found`），没有可用会话与结论；runner 按设计停在 `REVIEW_REJECTED`，没有伪造审查或继续。该失败是外部服务状态，不是审查门的否定结论，**也不是当前候选 head 的运行结果**。
- **当前候选 head：`NOT_RUN`**。本文件没有任何真实运行可以被当作当前候选的实现证据；`PREPARED`、退出码与审查结论都只有上面的历史候选记录，不得把它们归给当前候选。要更新本段，必须先在当前 head 上真实重跑并记录可定位的提交与结果。
- 自动合并请求在启动 Agent 之前被拒绝；依赖任务在上游仅 `PREPARED` 时保持 `PENDING`；edition 缺失/非法在派发前拒绝。

**没有验证、也不宣称的事项**：当前 head 上的真实审查结论、自动合并、GitHub PR/CI 接线、Issue/PR 状态同步、从 `docs/implementation-plan.md` 自动选任务、MCP/插件通道与 OS 隔离、模型独立、Windows 真机、真实 CI 触发。

## 7. 已知限制

- **合并未实现**是本切片的核心边界；`PREPARED` 不等于 Master 审批，CLI 退出码 0 只表示计划内任务都已准备完毕，不解锁依赖、不触发合并。
- **在存在 clean/process filter、`core.fsmonitor` 辅助程序或 commit hook 的仓库里不创建提交**：这是未实现能力，不是 writer 竞态。porcelain `git commit` 在进程内运行这些代码（fsmonitor 在 index 刷新时、hook 在提交时），Git 没有可靠的隔离开关，所以 runner 在启动前证明它们不会运行；证明不了就停在 `BLOCKED` 并把已暂存内容留给人工提交。拒绝原因只列配置键，不列配置值，避免把任意命令行中的秘密写进 journal 或摘要。惰性证明与 `git commit` 启动之间仍保留非原子窗口：另一 writer 可以在这段时间新增 filter / fsmonitor / hook 或改写配置，runner 不声称消除它。要支持这类仓库，需要先有可证明的提交阶段隔离（例如由可信运行时执行过滤、fsmonitor 与 hook，或 Git 提供关闭 filter 的稳定开关），这不在本切片内。
- **执行产物无法跨进程证明**：本切片不写“绑定提交”，也不把任何 Git trailer、提交或 journal 字段当作“执行会话产出了这个 head”的证据。有仓库写权限的人可以手工提交、伪造 trailer 或改写 journal。已提交的 `verify` / `review` 尝试因此不能恢复或重试；从 journal 读取的旧 `PREPARED` 批准也不能跨进程重证，而是一律失败关闭。要真正闭环，需要运行时提供可独立核验的执行产物绑定（例如签名或可信时间戳），这不在本切片内。
- **transcript 不是签名证据**：审查结论来自运行时 `opencode session export`，同一用户权限下仍可被篡改。它比 journal 字段更接近独立证据，因为由运行时持有而非 runner 自述，但不提供防篡改或可信时间戳保证；因此可篡改的 transcript 不能单独把旧 `PREPARED` 记录变回可信批准。
- 验证命令是受信输入；AC 只由独立审查逐条判定，命令退出 0 不能单独充当批准。受信命令也可以写到别的任务 worktree：runner 不会阻止它，只能在本进程汇总前重证已批准任务的 head/diff/干净状态并阻断被改动者；若命令把工作区恢复得与批准时逐字节一致，则没有可观察的变化。
- **没有原子性，也不声称消除并发写入**：每一次“重证”都是一次普通文件或进程读取，没有事务、快照或签名。`docs/PROJECT.md`、worktree、index 与运行时 profile 都可能在同一用户的另一次写入中，于“证明通过”和“随后的动作/保存/输出”之间再次改变；运行时不提供可原子绑定“配置版本 + 提交 + 审查”的原语。runner 能做的是把证明尽量贴近决策点（每条验证命令前、导出后启动审查前、`add` 与 `commit` 之间读 index、Git 事实之后与保存前），并在证明失败时停在 `BLOCKED`；共享 edition/授权失败时还会停止整个运行（见 §1 的 edition 段），但仍不声称证明之后没有任何写入。
- `PREPARED` 只在产生它的同一进程内被当作实时批准；跨进程重证依赖保留任务 worktree 与会话记录，即使全部保留也一律 `BLOCKED`，而不是跳过核验。
- 任务 worktree 必须是独立 linked worktree：等于仓库自身 worktree、或使用仓库当前分支的 worktree 会被拒绝；该检查在所有执行/恢复/重试/重证路径共用。
- 审查 Agent 的只读性只覆盖 `edit`/`shell` 两个工具动作的通配规则与作用域 allow；未知工具、MCP/插件与操作系统权限未证实。
- Windows 上进程存活检查直接 fail closed；锁使用 `msvcrt`，未在 Windows 真机实测。
