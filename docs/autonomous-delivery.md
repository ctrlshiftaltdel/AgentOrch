# 自主交付执行器：只准备、不合并的安全切片

> 状态：`ACTIVE`，**仅仓库**，不在 `scripts/validate_kit.py` 的 `REQUIRED` 清单中，不随发行包发布。它不替代 `kit/orchestration.md` 的状态机、G1–G6 合并门、Issue 登记块、路径锁或资源锁。
>
> 入口：`scripts/autonomous_delivery.py`。当前交付的是**证据采集与人工接管切片**：对已授权的任务 worktree 真实执行、验证、独立审查，最后停在 `APPROVED` 等人工/Master 合并。**自动合并未实现，写路径被禁用**。

## 1. 能做什么、不能做什么

能做：

- 读真实载体：GitHub Issue（`--issue`）作为任务来源；Issue 的「可改路径」段落收窄为允许路径，正文摘要（sha256）在运行前重新比对。
- 读项目自身配置：`docs/PROJECT.md` 的 `edition` 必须是 `local` 或 `github`；任务的允许路径必须落在「允许改动路径」内，且不得与「禁止改动路径」互为祖先/后代。缺失或非法配置在派发前拒绝。
- 启动真实 OpenCode 会话执行（`opencode run --standalone --format json`），不传 `--auto`，不复用会话。
- 用计划里的命令验证真实 diff；退出码、head 与 diff 指纹都会记录，head 或 diff 在验证/审查期间变动即失败。
- 用**另一个**新会话、以声明的 Agent 做审查；结论必须来自该会话自身的 transcript，并绑定当前 head、diff 指纹、模式、允许路径原文与每条验收标准原文。
- 失败、`--retry`、`--resume` 与局部阻塞：某个任务失败不阻止无依赖、无路径冲突的任务继续。

不能做 / 不宣称：

- **不能自动合并。** 计划中含 `merge`/`merge_into` 或 CLI 带 `--merge-into` 时，在启动任何 Agent 之前就拒绝运行。原因是本切片没有权威任务锁、原子恢复与可核验的独立授权绑定，无法安全地完成合并与合并后验证。合并仍由 Master 按 PR + 当前 head CI 执行。
- **不写权威载体。** journal 是本地私有记录，不是 GitHub Issue、本地任务库、`ORCH-TASK-REGISTRY`、锁或证据矩阵的替代；它可被复制或重算，因此每次使用都会从 Git 与会话 transcript 重新取证。
- **不是沙箱。** OpenCode V2 可能允许 shell、外部目录、网络与凭据访问；worktree、路径检查与 `--standalone` 都不改变这一点。
- **不证明 OS 隔离或模型独立。** 审查门只检查已知工具动作（`edit`、`shell`）的最终规则与作用域 allow；MCP/插件通道、未知工具与底层模型都没有被证明。
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
# 计划文件（prepare-only；退出码 0 表示全部任务已 APPROVED）
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

退出码：`0` 仅当计划内所有任务都达到 `APPROVED`（可供人工合并；不代表已合并或已验收）；`1` 表示运行结束但存在未批准/阻塞任务；`2` 表示输入、权限或前置检查失败（未开始执行）。

## 4. 审查与恢复门禁

1. **不可变绑定**：审查结论绑定审查时的 commit SHA、diff 指纹、`mode=prepare-only`、允许路径**原文**与每条 AC **原文**；journal/plan 被改写后旧 transcript 不再匹配，任务不能继续被信任。
2. **transcript 为准**：流式输出与 `session export` 得到的 transcript 必须一致；journal 里记录的结论只用于比对，不能单独作为证据。
   - 项目授权在每个写操作前后都重新读取 `docs/PROJECT.md`；即使允许前缀更宽，实际变更路径命中禁改路径（含子文件）也会在提交前被拒绝。
3. **恢复先重证**：`--resume` 处理 `RUNNING` 与 `UNKNOWN`；在运行任何验证命令前，先核验：同一 Git 仓库身份（common dir）、任务包与项目授权（含每个**实际**变更路径）、执行会话的 Agent/目录/终局、审查 Agent 的工具级门禁、worktree head 与指纹。任一不能证明即 `BLOCKED`，不进入审查。
4. **审查门**：`--reviewer-agent` 的 profile 按规则顺序取最终通配规则；`edit`、`shell` 必须最终为 `deny`，且不能存在无法证明范围的作用域 `allow`（例如先 `deny shell:*` 后 `allow shell:rm *`）。未通过时任务 `BLOCKED`，不会启动执行。
5. **重试不破坏数据**：`--retry` 只在没有未提交改动时开放；执行阶段要求 head 等于基线，验证/审查阶段要求 head 等于失败尝试的 head。它不 stash、不 reset；有提交或未提交内容时要求人工处理。
6. **合并检查点不存在**：journal 中出现 `MERGED`/`VERIFIED` 或 `merge`/`post_verify` 阶段时直接 `BLOCKED`，因为本切片不产生也不信任这类状态。
7. **并发**：按目标仓库、每个任务 worktree 与 journal 分别取得跨进程锁；锁位于仓库 Git common dir 下的 `agentorch-delivery-locks/`，不随 `TMPDIR` 变化。任一被占用即拒绝运行。
8. **命令进程树**：验证命令在独立进程组中运行，超时会杀掉整个进程组，避免遗留子进程继续写文件。

## 5. 测试套件清单

| 文件 | 覆盖 |
|---|---|
| `tests/test_autonomous_delivery.py` | 真实 Git 夹具：prepare-only 批准与目标不变；自动合并被拒绝且不启动 Agent；审查会话/身份/目录/transcript/AC 原文/路径集伪造；恢复前重证（失败验证、跨仓库 worktree、错误 Agent、未证只读 profile）；不执行 `reset`/`stash` 且保留 ignored 数据；禁改子路径与实际变更路径；edition 缺失/非法；跨进程锁（不同 `TMPDIR`）；验证命令超时杀掉整个进程树；journal 伪造与合并状态拒绝；argv 凭据不外泄。 |
| `tests/test_opencode_probe.py` | 单次探针事件流的保守分类，不构成交付成功。 |
| `scripts/opencode_probe.py` | 探针事件流分类辅助；不被执行器用来判定任务成功。 |

运行：`python3 -m unittest tests.test_autonomous_delivery tests.test_opencode_probe`。这些测试不启动真实模型；真实运行摘要见 §6。

## 6. 实测摘要（2026-11-14，本机 OpenCode v2.2.7）

真实调用后的摘要，未包含会话标识或个人路径；原始 journal 只在本机受保护目录，不入库。

- 在 AgentOrch 本仓库（`github` edition）对一个文档任务做了 prepare-only 运行：真实执行会话与真实审查会话均为 `succeeded`、目录为该任务 worktree、Agent 不同；验证命令退出 0；审查 transcript 中全部验收标准 `PASS`、结论 `APPROVE` 且绑定该 commit 与 diff 指纹；最终 `APPROVED`，未改动目标分支与 `main`。
- 在同一计划上请求自动合并时，运行在启动 Agent 之前被拒绝（见 §1）。
- 真实失败也保留：同一任务的审查前两次只给出散文而未给出机器可读结论，任务停在 `REVIEW_REJECTED`，没有被当作批准；第三次审查在提示中提供了 runner 已从 Git 核验的 head、diff 指纹与变更路径后，逐条 AC 给出 `PASS` 并 `APPROVE`，最终 `APPROVED`、CLI 退出码 0（仅表示可供人工合并）。
- 另一次自动化尝试被运行前的 edition 检查拒绝，未启动 Agent；依赖任务在上游仅 `APPROVED` 时保持 `PENDING`。

**没有验证、也不宣称的事项**：自动合并、GitHub PR/CI 接线、Issue/PR 状态同步、从 `docs/implementation-plan.md` 自动选任务、MCP/插件通道与 OS 隔离、模型独立、Windows 真机、真实 CI 触发。

## 7. 已知限制

- **合并未实现**是本切片的核心边界；需要合并时由人工/Master 按仓库既有门禁执行。
- 验证命令是受信输入；AC 只由独立审查逐条判定，命令退出 0 不能单独充当批准。
- `APPROVED` 重证依赖保留任务 worktree 与会话记录；worktree 或会话被清理后会保持 `BLOCKED`，而不是跳过核验。
- 审查 Agent 的只读性只覆盖 `edit`/`shell` 两个工具动作的通配规则与作用域 allow；未知工具、MCP/插件与操作系统权限未证实。
- Windows 上进程存活检查直接 fail closed；锁使用 `msvcrt`，未在 Windows 真机实测。
