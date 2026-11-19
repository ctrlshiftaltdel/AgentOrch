# 批次工具终止流与工作树边界契约

> **状态**：冻结候选（`FROZEN-CANDIDATE`）：契约文本由 Issue #263 提交冻结审查，合并到 `main` 后才生效；开放决定见 §7。
> **实现状态**：`NOT_IMPLEMENTED`。除标注 `CURRENT` 的规则外，本文要求的行为尚未在 `main` 上实现；不得把本文当作“已实现”或“#261/#262 已修复”的证据。
> **证据基线**：`main` @ `490d31e87aa2f715e95a68fb3b7be358cf90b8c0`。代码位置均相对该 SHA 的 `scripts/batch_delivery.py`。
> **设计输入**：Issue #263 正文；#261 的 5 轮审查 findings；#262 的 5 轮审查 findings。Issue #261 / #262 已取消，**不是**待修复对象。
> **文本约束**：本文只凭文字成立，不依赖图片；所有规则都给出可观察输入与要求结果。

## 0. 标记、术语与总原则

### 0.1 规则状态标记

| 标记 | 含义 |
|---|---|
| `CURRENT` | `main` 当前代码已实现，且可由代码位置或命令输出观察。 |
| `REQUIRED` | 本契约要求、但 `main` 当前代码**未实现**；由后续切片实现，不得提前声称完成。 |
| `NEEDS_HUMAN_DECISION` | 现有证据不足以判定语义，或会改变产品行为 / 权限 / 锁 / 状态机；由项目所有者决定，执行者不得自行选择。 |

### 0.2 总原则

1. **失败关闭（fail-closed）**：任何无法逐字节、逐字段判定为成功的结果，一律按失败处理；`UNKNOWN`、`NOT_RUN`、`BLOCKED`、超时、空输出、不可解析输入都不等于成功。
2. **证据绑定 head**：本文的 `CURRENT` 判断只绑定 `490d31e` 的代码；`head` 变化后必须重新核对，不得把旧结论套到新 head。
3. **契约不授权实现**：本文冻结“输入满足什么条件才算成功、边界如何证明”，不新增命令、不改变状态机、不授权自动合并。
4. **不可判定即拒绝**：字段缺失、重复、未知、分隔符不完整、编码无法还原、路径无法证明包含关系时，要求结果是失败，而不是“尽力解析”。

---

## 1. 终止流契约

### 1.1 适用对象

本契约约束**一次单会话 Agent 调用**：一个 `opencode run --standalone --format json` 进程的 stdout 与退出码。该调用的产物只有两类：可采纳的单会话结果，或失败。批次工具后续的提交、scope、verify、integration 都不改变“流本身是否成功”的判定。

- 观察对象：子进程退出码、stdout 的事件序列。
- 不观察、也不得据以判成功：stderr 文本、进程名、pid 文件是否存在、调用者自述、聊天记录。
- 来源：`scripts/batch_delivery.py:1059 _agent`（启动、超时、`_classify_stream` 调用）、`:918 _classify_stream`；Issue #263 AC-1。

### 1.2 允许的单会话事件类型

每个**非空行**必须是一个 JSON 对象。允许的事件类型只有下列四种：

| `type` | 允许出现的位置 | 必需/可选字段 | 说明 |
|---|---|---|---|
| `step_start` | 一个 step 开始前 | `sessionID` | 打开一个新 step；不得嵌套。 |
| `text` | 打开的 step 内 | `sessionID`、可选 `part.text` | 只追加正文；文本本身不构成终止。 |
| `tool_use` / `tool` | 打开的 step 内 | `sessionID` | 仅表示工具调用事件；不追加正文。 |
| `step_finish` | 打开的 step 内 | `sessionID`、`part.reason` | 关闭 step；`reason` 只能是 `stop`、`tool-calls`、`tool_calls`。 |

`part` 若存在必须是对象；`part.sessionID` 若存在必须等于事件的 `sessionID`。任何其他 `type`、非对象行、无法解析的 JSON、缺失或不可用的 `sessionID` 都失败。

来源：`scripts/batch_delivery.py:929-967`；#262 T1 AC-1（终止后只允许 `retry` 曾被提出，但该放宽不在本契约内，见 §1.9）。

### 1.3 顺序约束

一条可采纳的流必须满足全部约束：

1. 所有事件属于**同一个** `sessionID`，且该值是以 `ses_` 开头的非空字符串。
2. 事件序列由零个或多个“工具轮次”加恰好一个“终止轮次”组成：
   - 工具轮次：`step_start` → 一个或多个 `text` / `tool_use` / `tool` → `step_finish(reason = tool-calls | tool_calls)`；
   - 终止轮次：`step_start` → 零个或多个 `text` / `tool_use` / `tool` → `step_finish(reason = stop)`。
3. `step_start` 之前不得出现 `text` / `tool_use` / `tool` / `step_finish`。
4. 一个 step 未关闭时不得再次 `step_start`；`step_finish` 必须对应一个打开的 step。
5. 终止 `step_finish(reason = stop)` 必须是**最后一个非空事件**；它之后不得再有任何非空事件。
6. 流结束时不得仍有打开的 step。
7. 至少出现过一个 `step_start`；空流不算成功。

来源：`scripts/batch_delivery.py:926-975`；Issue #263 AC-1。

### 1.4 唯一终止 `stop` 的判据

唯一的终止条件是：**恰好一条** `type == "step_finish"` 且 `part.reason == "stop"` 的事件，并且它之后没有任何非空事件。

- “恰好一条”是强制的：第二条第 `stop` 或其后的任何事件都触发 `event after the terminal stop`，整体失败。
- `step_finish(reason = tool-calls / tool_calls)` **不是**终止；它只关闭工具轮次。
- 仅凭退出码 0、会话 id、正文非空、或 `stop` 出现在别的字段里，都不构成终止。
- 终止事件本身不产出正文；正文只来自终止前 step 内的 `text` 事件。

来源：`scripts/batch_delivery.py:957-965、970-977`；Issue #263 AC-1。

### 1.5 失败关闭判定表

下表每一行都是独立规则：只要命中，整次调用失败，且**不得**采纳 session id、正文或任何部分产物。

| ID | 可观察输入 | 要求结果 | 来源 | 状态 |
|---|---|---|---|---|
| STOP-01 | 子进程退出码不是 `0` | 失败；即使 stdout 含合法 `stop` 也不得采纳 | `_classify_stream:922-923` | `CURRENT` |
| STOP-02 | stdout 为空或只有空白 | 失败（`OpenCode produced no events`） | `_classify_stream:924-925` | `CURRENT` |
| STOP-03 | 任一非空行不是 JSON、不是对象、或 `part` 不是对象 | 失败；不得跳过坏行后继续 | `_classify_stream:929-944、968-969` | `CURRENT` |
| STOP-04 | 事件 `sessionID` 缺失、不是字符串、或不以 `ses_` 开头 | 失败 | `_classify_stream:935-937` | `CURRENT` |
| STOP-05 | 同一流出现两个不同 `sessionID` | 失败（`more than one session in the stream`） | `_classify_stream:938-940` | `CURRENT` |
| STOP-06 | `part.sessionID` 既不是 `None` 也不等于事件 `sessionID` | 失败 | `_classify_stream:945-946` | `CURRENT` |
| STOP-07 | `text` / `tool_use` / `tool` 出现在 step 之外 | 失败 | `_classify_stream:952-954` | `CURRENT` |
| STOP-08 | 一个 step 仍未关闭时出现 `step_start` | 失败 | `_classify_stream:948-951` | `CURRENT` |
| STOP-09 | `step_finish` 没有对应的打开 step | 失败 | `_classify_stream:957-959` | `CURRENT` |
| STOP-10 | `step_finish.part.reason` 不属于 `{stop, tool-calls, tool_calls}` | 失败（`unrecognized finish reason`） | `_classify_stream:960-965` | `CURRENT` |
| STOP-11 | 流结束仍有一个打开的 step | 失败（`stream ended inside a step`） | `_classify_stream:972-973` | `CURRENT` |
| STOP-12 | 流从未出现 `step_start` | 失败（`stream never started a step`） | `_classify_stream:970-971` | `CURRENT` |
| STOP-13 | 流结束但没有 `step_finish(reason = stop)` | 失败（`stream ended without a stop finish`） | `_classify_stream:974-975` | `CURRENT` |
| STOP-14 | 终止 `stop` 之后出现任何**非空**事件（`text`、`tool`、`step_start`、`step_finish`、`retry`、未知类型都算） | 失败（`event after the terminal stop`）；不得把 stop 后的输出并入正文，也不得据此判成功 | `_classify_stream:933-934`；#262 第 3/5 轮 finding；#261 第 5 轮“event after the terminal stop” | `CURRENT`（严格拒绝） |
| STOP-15 | 终止之后只有空行 / 空白行 | 空行不是事件；允许；成功与否仍由其他规则决定 | `_classify_stream:930-931` | `CURRENT` |
| STOP-16 | 任一事件类型不在 §1.2 允许集合内 | 失败（`unexpected event type`） | `_classify_stream:966-967` | `CURRENT` |
| STOP-17 | 可采纳流返回的正文 | 只拼接终止前 `text` 事件的 `part.text`；其他事件不得贡献正文 | `_classify_stream:955-956、976-977` | `CURRENT` |

**关于 `retry` 生命周期事件**：#262 第 3 轮观察到第 5 轮审查会话在 `stop` 之后仍出现后台 shell 结果与第二个 assistant 响应；第 4/5 轮确认只忽略 `retry` 不足以覆盖该继续序列。本契约因此**不**把 `retry` 作为终止后允许事件；任何终止后放宽都需要所有者决定并新增回归（§1.9）。

### 1.6 timeout：终止尝试与终止证明分离

“发出 kill”与“证明进程已终止”是两件事，必须分别记录、分别判断：

| ID | 可观察输入 | 要求结果 | 来源 | 状态 |
|---|---|---|---|---|
| STOP-18 | 子进程超过 timeout，`_kill_group` 已发出 kill（POSIX `killpg(SIGKILL)`；Windows `taskkill /F /T /PID`） | 记录“终止尝试已发出”；不得因此判成功 | `scripts/batch_delivery.py:983-1003、1022-1026` | `CURRENT` |
| STOP-19 | `communicate()` 在宽限期内返回，且 POSIX 上 `killpg(pgid, 0)` 抛 `ProcessLookupError` | 才可记“终止已证明”（`stopped = True`） | `scripts/batch_delivery.py:1006-1019` | `CURRENT`（POSIX） |
| STOP-20 | 直接子进程已退出，但进程组仍存在；或只删除了 pid 文件；或只看 return code | **不得**视为终止已证明；必须继续按未证明处理 | #261 第 5 轮 finding 2；`_prove_stopped:1011-1019` | `REQUIRED`（当前实现未覆盖“子进程退出但同组后台进程存活”的全部路径） |
| STOP-21 | `stopped = True` 的超时 | 任务记 `FAILED`；不得记成功；不得把已产生的 stdout 当作可采纳流 | `_agent:1075-1080`；`_execute_task:1736-1738`；`_verify_task:1699-1703` | `CURRENT` |
| STOP-22 | `stopped = False` 或无法证明（含 Windows 当前恒不可证明） | 任务 `FAILED`，并 halt：所有仍 `PENDING` 的任务转 `BLOCKED`；在证明终止前不得复用该 worktree、不得 `--resume`、不得 `--retry` | `_execute_task:1736-1738`；`_verify_task:1700-1703`；`command_run:1933-1937、1952-1958`；`_prove_stopped:1011-1012`（Windows 返回 False） | `CURRENT`（halt 语义）；`REQUIRED`（retry/resume 的前置证明，见 S1） |
| STOP-23 | 超时同时 stdout 中出现 `stop` | 仍失败；超时判定优先于流内容 | `_agent:1075-1080`；`_classify_stream` 未被调用 | `CURRENT` |
| STOP-24 | Windows 上 `_prove_stopped` 的当前实现 | 恒返回 `False`；所有超时都按“未能证明终止”处理（halt）。这是平台限制，不是成功 | `scripts/batch_delivery.py:1011-1012` | `CURRENT`；平台证明能力 `NEEDS_HUMAN_DECISION` |

**处置顺序**：先判超时；超时即失败。只有非超时才读取 stdout 并进入 §1.2–§1.5。`stopped` 只决定“是否允许继续使用 worktree 和是否 halt”，从不决定流是否成功。

### 1.7 `--resume` / `--retry` 的允许与拒绝条件

| ID | 操作 | 允许条件 | 拒绝条件（任一命中即拒绝） | 来源 | 状态 |
|---|---|---|---|---|---|
| STOP-25 | `--resume` | 仅用于“协调器中断”而非“执行完成”：journal 记录为 `RUNNING`，且没有已记录的 head / 已采纳的流 / 已证明的 stop；分支仍位于该次尝试的 base；执行器与验证器均已证明停止 | 已出现合法 `stop`（执行已完成，不是中断）；已有 `head` / `observed_head` 证据；已 `INTEGRATED`；`integration_sha` 非空；流因 §1.5 任一原因失败（应走 retry）；执行器/验证器存活未证明；worktree 不干净 | `_normalize_interrupted:1812-1825`；#261 第 5 轮 finding 1/2；Issue #263 AC-1 | `CURRENT`（仅 RUNNING + 无 head + `--resume`）；其余条件 `REQUIRED` |
| STOP-26 | `--retry` | 任务状态为 `FAILED`；未 `INTEGRATED`；`integration_sha` 为空；分支 head 等于该次尝试已记录的观测 head（无记录时按 §S2 的 attempt base 规则）；执行器/验证器已证明停止；worktree 干净 | 任务不是 `FAILED`；已 `INTEGRATED`；`integration_sha` 非空；分支 head 有 journal 未记录的提交；执行器/验证器存活未证明；超时终止未证明；把“缺少 stop 的流”当成可重放成功 | `_apply_retry:1765-1793`；#261 第 5 轮 finding 3/4；Issue #263 AC-1 | `CURRENT`（基础条件）；观测 head / attempt base / 存活证明 `REQUIRED`（S1/S2） |
| STOP-27 | 终止后的任何重放 | 每次 `--resume` / `--retry` 都创建新 attempt，`attempts` 递增；旧 attempt 的证据保留，不得清空后假装没有发生 | 复用旧 session 结果；把旧 head 当作新 head 的通过证据；用 `--resume` 绕过失败流 | #261 第 5 轮 finding 4/5；`command_run:1939-1940` | `CURRENT`（`attempts` 递增）；attempt 历史 `REQUIRED`（S1） |
| STOP-28 | 失败流后的任何继续 | 只有在 §1.5 判定失败后，才允许把任务标为 `FAILED`；不得以“stdout 有 stop 片段”或“进程退出 0”翻案 | 把 `FAILED` 写成 `INTEGRATED`；把失败流的正文当作交付内容 | `_execute_task:1739-1741`；#261 第 5 轮 finding 6 | `CURRENT` |

**关键约束**：`--resume` 与 `--retry` 都**不能**让一条违反 §1.2–§1.5 的流变成成功。它们只能开启新的 attempt；新 attempt 必须重新产生合法流。

### 1.8 OpenCode 终止后事件与语义未决项

| ID | 问题 | 现有证据 | 处置 |
|---|---|---|---|
| STOP-HD-1 | `stop` 之后 OpenCode 还可能发出哪些事件？ | #262 第 3 轮：真实会话导出包含后台 shell 结果与第二个 assistant 响应；原始 raw stream 不可得，无法穷举首个触发事件 | `NEEDS_HUMAN_DECISION`：是否允许任何终止后事件；若允许，允许的精确类型、顺序、正文处理与回归 fixture 必须由所有者先决定。当前契约按 STOP-14 全部拒绝。 |
| STOP-HD-2 | 是否只允许 `retry` 生命周期事件（#262 T1 的实现方向）？ | #262 第 4 轮判定 T1 AC-1/2/3 PASS，但第 5 轮仍因工作树边界失败；该放宽未合并。第 3 轮同时指出 `retry`-only 不足以覆盖真实继续序列 | `NEEDS_HUMAN_DECISION`：不得把“retry-only”写成已冻结契约或已修复。 |
| STOP-HD-3 | `step_finish.reason` 是否还有 `stop` / `tool-calls` / `tool_calls` 之外的合法值？ | 现有代码只接受这三个值（`_classify_stream:962-965`）；未对全部 provider / 版本穷举 | `NEEDS_HUMAN_DECISION`：新增值必须由真实流证据 + 新契约冻结引入；当前按未知 finish reason 失败（STOP-10）。 |
| STOP-HD-4 | 一次调用是否可能出现多个 `stop`？ | 现有代码在第二个终止事件处失败（STOP-14）；未观察到合法多 stop | 当前契约要求恰好一个；若真实运行出现多 stop，按失败处理，需所有者决定是否改契约。 |
| STOP-HD-5 | Windows 上如何“证明”进程组终止？ | 当前 `_prove_stopped` 对 Windows 恒返回 False（`1011-1012`）；没有平台级证明 | `NEEDS_HUMAN_DECISION`：是否实现 Windows 证明、使用何种句柄/作业对象；在此之前 Windows 超时一律 halt。 |

---

## 2. 工作树边界契约

### 2.1 观察对象与字节级要求

边界必须来自**一次完整的** `git worktree list --porcelain -z` 输出，而不是路径字符串、单个 worktree 的 `.git`、或调用者的当前目录。

- 必须以二进制读取 stdout；不得使用文本模式、`splitlines()`、`strip()`、Unicode 归一化或 shell 管道。
- 必须按 NUL 字段解析，不得按换行拆分。
- 路径必须按文件系统编码解码；无法逐字节还原的输入失败关闭。
- 命令失败、输出为空、输出不以完整记录结束，一律失败。
- 只解析每条记录开头的 `worktree` 路径、忽略其余字段，**不算通过**（§2.7）。

来源：#262 第 4/5 轮 findings；Issue #263 AC-2。当前 `main` 只在 `_resolve_journal:813-818` 用仓库路径 / worktree 路径做包含检查，**没有**解析 `git worktree list --porcelain`，因此本组规则整体为 `REQUIRED`。

### 2.2 记录语法（实测格式）

在本仓库实测（`git 2.55.0.windows.3`）的单记录输出为：

```text
worktree D:/Users/.../AgentOrch<NUL>HEAD 490d31e...<NUL>branch refs/heads/main<NUL><NUL>
```

即：每个字段以单个 `<NUL>` 结束；一条记录以**额外一个空字段**结束，表现为连续两个 `<NUL>`。多记录之间也是连续两个 `<NUL>`。不得假设末尾有换行。

| ID | 规则 | 可观察输入 | 要求结果 | 来源 |
|---|---|---|---|---|
| WT-01 | 读取方式 | 使用 `git worktree list --porcelain -z` 的字节 stdout | 使用 `-z`；拒绝无 `-z` 的文本解析；命令失败或空输出即失败 | #262 第 4 轮 high finding；AC-2 |
| WT-02 | 字段分隔 | 单 `<NUL>` 分隔字段，双 `<NUL>` 结束记录 | 严格按此拆分；不得 `splitlines()` / `strip()`；EOF 落在记录结束符之前即“记录被截断”，失败 | #262 第 4/5 轮；AC-2 |
| WT-03 | 必需字段 | 每条记录必须恰好有一个 `worktree <path>`、恰好一个 `HEAD <sha>`，以及恰好一个 `branch <ref>` **或** 恰好一个 `detached` | 缺失、重复、二者并存（`branch` + `detached`）都失败 | #262 第 5 轮 high finding；AC-2 |
| WT-04 | `worktree` 值 | 非空、可解码、无内嵌 NUL | 空值或解码失败 → 失败；不得用空路径参与包含判断 | AC-2；#262 第 4 轮 |
| WT-05 | `HEAD` 值 | 40 位或 64 位小写十六进制对象 ID | 其他形状 → 失败；不得只检查非空 | #262 第 5 轮；AC-2 |
| WT-06 | `branch` 值 | 以 `refs/heads/` 开头的非空 ref | 其他 ref 形状或空值 → 失败 | #262 第 5 轮；AC-2 |
| WT-07 | `detached` | 独立的无值字段 | 带值、重复、或与 `branch` 并存 → 失败 | #262 第 5 轮；AC-2 |
| WT-08 | 已知可选字段 | `bare`、`locked [reason]`、`prunable [reason]` | 只接受这些字段；`bare` 的处置见 WT-HD-3（失败关闭） | #262 第 5 轮；AC-2 |
| WT-09 | 未知字段 | 任何不在 WT-03 / WT-08 内的字段关键字 | 失败关闭；不得忽略未知字段后继续 | #262 第 5 轮；AC-2 |
| WT-10 | 记录分隔 | 记录之间必须有一个空字段（双 NUL） | 缺失分隔符、sibling 的 `worktree` 字段被并入上一条记录时，必须失败；不得把 sibling 吞进同一记录 | #262 第 5 轮 finding 1；AC-2 |
| WT-11 | 记录完整性 | 每个记录必须构成完整三元组并通过 WT-03–WT-09 | 任一记录不完整 → 整次边界计算失败；不得只使用能解析的前缀 | #262 第 5 轮；AC-2 |
| WT-12 | 记录集合 | 至少一条记录；当前仓库的 `git rev-parse --show-toplevel` 必须在集合中恰好出现一次；所有 `worktree` 路径不得重复 | 否则失败 | AC-2 |
| WT-13 | 路径解码 | 文件系统编码 + 不可解码字节的安全还原（如 `surrogateescape`） | 解码失败或路径无法解析为真实路径 → 失败；不得丢弃该记录 | #262 第 4 轮；AC-2 |
| WT-14 | 路径比较 | 逐组件包含判断，而不是字符串前缀 | `C:\repo-evil` 不得被当作位于 `C:\repo` 内；无法证明包含关系 → 失败 | #262 第 4 轮；AC-2 |
| WT-15 | Windows 平台 | Windows 路径大小写、分隔符与 reparse point 行为 | 契约要求逐字节对齐与真实路径解析；平台未逐项实测的部分标 `NEEDS_HUMAN_DECISION`，不可判定即失败 | AC-2；#262 第 4 轮 |

### 2.3 linked worktree 的 gitdir 与 common dir

对每条记录（`bare` 除外）都必须完成下列解析；任一失败则整次边界计算失败。

| ID | 规则 | 可观察输入 | 要求结果 | 来源 |
|---|---|---|---|---|
| WT-16 | 当前 worktree 的 git dir | `git -C <worktree> rev-parse --git-dir` | 必须成功并解析为真实路径；失败即失败 | AC-2 |
| WT-17 | common dir | `git -C <worktree> rev-parse --git-common-dir` | 必须成功；所有记录的 common dir 必须解析为同一真实路径 | AC-2 |
| WT-18 | 主 worktree | 主 worktree 的 `--git-dir` | 必须等于 common dir；不相等 → 失败 | AC-2 |
| WT-19 | linked worktree 的 `.git` | linked worktree 根下 `.git` 必须是文件，内容恰好一行 `gitdir: <path>` | 缺失、是目录、多行、空值、无法读取 → 失败 | AC-2 |
| WT-20 | gitdir 目标 | `.git` 中的 `<path>` 允许相对路径；相对路径以 worktree 根为基准解析 | 必须解析为真实路径，且位于 `<common>/worktrees/` 之下；否则失败 | AC-2 |
| WT-21 | 管理目录回指 | `<common>/worktrees/<name>/gitdir` 若存在，必须指回该 worktree 的 `.git` 文件 | 回指不一致或不可读 → 失败 | AC-2 |
| WT-22 | 管理目录纳入边界 | `<common>/worktrees/` 及其所有内容 | 必须纳入“禁止写 prompt 临时文件”的边界集合 | AC-2；#262 第 3 轮 |

### 2.4 `core.worktree` 与 `extensions.worktreeConfig` 的重定位边界

| ID | 规则 | 可观察输入 | 要求结果 | 来源 |
|---|---|---|---|---|
| WT-23 | `core.worktree` 生效值 | `git -C <worktree> config --get core.worktree` | 退出码 1（未设置）允许；其他非零退出或读取错误 → 失败；返回值非空时，按 Git 语义解析为真实路径并**加入**边界集合；无法解析 → 失败 | AC-2 |
| WT-24 | 重定位不替代 porcelain | `core.worktree` 指向另一个根 | 不得用 `core.worktree` 覆盖或丢弃 porcelain 记录中的 `worktree` 路径；两者都必须作为边界 | AC-2 |
| WT-25 | `extensions.worktreeConfig` 关闭 / 缺失 | `git config --get extensions.worktreeConfig` 不是 `true` | 退出码 1（未设置）允许；其他非零退出或读取错误 → 失败；不读取 `config.worktree` 作为权威；若存在仍不得据此缩小边界 | AC-2 |
| WT-26 | `extensions.worktreeConfig = true` | 每个 worktree 的 `$GIT_DIR/config.worktree` | 文件不存在允许（表示无 per-worktree 配置）；存在但不可读、解析失败、或其中的 `core.worktree` 无法解析 → 失败 | AC-2 |
| WT-27 | 每 worktree 配置的查询方式 | `git -C <worktree> config --worktree --get core.worktree`（扩展启用时） | 退出码 1（未设置）允许；其他非零退出或读取错误 → 失败；非空值按 WT-23 加入边界 | AC-2 |
| WT-28 | 配置作用域不可判定 | 系统 / 全局 / 本地 / worktree 配置中任一读取失败 | 失败关闭；不得假设“没有重定位” | AC-2 |

### 2.5 prompt 临时文件边界

| ID | 规则 | 可观察输入 | 要求结果 | 来源 |
|---|---|---|---|---|
| WT-29 | 边界集合 | 所有 porcelain 记录的 `worktree` 路径、所有解析后的 `core.worktree` 根、所有 worktree 的 git dir、common dir、`<common>/worktrees/` 管理目录 | prompt 临时文件必须位于**全部**这些路径之外；不能只检查当前 task worktree 或主 checkout | #262 第 3 轮 medium finding、第 4 轮 high finding |
| WT-30 | 创建时机 | 先完成 §2.2–§2.4 的全部解析 | 任一记录 / 路径 / 配置不可判定时，**不得创建**临时文件；直接失败 | #262 第 3/4 轮 |
| WT-31 | 包含判断 | 真实路径 + 组件级比较 | 字符串前缀、只比较当前 worktree、或把 sibling checkout 排除在集合外，都算失败 | #262 第 4/5 轮；WT-14 |
| WT-32 | 失败回退 | 首选临时目录不在边界外，或无法证明 | 失败关闭；不得回退到仓库、worktree、common dir 或当前目录 | #262 第 3/4 轮 |
| WT-33 | 大 prompt 传输 | prompt 超过实现声明的阈值（#262 使用 100,000 UTF-8 bytes） | 必须通过 `--file <path>` 或等价的文件传输；不得把完整 prompt 放进 argv | #262 T1 AC-2；当前 `_agent:1065` 仍直接 `argv.append(prompt)`，故为 `REQUIRED` |
| WT-34 | 清理 | 进程结束（含失败、超时、取消） | 临时文件必须删除；删除失败不得判成功，且必须保留 timeout / stopped 事实 | #262 T1 AC-2、第 2/3 轮 finding |
| WT-35 | 文件内容 | 临时文件字节 | 必须等于完整 prompt 的 UTF-8 字节；截断或追加 → 失败 | #262 T1 AC-2 |
| WT-36 | 文件名 | 临时文件名 | 不得由 prompt 内容、worktree 路径或用户输入拼接；必须不可预测且独占创建 | #262 第 3 轮 |

### 2.6 `git worktree list --porcelain` 异常输入判定表

| 输入 | 要求结果 |
|---|---|
| 命令退出非 0 | 失败 |
| stdout 为空 | 失败 |
| 无 `-z`，只有换行分隔文本 | 失败（解析方式不符合 WT-01/WT-02） |
| 记录缺少结束空字段（EOF 截断） | 失败（WT-02） |
| 两条记录之间只有一个 NUL | 失败；不得猜测记录边界（WT-10） |
| 记录缺 `HEAD` | 失败（WT-03） |
| 记录同时有 `branch` 与 `detached` | 失败（WT-03/WT-07） |
| 记录有未知字段（例如未来 Git 新增字段） | 失败（WT-09） |
| 同一字段出现两次 | 失败（WT-03） |
| `worktree` 路径含换行或尾随空格 | 必须按原始字节保留；不得 `strip()`；若因此无法与真实路径对齐 → 失败（WT-13） |
| sibling 记录的 `worktree` 被并入上一条 | 失败；该 sibling 不得被排除在边界外（WT-10） |
| 当前仓库 root 不在记录集合中 | 失败（WT-12） |
| 任一 `core.worktree` / `config.worktree` 不可读 | 失败（WT-23–WT-28） |

### 2.7 “只解析路径”为什么不算通过

以下实现都**不满足**本契约，即使它们在正常仓库上得到正确路径：

1. 只取每条记录第一行 `worktree <path>`，不校验 `HEAD`、`branch`/`detached`、终止空字段。
2. 用 `splitlines()` 解析：合法路径中的换行会把一条记录拆成两条，尾随空格会被 `strip()` 去掉。
3. 用 `split('\n\n')` 分记录：无法发现缺失的双 NUL， sibling 的 `worktree` 字段会被吞进上一条记录。
4. 只检查当前 task worktree 或主 checkout，不枚举 linked worktree / common dir。
5. 只做字符串前缀比较，不解析真实路径与 `core.worktree`。
6. 对 malformed / truncated 输出“尽量使用能解析的部分”。

来源：#262 第 4/5 轮 findings；Issue #263 AC-2 的“明确只解析路径不算通过”。

### 2.8 未验证的平台行为

| ID | 未验证项 | 当前处置 |
|---|---|---|
| WT-HD-1 | Windows 下 `git worktree list --porcelain -z` 对含换行 / 尾随空格 / 非 UTF-8 字节路径的逐字节输出 | 契约要求逐字节对齐；不可判定即失败。该平台行为未逐项实测，标 `NEEDS_HUMAN_DECISION`，实现切片必须补真实回归。 |
| WT-HD-2 | Windows reparse point / junction / 大小写不敏感文件系统下的真实路径与包含判断 | 必须解析真实路径；无法证明包含关系即失败。具体 API 与大小写规则未冻结，标 `NEEDS_HUMAN_DECISION`。 |
| WT-HD-3 | `bare` 仓库的 `git worktree list --porcelain` 记录 | 当前契约要求失败关闭（WT-08）；是否为 bare 仓库定义边界属于产品语义，标 `NEEDS_HUMAN_DECISION`。 |
| WT-HD-4 | `git` 版本差异（`-z` 可用性、字段集合） | 只接受能通过完整记录校验的输出；版本不支持 `-z` 或字段不符即失败。 |

---

## 3. AC5 切片

### 3.0 共同约束

1. 每个切片一个独立 Issue、独立分支 / worktree、独立审查；不得合并成一个批次。
2. 每个切片只允许实现自身目标；**禁止**夹带 #262 类工具修复（终止流解析、prompt `--file`、worktree porcelain 解析），也禁止夹带另一片的状态语义。
3. 每个切片的允许路径仅 `scripts/batch_delivery.py` 与 `tests/test_batch_delivery.py`；文档、模板、`kit/`、`editions/`、`AGENTS.md`、`docs/PROJECT.md`、`.github/` 一律禁改。文档同步另开任务。
4. 两片都基于包含本契约的 `main`；若契约在实现前被修改，切片必须重新基线，不得沿用手工假设。
5. 风险等级均为 `L2`（运行时控制与恢复状态机）；必须独立审查 + 完整回归，不得自动合并。
6. 每片都必须保留旧行为回归；不得删除断言、跳过测试或弱化失败条件。

### 3.1 S1 — executor / verifier 生命周期与 attempt 历史

| 项目 | 内容 |
|---|---|
| **单一目标** | 让每次 executor / verifier attempt 的生命周期可持久、可证明：每个 attempt 有独立记录与 pid/pgid；只有证明上一 attempt 的进程组与验证器都已停止，才允许复用 worktree、`--resume` 或 `--retry`；记录缺失 / 损坏即失败关闭。 |
| **允许路径** | `scripts/batch_delivery.py`、`tests/test_batch_delivery.py` |
| **禁改路径** | 上述以外全部；特别是 `docs/`、`templates/`、`kit/`、`editions/`、`AGENTS.md`、`docs/PROJECT.md`、`.github/`、`scripts/orchestrator.py`、`scripts/delivery_risk.py` |
| **前置条件** | 本契约已合并且为当前 `main`；S1 Issue `READY`；主分支干净；无可复用的 #261 分支 head（#261 已取消，其修复不得直接 cherry-pick 冒充新证据） |
| **不变量** | 1) 每个 attempt 在改动 worktree 前先持久化唯一 attempt id，并以 append-only 方式记录开始 / 结束 / pid / pgid / 结果；2) 直接子进程退出**不**等于进程组退出；3) 缺记录、缺 pid/pgid、记录不可读、pid 复用无法排除时，一律视为“未证明停止”；4) `--retry` / `--resume` 在 `_prepare_task_worktree` / `_agent` 之前检查 executor 与 task verifier 存活；5) 不改变 observed/integrated head 语义（属于 S2）。 |
| **负向场景** | 删除 / 损坏 attempt 记录后仍采纳并集成；pid 文件缺失被当作安全；直接子进程退出但同组后台进程仍存活；验证器仍存活时 `--retry` 先启动新 executor；attempt 记录覆盖前一次而不是追加；两个 attempt 复用同一 id；持久化失败后仍启动进程；Windows 上无法证明终止。 |
| **验证命令** | `python -m unittest tests.test_batch_delivery.ExecutorLifecycleTests`（S1 必须新增该测试类；在实现前为 `NOT_RUN`）；`python -m unittest tests.test_batch_delivery`；`python scripts/validate_kit.py`；`python -m py_compile scripts/batch_delivery.py`。所有命令必须在 S1 自己的最终 head 上真实执行并记录退出码。 |
| **风险等级** | `L2`（运行时控制 / 恢复安全），独立审查 + 完整回归。 |
| **明确不包含** | 不实现 observed / integrated head 分离；不改变 retry 的 base 语义；不实现 `git worktree list --porcelain` 解析；不实现 prompt `--file` 传输；不新增 `recover` 顶层 CLI；不改状态机、锁、journal schema 的已有字段语义；不改文档。 |
| **来源** | #261 第 5 轮 findings 1、2、5；第 4 轮 finding 2；第 3 轮 finding 1；#263 AC-3。 |

### 3.2 S2 — observed / integrated head 与 retry / base 语义

| 项目 | 内容 |
|---|---|
| **单一目标** | 把“观测到的候选 head”与“最后成功集成 head”分开存储与判断，并固定 retry 的 base：失败不得被误报为 `INTEGRATED`，retry 不得使用当前 batch head 或清空证据后再拒绝真实分支 head。 |
| **允许路径** | `scripts/batch_delivery.py`、`tests/test_batch_delivery.py` |
| **禁改路径** | 上述以外全部；特别是 `docs/`、`templates/`、`kit/`、`editions/`、`AGENTS.md`、`docs/PROJECT.md`、`.github/`、`scripts/orchestrator.py`、`scripts/delivery_risk.py` |
| **前置条件** | 本契约已合并且为当前 `main`；S2 Issue `READY`；主分支干净；不得依赖 #261 的未合并 head。S1 与 S2 都修改同一两个文件，因此**不得并行派发**；建议顺序 S1 → S2，但 S2 的语义设计不依赖 S1。 |
| **不变量** | 1) `integration_sha` 只在 verify 通过且 integration 成功后写入；2) `observed_head` / `observed_paths` 一旦观测到就保留，失败路径也不得清空；3) retry 的 base 是该 attempt 声明的 base 或任务原始 base，**不是** retry 时的当前 batch head；4) 失败验证不得被下一次恢复当作 `INTEGRATED` no-op；5) scope 判断相对 attempt base 与任务路径，后续集成的无关任务文件不得被算作本次越界；6) 旧 head / 旧批准不得复用到新 head。 |
| **负向场景** | verify 失败仍写 `integration_sha`；第二次 recover 误报 `INTEGRATED` no-op 而任务仍 `FAILED`；retry 在替代证据产生前清空 observed head，导致下一次 retry 拒绝真实分支 head；T1 失败后 T2 已集成，T1 retry 把 T2 的文件判为越界；batch head 前进后旧 head 被直接采纳为最新；普通执行路径在验证前把候选 head 写入“已集成”字段。 |
| **验证命令** | `python -m unittest tests.test_batch_delivery.ObservedIntegratedHeadTests`（S2 必须新增该测试类；在实现前为 `NOT_RUN`）；`python -m unittest tests.test_batch_delivery`；`python scripts/validate_kit.py`；`python -m py_compile scripts/batch_delivery.py`。所有命令必须在 S2 自己的最终 head 上真实执行并记录退出码。 |
| **风险等级** | `L2`（恢复状态机 / 证据语义），独立审查 + 完整回归。 |
| **明确不包含** | 不实现 executor / verifier 存活证明；不追加 attempt 生命周期历史（S1）；不实现 `git worktree list --porcelain` 解析；不实现 prompt `--file` 传输；不新增终止流事件容忍；不改锁 / 登记块 / journal schema 的已有字段含义；不改文档。 |
| **来源** | #261 第 5 轮 findings 3、4、6；第 4 轮 finding 1；第 3 轮 finding 2；#263 AC-3。 |

### 3.3 两片的独立性与审查边界

| 检查 | S1 | S2 |
|---|---|---|
| 能否单独派发 | 能；只依赖本契约与干净 `main` | 能；只依赖本契约与干净 `main` |
| 能否单独审查 | 能；审查证据只需 S1 的指标（attempt 记录、存活证明、halt） | 能；审查证据只需 S2 的指标（head 字段、retry base、scope） |
| 是否可并行 | 不与其他修改同一文件的切片并行 | 同左 |
| 是否包含工具修复 | 否 | 否 |
| 是否改变 #262 类解析 / 传输 | 否 | 否 |
| 是否可把另一片的行为写成本片已实现 | 否 | 否 |

**拆分理由**：#261 的五轮审查表明，把恢复状态机、并发生命周期、head 语义和工具边界放在同一批次会持续引入新的交叉边界。S1 与 S2 各自拥有可独立证伪的负向场景；任一失败都可以只退回该片，不牵动另一片。

---

## 4. 可追溯矩阵

| Finding / 证据 | 本文规则 | 说明 |
|---|---|---|
| #262 第 3 轮：`stop` 后仍有后台 shell 结果与第二个 assistant 响应；`retry`-only 不足 | STOP-14、STOP-16、STOP-HD-1、STOP-HD-2 | 终止后任何非空事件失败；放宽需人工决定。 |
| #262 第 3 轮：prompt 临时目录只检查当前 task worktree | WT-29–WT-32 | 必须覆盖所有 worktree 与 common dir。 |
| #262 第 4 轮：文本 `splitlines` / `strip` 会破坏含换行 / 尾随空格的 sibling 路径 | WT-01、WT-02、WT-13、WT-14、WT-HD-1 | 要求 `-z`、字节读取、不 strip。 |
| #262 第 5 轮：只校验 `worktree` 字段，malformed / truncated / 缺双 NUL 被接受，sibling 被吞 | WT-03–WT-12、WT-19、WT-20、WT-21、§2.7 | 完整记录校验。 |
| #262 T1 AC-2：大 prompt 使用 `--file`，内容完整，进程后删除 | WT-33–WT-36 | 当前 `_agent:1065` 仍用 argv，故为 `REQUIRED`。 |
| #261 第 5 轮 finding 1：缺失 / 损坏 executor 记录 + pid 缺失被当作安全 | STOP-22、S1 不变量 3、S1 负向场景 | 缺失即未证明停止。 |
| #261 第 5 轮 finding 2：直接子进程退出即删 pid，不证明同组退出 | STOP-20、S1 不变量 2 | 进程组证明。 |
| #261 第 5 轮 finding 3：retry 用当前 batch head 覆盖任务 base | STOP-26、S2 不变量 3、S2 负向场景 | retry base 固定。 |
| #261 第 5 轮 finding 4：retry 在替代证据前清空 observed head | STOP-27、S2 不变量 2 | 证据保留。 |
| #261 第 5 轮 finding 5：`attempt_history` 从未追加 | S1 不变量 1、S1 负向场景 | attempt 历史。 |
| #261 第 5 轮 finding 6：普通执行在 verify 前写 `record.head` | S2 不变量 1、S2 负向场景 | observed/integrated 分离。 |
| #261 第 4 轮 finding 1：observed head 与 integrated head 混用 | S2 不变量 1、4 | 分离字段。 |
| #261 第 4 轮 finding 2：缺失 executor 记录被当作已停止 | STOP-22、S1 不变量 3 | 失败关闭。 |
| #261 第 4 轮 finding 3：retry 先启动 executor 再检查 verifier | S1 不变量 4 | 前置检查。 |
| #261 第 3 轮 finding 1：执行器存活未证明 | STOP-22、S1 | 存活证明。 |
| #261 第 3 轮 finding 2：head 未在验证前落盘 | S2 不变量 2、4 | 观测证据保留。 |
| 当前代码 `_classify_stream:918-977` | STOP-01–STOP-17 | 已实现的流判定。 |
| 当前代码 `_stop_timed_out:1022-1026` / `_prove_stopped:1006-1019` | STOP-18–STOP-24 | 终止尝试与证明。 |
| 当前代码 `_prepare_batch_worktree:1206-1231` / `_prepare_task_worktree:1638-1668` | WT-12、S1/S2 前置条件 | 现有 worktree 身份检查；不解析完整 porcelain。 |
| 当前代码 `_apply_retry:1765-1793` | STOP-26、S2 | 现有 retry 条件。 |
| 当前代码 `_normalize_interrupted:1812-1825` | STOP-25 | 现有 resume 条件。 |
| 当前代码 `_resolve_journal:805-819` | WT-29、WT-31 | 现有 journal 边界只覆盖 repo / worktree 路径，未覆盖 linked worktree 与 common dir。 |
| 当前代码 `_agent:1059-1081` | STOP-18–STOP-24、WT-33 | argv prompt 与 timeout。 |

---

## 5. 明确不声称的事项

1. 本文**不**声称 #261 / #262 的任何 finding 已修复。两 Issue 均 `CANCELED`；其分支 head 不是本契约的实现证据。
2. 本文**不**声称 #250 AC5 已完成。AC5 仍为 `OPEN` / 未完成；S1、S2 均未派发、未实现。
3. 本文**不**新增或改变任务状态机、锁、journal、分支或证据格式；不改 `AGENTS.md`、`kit/`、`editions/`、`templates/`、CI 或发行包。
4. 本文的 `CURRENT` 只对 `490d31e` 的代码负责。任何 head 前进都必须重新核对行号与行为。
5. 本文的 `REQUIRED` 规则在实现前不得被引用为“工具已具备该能力”。

---

## 6. 结构化场景（本契约文档自身）

### 6.1 invariants

- 只新增 / 更新文档；不改变现有状态机、锁、journal、分支或证据格式。
- 不得把未实现行为写成已实现；`REQUIRED` 与 `CURRENT` 必须分开。
- 任何不可判定输入按失败关闭处理；不得“尽力解析”。
- 契约文本仅凭文字成立，不依赖图片。
- 每条规范规则都有来源（finding 或代码位置）；无法判定的语义标 `NEEDS_HUMAN_DECISION`。

### 6.2 negative_cases

- 把 `stop` 后的事件当作正常结束。
- 把缺少 `stop` 的流判成功。
- 只解析 porcelain 路径、不校验分隔符与完整记录。
- 把 sibling worktree 吞进同一记录，导致边界集合漏项。
- prompt 临时文件写进任一 worktree / common dir，或只检查当前 task worktree。
- 把“已修复”写成结论但没有当前 head 证据。
- 把 AC5 写成一个大切片，或让 S1 / S2 混入工具修复。
- 用 `--resume` / `--retry` 把失败流翻成成功。

### 6.3 migration_cases

`N/A: 本任务只新增契约文档与计划指针，不改变 journal / 分支 / 状态格式，无迁移。`

### 6.4 integration_paths

- 后续 S1 / S2 实现任务：`scripts/batch_delivery.py`、`tests/test_batch_delivery.py`。
- `docs/implementation-plan.md` 的切片指针。
- #250 AC5；#261 / #262 的证据记录。
- 发行包与模板不在本契约的改动范围；若未来需要同步模板，另开任务。

### 6.5 unverified_assumptions

- OpenCode 终止后的事件集合无法仅靠现有代码穷举；未确定项标 `NEEDS_HUMAN_DECISION`，见 §1.8。
- `git worktree list --porcelain` 在 Windows 路径转义、换行、尾随空格与异常记录下的行为未逐平台实测；契约要求逐字节对齐、不可判定即失败关闭，并标注平台未验证项，见 §2.8。
- `--porcelain -z` 的字段 / 记录分隔在本机 `git 2.55.0.windows.3` 实测为单 NUL 字段、双 NUL 记录；其他 Git 版本未逐版本实测，字段不符即失败。

---

## 7. 需要所有者决定的事项汇总

| ID | 问题 | 选项 | 推荐 |
|---|---|---|---|
| HD-1 | `stop` 之后是否允许任何事件（尤其 `retry` 生命周期事件）？ | A. 全部拒绝（本文当前规则）；B. 只允许 `retry`；C. 允许特定继续序列 | A，最保守；若选 B/C，必须先提供真实流 fixture 并改契约。 |
| HD-2 | Windows 上如何证明进程组终止？ | A. 维持“不可证明即 halt”；B. 引入作业对象 / 句柄证明 | A 可立即保持安全边界；B 需要单独设计与测试。 |
| HD-3 | bare 仓库是否纳入 prompt 临时文件边界？ | A. 失败关闭；B. 定义 bare 专用边界 | A，直到有真实使用场景。 |
| HD-4 | Windows 真实路径 / 大小写 / reparse point 的包含判断规则 | A. 不可证明即失败；B. 冻结具体 API 与大小写规则 | A，先失败关闭；B 作为后续平台任务。 |
| HD-5 | S1 / S2 的实现顺序与是否允许并行 | A. 串行 S1 → S2；B. 允许并行（同一文件，不建议） | A；两片改同一文件，路径锁要求串行。 |
