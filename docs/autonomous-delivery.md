# 自主交付执行器（仓库内实验切片）

> 状态：`ACTIVE`，**仅仓库**，不在 `scripts/validate_kit.py` 的 `REQUIRED` 清单中，不随发行包发布。它不替代 `kit/orchestration.md` 的状态机、G1–G6 合并门、Issue 登记块或路径锁，也不构成对这些规则的修改。
>
> 入口：`scripts/autonomous_delivery.py`。它只做一件事：在一个**已授权**的仓库里，对**已存在**的任务 worktree 跑一次「真实执行 → 验证 → 独立审查 → 受门禁的合并 → 合并后验证」的流程，并把可恢复状态写到私有 journal。

## 1. 能做什么、不能做什么

能做：

- 读真实载体：GitHub Issue（`--issue`）作为任务来源；Issue 的「可改路径」段落收窄为允许路径，正文摘要作为任务描述，body 摘要（sha256）作为载体凭据，运行前重新读取并比对。
- 启动真实 OpenCode 会话执行（`opencode run --standalone --format json`），不传 `--auto`，不复用会话。
- 用计划里的命令验证真实 diff；退出码、head 与 diff 指纹都会记录，head 或 diff 在验证/审查期间变动即失败。
- 用**另一个**新会话、以声明为只读（`edit` 被拒绝）的 Agent 做审查；审查必须给出绑定当前 head 与 diff 指纹的机器可读结论，否则不算通过。
- 仅在全部门禁通过时把任务分支合并进配置的目标分支（`merge_into`），并跑合并后验证；已经包含该 head 的目标分支不会被再次合并。
- 失败、`--retry`、`--resume` 与局部阻塞：某个任务失败不阻止无依赖、无路径冲突的任务继续。

不能做 / 不宣称：

- **不是沙箱。** OpenCode V2 的配置可能允许 shell、外部目录、网络与凭据访问；worktree、路径检查与 `--standalone` 都不改变这一点。只对已授权仓库与可信计划使用。
- **不替代 GitHub 任务载体。** 它不会写 Issue 状态、不写 `ORCH-TASK-REGISTRY`、不执行路径锁或资源锁判定，也不调用 GitHub 合并。在 `github` 底座仓库里，最终合并仍必须由 Master 走 PR + 当前 head 的 CI；本工具的 `merge: false` 运行只产出 `APPROVED` 候选。
- **不证明审查模型的独立性。** 「独立」指新会话 + 不同 Agent 身份 + 编辑权限被配置拒绝 + 审查期 worktree/head/指纹未变；不表示换了底层模型。默认两者使用同一模型。
- **看不到 worktree 之外的副作用。** 路径检查只覆盖 Git 能观察到的变更；被忽略文件、外部写入与执行过程中披露的数据无法回滚。

## 2. 计划格式

```json
{
  "merge_into": "integration",
  "merge": true,
  "executor_agent": "build",
  "reviewer_agent": "plan",
  "carrier": {"kind": "issue", "repo": "owner/name", "number": 230, "digest": "<sha256 of body>"},
  "tasks": [
    {
      "id": "one",
      "worktree": "/abs/path/to/task-worktree",
      "prompt": "task description",
      "paths": ["allowed/relative/path", "another.md"],
      "verify": [["python3", "-m", "unittest", "tests.test_x"]],
      "depends": [],
      "attempts": 1
    }
  ]
}
```

约定：

- `worktree` 必须已存在、是同一仓库的 Git worktree 根、分支不是 `main` / `master` / 目标分支、且干净；任务基线必须与目标分支有共同历史。
- `paths` 只接受跨平台无歧义的相对路径：拒绝反斜杠、绝对路径、盘符、`..`、空段、`:`、空白、通配符、`.git` 与 `.orchestration`。
- `verify` 是 argv 列表的列表，不经 shell。它是受信输入，以调用者权限运行；命令文本只以 `argv_sha256` 与程序名写入 journal，避免把可能的凭据写进状态文件。
- `merge` 省略时为 `true`，此时 `merge_into` 必填，且 `--repo` 必须检出该分支。`merge: false` 表示「只准备到 `APPROVED`」，供 Master 走 PR。
- `carrier` 可选；给了就会在运行前重新读取 Issue 并比对 body 摘要，载体被改过即失败关闭。
- 计划一旦写入 journal 就会被哈希固定；改计划后必须换一个 journal，或人工处理旧 journal。

Issue 模式（`--issue`）等价于自动生成单任务计划：`id` 为 `issue-<n>`，允许路径取自「可改路径」段落中的反引号 token，`--allow` 只能**收窄**到这些路径之内，`--verify` 必须显式给出（工具不会凭空发明检查命令）。

## 3. 运行

```bash
# 计划文件；merge:false 时只准备到 APPROVED
python3 scripts/autonomous_delivery.py --repo <目标worktree> --plan plan.json \
  --state ~/.cache/agentorch/delivery-state.json --execute

# 真实 Issue 作为任务来源，收窄到一段路径
python3 scripts/autonomous_delivery.py --repo <目标worktree> --issue 230 \
  --issue-repo <owner/name> --worktree <任务worktree> --allow docs/example.md \
  --verify '["python3","scripts/validate_kit.py"]' \
  --state ~/.cache/agentorch/delivery-state.json --execute

# 中断后：先看 journal，再决定 resume / retry
... --execute --resume
... --execute --retry one
```

退出码：`0` 仅当计划内所有任务都达到 `VERIFIED`；`1` 表示运行完成但存在未验证/阻塞任务（并打印状态摘要）；`2` 表示输入、权限或前置检查失败（未开始执行）。

`--resume` 只从可重证的事实继续：上一个 runner 进程必须已不在（`pid` 检查失败关闭）、执行会话必须有可核验的 `succeeded`，且 head/指纹与记录一致；在执行阶段中断、无法证明时保持 `BLOCKED`，不自动重派。`--retry <id>` 仅对 `FAILED` / `REVIEW_REJECTED` / `BLOCKED` / `UNKNOWN` 且阶段为执行/验证/审查的任务开放；重置前把未提交改动 `git stash`、把旧 head 记录到 `refs/agentorch/delivery/<id>/attempt-<n>`，再 `reset --hard` 回基线，两种备份引用都会写进 journal。

## 4. 合并门（工具内检查）

1. 变更路径全部在 `paths` 内；符号链接、子模块、`.git` / `.orchestration` 一律拒绝；提交后再次复核，防止 hook 扩围。
2. 每条验证命令在当前 head 上退出码为 0，且验证期间 head 与 diff 指纹不变。
3. 审查会话：新会话、与执行会话不同、`opencode session export` 可读（只持久化 outcome / 目录 / Agent / 轮次等元数据，不写转写文本）、`outcome == succeeded`、工作目录等于任务 worktree、assistant 轮次以 `stop` 结束、使用的 Agent 等于声明的 reviewer，且该 Agent 的 `edit` 权限为 `deny`。
4. 审查结论是一个 `json` 代码块，包含 `verdict` / `head` / `diff_sha256` / `findings`；必须 `APPROVE`、绑定当前 head 与指纹、`findings` 为空，并且审查期间 worktree 与 head/指纹未变。
5. 合并前：目标分支仍停在任务派发时记录的 `target_base`，目标 worktree 干净，任务 head 未变；冲突则 `git merge --abort` 并保持 `BLOCKED`。目标分支已包含该 head 时视为已合并，不重复合并。
6. 合并后在同一目标分支上重跑验证；失败时状态停在 `MERGED`，绝不写成 `VERIFIED`。

## 5. 状态与恢复

journal 是私有 JSON（原子替换写入，`flock` / `msvcrt` 跨进程锁；无跨平台锁时拒绝运行）。任务状态：`PENDING`、`RUNNING`、`APPROVED`、`MERGED`、`VERIFIED`、`FAILED`、`REVIEW_REJECTED`、`BLOCKED`、`UNKNOWN`。

journal 里的状态**不被直接信任**：`APPROVED` / `MERGED` / `VERIFIED` 每次使用前都会重新核验 head、指纹、验证退出码、合并提交在目标分支内，以及审查会话与结论；核验不过即降级为 `BLOCKED`，既不解锁依赖也不返回 0。因此手写或复制来的状态不能冒充成功。

## 6. 测试套件清单（本切片）

| 文件 | 覆盖 |
|---|---|
| `tests/test_autonomous_delivery.py` | 真实 Linux 上的 Git 仓库夹具：完整合并闭环与依赖推进；审查会话/身份/目录/结论伪造；验证失败与重启不重派；`--retry` 的可恢复重置；`--resume` 的可重证恢复；合并幂等、目标分支移动、冲突中止；路径越界、反斜杠/盘符/`..`、符号链接与同字节符号链接指纹；journal 伪造状态、预置无哈希 journal、状态写入边界；argv 凭据不外泄；OpenCode 调用参数、尾部不完整事件、超时杀进程组；`session export` 调用与元数据解析。 |
| `tests/test_opencode_probe.py` | 单次探针事件流的保守分类，不构成交付成功。 |
| `scripts/opencode_probe.py` | 探针事件流分类辅助；不被执行器用来判定任务成功。 |

运行：`python3 -m unittest tests.test_autonomous_delivery tests.test_opencode_probe`。这些测试不启动真实模型；真实运行证据另见下节。

## 7. 已知限制与未完成项

- **本仓库（`github` 底座）的最终合并仍属 Master**：本工具的合并门只对 `--repo` 里检出的目标分支生效；本仓库的运行使用 `merge: false`，只产出 `APPROVED`，不碰 `main`。
- **不实现 Issue/PR 状态同步**：不写评论、不加 label、不建 PR；「交付闭环」中的载体更新仍由 Master 按 `editions/github/master.md` 执行。
- **不会自动从阶段计划挑选子任务**：`docs/implementation-plan.md` 是 Markdown 状态表；本工具能读真实 Issue 与显式计划，但不解析阶段计划表来生成任务，也不推断 Issue 内的子步骤。
- **审查 Agent 仍可通过 shell 写文件**：只读性来自 `edit` 权限拒绝与事后 head/指纹核验，不是操作系统隔离；审查期间「写入后恢复」理论上可规避事后检查。
- **同一 journal 之外的并发写者不被阻止**：锁只覆盖同一 journal；不同 journal 同时合并同一目标分支时依靠 Git 自身锁与合并前基线检查，未做全局互斥。
- **Windows 上的进程存活检查**：为避免 `os.kill` 在 Windows 上终止进程，恢复时不做存活探测，直接 fail closed（要求人工确认）。锁使用 `msvcrt`，未在 Windows 真机实测。

## 8. 实测记录（2026-11-13，本机 OpenCode v2.2.7）

以下都是真实调用后的结果，不是模拟。journal 与运行日志都在本机临时目录，不入库。

**A. 本仓库真实任务（`merge: false`，不碰 `main`）**

- 任务：按 Owner 2026-11-13 方向修正 `docs/charter.md` 中与交付执行器冲突的「非目标」表述；允许路径只有 `docs/charter.md`；验证命令 `python3 scripts/validate_kit.py`；计划附带 Issue #230 的 body 摘要，运行前重新读取并核对。
- 基线与结果：任务 worktree 分支 `task/230-charter-scope`，基线 `b5451fa593fa2d660d652e065c14046fe0e725e4`，最终 head `47d7ba556c891e519f6c5d3675b66abe4dc400bf`；执行会话 `ses_e2ac111e1ffetOc5f1rkZy32ud`（agent `build`），审查会话 `ses_e2abdf481ffeM0tYDsqnttTScs`（agent `plan`，`edit` 被拒绝）；两次 `session export` 均为 `outcome: succeeded`，目录都是该任务 worktree；验证退出码 0；审查结论 `APPROVE` 绑定上述 head 与 diff 指纹 `7656c697…`。最终状态 `APPROVED`，CLI 退出码 1（未合并、未 `VERIFIED`，符合预期）。
- 前三次尝试失败并如实保留：① 分类器把工具步骤的 `step_finish: tool-calls` 当成异常，判为失败（worktree 实际已被修改，证据保存为 stash `30295baf…`）；② `session export --sanitize` 会把工作目录脱敏，身份核对失败（证据保存为 stash `33f4fa73…`）；③ 第二次失败留下的改动使 worktree 不干净，被预检拒绝。修正后重跑成功。这三条正是「未知不得当成功」的行为。

**B. 真实内容克隆上的完整合并闭环（`merge: true`）**

- 环境：对本仓库的本地克隆（`git clone --local`），基线 `d99429b6daa68d3f541201934003d2f404c744e1`，目标分支 `delivery/base`（不是 `main`，也不在 AgentOrch 仓库内）。
- 两个任务有依赖：`smoke-doc` 新建 `docs/testing/delivery-executor-smoke.md` 并在 `docs/architecture/repository-map.md` 注册；`cross-ref` 依赖前者，向 `docs/autonomous-delivery.md` 添加交叉引用。各自验证 `python3 scripts/validate_kit.py`。
- 结果：两个任务都 `VERIFIED`；合并提交 `bfd98410481544613fd3a5fb3507ac47773681b4` 与 `61ae525cf07f4287e42908d50bbf0c2b2a505f3d`；合并后验证退出码均为 0；`delivery/base` 上 `validate_kit` 通过。执行/审查会话分别为 `ses_e2ab56f72ffeSTOzgk2gm6wIKP`、`ses_e2ab0db85ffel3g0gWyezCoD2K`、`ses_e2aaef6c3ffeALGsmuXJdkWIsn`、`ses_e2aad1794ffeyH7DHJhnrilhyJ`；两两目录分别等于各自任务 worktree，执行 agent 为 `build`、审查 agent 为 `plan`。
- 幂等重跑：同一计划与 journal 再运行一次，退出码 0，journal 字节不变，没有新增合并提交。

**没有验证、也不宣称的事项**

- 没有对 GitHub 执行任何合并或状态写入：不建 PR、不写 Issue 评论/label、不调用 `gh pr merge`；`github` 底座的最终合并仍由 Master 按 PR + 当前 head CI 完成。
- 没有实现「从 `docs/implementation-plan.md` 自动挑任务」：该文件是 Markdown 状态表，本工具只读显式计划或真实 Issue。
- 审查与执行使用同一底层模型（本机默认 `gemini-4-hydrogen`）；「独立」只按会话、Agent 身份、编辑权限与证据绑定判定。
- Windows、非 Git Bash shell、真实 GitHub CI 触发均未在本轮实测。
