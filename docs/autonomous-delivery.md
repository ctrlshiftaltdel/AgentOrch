# 自主交付执行器（仓库内实验切片）

> 状态：`ACTIVE`，**仅仓库**，不在 `scripts/validate_kit.py` 的 `REQUIRED` 清单中，不随发行包发布。它不替代 `kit/orchestration.md` 的状态机、G1–G6 合并门、Issue 登记块或路径锁，也不构成对这些规则的修改。
>
> 入口：`scripts/autonomous_delivery.py`。它在一个**已授权**的仓库里，对**已存在**的任务 worktree 跑一次「真实执行 → 验证 → 独立审查 → 受门禁的合并 → 合并后验证」的流程，并把可恢复状态写到私有 journal。

## 1. 能做什么、不能做什么

能做：

- 读真实载体：GitHub Issue（`--issue`）作为任务来源；Issue 的「可改路径」段落收窄为允许路径，正文摘要作为任务描述，body 摘要（sha256）作为载体凭据，运行前重新读取并比对。
- 读项目自身配置：`docs/PROJECT.md` 的 `edition`、`允许改动路径` 与 `禁止改动路径` 是计划的授权边界。计划路径越界或指向禁改路径时拒绝运行；缺失配置时拒绝运行（无法证明授权）。
- 启动真实 OpenCode 会话执行（`opencode run --standalone --format json`），不传 `--auto`，不复用会话。
- 用计划里的命令验证真实 diff；退出码、head 与 diff 指纹都会记录，head 或 diff 在验证/审查期间变动即失败。
- 用**另一个**新会话、以声明为只读的 Agent 做审查；审查结论必须来自该会话自身的 transcript，且绑定当前 head、diff 指纹与逐条验收标准（AC）。
- 仅当目标项目是 `local` edition 且全部门禁通过时，把**已审查的不可变 head SHA**（不是可变分支名）合并进目标分支，并证明合并结果与审查内容一致、合并后验证未失败且未移动目标 head。
- 失败、`--retry`、`--resume` 与局部阻塞：某个任务失败不阻止无依赖、无路径冲突的任务继续。

不能做 / 不宣称：

- **不是沙箱。** OpenCode V2 的配置可能允许 shell、外部目录、网络与凭据访问；worktree、路径检查与 `--standalone` 都不改变这一点。只对已授权仓库与可信计划使用。
- **不在 `github` edition 上自动合并**。`docs/PROJECT.md` 的 `edition` 不是 `local`（或无法读取）时，任何 `merge: true` 计划在运行前被拒绝；`github` 仓库只能 `merge: false` 产出 `APPROVED`，最终合并仍由 Master 走 PR + 当前 head CI。没有实现 GitHub PR/CI 的自动合并。
- **不替代 GitHub 任务载体。** 不写 Issue 状态、不写 `ORCH-TASK-REGISTRY`、不执行路径锁或资源锁，也不调用 `gh pr merge`。
- **不证明审查模型的独立性。** 「独立」指新会话 + 不同 Agent 身份 + 工具级写权限被配置拒绝 + 审查期 worktree/head/指纹未变 + 结论来自 transcript；不表示换了底层模型，也不证明 OS 级隔离。
- **看不到 worktree 之外的副作用。** 路径检查只覆盖 Git 能观察到的变更；被忽略文件、外部写入与执行过程中披露的数据无法回滚。

## 2. 计划格式

```json
{
  "merge_into": "integration",
  "merge": true,
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
      "depends": [],
      "attempts": 1
    }
  ]
}
```

约定：

- `worktree` 必须已存在、是同一仓库的 Git worktree 根、分支不是 `main` / `master` / 目标分支、且干净；任务 head 必须**正好**位于目标基线。落后于基线且没有自身提交时会被安全地前移（不丢弃内容并记录 `baseline_rebased_from`）；已有自身提交则拒绝，绝不 reset。
- `paths` 只接受跨平台无歧义的相对路径：拒绝反斜杠、绝对路径、盘符、`..`、空段、`:`、空白、通配符、`.git` 与 `.orchestration`；且必须落在 `docs/PROJECT.md` 的允许路径内、不命中禁改路径。
- `acceptance` 必填；审查结论必须逐条给出 `PASS` 与证据，任一条不是 `PASS` 就不批准。
- `verify` 是 argv 列表的列表，不经 shell。它是受信输入，以调用者权限运行；命令文本只以 `argv_sha256` 与程序名写入 journal，避免把可能的凭据写进状态文件。
- `merge` 省略时为 `true`，仅 `local` edition 允许；此时 `merge_into` 必填且 `--repo` 必须检出该分支。`merge: false` 表示「只准备到 `APPROVED`」。
- `carrier` 可选；给了就会在运行前重新读取 Issue 并比对 body 摘要，载体被改过即失败关闭。
- 计划一旦写入 journal 就会被哈希固定；改计划后必须换一个 journal，或人工处理旧 journal。

Issue 模式（`--issue`）等价于自动生成单任务计划：`id` 为 `issue-<n>`，允许路径取自「可改路径」段落中的反引号 token，`--allow` 只能**收窄**到这些路径之内，`--verify` 与 `--acceptance` 必须显式给出（工具不会凭空发明检查命令或验收标准）。

## 3. 运行

```bash
# 计划文件；merge:false 时只准备到 APPROVED
python3 scripts/autonomous_delivery.py --repo <目标worktree> --plan plan.json \
  --state ~/.cache/agentorch/delivery-state.json --execute

# 真实 Issue 作为任务来源，收窄到一段路径
python3 scripts/autonomous_delivery.py --repo <目标worktree> --issue 230 \
  --issue-repo <owner/name> --worktree <任务worktree> --allow docs/example.md \
  --verify '["python3","scripts/validate_kit.py"]' \
  --acceptance 'the paragraph matches the owner decision' \
  --state ~/.cache/agentorch/delivery-state.json --execute

# 中断后：先看 journal，再决定 resume / retry
... --execute --resume
... --execute --retry one
```

退出码：`0` 仅当计划内所有任务都达到 `VERIFIED`；`1` 表示运行完成但存在未验证/阻塞任务（并打印状态摘要）；`2` 表示输入、权限或前置检查失败（未开始执行）。

## 4. 不可审查 / 越权合并的防线

1. **目标绑定**：合并只使用审查过的那一个 commit SHA；分支 ref 在审查期间被移动不会改变合并对象。合并后要求该 SHA 是合并提交的第二父、且目标分支 `baseline..merge` 的变更文件集**等于**审查过的路径集。
2. **基线夹带**：派发要求任务 head 正好在目标基线；合并前重新计算 `target_base..head` 的完整文件集，必须与审查路径一致，且 `base == target_base`、目标 head 未移动。执行前已有的范围外提交会被拒绝而不是被夹带。
3. **恢复不等于放行**：`--resume` 在任何外部写入前重新核验会话终局、head/指纹、重新跑验证、重新从 transcript 解析审查结论；`phase=merge` 缺验证或审查证据时保持 `BLOCKED`。
4. **journal 不被信任**：`APPROVED`/`MERGED`/`VERIFIED` 每次使用前从 Git 重算 head/指纹/路径集、重跑验证、重读两个会话的 transcript 并比对记录的结论；核验不过即 `BLOCKED`，既不解锁依赖也不返回 0。`merge: false` 的 `APPROVED` 同样会被核验。
5. **审查身份**：审查会话必须是另一个会话；两段 transcript 与流式输出必须一致；结论必须 `APPROVE`、绑定当前 head 与 diff 指纹、`findings` 为空、每条 AC 为 `PASS` 且有证据；审查后 worktree/head/指纹变动即拒绝。
6. **工具级只读分级**：`merge: false` 要求 reviewer 的 `edit` 被拒绝；`merge: true` 还要求 `shell` 被拒绝（否则 reviewer 可经 shell 写入）。每个任务 worktree 都单独查询 Agent profile，不能只查第一个。未满足时任务 `BLOCKED`，不会自动批准合并。
7. **合并后验证**：固定目标 head 后运行检查；检查期间 head 变动或工作区变脏即失败；失败时状态停在 `MERGED`，绝不写成 `VERIFIED`。
8. **并发写入**：按目标仓库、每个任务 worktree 与 journal 路径分别取得跨进程独占锁；任一被占用即拒绝运行，两个 journal 不能同时写同一仓库或 worktree。

## 5. 状态与恢复

journal 是私有 JSON（原子替换写入，`flock` / `msvcrt` 跨进程锁；无跨平台锁时拒绝运行）。任务状态：`PENDING`、`RUNNING`、`APPROVED`、`MERGED`、`VERIFIED`、`FAILED`、`REVIEW_REJECTED`、`BLOCKED`、`UNKNOWN`。

- `--resume` 处理 `RUNNING` 与 `UNKNOWN`：先确认旧 runner 进程已不在、执行会话可核验，再从 Git 重算 changed paths 与指纹；验证/审查阶段可继续，合并阶段必须能证明全部前置门，否则 `BLOCKED`。
- `--retry <id>` 只对 `FAILED` / `REVIEW_REJECTED` / `BLOCKED` / `UNKNOWN` 且阶段为执行/验证/审查的任务开放；**不 stash、不 reset**。工作区有未提交改动或失败尝试留下提交时直接拒绝，并要求人工检查；只有工作区干净且 head 仍等于基线、旧会话终局可核验时才重新派发。
- 不自动重试、不自动在未知状态下重派。

## 6. 测试套件清单（本切片）

| 文件 | 覆盖 |
|---|---|
| `tests/test_autonomous_delivery.py` | 真实 Git 夹具：完整合并闭环与依赖推进；按不可变 SHA 合并、分支 ref 移动；基线夹带（执行前已有范围外提交）与目标历史文件集核对；恢复/未知的重证；`--retry` 拒绝丢弃数据；审查会话/身份/目录/transcript/结论伪造；逐条 AC；合并后 head 变动；edition 与项目授权；journal 伪造状态、无哈希 journal、写入边界；并发锁；路径越界与符号链接指纹；OpenCode 调用与流分类；`session export` 元数据。 |
| `tests/test_opencode_probe.py` | 单次探针事件流的保守分类，不构成交付成功。 |
| `scripts/opencode_probe.py` | 探针事件流分类辅助；不被执行器用来判定任务成功。 |

运行：`python3 -m unittest tests.test_autonomous_delivery tests.test_opencode_probe`。这些测试不启动真实模型；真实运行证据见 §8。

## 7. 已知限制与未完成项

- **本仓库（`github` 底座）的最终合并仍属 Master**：本仓库只能 `merge: false` 产出 `APPROVED`；没有实现 GitHub PR/CI 自动合并。
- **不实现 Issue/PR 状态同步**：不写评论、不加 label、不建 PR。
- **不会自动从阶段计划挑选子任务**：`docs/implementation-plan.md` 是 Markdown 状态表；本工具只读显式计划或真实 Issue。
- **审查 Agent 的工具级拒绝不等于 OS 隔离**：`shell` 被拒绝时仍可能有 MCP/插件通道；「写入后恢复」理论上可规避事后指纹检查。因此这里只声称工具级只读，且 `merge: true` 只在本机配置的 Agent 确实拒绝 `edit` 与 `shell` 时才开放。
- **验证命令是受信输入**：不按 AC 自动生成检查，也不限制 argv 的程序；AC 只能经独立审查逐条判定，不能由「命令退出 0」单独充当批准。
- **同一 journal 之外的并发写者**：仓库/worktree/journal 锁覆盖本工具的运行；不阻止其他工具或人直接写仓库。
- **Windows**：进程存活检查为避免 `os.kill` 终止进程而直接 fail closed；锁使用 `msvcrt`，未在 Windows 真机实测。

## 8. 实测记录（2026-11-13/14，本机 OpenCode v2.2.7）

以下都是真实调用后的结果；journal 与日志在本机临时目录，不入库。两次运行使用的都是本文件所述门禁（合并按不可变 head SHA、逐条 AC、transcript 结论、工具级只读检查）。

**A. AgentOrch 本仓库（`github` edition，只能 `merge: false`）**

- 任务：按 Owner 2026-11-13 方向修正 `docs/charter.md` 中与交付执行器冲突的「非目标」表述；允许路径仅 `docs/charter.md`；4 条验收标准；验证 `python3 scripts/validate_kit.py`；计划附带 Issue #230 body 摘要并在运行前重新核对。
- 结果：分支 `task/230-charter-gated`，基线 `e0e2e96d74c6a5d7171483fd4774b0d593b26c60`，head `4a5aa6cf918dfa2d7c6c6b01e90422e06341ff53`；执行会话 `ses_e288cdc2fffeurLa5zifUL5gVk`（agent `build`）、审查会话 `ses_e288c0b66ffeGzMeRHMguaxDyM`（agent `scout`，profile 拒绝 `edit` 与 `shell`）；两个会话 `outcome: succeeded` 且目录都是该任务 worktree；验证退出码 0；审查 transcript 中 4 条 AC 全部 `PASS` 且结论 `APPROVE` 绑定该 head 与 diff 指纹。最终状态 `APPROVED`，CLI 退出码 1（未合并、未 `VERIFIED`，符合 `github` edition 的预期）。
- 运行时也确认：同一计划在 `github` edition 上请求 `merge: true` 会在执行前被拒绝并给出 edition 说明；本仓库没有发生任何目标分支或 `main` 变更。

**B. 本地 `local` edition 克隆上的完整合并闭环（`merge: true`）**

- 环境：对本仓库的本地克隆，基线为 `e0e2e96`；为演练合并，克隆里用一次提交 `b35be4b` 把 `docs/PROJECT.md` 的 `edition` 改为 `local`（这只是演练配置，不是对 AgentOrch 仓库的授权变更）。目标分支 `delivery/base`（不是 `main`，也不在 AgentOrch 仓库内）。
- 任务：`smoke-doc`（新建 `docs/testing/delivery-executor-smoke.md` 并在 repository-map 注册）与依赖它的 `cross-ref`（在 `docs/autonomous-delivery.md` 加交叉引用）；验证均为 `python3 scripts/validate_kit.py`；reviewer 为 `scout`（`edit` 与 `shell` 都被拒绝，`merge` 门要求两者）。
- 结果：两个任务都 `VERIFIED`；合并提交 `03153de2469e5abb01a3c24c90057fcaf1c712a1` 与 `222d747203f3e9a10974c2477fd037c6c790b1a9`，各自第二父提交就是审查过的 head；合并后验证退出码 0；`delivery/base` 上 `validate_kit` 通过；两个合并各自相对于基线只包含审查过的文件集。
- 真实失败与重试：`cross-ref` 第一次审查（`ses_e2882c421ffeT3LYy4WIBJB1WR`）没有给出机器可读结论，任务停在 `REVIEW_REJECTED`；`--retry` 在没有 stash/reset、head 与提交原样保留的前提下重跑验证并换了新的审查会话 `ses_e287ef204ffekte6xg6TDYROr2`，逐条 AC `PASS` 后才合并。这个失败与恢复都是真实发生的，不是构造场景。
- 幂等重跑：同一计划与 journal 再运行，退出码 0，journal 字节不变，没有新增合并提交。

**没有验证、也不宣称的事项**

- 没有对 GitHub 执行任何合并或状态写入：不建 PR、不写 Issue 评论/label、不调用 `gh pr merge`；`github` 底座的最终合并仍由 Master 按 PR + 当前 head CI 完成。
- 没有实现「从 `docs/implementation-plan.md` 自动挑任务」：该文件是 Markdown 状态表，本工具只读显式计划或真实 Issue。
- 审查与执行使用同一底层模型（本机默认 `gemini-4-hydrogen`）；「独立」只按会话、Agent 身份、工具级写权限与证据绑定判定，不是模型独立，也不是 OS 隔离。
- 验证命令仍是受信输入；AC 只由独立审查逐条判定，命令退出 0 不能单独充当批准。
- Windows、非 Git Bash shell、真实 GitHub CI 触发均未在本轮实测。
