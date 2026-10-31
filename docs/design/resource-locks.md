# RESOURCE-LOCKS.md — 共享资源锁契约（v1，冻结）

> 状态：**已冻结（v1）**，冻结载体 Issue #5；实现任务 #6 只能按本契约实现，不得自行修改语义。
> 权威范围：本文件只定义**具名资源锁**（named resource lock）。任务状态机、任务锁取得条件、合并门 G1–G6、运行时能力 C1–C4 与证据矩阵的定义仍在 `docs/orchestration.md`，本文件**只引用、不复制、不改写**。
> 变更方式：任何语义改动必须新开 Issue 重新冻结为 v2，并在 PR 中说明兼容影响；不得在实现 PR 里顺手改契约。

相关文件（本文只引用，不复制其中定义）：

- 状态机、活动任务锁、合并门 G1–G6、证据矩阵：[docs/orchestration.md](../orchestration.md)
- 阶段收口检查项：[docs/orchestration.md](../orchestration.md)
- 设计文档需回答的资源锁问题：[templates/design.md](../../templates/design.md)
- 任务包字段定义（本 Issue 不改）：[templates/task-packet.md](../../templates/task-packet.md)
- `github` 底座活动任务登记块：[editions/github/active-task-registry.md](../../editions/github/active-task-registry.md)

## 1. 范围与术语

### 1.1 为什么需要第二类锁

路径锁只能回答“两个任务是否改同一批文件”。真实流水线还存在**不落在文件上**的独占需求：

| 真实场景 | 冲突对象 | 路径锁能否表达 |
|----------|----------|----------------|
| 共享测试数据库（同一 schema 会被并发读写破坏） | 数据库实例 / schema | 否 |
| 共享构建环境或设备池（同一台机器只能跑一个构建） | 机器 / 容器 / 设备 | 否 |
| 串行测试锁（同一时间只允许一个端到端测试） | 测试执行槽 | 否 |
| 共享服务文件与数据切片（同一份固定装置被覆盖） | fixture / 数据卷 | 部分（仅当它是仓库内文件） |

路径锁不冲突**不等于**可以并行。资源锁是路径锁之外的**增量且独立**的第二道判定：两者都通过才可派发。

### 1.2 path lock 与 named resource lock 的区别

| 维度 | path lock（既有，不变） | named resource lock（本契约新增） |
|------|------------------------|-----------------------------------|
| 标识对象 | 文件 / 目录路径 | 抽象资源 ID（`db:test/mysql-main` 之类） |
| 标识来源 | 任务允许路径自动推导 | 任务显式声明（未声明即不参与） |
| 冲突判定 | 路径相同或父子前缀重叠 | 同一 `resource_id` 上的模式冲突（见 §3） |
| 语义 | 写冲突互斥 | 共享读 / 独占写两类语义 |
| 是否可共享 | 否，一路径一持有者 | 是，`shared` 允许多持有者 |
| 生命周期 | 随任务状态自动释放（`VERIFIED` / `CANCELED`） | 有租约，随任务状态 + 租约 + 回收动作释放 |
| 存储 | `local`：`locks(path, task_id, created_at)` | `local`：`resource_locks` 表（§2.2）；`github`：登记块字段（§7.2） |
| 过期与恢复 | 无租约；靠显式 `task unlock` | 有租约；过期不等于释放（§6.2） |

两条铁律：

1. **路径锁语义不变**。本契约不改变既有路径锁的冲突规则、CLI 行为与自动释放时机。
2. **资源锁不能替代路径锁**。声明了资源锁的任务仍然必须通过路径锁检查；反之亦然。

### 1.3 非目标

- 不定义资源目录 / 资源发现 / 自动推断（任务必须显式声明）。
- 不定义分布式多主机协调、跨仓库锁、优先级或公平队列。
- 不定义阻塞式等待语义：本契约一律 **fail-fast**（见 §4.3）。
- 不定义资源本身的健康检查与配额；锁只保证互斥，不保证资源可用。

## 2. 锁数据模型

### 2.1 资源 ID 规范

```text
<type>:<name>
type = ^[a-z][a-z0-9-]{0,15}$        例：db, env, svc, port, vol, fixture
name = ^[a-z0-9][a-z0-9._/-]{0,127}$  例：test/mysql-main, build/windows-01
```

规范化规则（实现必须逐条执行，规范化在比较与排序**之前**完成）：

1. 仅接受小写；出现大写、空格、制表符、反斜杠、`?`、`*`、`#` 一律拒绝（返回 `RESOURCE_ID_INVALID`，不静默改写）。大小写混用会产生不稳定排序与“看似不同实则同资源”的漏洞。
2. 拒绝 `.` / `..` 路径段与空段（`a//b`）。
3. 使用 Unicode NFC 归一化（防止视觉相同的两个 ID 被当成不同资源）。
4. **精确匹配**：资源 ID 比较为字符串全等，不做前缀、父子或通配推导。路径锁的前缀重叠规则**不**适用于资源 ID。需要“同一组资源”语义时，任务必须逐条列出，不得依赖 `group:` 之类的隐式展开。
5. 规范化后的 ID 是唯一权威形式：审计、报错、展示都必须使用规范化形式。

### 2.2 锁记录字段（唯一权威定义）

`local` 落地为 `resource_locks` 表的一行；`github` 落地为登记块里的一条结构化条目（§7.2）。字段名两侧一致，含义只在本表定义。

| 字段 | 必填 | 含义 | 取值 / 规则 |
|------|------|------|-------------|
| `lock_id` | 是 | 锁记录唯一标识 | `<resource_id>#<mode>#<task_id>#<generation>`；`generation` 自 0 起，同一 `(resource_id, task_id)` 每次重新获取 +1，保证历史记录不复用主键 |
| `resource_id` | 是 | 被锁资源 | §2.1 的规范化形式 |
| `mode` | 是 | 锁模式 | `shared` / `exclusive`，只能是这两个值 |
| `holder` | 是 | 持有者实例标识 | 运行时实例 ID / 会话 ID / 人工执行者标识；不得写“agent”“subagent”这类无法定位的泛称 |
| `task_id` | 是 | 归属任务 | 与任务载体中的任务 ID 一致（`local` 任务号 / `github` Issue 编号） |
| `acquired_at` | 是 | 获取成功时间 | ISO 8601 UTC；集合获取时为整批统一时间戳（同一事务内同一时刻） |
| `lease_until` | 是 | 租约到期时间 | `acquired_at + ttl`；`ttl` 由项目配置给出且必须有上限；缺失或非正数视为配置错误并拒绝派发 |
| `renewed_at` | 是 | 最近一次续租时间 | ISO 8601 UTC，单调不减；未续租时等于 `acquired_at` |
| `state` | 是 | 锁状态 | `ACTIVE` / `EXPIRED` / `QUARANTINED` / `RELEASED`，状态转移见 §5.1 |
| `release_reason` | 是（`RELEASED` 时必填） | 释放理由 | `NORMAL_COMPLETE` / `CANCELED` / `FORCED_UNLOCK` / `LEASE_RECLAIMED` / `TRANSFER` / `MIGRATION_DROP`；非 `RELEASED` 状态写 `(none)` |
| `released_at` | 是（`RELEASED` 时必填） | 释放时间 | ISO 8601 UTC；非 `RELEASED` 写 `(none)` |
| `reason` | 是 | 获取或状态变更的人类可读理由 | 派发自动获取可写 `dispatch`；强制解锁、隔离、回收必须写明具体原因与判断依据 |
| `evidence_ref` | 是 | 该锁对应证据的可定位来源 | 审计事件 ID、PR / Issue 评论、演练记录路径；`(none)` 仅允许出现在 `ACTIVE` 的自动获取行 |
| `backend` | 是 | 执行该锁判定的底座 | `local` / `github`；用于区分“原子互斥”与“协作登记”，不得跨底座混记（§7.3） |

`local` 表结构约束（实现契约，不是本次改动）：

- `PRIMARY KEY(lock_id)`；唯一约束 `UNIQUE(resource_id, task_id, mode, generation)`。
- 部分索引保证同一 `resource_id` 上 `ACTIVE` 的 `exclusive` 至多一条，且 `ACTIVE` 的 `exclusive` 存在时该资源上不得有 `ACTIVE` 的 `shared`（§3.2 的数据库级兜底）。若引擎不支持部分索引，必须在事务内以 `SELECT ... ` 校验并配合串行化写；校验与写入必须在同一事务。
- 索引 `(resource_id, state)`、`(task_id, state)`、`(lease_until)` 用于冲突判定、任务视图与过期扫描。

### 2.3 审计事件字段（唯一权威定义）

锁的每一次变化都产生一条不可变事件。锁表是“当前状态”，审计事件是“历史事实”；**只有锁表而没有事件，视为证据缺失**（§6.6）。

| 字段 | 必填 | 含义 | 取值 / 规则 |
|------|------|------|-------------|
| `event_id` | 是 | 事件唯一标识 | 单调递增序号或 UUID；同一 `event_id` 重复写入必须幂等（重复写返回原结果，不产生第二条） |
| `event_type` | 是 | 事件类型 | 见下表枚举；不得自造未登记类型 |
| `at` | 是 | 事件时间 | ISO 8601 UTC |
| `actor` | 是 | 发起者 | 实例 ID / 人工执行者 / 调度器（`orchestrator`）；不得留空 |
| `task_id` | 是 | 相关任务 | 与锁记录一致 |
| `resource_id` | 是 | 相关资源 | 集合事件逐条展开，一条事件只描述一个资源，禁止用 `a,b,c` 合并 |
| `mode` | 是 | 相关模式 | `shared` / `exclusive` |
| `prev_state` / `new_state` | 是 | 状态迁移 | 与 §5.1 的合法迁移一致；获取时 `prev_state` 为 `(none)` |
| `request_fingerprint` | 是 | 该次集合请求的指纹 | 规范化资源集合（含 mode）按 §4.2 排序后拼接的 SHA-256；用于识别重复请求与半锁 |
| `reason` | 是 | 理由 | 与锁记录 `reason` 同源 |
| `evidence_ref` | 是 | 证据位置 | 命令输出、日志路径、PR / Issue 评论链接 |

事件类型枚举：

| `event_type` | 触发时机 | 说明 |
|--------------|----------|------|
| `LOCK_REQUESTED` | 集合请求开始 | 每个资源一条，先写入再判定 |
| `LOCK_GRANTED` | 事务内获取成功 | 与 `LOCK_REQUESTED` 一一对应 |
| `LOCK_DENIED_CONFLICT` | 冲突拒绝 | 必须写明冲突资源与占用方 `task_id` / `holder` |
| `LOCK_SET_ROLLED_BACK` | 集合获取失败回滚 | 记录已尝试的资源数量，证明无半锁（§4.4） |
| `LOCK_RENEWED` | 续租成功 | 只延长自身锁 |
| `LOCK_EXPIRED` | 判定超过 `lease_until` | **不是释放**（§6.2） |
| `LOCK_RECLAIMED` | 证明 holder 已终局后回收 | 必须附终局证据 |
| `LOCK_RELEASED` | 正常释放 | 携带 `release_reason` |
| `LOCK_FORCED` | 人工强制解锁 | 必须写明判断依据与授权人 |
| `LOCK_QUARANTINED` | 状态未知 / 证据冲突 | 进入人工复核 |
| `LOCK_ADOPTED` | 跨会话恢复接管 | 记录旧 holder → 新 holder |
| `LOCK_TRANSITION_REJECTED` | 非法状态迁移 | 拒绝并保留现场，不得静默忽略 |
| `RESOURCE_REGISTERED` | `github` 底座登记块写入 | 仅登记，不构成原子锁（§7.2） |

## 3. 冲突矩阵

### 3.1 同一 `resource_id` 上的模式矩阵

| 已存在的 `ACTIVE` 锁 | 新请求 `shared` | 新请求 `exclusive` |
|----------------------|-----------------|--------------------|
| 无 | **允许** | **允许** |
| `shared`（1 个或多个持有者） | **允许**（追加持有者） | **拒绝**（`MODE_CONFLICT`） |
| `exclusive`（1 个持有者） | **拒绝**（`MODE_CONFLICT`） | **拒绝**（`MODE_CONFLICT`） |

补充判定（同样属于契约，不是实现自由裁量）：

1. **不同 `resource_id` 之互不冲突**，包括同一 namespace 下的不同 name（`db:test/a` 与 `db:test/b` 不冲突）。
2. 同一 `task_id` 已持有同一 `resource_id` 的 `shared`，再申请 `exclusive`：**拒绝**（`UPGRADE_FORBIDDEN`）。必须先释放再整体重新申请；禁止就地升级，否则两个 shared 持有者同时升级即死锁。
3. 同一 `task_id` 重复申请同一 `(resource_id, mode)` 且原锁 `ACTIVE`：返回 `ALREADY_HELD`，**不新增记录、不刷新租约、不视为冲突**（幂等，见 §6.5）。
4. `EXPIRED` 与 `QUARANTINED` 的锁**仍然参与冲突判定**，按 `ACTIVE` 等价处理，直到进入 `RELEASED`。这是“过期锁不被静默认定安全释放”的直接实现（§6.2）。
5. 路径锁与资源锁分属两个判定空间，互不抑制：路径不冲突不能“抵消”资源冲突，资源不冲突也不能“抵消”路径冲突。

### 3.2 冲突判定的原子性要求

冲突读取与写入必须在**同一个串行化事务**内完成（`local`：`BEGIN IMMEDIATE`）。事务内不得有用户交互、网络调用或长耗时计算；判定所用集合必须是事务开始后的单次快照。

## 4. 多锁获取：原子集合与确定性顺序

### 4.1 请求形态

任务是**一个资源集合**（`declared_resources`）的持有者，不是逐个资源独立申请。集合中的每一项为 `(resource_id, mode)`，同一请求内 `resource_id` 不得重复。

### 4.2 确定性顺序

1. 规范化（§2.1）→ 去重 → 按 `resource_id` 的 UTF-8 字节序**升序**排序。
2. 排序结果既用于写入与审计事件的固定顺序，也用于计算 `request_fingerprint`（§2.3）。
3. 排序必须在事务内重新计算一次，不得采信调用方传入的顺序（防止不同客户端产生不同顺序与不同指纹）。

确定性顺序的作用是**可重放与可比较**：同一集合在任何会话、任何重试中产生相同指纹与相同事件顺序，崩溃恢复可以据此判断“这次的请求是否已经部分应用”。

### 4.3 冲突即整体失败（fail-fast）

- 本契约**不实现阻塞等待**。任一资源冲突 → 整个集合获取失败，任务不进入 `DISPATCHED`。
- 因为不存在“持有 A 等待 B”的等待边，**不存在循环等待**，死锁在结构上被排除；确定性顺序是第二重保险（保证即使未来引入等待也有一致的全局序）。
- 失败返回必须携带：冲突资源 ID、占用方 `task_id` / `holder`、占用方 `mode`、占用方 `lease_until`、可执行建议（等待、改资源、串行化）。**不得只返回“有冲突”**。

### 4.4 无半锁（no partial locks）

- 集合获取是一个事务：要么全部 `GRANTED`，要么全部不存在。
- `local`：单事务 `BEGIN IMMEDIATE` … `COMMIT`；任何异常路径 `ROLLBACK`，并写 `LOCK_SET_ROLLED_BACK` 事件（事件写在同一事务的独立审计表或事务外补偿表，二者都必须保持“失败时锁表净空”的可验证性）。
- 失败后必须能证明净空：`SELECT count(*) FROM resource_locks WHERE task_id=<id> AND state<>'RELEASED'` 为 `0`。
- **禁止**“先占能占的，冲突的稍后重试”——那正是半锁。任务若需分批使用资源，必须拆成多个任务，每个任务各自声明完整集合。

### 4.5 释放

- 释放按集合进行，顺序同 §4.2；单条释放失败必须继续尝试其余条目，并逐条记录结果，最后汇总为一次失败（不得因一条失败而静默跳过全部）。
- 释放不是幂等豁免：对已 `RELEASED` 的 `lock_id` 再次释放返回 `ALREADY_RELEASED`，不产生新状态迁移，但**仍然写审计事件**（记录了一次错误的调用）。

## 5. 锁状态与任务状态

### 5.1 锁状态转移

```text
(无) --LOCK_REQUESTED--> GRANTED/ACTIVE
ACTIVE --LOCK_RENEWED--> ACTIVE
ACTIVE --LOCK_EXPIRED--> EXPIRED
ACTIVE --LOCK_RELEASED--> RELEASED
ACTIVE --LOCK_QUARANTINED--> QUARANTINED
EXPIRED --LOCK_RECLAIMED--> RELEASED
EXPIRED --LOCK_FORCED--> RELEASED
EXPIRED --LOCK_QUARANTINED--> QUARANTINED
EXPIRED --LOCK_RENEWED--> ACTIVE      (仅当 holder 存活且未进入回收，需记录依据)
QUARANTINED --LOCK_FORCED--> RELEASED
QUARANTINED --LOCK_ADOPTED--> ACTIVE  (人工确认 holder 变更)
RELEASED --(任何)--> 拒绝，写 LOCK_TRANSITION_REJECTED
```

规则：

- `RELEASED` 是唯一终态；终态不可复活，重新获取必须使用新的 `generation`。
- 除 `LOCK_RENEWED`（`ACTIVE→ACTIVE`）外，每次迁移都必须有明确的 `event_type`、`actor`、`reason`。
- 任何未在上图出现的迁移一律拒绝并记录 `LOCK_TRANSITION_REJECTED`，不得“就近猜一个状态”。

### 5.2 锁生命周期与任务状态的关系

| 任务状态 | 资源锁动作 | 触发者 | 审计要求 |
|----------|------------|--------|----------|
| `PLANNED` / `READY` | 无锁 | — | 不产生锁事件 |
| `DISPATCHED` | 取得完整集合（原子） | `task dispatch` / 登记 | 每个 `LOCK_GRANTED` |
| `IN_PROGRESS` | 按周期续租 | 执行实例 | `LOCK_RENEWED` |
| `PR_OPEN` / `REVIEW_READY` / `MASTER_REVIEW` / `APPROVED` | **保持并继续续租** | 执行实例 / 调度器 | 续租不得中断；审查期间释放会让新任务误判资源空闲 |
| `MERGED` | 保持（合并后仍要跑验证） | 调度器 | 不得在 `MERGED` 自动释放 |
| `VERIFIED` | 释放，`NORMAL_COMPLETE` | 调度器 | `LOCK_RELEASED` |
| `CANCELED` | 释放，`CANCELED`（必须写理由） | 调度器 / 人工 | `LOCK_RELEASED` |
| `FAILED` | **不释放**；租约到期后转 `EXPIRED`，再按 §6 回收或隔离 | 无自动释放 | `LOCK_EXPIRED` |
| `BLOCKED` | **不释放**（任务可能仍占着资源），写 `blocked_reason` | 调度器 | 不产生释放事件 |
| `NEEDS_HUMAN_DECISION` | **不释放**，转 `QUARANTINED` | 人工 | `LOCK_QUARANTINED` |
| 状态未知（载体不可读、证据冲突、时间戳倒退） | **不释放**，转 `QUARANTINED`，停止派发 | 人工 | `LOCK_QUARANTINED` |

关键取舍：**只有 `VERIFIED` 与 `CANCELED` 自动释放**。这与路径锁的既有规则一致，也是本契约拒绝“失败即释放”的原因——失败任务的进程可能仍在写资源。

## 6. 异常与恢复语义

### 6.1 通用原则

1. **不确定即占用**：任何无法证明“持有者已停止”的情况，锁保持占用。
2. **释放必须留痕**：没有审计事件的释放等于伪造。
3. **人工动作需授权与理由**：强制解锁、回收、隔离解除都必须记录判断依据；无依据的动作在审查中按未通过处理。
4. **不改写历史**：恢复通过在旧记录上追加状态迁移与新事件表达，禁止删除或篡改既有锁行。

### 6.2 租约过期

- 超过 `lease_until` 未续租 → 写 `LOCK_EXPIRED`，`state=EXPIRED`。
- **`EXPIRED` 不等于释放**：该锁仍参与冲突判定（§3.1 规则 4），新请求仍然被拒绝。这是为了挡住“持有者其实还活着、只是心跳丢了”的最危险情况。
- 从 `EXPIRED` 到 `RELEASED` 必须经过以下之一：
  1. **回收**（`LOCK_RECLAIMED`）：调度器能提供 holder 已终局的可核对证据（进程不存在、会话已关闭、实例记录为终局），并记录证据引用；
  2. **强制解锁**（`LOCK_FORCED`）：人工判断并授权（§6.4）；
  3. **隔离后处理**（`QUARANTINED` → `FORCED` / `ADOPTED`）。
- 回收前必须有**冷却期**（`grace`，由项目配置，且必须显式配置而非隐式默认），冷却期内只允许续租与告警，不允许回收。冷却期存在的意义是排除“调度器时钟抖动 / 网络抖动”导致的误回收。

### 6.3 僵尸实例与 holder 崩溃

| 现象 | 判定 | 行为 |
|------|------|------|
| holder 进程消失，未到期 | 疑似僵尸 | 锁保持 `ACTIVE` 至 `lease_until`；不使用“进程不在”单独作为立即回收依据（进程可能只是重启） |
| holder 进程消失且已过期 | 僵尸 | 冷却期后按 §6.2 回收，需终局证据 |
| holder 存活但停止续租 | 失联 | 到期转 `EXPIRED`；恢复续租必须写 `LOCK_RENEWED` 并说明中断原因 |
| 同一 `task_id` 出现两个不同 `holder` | 重复所有权 | 双方均不得继续写入；转 `QUARANTINED`，人工确认唯一 holder 后 `LOCK_ADOPTED` 或 `LOCK_FORCED` |
| holder 标识为空或无法定位 | 不可判定 | 拒绝获取（`HOLDER_INVALID`）；已存在的这类记录一律 `QUARANTINED` |

### 6.4 强制解锁

- 必须同时具备：理由（为什么判断持有者已停止）、授权人、目标 `lock_id`、执行时间。
- 强制解锁**跳过冲突**，因此必须先确认原任务已停止；无法确认时不得强制，改为隔离并升级为人工决策。
- 强制解锁后必须做一致性检查：原任务若仍活跃，其后续续租 / 写入必须失败（`LOCK_NOT_HELD`），不得因“上次成功了”继续写。
- `github` 底座：强制解锁等于由人工修改登记块并留评论说明；不具原子性，因此**必须串行执行**（同一时刻只允许一个人改）。

### 6.5 重复调度与重复请求

| 情形 | 行为 |
|------|------|
| 同一 `task_id` 重复 `dispatch`，集合与 mode 完全相同，原锁 `ACTIVE` | 返回 `ALREADY_HELD`（幂等），不新增记录、不刷新租约，写审计事件 |
| 同一 `task_id` 重复 `dispatch`，集合**更小**或无资源声明 | 返回 `LOCK_SET_MISMATCH`（已持有集合与新声明不一致），拒绝并转 `QUARANTINED` |
| 同一 `task_id` 重复 `dispatch`，集合**更大** | 按“扩范围”处理：原集合仍有效，新集合作为一次新的集合获取；失败则整体回滚到原集合，不得残留部分新锁（见 §6.5 补充） |
| 两个不同 `task_id` 争同一 `exclusive` | 后者 `LOCK_DENIED_CONFLICT`，任务不进入 `DISPATCHED` |
| 同一 Issue 被派发两次（`github`） | 以登记块的 `dispatched_at` / `updated_at` 与评论时序判定；无法判定唯一派发 → `QUARANTINED` + 人工决策 |

补充（扩范围）：扩范围只允许在任务处于 `BLOCKED` 且已更新登记之后进行；实现必须把“新增部分”作为独立事务获取，成功后与既有集合合并记账，失败则保持原集合不变，并在事件中写明未新增任何锁。

### 6.6 跨会话恢复

新会话（新 Master / 新执行实例）接管时必须按以下顺序重建事实，**不得从聊天记忆重建**：

1. 读取锁表 / 登记块与审计事件，列出全部非 `RELEASED` 锁；
2. 对每条锁核对：`task_id` 的任务状态、`renewed_at` 是否在 `ttl/2` 内、`holder` 是否可定位、`evidence_ref` 是否存在；
3. 分类处理：
   - 全部可核对且在租约内 → 视为有效持有，新会话**只读**，不擅自续租他人锁（除调度器代表同一任务续租，且必须写 `actor`）；
   - 缺失审计事件或字段为空 → `QUARANTINED`，停止该资源上的新派发；
   - 已过期 → 按 §6.2 处理；
   - 载体时间戳倒退或与评论历史矛盾 → `QUARANTINED` + `NEEDS_HUMAN_DECISION`。
4. 恢复动作本身必须产生 `LOCK_ADOPTED` / `LOCK_QUARANTINED` 事件，并在任务记录写明恢复依据。

**只有锁表没有事件 = 证据缺失**：此类记录不得被当作有效持有直接续租，也不得被当作空闲直接派发，只能进入隔离。

## 7. 两个底座的能力边界

### 7.1 `local`：可以声明原子互斥

- 载体：SQLite 单库单文件；写事务用 `BEGIN IMMEDIATE`，同一时刻只有一个写者；唯一约束 + 部分索引提供数据库级兜底。
- 因此 `local` 可以声明：**在“同一数据库文件 + 单主机（或受支持的一致性共享文件系统）”范围内，资源锁的获取是原子的，冲突判定与写入不可分割，失败无半锁。**
- 不能声明：跨主机并发（网络文件系统、多副本、只读镜像共享）、跨数据库文件、绕过 CLI 直接改库后的正确性。
- 部署限制：数据库文件所在文件系统必须支持可靠的锁语义；若检测到网络文件系统或不支持的文件系统，必须在派发前拒绝并提示，不得“先跑再看”。

### 7.2 `github`：只有协作性登记，禁止宣称原子锁

- 载体：Issue 正文 / 评论中的 `ORCH-TASK-REGISTRY` 登记块与 `locked_resources` 条目，配合开放 PR 的变更文件检查。
- GitHub 平台**不提供**跨 Issue / 跨评论的 compare-and-swap 或事务；两次并发写入的最终结果是“后写覆盖”，中间没有互斥窗口。API 调用成功 ≠ 排他成功。
- 因此 `github` 只能声明：**协作性登记 + 冲突检查（advisory reservation）**，其正确性依赖“同一时刻只有一个 Master 在派发”这一运行纪律，以及每次派发前重新读取登记块与开放 PR 的完整快照。
- 必须写明的具体限制：
  1. 检查与写入之间存在 TOCTOU 窗口；两个 Master 同时派发的冲突可能只在事后被发现。
  2. 评论不可变仅指“不修改历史文本”，不代表写入原子；编辑登记块会留下中间态。
  3. 无租约强制力：`lease_until` 只是记账字段，平台不会拒绝过期后的写入。
  4. 无法阻止绕过登记的写入（直接推送分支、直接改 Issue 正文）。
- 因此 `github` 底座：
  - 冲突拒绝后的**处理权在人工**：可串行化、改资源、或明确接受风险并记录；
  - 禁止在 PR / Issue / 报告中使用“原子锁”“互斥已保证”“并发安全”等表述；
  - 证据口径必须写成“登记时间 + 检查时刻的开放 PR 快照 + 人工确认”，不得写成“锁已取得（atomic）”。
- 可以做的加固（不改变上述结论）：登记块前置写入、写后复读校验、`updated_at` 单调递增检查、开放 PR 全量文件列表记录、冲突时升级为 `NEEDS_HUMAN_DECISION`。

### 7.3 混合与迁移

- 同一任务的活动锁只能记在一个底座上（与 `docs/orchestration.md` 第 6 节“不允许双写”一致）。
- 底座切换时，资源锁必须按 §8.4 重新登记，不能把 `local` 的原子性结论带入 `github`。

## 8. 迁移与兼容

### 8.1 阶段划分

| 阶段 | 内容 | 交付物 |
|------|------|--------|
| 0（本 Issue） | 冻结语义与字段；只加文档与引用 | `docs/design/resource-locks.md` + 三处引用 |
| 1（#6） | 增量实现：`resource_locks` 表、审计事件、集合原子获取、冲突诊断、过期与回收、人工恢复命令 | 代码 + 测试 + 演练记录 |
| 2（后续） | 任务包 / Issue 字段、`task list` / `status` 展示、资源目录 | 另行冻结 |

### 8.2 兼容规则（既有行为不变）

1. 未声明 `declared_resources` 的任务：**完全不触碰资源锁**，行为与当前版本一致。
2. 既有路径锁：冲突规则、重叠判定、自动释放时机、`task unlock --reason` 语义**全部不变**。
3. 状态机与状态枚举：不新增状态；`FAILED` / `BLOCKED` / 状态未知仍然不自动释放。
4. CLI：本契约不新增命令名之外的任何行为变更；#6 新增的参数必须为可选，缺省时保持旧行为。
5. 旧数据库：打开时按增量方式创建 `resource_locks` 与事件表（空表）。**表缺失不得被解释为“无冲突”**；实现必须先建表再判定，否则视为未实现。

### 8.3 回退

- 删除三处引用、任务不再声明资源即可回退到纯路径锁行为；不需要数据迁移。
- 回退前必须处理残留的 `ACTIVE` / `EXPIRED` 锁：逐条按 §6.4 强制解锁并记录理由，或保留（保留会让同一资源持续冲突）。**不允许直接删表了事**——那等于静默释放（§6.1 规则 2）。
- 无历史资源锁数据可迁移（本契约之前不存在该表）。

### 8.4 底座迁移时的重新登记

`local → github` 切换（按 `docs/orchestration.md` 第 6 节）时：把非 `RELEASED` 资源锁逐条写入 Issue 的 `locked_resources`，标注“迁移前 `local` 登记 + 迁移时间”，并在 PR / Issue 说明其不再具有原子性保证；`github → local` 由项目所有者单独立项，不做自动回退。

## 9. 故障场景与演练

### 9.1 故障场景（每个场景都必须有：预期行为 + 证据形式）

| 编号 | 场景 | 预期行为 | 证据形式 |
|------|------|----------|----------|
| F1 | 并发竞争：两个任务同时申请同一 `exclusive` | 仅一个 `LOCK_GRANTED`，另一个 `LOCK_DENIED_CONFLICT` 并带冲突方 `task_id` / `holder` / `lease_until`；失败方无任何残留锁行 | 两个任务的审计事件 + `SELECT count(*) ... state<>'RELEASED'` 为 0 + `task status` 输出 |
| F2 | holder 崩溃：进程被杀，租约未到期 | 锁保持 `ACTIVE`；新请求仍被拒绝；到期后仅 `LOCK_EXPIRED`，不自动 `RELEASED` | 进程终止时间、`lease_until`、`LOCK_EXPIRED` 事件、拒绝记录 |
| F3 | 租约过期：holder 存活但停止续租 | 转 `EXPIRED` 且**仍参与冲突判定**；冷却期内仅允许续租与告警；回收需终局证据 | `LOCK_EXPIRED` + 冷却期配置值 + 候选新请求被拒的记录 + 回收事件 |
| F4 | 重复获取：同一任务重复 `dispatch` | 相同集合返回 `ALREADY_HELD`（不新增记录、不刷新租约）；集合不一致返回 `LOCK_SET_MISMATCH` 并隔离 | 两次调用的返回码 + 唯一约束未触发重复行 + 事件序列 |
| F5 | 集合获取失败：3 个资源中第 2 个冲突 | 整批失败，**零残留**；返回冲突资源与占用方；`LOCK_SET_ROLLED_BACK` 存在；任务未进入 `DISPATCHED` | 事务回滚日志 / 净空查询结果 + 事件 + 任务状态未变 |
| F6 | 状态未知 / 人工恢复：锁行缺事件、`holder` 无法定位、登记块时间戳倒退 | 转 `QUARANTINED`，停止该资源上新派发，升级 `NEEDS_HUMAN_DECISION`；不得续租、不得当作空闲 | 隔离事件 + 决策记录 + 新派发被拒证据 + 恢复后 `LOCK_ADOPTED` / `LOCK_FORCED` |
| F7 | 强制解锁：人工对活跃他人锁执行 `unlock --force` | 拒绝执行（任务非终局）除非提供依据与授权；若执行，原任务后续续租 / 写入必须 `LOCK_NOT_HELD` | 拒绝记录 + 授权与理由 + 原任务后续失败证据 |
| F8 | 崩溃后跨会话恢复：新会话接管 | 重建事实顺序（§6.6），逐条分类，不中断有效锁、不放行缺失证据的锁 | 恢复清单（每条锁的分类与依据）+ 新会话首个动作的事件 |
| F9 | `github` 底座重复调度：同一 Issue 两个派发记录 / 两个 PR 改同一资源 | 判定不唯一即 `QUARANTINED`；冲突时人工串行化并记录“非原子”结论 | 登记块快照 + 开放 PR 列表快照 + 人工决策评论 |

### 9.2 可控故障演练方案

- **隔离**：在演练仓库或临时 Worktree 中执行（例如 `.worktrees/drill-resource-locks`），使用独立数据库文件。禁止在真实项目库上制造故障。
- **步骤**：
  1. 建立演练任务与资源（如 `db:drill/main`），声明 `exclusive`；
  2. 逐条复现 F1–F9，每次只制造一个故障；
  3. 每次记录：时间、底座、任务 ID、命令、退出码、锁表快照（`resource_id` / `state` / `holder` / `lease_until`）、事件序列、结论；
  4. 故障后必须验证“净空或已隔离”：不存在既非 `RELEASED` 又无事件的记录；
  5. 清理：按 §6.4 逐条释放并记录理由，删除演练分支 / Worktree。
- **判定**：任一场景出现“自动静默释放”“半锁”“把 `EXPIRED` 当空闲”“把 `NOT_RUN` 当通过”，演练判定为失败，必须修复后重跑并给出新 head。
- **证据留存**：演练记录写入任务记录 / PR 评论，包含上表编号与逐条结论；证据矩阵里每个资源锁相关的 AC 至少引用一条演练编号。

## 10. 对 #6 的实现约束

### 10.1 必须实现（缺一即契约未落地）

1. `resource_locks` 表与审计事件表（字段严格按 §2.2 / §2.3）。
2. 资源 ID 规范化与拒绝规则（§2.1）。
3. 集合原子获取 / 释放，失败净空，确定性顺序与指纹（§4）。
4. 冲突诊断输出（§4.3）。
5. 状态转移白名单与拒绝记录（§5.1）。
6. 租约、续租、过期、冷却期、回收与证据要求（§6.2 / §6.3）。
7. 强制解锁的授权与理由强制项（§6.4）。
8. 幂等与集合不一致处理（§6.5）。
9. 跨会话恢复的分类逻辑（§6.6）。
10. 两个底座的表述边界（§7）；`github` 路径不得出现“原子”措辞。

### 10.2 禁止事项（出现即视为未实现契约）

- 把 `EXPIRED` / `QUARANTINED` 当作空闲资源放行。
- 多资源获取失败后保留任意一条锁。
- `FAILED` / `BLOCKED` / 状态未知时自动释放。
- 就地升级 `shared → exclusive`。
- 允许调用方决定申请顺序（导致指纹与事件顺序不稳定）。
- 直接删除锁行代替状态迁移（无 `LOCK_RELEASED` 事件）。
- 在 `github` 底座上宣称原子互斥。
- 在同一 PR 中同时改契约文本与实现行为（契约变更必须独立 Issue 重新冻结）。

### 10.3 需要 #6 落地的新增字段（本 Issue 不改模板，待 Master 决策）

以下字段在 #6 实现时才会进入任务载体；`templates/task-packet.md` 与 `editions/github/issue-template.md` 的改动**不属于本 Issue 的允许路径**，必须先由 Master 决定是否纳入 #6 并协调模板所有权（与 #2 的字段定义保持一致）：

| 载体 | 字段 | 用途 |
|------|------|------|
| 任务包 / Issue | `declared_resources` | 任务声明的资源集合，格式 `<resource_id>:<mode>[:<lease_ttl>]`，逗号分隔；空表示不参与资源锁 |
| `ORCH-TASK-REGISTRY` | `locked_resources` | 已登记资源集合（`github` 为协作登记），含 `mode` 与登记时间 |
| `ORCH-TASK-REGISTRY` | `resource_lease_until` | `github` 底座下最新的租约记账值（无约束力，仅记账） |
| 配置 | `resource_lease_ttl` / `resource_reclaim_grace` | 租约时长与回收冷却期，必须显式配置且有上限 |
| CLI | `task dispatch --resource <id:mode>`（可选） | 与声明合并；缺省时行为不变 |
| CLI | `task unlock <id> --resource <id> --reason <text> [--force]` | 单资源释放 / 强制解锁 |
| 展示 | `task list` / `task status` | 展示 `resource_id`、`mode`、`state`、`lease_until`、`holder` |

## 11. 不变量与禁止事项（本契约自身）

**不变量**

1. 路径锁的冲突规则、CLI 行为、自动释放时机不变。
2. 状态机与状态枚举不变；本契约不新增任务状态。
3. 未声明资源的任务行为与当前版本完全一致。
4. 任一时刻，同一 `resource_id` 上不存在“`ACTIVE` 的 `exclusive` 与其他 `ACTIVE` 锁并存”。
5. 任一失败获取之后，该任务在锁表中的非 `RELEASED` 行数为 0。
6. 每次状态迁移都有对应审计事件；无事件的锁记录不被当作有效事实。

**负向用例（必须被拒绝）**

- 声称 GitHub Issue 评论天然原子、可作互斥。
- 多资源获取失败留下半套锁。
- 过期锁被静默当作安全释放。
- `FAILED` / `BLOCKED` 任务被自动解锁。
- 契约与实现混在同一个 PR。
- 通过删除锁行或删表来“清理”冲突。

**迁移用例**

- 既有项目只用路径锁：零改动可用。
- 资源锁为增量能力：未声明资源时无新表读写。
- 回退：删除三处引用即可，不影响既有数据（残留锁按 §6.4 处理）。
