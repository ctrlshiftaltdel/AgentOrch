# 批次交付（Batch Delivery）

> 这是模板：复制到项目的 `docs/` 后再按项目情况编辑。本文件只解释**怎么用**批次交付；任务状态机、合并门、证据标准和 G1–G6 的权威定义只在 `kit/orchestration.md`，本文件引用不重述，也不另立一套规则。

批次交付把多个已登记任务装进一个批次：每个任务仍在自己的 Worktree 中由真实 Agent 执行、用任务自己的命令验证，然后合入**一个**集成分支；整批只做一次独立审查、一个 PR、一次检查门和一次合并。它减少的是每任务的 PR / 审查 / CI 次数，不是审查强度。

## 1. 批次是什么，不是什么

一个批次包含：

- 一个集成分支与一个集成 Worktree；
- 按依赖顺序排列的任务，每个任务有唯一 ID、允许路径、验收标准和验证命令；
- 一段批次级验证、一个独立审查（按风险等级）和一个合并动作；
- 一个位于仓库之外、可恢复的 journal。

它**不是**：

- 沙箱。Worktree、提示词和路径检查都不会限制 Agent 运行时的文件系统、shell、网络或凭据访问。
- 审查的替代品。批次审查仍要逐条核对验收标准；CI 绿灯不能替代验收。
- 自动改主分支的授权。合并仍要求项目所有者给出的授权文本，且必须与计划中的 `finalize.authorization` 完全一致。
- 跨项目写入授权。批次只处理本仓库的计划与路径。

## 2. 计划文件

计划是一个 JSON 文件。命令入口：

```text
python scripts/batch_delivery.py check    --plan PLAN [--repo PATH]
python scripts/batch_delivery.py run      --plan PLAN --journal JOURNAL [--repo PATH] --execute [--only T1,T2] [--retry T1] [--resume] [--timeout SECONDS]
python scripts/batch_delivery.py finalize --plan PLAN --journal JOURNAL [--repo PATH] [--review] [--push] [--merge --authorization TEXT] [--resume] [--checks-timeout SECONDS] [--next-plan PLAN2]
python scripts/batch_delivery.py status   --journal JOURNAL [--json]
```

最小计划：

```json
{
  "version": 1,
  "batch": {
    "id": "example-batch",
    "title": "short title",
    "summary": "why this batch exists and the user-visible result",
    "repo": "/absolute/path/to/repo",
    "base": "main",
    "branch": "task/example-batch",
    "worktree": "/absolute/path/to/integration-worktree",
    "issue": 123,
    "issue_repo": "owner/name"
  },
  "executor_agent": "builder",
  "reviewer_agent": "reviewer",
  "tasks": [
    {
      "id": "T1",
      "title": "one verifiable result",
      "prompt": "full instruction for the executor",
      "worktree": "/absolute/path/to/task-worktree",
      "branch": "task/example-batch-t1",
      "paths": ["src/app.py", "tests/test_app.py"],
      "acceptance": ["AC text, one result per item"],
      "acceptance_checks": [["python", "-c", "assert result_is_present()"]],
      "acceptance_decisions": [{"id": "AC-1", "status": "PASS",
                                "evidence": "locatable evidence for AC-1"}],
      "verify": [["python", "-m", "unittest", "tests.test_app"]],
      "depends": []
    }
  ],
  "finalize": {
    "verify": [["python", "-m", "unittest", "discover", "-s", "tests"]],
    "post_merge_verify": [["python", "-m", "unittest", "discover", "-s", "tests"]],
    "required_checks": ["checks (Python 3.12)"],
    "merge_method": "merge",
    "authorization": "exact owner authorization text",
    "next_plan": null
  }
}
```

规则要点：

- 任务 ID 唯一；任务分支不得是 `main`/`master`、不得与批次分支相同。
- Worktree 用绝对路径，互不相同、互不嵌套，也不同仓库根目录。
- `paths` 是相对 POSIX 路径；不接受绝对路径、`..`、反斜杠、通配符、`.git`、`.orchestration`、`.local`。任务之间不得有路径前缀重叠。
- 依赖只能指向批次中更早的任务，依赖由 `INTEGRATED` 满足，而不是 `VERIFIED`。
- 每个任务必须有非空 `acceptance` 与 `verify`；`finalize.verify` 与 `finalize.post_merge_verify` 也必须非空。
- `acceptance_checks`（可选）是逐条验收标准对应的 argv 列表，长度必须与 `acceptance` 相同。`acceptance_decisions` 只作为可选的计划期上下文，**不能**单独满足 L0 合并门。L0 自动合并必须同时具备完整 `acceptance_checks` 和 `finalize --acceptance-attestation PATH` 提供的执行后 Master 证明；证明文件格式为 `{"schema":"batch-acceptance/1","head":"<sha>","diff_sha256":"<sha>","tasks":{"T1":[{"id":"AC-1","status":"PASS","evidence":"..."}]}}`，head/diff 必须与已审查内容完全一致，每个 AC 都是 PASS 且有可定位证据。缺失、head/diff 不符或任务集合不符时，只能走独立审查或阻塞。
- 任务路径不得覆盖 `docs/PROJECT.md`：项目配置在 journal 中以 SHA-256 绑定，批次内修改会让恢复失效。请把配置变更作为单任务或批次外工作交付。本地任务库中的 batch 内依赖是明确不支持的限制：计划 DAG 不是任务记录，也不能替代任务记录。
- `docs/PROJECT.md` 的 `## 验证命令` 按阶段解析：`快速测试` 与 `静态检查` 的命令必须出现在 `finalize.verify` 或 `finalize.post_merge_verify`；`完整回归` 与 `合并后必须执行` 行上的**每一个**完整命令都必须出现在 `finalize.post_merge_verify`。同一标签行上的多个完整命令全部生效；占位符/散文片段被忽略，但标签行无法解释时直接阻塞。`python`、`python3`、`sys.executable` 视为同一程序，前导 `uv run` 会被忽略。该节不存在时 journal 记录 `project_verification: absent`。
- 授权载体是必需的：`github` 批次必须有 `batch.issue` 与 `batch.issue_repo`，并在任何 Agent 启动前写入 `ORCH-TASK-REGISTRY`（`status: DISPATCHED`）；`local` 批次必须有 `scripts/orchestrator.py` 与每个任务的记录，并在任何 Agent 启动前完成 `READY` / `DISPATCHED`。载体缺失时批次阻塞，不会以 `not_applicable` 假装交付。**`local` 的任务库依赖仍要求 `VERIFIED`；本工具不支持在任务库中登记批内依赖**：遇到这种组合会保持记录不变并阻塞，要求改用单任务/人工流程或等待批次感知的本地派发。`github` 与计划 DAG 的批内 `INTEGRATED` 例外不受影响。
- 密钥形态的路径（如 `.env`、`*.pem`、`*token*`）在预检阶段直接拒绝。
- `merge_method` 取 `merge`、`squash` 或 `rebase`。
- `--merge` 时 `--authorization` 必须与 `finalize.authorization` 完全相等且非空；缺失或不等是预检错误，不会先推送再失败。
- 项目底座由 `docs/PROJECT.md` 的 `edition` 唯一决定。`local` 只在本地合并，不调用 `gh`；`github` 使用 `gh` 的 PR、检查和合并流程。
- 项目允许/禁止路径从 `docs/PROJECT.md` 的「允许改动路径」「永远禁止改动路径」读取。缺失该节时不会声称检查过；journal 记录 `project_paths: unknown`，只做计划级路径检查。journal 同时绑定 `docs/PROJECT.md` 的 SHA-256；run / finalize 之间该文件变化即拒绝继续，必须重新对齐计划。
- `github` 的必需检查来自 `gh api repos/{owner}/{repo}/rules/branches/{base}` 的有效规则，而不是检查运行结果：计划里的 context 必须全部出现在有效规则中。每个 context 还必须在该 head 上有同名 check run，`app.id` 与规则中的 `integration_id` 一致、`status == completed`、`conclusion == success`；检查运行失败、缺失、app 不符或列表被截断（`total_count > len(check_runs)`）都阻塞。推送和合并使用审查过的确切 SHA，并在合并前重新核对 PR base、head、`mergeable == MERGEABLE` 以及 `origin/<base>` 是否仍为已审查 head 的祖先。

## 3. 风险分级

`scripts/delivery_risk.py` 是只读分类器。它只看 NUL 分隔的 `git diff --name-status -z --no-renames`，因此带空格、制表符或非 ASCII 的路径不会被 Git 的引号格式降级；不接受调用方指定的等级。

| 等级 | 典型路径 | 验证 | 审查 |
|---|---|---|---|
| `L0` | `docs/` 下的 Markdown / 文本 / 图片、`assets/`、根目录 Markdown（契约与版本文件除外） | 结构检查与批次验证；有 `acceptance_checks` 时逐条执行 | 不需要第二模型（缺 `acceptance_checks` 时需独立审查） |
| `L1` | 其他 `scripts/`、`tests/`、非 workflow 的 `.github/`、普通源码（`.py`、`.js`、`.ts`、`.go`、`.rs` 等）、常见配置（`package.json`、`Dockerfile`、`.toml`、`.yaml`、`.json` 等） | 增量检查与批次验证 | 一次独立批次审查 |
| `L2` | 契约、`kit/`、`editions/`、`templates/`、项目配置、workflow、发布与运行时控制脚本 | 完整验证 | 必须独立审查 |
| `L3` | 密钥形态路径，或删除许可证文件 | 不自动交付 | 必须人工授权，命令停止 |

未识别的路径按 `L2` 处理，而不是 `L0`：不知道就是更严格。混合等级取最大值；空 diff 为 `L0`。

## 4. 阶段与状态

- `run --execute` 先完成权威派发：`github` 写 Issue 的 `ORCH-TASK-REGISTRY`（保留首次 `dispatched_at`），`local` 走 `task ready` / `task dispatch`；随后才创建/校验任务 Worktree。派发失败时任何 Agent 都不会启动，已取得的锁仍按契约保留并需人工核对。
- `github` 派发前完整枚举开放 PR 与开放 Issue 的登记块（各以 1000 为上限）；任一枚举达到上限即视为不可证明并整体阻塞。每个开放 Issue 必须最多一个可读登记块；活动状态（`DISPATCHED`/`IN_PROGRESS`/`REVIEW_READY`/`MASTER_REVIEW`/`APPROVED`/`BLOCKED`/`NEEDS_HUMAN_DECISION`/`FAILED`）的 `locked_paths` 与本批次并集重叠、或多个/不可读登记块，都整体阻塞；当前批次 Issue 自身除外。恢复时重新执行同一枚举。
- `run --execute` 按稳定依赖顺序：创建/校验任务 Worktree → 启动一个 `opencode run --standalone --format json --agent ...` 会话 → 检查实际改动是否都在声明路径内 → 用任务命令验证 → 在集成分支上 `git merge --no-ff`。
- 合并成功只把任务标为 `INTEGRATED`。这是**内部里程碑**：它只说明改动已进入集成分支，不说明外部交付完成。
- `finalize` 做批次验证、风险分类、审查、推送、PR、必需检查、合并、合并后验证和载体更新。
- 只有合并成功、合并后验证通过、载体（Issue 登记块或本地任务库）更新完成之后，任务才标 `VERIFIED`。`VERIFIED` 才是外部交付完成。
- 合并后验证失败时任务保持 `BLOCKED`，合并记录写为 `merged_but_post_verify_failed`，不会伪装成已验证。

## 5. 一个批次一个 PR

批次只创建一个 PR。PR 正文包含批次摘要、逐任务验收与验证证据、风险等级和原因、审查会话和“没有逐任务 PR”的说明。`required_checks` 中的每个名字都必须是 `SUCCESS`；出现 `FAILURE`、`CANCELLED`、`TIMED_OUT`、`ACTION_REQUIRED` 立即失败；必需检查名始终没出现按 `NOT_RUN` 失败，绝不当作通过。

## 6. 审查要求

- `L0`：不启动第二模型；每个任务必须有覆盖全部 AC 的 `acceptance_checks`，在已审查的 head 上逐条执行并记录 PASS/FAIL。缺 `acceptance_checks` 时必须有独立审查，二者都缺则阻塞；journal 记录 `NOT_REQUIRED_L0`、diff 哈希和逐 AC 证据。
- `L1`：一次独立批次审查。
- `L2` / `L3`：独立审查；`L3` 在审查前就要求人工授权。
- 审查会话必须使用与执行不同的 Agent，且该 Agent 的有效 `edit` 规则为 `deny`；查询失败或规则不是 deny 一律阻塞。
- 审查必须返回唯一的 fenced JSON 裁决，绑定确切的 base、head、diff 哈希和风险等级，逐条验收标准为 `PASS` 且有证据，`findings` 为空。
- 流式输出和 `opencode session export` 必须包含同一份裁决；不一致即拒绝。
- 审查绑定的 head / diff 一旦变化，批准立即失效；推送或合并前会重新计算并拒绝。

## 7. 恢复

journal 是 JSON（`batch-delivery-journal/1`），在仓库与每个 Worktree 之外，原子写入，旁边有进程锁。

- 恢复时先核对 `plan_sha256`；计划变了直接报“plan changed; reconcile manually”，不猜测。
- 中断在提交之前（`RUNNING` 且没有记录 head）：只有 `--resume` 才会重跑。
- 中断在提交之后（`RUNNING` 且有 head）：不自动重跑，标 `BLOCKED`，需要人工检查后显式 `--retry`。
- `--retry` 只接受没有 `integration_sha` 的 `FAILED` 任务；分支上有 journal 未记录的提交时拒绝。
- 锁以操作系统文件锁为准；持锁进程死亡后锁自动释放。锁文件里的 PID 只用于报告，不替代 OS 锁。
- 重复 `finalize` 会先查 PR 状态或 base 祖先关系；已合并就采纳该事实，不再合并第二次。
- 依赖失败时只把依赖它的任务标 `SKIPPED`，无依赖的任务继续；批次退出 1。
- 超时进程树无法证明已停止时，批次整体停止，不启动后续任务。

## 8. 限制（必须如实告知）

- **不是沙箱。** 路径检查、Worktree 和 `--standalone` 不能限制运行时权限；MCP、插件、网络和凭据访问不在证明范围内。
- **transcript 不是防篡改证据。** journal、会话导出和 Git 历史都可被同一用户改写；一致性检查缩小窗口，但不能证明某个会话真的产出了某个提交。
- **hooks / filters 会让提交直接失败。** `git commit` 在进程内部运行 clean/process filter 与提交钩子，Git 没有开关能阻止 clean filter；因此检测到任何会执行的 hook、filter 或 `core.fsmonitor` 时拒绝创建提交，把改动留给人工处理，而不是声称绕过。
- **外部项目改动没有授权。** 批次只按计划路径和项目 `docs/PROJECT.md` 的路径策略工作；策略缺失时记录 `project_paths: unknown`，不声称已检查。
- **审查独立性只是配置检查。** `edit` 为 deny 不代表 reviewer 通过 shell 写文件被阻止；shell 无法区分只读命令。
- 只在 Python 标准库内运行；不引入第三方依赖。

## 9. 与编排契约的关系

任务登记、状态取值、合并门、证据矩阵与 G1–G6 的定义以 `kit/orchestration.md` 为准。本文件只说明批次工具的使用方式；两者冲突时停下对齐，不以工具行为改写契约。
