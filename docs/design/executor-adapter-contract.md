# 执行体适配契约（Executor Adapter Contract）

> **状态**：由 Issue #274 实现并随代码交付。本文描述**当前 head** 上 `scripts/batch_delivery.py` 已实现的行为；未实现或未实测的部分明确标为限制。
> **范围**：批次入口的**执行**与**独立审查**如何选择运行方式、如何取回终局与会话证据、如何失败关闭。本文不改变任务状态机、路径锁、资源锁、G1–G6、证据矩阵或 CI 必需检查。
> **权威顺序**：项目所有者决定 > `AGENTS.md` > `kit/orchestration.md` > 本文与随包模板。本文与契约冲突时停下对齐，不以工具行为改写契约。

## 0. 术语与总原则

| 术语 | 含义 |
|---|---|
| `command` 传输 | 批次工具用 `subprocess` 启动一个执行体进程，读取它的 stdout 事件流与退出码。 |
| `attached` 传输 | 批次工具不 spawn 执行体；宿主（Master）启动真实执行体，工具通过请求/结果文件与显式 `attach` 命令交接。 |
| 宿主（host） | 实际能启动子代理的运行时，例如 ZCode Desktop 的 `Agent` 工具。 |
| 请求文件 | 工具在等待宿主时写出的 JSON，包含任务、基线、路径、worktree、branch 与 prompt。 |
| 结果文件 | 宿主写出的 JSON，包含会话标识、终局、退出码、报告、产物、实际 head 与 diff 摘要。 |

总原则：

1. **失败关闭**：缺失、重复、未知、不可解析、`unknown`、head/diff 不符、产物不可读，一律按失败或阻塞处理，绝不当作成功。
2. **证据绑定 head**：执行结果绑定任务 base 与真实 head；审查结果绑定被审 base、head、diff 摘要与风险等级。任何字段在 attach 时重新计算并比对。
3. **适配器自报不是证据**：终局、权限、审查结论不能只看宿主声明；工具必须能重新计算 head、diff、AC 覆盖与产物摘要。
4. **不隐式回退**：未显式选择适配器时只使用旧计划的 `opencode` 命令路径；显式选择后全程使用该路径，失败不会偷偷改回 `opencode`。
5. **只在现有门禁内工作**：适配层只决定“如何启动、如何取回”，不新增合并权限，不豁免审查，不降低测试强度。

## 1. 选择、默认与预检

计划字段（可选）：

```json
"executor_adapter": {
  "transport": "command" | "attached",
  "name": "<adapter id>",
  "command": ["<argv>", "..."],
  "capabilities": {"C1": "PASS", "C2": "PASS", "C3": "PASS", "C4": "PASS"},
  "capability_evidence": "<locatable reference>"
}
```

| 情况 | 规范化结果 | 记录 |
|---|---|---|
| 缺省 `executor_adapter` | `transport=command`、`name=opencode`、`source=legacy-default` | journal `adapter` 写明 `legacy-default`；不声明 C1–C4。 |
| 显式 `command` + `name=opencode` | 内置 argv；`command` 必须缺省 | 必须带 C1–C4 全 `PASS` 与证据引用。`BATCH_OPENCODE` 仍是 argv 覆盖。 |
| 显式 `command` + 其他 `name` | 必须给非空 `command` argv | 工具按 argv 原样启动；prompt 走 stdin；环境带 `BATCH_ROLE` / `BATCH_AGENT` / `BATCH_TASK_ID` / `BATCH_BASE_SHA` / `BATCH_WORKTREE`。 |
| 显式 `attached` | `command` 必须缺省 | 必须带 C1–C4 全 `PASS` 与证据引用。 |

约束：

- `transport` 只能是 `command` 或 `attached`；`name` 必须是 `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`。
- 显式选择必须带 C1–C4，任一不是 `PASS` 或 `capability_evidence` 为空即 `InputError`（退出 2），发生在派发与 worktree 写入之前。
- 显式 `command` 适配器的第一个 argv 元素必须是存在文件或可被 `shutil.which` 找到；不满足即退出 2，且不创建 journal、不创建 worktree。
- 旧计划默认**不做**运行时预检：它保持既有 `opencode` spawn 失败行为，不把“没装 opencode”伪装成输入错误。
- `check`、dry run、`status` 只读，不需要安装任何运行时可运行。
- journal 记录规范化后的 adapter；后续 `run` / `finalize` / `attach` 发现 plan 的 adapter 与 journal 不同即拒绝，禁止批次中途更换适配器。

## 2. `command` 传输

### 2.1 事件流与终局

`command` 传输沿用既有单会话事件流判定（`_classify_stream`）：每个非空行是一个 JSON 对象，只允许 `step_start`、`text`、`tool_use` / `tool`、`step_finish`；必须恰好一个终止 `step_finish(reason=stop)`，且之后不得有非空事件。详细规则与失败关闭表见 `docs/design/batch-tool-stop-and-worktree-contracts.md` §1，本文不重述。

适配器差异只在会话标识：

| 适配器 | 会话标识要求 |
|---|---|
| `opencode` | 以 `ses_` 开头的非空字符串（既有行为）。 |
| 通用命令 | 非空字符串即可；不要求产品特定前缀。 |

### 2.2 启动方式

| 项目 | `opencode` | 通用命令 |
|---|---|---|
| argv | `<BATCH_OPENCODE 或 opencode> run --standalone --format json [--agent NAME] [--title T] <prompt>` | 计划里的 `command` 原样启动 |
| prompt | argv 最后一个参数 | stdin 全文 |
| 环境 | 继承，含 `PWD` | 继承，另加 `BATCH_ROLE`、`BATCH_AGENT`、`BATCH_TASK_ID`、`BATCH_BASE_SHA`、`BATCH_WORKTREE` |
| stdout | 事件流 | 事件流 |
| 进程证明 | pid / pgid / token / stopped | 相同 |
| 审查证据 | 事件流 + `session export` + `debug agents`（edit=deny） | 只有事件流；**不**提供独立审查证据提供者 |

因此通用命令适配器可以执行任务，但 `L1` / `L2` 的独立审查必须使用 `opencode` 或 `attached`。通用命令 + `--review` 在 L1/L2 会以 `BLOCKED` 结束，不会降级成自审或跳过审查。

命令传输的 stdin 与 stdout 固定为 UTF-8 严格解码/编码。prompt 写入或 stdout 读取失败（`UnicodeEncodeError` / `UnicodeDecodeError` / 管道 `OSError`）不是“空结果”，工具会先终止子进程并尝试证明停止，再记 `FAILED`；未能证明停止时同既有 timeout 语义一样 halt，绝不留下 `spawning` / `running` 的孤儿记录。

## 3. `attached` 执行

### 3.1 请求

`run --execute` 在权威派发与 `_prepare_task_worktree` 之后写出：

- 路径：`<journal 所在目录>/<journal 名>.adapter/execution-<task>.request.json`（在仓库与所有 worktree 之外）。
- schema：`batch-execution-request/1`。
- 必需内容：`task_id`、`batch_id`、`executor_agent`、`base_sha`（任务基线）、`worktree`、`branch`、`paths`、`acceptance`、`verify`、`prompt`、`result_path`、`diff_contract`、`created_at`、`instructions`。
- 任务状态：保持 `RUNNING`，记录 `awaiting_host = {kind, request_path, request_sha256, created_at}`。
- 进程：工具**不** spawn 执行体；attempt 的 `executor.state` 保持 `pending`，`attempt.adapter` 写明 `awaiting_host` 与 request 摘要。
- 退出：命令以 **3** 结束，并打印请求路径。再次 `run --execute` 只会重复打印同一请求，不追加 attempt，也不启动第二个执行体。

### 3.2 结果

宿主执行后写出：

```json
{
  "schema": "batch-execution-result/1",
  "kind": "execution",
  "request_sha256": "<request 文件字节的 SHA-256>",
  "batch_id": "...",
  "task_id": "T1",
  "session_id": "<宿主会话句柄>",
  "executor_agent": "<与计划一致>",
  "workdir": "<任务 worktree 绝对路径>",
  "terminal": "success | failure | cancelled | unknown",
  "exit_code": 0,
  "report": "...",
  "artifacts": [
    {"path": "<绝对路径或相对 worktree>", "sha256": "<64 hex>", "bytes": 123}
  ],
  "head": "<40/64 hex commit>",
  "diff": {"base": "<base>", "head": "<head>", "paths": ["..."],
           "sha256": "<diff 字节的 SHA-256>"},
  "ended_at": "..."
}
```

约定：

- 顶层字段集合固定；未知字段、重复键、缺失必需字段都拒绝。
- `diff.sha256` 是 `git diff --no-ext-diff --no-color --no-renames <base> <head>` 的 **stdout 原始字节** 的 SHA-256；`diff.paths` 是 `git diff --no-ext-diff --no-renames --name-status -z <base>..<head>` 的路径集合（排序后比较）。
- 成功结果要求执行体自行提交并让 worktree 干净；工具不会替宿主提交未提交改动。
- artifact 可以是任务 worktree 内的相对路径，或 adapter 目录内的绝对路径；必须在读取时字节数与 SHA-256 都匹配。
- 结果文件提交：`batch_delivery.py attach --plan PLAN --journal JOURNAL --result PATH`。

### 3.3 校验与处置

`attach` 按顺序校验；任一失败即失败关闭：

| 检查 | 不满足时 |
|---|---|
| 结果文件可读、严格 JSON、schema/kind 正确、字段集合固定 | `BLOCKED` |
| `request_sha256` 等于请求文件当前字节摘要，且等于 journal 记录的摘要 | `BLOCKED` |
| `batch_id` / `task_id` 与唯一 pending 请求一致 | `BLOCKED` |
| `terminal` 属于四个允许值 | `BLOCKED` |
| `session_id` 非空；`executor_agent` 与计划一致；`workdir` 等于任务 worktree | `BLOCKED` |
| `exit_code` 为整数；`report` 为字符串；成功时 exit 0 且 report 非空 | `BLOCKED` |
| 成功结果的 `head` 是 worktree 中真实 commit、是 base 的后代、等于当前 `HEAD`，且 worktree 仍在任务分支上（detached / 错分支拒绝） | `BLOCKED` |
| 成功结果时 worktree 干净（无未提交/未跟踪改动） | `BLOCKED` |
| 重新计算的 diff 路径与摘要与结果完全一致 | `BLOCKED` |
| artifact 存在、在允许位置、字节数与 SHA-256 匹配 | `BLOCKED` |
| 重新计算的路径通过 `_scope_check`（任务允许路径、禁改路径、密钥形态、项目路径策略） | `FAILED` |
| 验证命令通过且不改变 head、不留下脏 worktree | `FAILED` / `BLOCKED` |

处置：

| 宿主终局 | 任务 | attempt result | 集成 |
|---|---|---|---|
| `success` 且全部校验通过 | `INTEGRATED` | `ok` | 执行验证后 `git merge --no-ff` |
| `failure` / `cancelled` | `FAILED` | `failed` | 不集成 |
| `unknown` | `BLOCKED` | `adapter_rejected` | 不集成，worktree 复用被门禁阻断 |
| 缺失 / 损坏 / 摘要不符 / head 不符 / 越界 / 脏 worktree / 产物不可读 | `BLOCKED` 或 `FAILED`（`_scope_check` 为 `FAILED`） | `adapter_rejected` | 不集成，不复用 |

`adapter_rejected` 是新增的 attempt 结果值；`_attempt_problems` 会让后续 `--retry` / `--resume` 继续拒绝该 worktree，必须人工核对后处理。合法宿主 `failure` / `cancelled` 不标记 `adapter_rejected`，但仍不集成；是否重试仍受既有 worktree / attempt 门禁约束。

### 3.4 记录

- attempt：`attempt.adapter = {transport, kind, state, request_path, request_sha256, session, terminal, result_sha256}`；`attempt.state` / `result` 按上述处置写入，不覆盖旧 attempt。
- journal：`record.execution_session`、`record.adapter_history`（append-only）追加 `{kind, accepted, terminal, session_id, exit_code, request_sha256, result_sha256, head, diff_sha256, artifacts, integrated, at}`；拒绝项 `accepted=false` 并带 `reason`。
- `record.awaiting_host` 在结果被接受或拒绝后清空。

## 4. `attached` 审查

### 4.1 请求

`finalize --review`（L1/L2；L0 只有缺 attestation 且显式 `--review` 时）写出：

- 路径：`<journal>.adapter/review.request.json`。
- schema：`batch-review-request/1`。
- 内容：`base_sha`、`head_sha`、`diff_sha256`、`risk`、`risk_reasons`、每个任务的 `acceptance` / `acceptance_checks` / `verify_commands`、完整 `diff`、`prompt`、`result_path`。`prompt` 明确要求把 `batch-review-result/1` 写到 `result_path`，并列出必需字段；它不再只要求输出 fenced JSON。
- 状态：`state.review = {head, diff_sha256, risk, verdict: "AWAITING_HOST", reviewer_agent, request_path, request_sha256}`。
- 退出：**3**。

### 4.2 结果

```json
{
  "schema": "batch-review-result/1",
  "kind": "review",
  "request_sha256": "<review request 字节摘要>",
  "batch_id": "...",
  "session_id": "<审查会话>",
  "reviewer_agent": "<与计划一致>",
  "workdir": "<batch worktree>",
  "edit_denied": true,
  "declared_executor_sessions": ["<执行会话>"],
  "base": "<base>",
  "head": "<reviewed head>",
  "diff_sha256": "<reviewed diff>",
  "risk": "L0|L1|L2|L3",
  "verdict": "APPROVE|REJECT",
  "tasks": [{"id": "T1", "acceptance": [
    {"id": "AC-1", "criterion": "<exact>", "status": "PASS",
     "evidence": "<locatable>"}]}],
  "findings": [],
  "report": "...",
  "ended_at": "..."
}
```

### 4.3 校验与处置

`attach` 重新计算并比对：

| 检查 | 不满足时 |
|---|---|
| schema/kind/字段集合、request 摘要、batch | `BLOCKED` |
| `reviewer_agent` 与计划一致；`workdir` 等于 batch worktree；`edit_denied` 为 `true` | `BLOCKED` |
| `declared_executor_sessions` 覆盖本批所有已记录执行会话（`execution_session`、`adapter_history.session_id`、attempt 的 executor/adapter 会话） | `BLOCKED` |
| 审查会话不等于任何执行会话，也不出现在声明列表 | `BLOCKED` |
| 结果 `base` / `head` 等于当前 base / head；`head` 仍等于请求时的 head | `BLOCKED` |
| `diff_sha256` 等于重新计算的 reviewed diff | `BLOCKED` |
| `risk` 等于重新计算的风险等级 | `BLOCKED` |
| verdict 为 `APPROVE`，覆盖每个任务与每条 AC，`criterion` 逐字一致，状态 `PASS` 且 evidence 非空，`findings` 为空 | `BLOCKED` |

通过后写入 `state.review`（`APPROVE`、会话、request/result 摘要、`edit_denied`）并追加 `state.review_history`（append-only）。失败写入 `state.review.verdict = "BLOCKED"`，追加拒绝记录，**不得推送或合并**。`_prove_review_current` 只接受 `APPROVE` / `NOT_REQUIRED_L0`，因此 `AWAITING_HOST` / `BLOCKED` 永远不能进入合并。

## 5. journal、恢复与适配器切换

- 新 journal 记录 `adapter`（规范化后的 transport/name/command/source/capabilities/evidence）。
- 旧 journal 没有 `adapter` 时，只有**当前计划也未声明** `executor_adapter` 才按 legacy 默认补写；计划显式选择适配器时拒绝回填（旧 journal 无法证明当时的运行时），必须开新批次。
- journal 已有 `adapter` 且与当前计划不同：`InputError`，拒绝继续；不允许中途换适配器。
- `awaiting_host` 的记录在 `_normalize_interrupted` 中被跳过：它是等待宿主，不是中断，`--resume` 不会把它改回 `PENDING`。
- 有 pending 请求时，`run --execute` 直接返回 3 并打印请求路径，不追加 attempt。
- `attached` 传输**没有工具侧执行超时**：宿主负责给真实执行体/审查体设上限。`--timeout` / `attach --timeout` 只约束 attach 之后的验证命令，不约束宿主的会话；宿主长时间不返回时，工具既不会杀死宿主进程，也不会把等待当成成功。
- `run --execute`、`run --resume` 在有 pending 请求时只重新打印同一请求路径并退出 3；`--resume` 不会把 `awaiting_host` 改回 `PENDING`，也不会启动第二个执行体。
- `attach` 必须找到唯一 pending 请求；没有 pending 请求时退出 2，防止结果重放。
- 适配器拒绝结果会使 attempt 结果为 `adapter_rejected`，复用门禁继续阻断该 worktree，直到人工核对。

## 6. 退出码

| 退出码 | 含义 |
|---|---|
| 0 | 成功；或只读命令完成。 |
| 1 | 部分成功 / 任务失败 / 阻塞 / 宿主结果被拒绝。 |
| 2 | 输入或前置检查非法，尚未写入。 |
| 3 | **等待宿主**：请求文件已写出，等待 `attach`。3 不是成功，不得当作通过。 |

## 7. 矩阵（传输 × 输入 × 终局 × 副作用）

| 传输 | 输入 | 终局 | 副作用 / 证据 |
|---|---|---|---|
| `command` / `opencode`（旧计划默认） | argv 前缀 `BATCH_OPENCODE` 或 `opencode`；prompt 在 argv；stdout 事件流；退出码 | 0 + 恰好一个 `stop` → 成功；非 0 / 空 / 非法 / 缺终局 → 失败；超时优先 → 失败 | 工具 spawn；pid/pgid/token/stopped；`ses_*`；`session export` + profile（edit=deny）；attempt 与 journal 追加 |
| `command` / 通用命令 | 计划 `command` 原样启动；prompt 走 stdin；`BATCH_*` 环境；stdout 同一事件流 | 同一事件流规则；会话标识非空即可；非 0 / 空 / 非法 → 失败；超时 → 失败 + 停止证明 | 相同进程证明；不做 export/profile；L1/L2 独立审查 `BLOCKED`，不降级 |
| `attached` / 执行 | 请求文件（任务、base、路径、worktree、branch、prompt、AC、verify）；退出 3 | `success` / `failure` / `cancelled` / `unknown`；`unknown`、缺失、损坏、摘要/head/diff 不符、产物不可读 → `FAILED` / `BLOCKED`，零集成 | 工具不 spawn；`attach` 校验并验证/集成；attempt 记录会话与摘要；`adapter_history` 追加；拒绝结果阻断复用 |
| `attached` / 审查 | `finalize --review` 写请求（head、diff 摘要与全文、逐条 AC、风险）；退出 3 | `APPROVE` / `REJECT`；head/diff/风险不符、缺 AC、无证据、同会话自审 → `BLOCKED`，不合并 | 重新计算 head/diff/风险；`review_history` 追加；只有有效 `APPROVE` 才可进入原有合并门 |

## 8. 负向场景（必须失败关闭）

- 结果文件缺失、不可读、非 JSON、重复键、未知字段、缺字段。
- `request_sha256` 与请求文件字节不符（请求被改写或结果绑定旧请求）。
- `batch_id` / `task_id` 不符；worktree 或 `workdir` 不符。
- 终局 `unknown`；退出码与终局矛盾（成功但 exit 非 0）。
- head 不是真实 commit、不是 base 后代、不是当前 HEAD；worktree 有未提交改动。
- diff 路径或摘要与工具重算不一致；artifact 不存在、字节数或 SHA-256 不符。
- 改动越界、命中禁改路径、密钥形态路径或项目路径策略。
- 审查 head/diff/风险不符；缺任务或 AC；AC 非 `PASS`；evidence 为空；`findings` 非空。
- 审查会话等于任一执行会话，或未声明所有执行会话。
- 有 pending 请求时重复 `run --execute` 启动第二个执行体；无 pending 请求时重放 `attach`。
- 批次中途更换适配器。

## 9. 明确不声称与限制

1. **不是沙箱**：`attached` 的 `edit_denied` 与 `workdir` 是宿主声明；工具能拒绝同会话自审并重新计算 head/diff，但不能 OS 级阻止审查者写文件。
2. **结果文件可写**：journal、请求、结果与 Git 历史都可被同一用户改写；一致性检查缩小窗口，不构成签名或防篡改。
3. **没有真实宿主的自动化测试**：回归用合成结果文件验证契约；不得把合成夹具写成“真实子代理实机结果”。实机验收按 Issue #274 AC-10 单独取证。
4. **Windows 停止证明**：`command` 传输的停止证明沿用既有平台限制（见 `docs/design/batch-tool-stop-and-worktree-contracts.md` §1.6）；`attached` 传输没有工具侧进程，不产生进程组停止证明。
5. **通用命令审查**：不接受通用命令适配器作为 L1/L2 独立审查来源；必须改用 `opencode` 或 `attached`，否则阻塞。
6. **未改变的门禁**：状态机、路径锁、资源锁、G1–G6、证据矩阵、CI 必需检查、L0/L3 规则均未改变。

## 10. C1–C4 的消费方式

本契约不重新定义 C1–C4，也不在工具内重新探测。显式选择适配器时，计划必须携带既有探测结论：

- `C1` 启动：宿主能返回可寻址会话句柄。
- `C2` 隔离：执行体上下文互不共享。
- `C3` 等待取回：能取回状态、产物、退出码与 head。
- `C4` 终局：成功、失败、取消可区分，未知不算成功。

四条必须全 `PASS`，并在 `capability_evidence` 给出可定位的探测记录。工具只消费这些事实；`attached` 结果中的 `session_id`、`terminal`、`exit_code`、`head`、`diff`、`artifacts` 就是 C3/C4 在单次调用上的落地证据。缺证据或能力不完整时在派发前报缺项，不得开始写 worktree。
