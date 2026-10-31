# Lesson 生命周期、证据等级、去重与冲突决策（v1）

> 机器定义：[`lesson.schema.json`](../contracts/schemas/lesson.schema.json)。本文件定义**语义与转换**；schema 中的枚举是本文件的机器投影，两者不一致视为契约缺陷。
> 本文件不定义来源采集（见 [sources-and-trust.md](sources-and-trust.md)），不定义触发语与人工决策目录（见 [entrypoints-and-decisions.md](entrypoints-and-decisions.md)）。

## 1. 记录边界

一条 Lesson 是一个**可追溯的经验单元**，不是 Issue、不是任务、也不是规则。它必须能回答：来源证据在哪、事实是什么、根因与影响是什么、为什么原机制没发现、适用于哪里、建议改什么、谁做过什么决定。

- 一条 Lesson 只能映射到一个建议对象（`suggested_action.target` 单值）；需要改多个对象时拆成多条。
- `lesson_id` 形如 `LSN-<SOURCE>-<NNN>`，全局唯一、不再复用。来源停用或删除后 ID 仍然有效并保留。
- Lesson 不复制来源正文。`source.evidence_urls` 只放可定位链接；合成 fixture 另行独立编写。

## 2. 四类内容与证据等级

### 2.1 内容分类（互斥，必属其一）

| `content_class` | 判定标准 | 允许的证据等级 | 允许的状态 | 能否生成改进任务 |
|---|---|---|---|---|
| `CONFIRMED_FACT` | 已用可定位证据证实，且结论限定在观察范围：复现成功、或源码级确证、或修复已被带测试的 PR 证明 | E1–E4 | `OBSERVED` / `NEEDS_VERIFICATION` / `VERIFIED` / `REJECTED` / `REGRESSION_WATCH` | 否（只作为证据与报告） |
| `UNVERIFIED_ASSUMPTION` | 仅有 Issue 标题、审查猜测、单条日志或未复现的推断 | **仅 E0–E1** | `OBSERVED` / `NEEDS_VERIFICATION` / `REJECTED` | 否 |
| `SOURCE_SPECIFIC_EXPERIENCE` | 已证实，但适用条件只在该来源的业务 / 产品条件下成立，不能泛化 | E1–E4 | `OBSERVED` / `NEEDS_VERIFICATION` / `VERIFIED` / `REJECTED` / `REGRESSION_WATCH` | 否（保留为来源知识） |
| `GENERAL_LESSON` | 已泛化为与项目无关的规则 / 测试 / 流程要求，并映射到 AgentOrch 的明确对象 | E2–E4 | `VERIFIED` / `GENERALIZED` / `IMPLEMENTED` / `REJECTED` / `REGRESSION_WATCH` | 是，但必须先到 `GENERALIZED` 且无 `OPEN` 冲突 |

补充规则：

1. `UNVERIFIED_ASSUMPTION` **不能**标 `VERIFIED`，也不能生成任务；只能等待补证或拒绝。
2. `SOURCE_SPECIFIC_EXPERIENCE` 的 `applicability.scope` 必须是 `source_only`；`GENERAL_LESSON` 必须是 `agentorch_general`。
3. 分类变化（例如 `CONFIRMED_FACT` → `GENERAL_LESSON`）是一次**显式提升**，必须记 `decision`，不得静默改字段。
4. “观察到的流程问题”按证据强度归入 `CONFIRMED_FACT`（有审查 / CI / diff 证据）或 `UNVERIFIED_ASSUMPTION`（只有印象）。

### 2.2 证据等级

| 等级 | 定义 | 典型证据 | 能否支撑 |
|---|---|---|---|
| `E0_CLAIM` | 只有主张：Issue 标题、聊天、未附证据的评论 | 链接 | 只能支撑 `UNVERIFIED_ASSUMPTION` |
| `E1_SOURCE_ARTIFACT` | 有来源侧原始产物：diff、提交、CI 日志、审查评论，但未独立复现 | 可定位 URL + 摘要 | `CONFIRMED_FACT` 的源码级结论；不足以证明运行时行为 |
| `E2_REPRODUCED` | 在获准的隔离环境复现，或等价的最小可执行证明 | 复现步骤 / 命令 + 结果 | `VERIFIED` 的最低门槛 |
| `E3_FIXED_WITH_TEST` | 来源侧修复已被带回归测试的合并证明 | 修复 PR + 测试 | `GENERAL_LESSON` 的推荐门槛 |
| `E4_REGRESSION_COVERED` | AgentOrch 侧已有对应回归场景 / 测试覆盖 | 本仓库测试或合成场景 | `IMPLEMENTED` / `REGRESSION_WATCH` |

规则：

- 等级是**证据强度**，不是优先级。E3 的来源专属经验仍是来源专属。
- 只有链接没有可核对内容（链接失效、需登录、内容被删）时，等级按 `E0_CLAIM` 处理并写进 `notes`。
- 不得把“来源说已修复”当作 `E3`；必须有测试证据。

## 3. 状态与转换

### 3.1 状态清单

| 状态 | 含义 |
|---|---|
| `OBSERVED` | 候选已记录，尚未判断证据是否充分 |
| `NEEDS_VERIFICATION` | 需要补证 / 复现；不得进入采纳 |
| `VERIFIED` | 证据达到门槛，结论成立（但仍可能来源专属） |
| `REJECTED` | 明确不采纳；必须有决策与理由；记录保留 |
| `GENERALIZED` | 已泛化并映射到 AgentOrch 的改进对象；可生成任务 |
| `IMPLEMENTED` | 对应改进已由任务实现并合并（任务自身仍按核心契约验收） |
| `REGRESSION_WATCH` | 已实现，进入观察期，仍需回归监控 |

### 3.2 合法转换（唯一转换表）

| # | From → To | 触发 | 守卫条件 |
|---|---|---|---|
| T1 | （新）→ `OBSERVED` | 采集到候选并写入来源证据 | `evidence_urls` 非空；`fingerprint` 已计算 |
| T2 | `OBSERVED` → `NEEDS_VERIFICATION` | 证据不足或存在未决冲突 | 不得同时声明 `VERIFIED` 结论 |
| T3 | `OBSERVED` → `VERIFIED` | 证据达到门槛 | `evidence_level ≥ E2`；`content_class ∈ {CONFIRMED_FACT, SOURCE_SPECIFIC_EXPERIENCE}` |
| T4 | `OBSERVED` / `NEEDS_VERIFICATION` → `REJECTED` | 不可复现、不适用、收益不足 | `decision.outcome=REJECT` + 理由 |
| T5 | `NEEDS_VERIFICATION` → `VERIFIED` | 补齐证据 | `evidence_level ≥ E2` |
| T6 | `VERIFIED` → `GENERALIZED` | 泛化评审通过 | `decision.outcome=ADOPT`；`content_class=GENERAL_LESSON`；`scope=agentorch_general`；无 `OPEN` 冲突；已检查现有机制是否覆盖 |
| T7 | `VERIFIED` → `REJECTED` | 复核后不采纳 | `decision` 必填 |
| T8 | `GENERALIZED` → `IMPLEMENTED` | 对应任务已合并并按核心契约验收 | 任务 Issue / PR 编号写入 `links`；`decision` 保留 |
| T9 | `IMPLEMENTED` → `REGRESSION_WATCH` | 需要观察回归 | 指定观察窗口与检查方式（写入 `notes` / 关联测试） |
| T10 | `REGRESSION_WATCH` → `NEEDS_VERIFICATION` | 观察到回归 | 新增证据链接；不得直接改回 `VERIFIED`；`GENERAL_LESSON` 必须先按 T12 语义撤销泛化（见 §3.2.1） |
| T11 | `VERIFIED` / `SOURCE_SPECIFIC_EXPERIENCE` → 降级 | 证据被撤回或失效 | `content_class` 改为 `UNVERIFIED_ASSUMPTION`，`evidence_level` 降为 `E0/E1`，记录原因 |
| T12 | `GENERALIZED` → `VERIFIED` | 泛化被撤销（仍成立但不通用） | `content_class` 改回 `CONFIRMED_FACT` 或 `SOURCE_SPECIFIC_EXPERIENCE`；`decision` 记录 |

#### 3.2.1 T10 与 `GENERAL_LESSON` 的前置步骤（`HD-62-B` / B1）

§2.1 的状态表不变：`GENERAL_LESSON` 的允许状态不含 `NEEDS_VERIFICATION`，因此 `GENERAL_LESSON` 不能直接执行 T10。T12 的表格边同样不变，只覆盖 `GENERALIZED → VERIFIED`，不覆盖 `REGRESSION_WATCH`；不得把 T12 写成从 `REGRESSION_WATCH` 出发的边。

`GENERAL_LESSON` 处于 `REGRESSION_WATCH` 并观察到回归时，按以下顺序处理：

1. 记录回归证据（可定位链接）。
2. **先撤销泛化（T12 语义）**：用 §2.1 规则 3 的显式分类变更把 `content_class` 退回 `SOURCE_SPECIFIC_EXPERIENCE` 或 `CONFIRMED_FACT`，`applicability.scope` 回到 `source_only`，并记录 `decision`（决定人 + 理由）。此时 `status` 仍是 `REGRESSION_WATCH`。
3. **再执行 T10**：`REGRESSION_WATCH → NEEDS_VERIFICATION`，把上一步的回归证据加入 `source.evidence_urls`。
4. 直接对 `GENERAL_LESSON + REGRESSION_WATCH` 执行 T10 必须被拒绝；跳过第 2 步的任何写法都非法。

示例（`LSN-EXAMPLE-012`，合成值，仅示意字段变化）：

```text
初始：content_class=GENERAL_LESSON, status=REGRESSION_WATCH,
      evidence_level=E4_REGRESSION_COVERED, applicability.scope=agentorch_general,
      links.issues=[#49]

1) 观察到回归，新增证据 https://example.com/talkdnd/issues/99
2) 撤销泛化（T12 语义；reclassify 是 §2.1 规则 3 的显式分类变更，不新增表格边）：
   reclassify(record, 'SOURCE_SPECIFIC_EXPERIENCE',
              decision=make_decision('project-owner', 'DEFER',
                                     '观察到回归，撤销泛化并重新验证'))
   → content_class=SOURCE_SPECIFIC_EXPERIENCE, applicability.scope=source_only,
     status=REGRESSION_WATCH（不变）, decision 已记录
3) T10：
   transition(record, 'NEEDS_VERIFICATION',
              new_evidence_urls=['https://example.com/talkdnd/issues/99'])
   → status=NEEDS_VERIFICATION（content_class 保持 SOURCE_SPECIFIC_EXPERIENCE）

反例：跳过第 2 步，直接 transition(record, 'NEEDS_VERIFICATION', ...) 被校验拒绝：
  GENERAL_LESSON allows only VERIFIED, GENERALIZED, IMPLEMENTED, REJECTED, REGRESSION_WATCH
```

如果第 2 步选择 `CONFIRMED_FACT`，必须显式传 `scope='source_only'`；`reclassify()` 只为 `SOURCE_SPECIFIC_EXPERIENCE` 自动设置该 scope，不显式传会保留 `agentorch_general`（语义错误，虽然 schema 不拒绝）。#49 的 `reclassify()` + `transition()` 已实现上述组合；本任务未改代码、未改 T12 表格边。

### 3.3 非法转换（必须被拒绝）

- `OBSERVED` / `NEEDS_VERIFICATION` → `GENERALIZED` / `IMPLEMENTED`（跳过验证与泛化）。
- `UNVERIFIED_ASSUMPTION` → `VERIFIED` / `GENERALIZED` / `IMPLEMENTED`。
- `SOURCE_SPECIFIC_EXPERIENCE` → `GENERALIZED` / `IMPLEMENTED`（必须先显式分类改为 `GENERAL_LESSON` 并记录决策）。
- `GENERAL_LESSON` 在 `REGRESSION_WATCH` 直接 `NEEDS_VERIFICATION`（必须先按 §3.2.1 撤销泛化；`GENERAL_LESSON` 的允许状态已排除 `NEEDS_VERIFICATION`）。
- 在 `REGRESSION_WATCH` 上执行 T12 表格边（T12 只从 `GENERALIZED` 出发；`REGRESSION_WATCH` 的撤销泛化按 §3.2.1 用显式分类变更完成）。
- `REJECTED` → 任何其它状态（拒绝是终局；重新评估必须新建 Lesson 并引用旧 ID）。
- 任一带 `OPEN` 冲突的 Lesson → `GENERALIZED` / `IMPLEMENTED`。
- 任何没有 `decision` 的 `REJECTED` / `GENERALIZED` / `IMPLEMENTED`。
- 任何把 `NOT_RUN` / `UNKNOWN` 证据当作“已验证”的转换。

schema 能机器判定的部分（证据等级下限、分类与状态匹配、决策必填、`GENERALIZED` 无 `OPEN` 冲突）已在 `lesson.schema.json` 的 `allOf` 中强制；其余为语义检查，由 #49 的校验器与审查共同保证。§3.2.1 的 `GENERAL_LESSON` 前置属于语义检查；跳过撤销泛化直接 T10 的最终状态会被 `GENERAL_LESSON` 的允许状态拒绝。

## 4. 去重指纹

### 4.1 算法 `sha256-normalized-v1`（冻结）

只使用 `fingerprint.normalized_fields` 列出的字段（至少三个，取值只能来自 `root_cause`、`impact`、`affected_surface`、`suggested_action_target`）。

规范化步骤（按顺序，对每个字段）：

1. Unicode NFKC 归一。
2. `casefold()` 转小写。
3. 把连续空白（含换行）折叠为单个空格。
4. 去掉首尾空白。
5. 去掉结尾标点：`. , ; : ! ? 。 ， ； ： ！ ？`
6. 对 `affected_surface` / `suggested_action_target`，仅保留字符 `[a-z0-9_./-]`，并再次折叠空白（这两个字段通常是标识符，不保留自然语言差异）。
7. 空字段视为空字符串，不参与消除歧义。

摘要：

```text
canonical = json.dumps(
    [[field_name, normalized_text] for field_name in sorted(normalized_fields)],
    ensure_ascii=False, separators=(",", ":"), sort_keys=False,
).encode("utf-8")
value = sha256(canonical).hexdigest()   # 64 位小写十六进制
```

`root_cause` 与 `impact` 保留自然语言，因此同义改写会产生不同指纹；这是**有意的保守选择**（见 4.3）。

### 4.2 命中同一指纹

- 视为**同一教训**：保留最早 `lesson_id`，把新证据追加到 `source.evidence_urls`，更新 `captured_at` 与 `updated_at`。
- 新记录写 `duplicate_of=<原 lesson_id>`，且**不得**生成新的改进任务或 Issue。
- 重复扫描同一窗口必须幂等：同一 `(source_id, evidence_url, fingerprint)` 不产生第二条记录。

### 4.3 指纹碰撞与近似重复

- 不同语义得到同一指纹（碰撞）：保留证据，标 `conflicts_with: OVERLAPS` 且 `OPEN`，交人工判断；不得自动合并或删除。
- 语义相同但指纹不同（同义改写）：标记为 `OVERLAPS`（`OPEN`），由审查者判断是否合并；合并时保留两条证据链接并只保留一个 `lesson_id` 作为主记录。
- 指纹重算（例如根因描述被修正）不得创建新 Lesson；实现层索引允许旧指纹映射到同一 `lesson_id`，记录只保存当前值。

## 5. 冲突决策

冲突类型：`CONTRADICTS`（结论相反）、`SUPERSEDES`（新证据取代旧结论）、`OVERLAPS`（部分重叠或语义近似）。

规则：

1. **默认 `OPEN`**，不得单方面选择一方；`OPEN` 冲突阻断 `GENERALIZED` / `IMPLEMENTED`（schema 强制）。
2. 只有两种解除方式：
   - `RESOLVED_BY_EVIDENCE`：有可复现证据证明适用条件不同或一方不成立；`resolution_evidence` 必须是可定位链接 / 命令结果。
   - `RESOLVED_BY_OWNER`：项目所有者决定；`resolution_evidence` 指向 Issue 评论或决策 ID。
3. 证据等级高**不自动**胜出。只有在适用条件相同、且高等级证据可复现时，才可建议 `SUPERSEDES`；最终仍需记录决策。
4. 两条教训适用条件不同 → 都不是通用结论；分别降为 `SOURCE_SPECIFIC_EXPERIENCE`，不得合并成一条“折中规则”。
5. 涉及安全 / 权限 / 锁 / 状态机 / 数据迁移的冲突 → 强制 `HD-04`，由项目所有者裁决。
6. 冲突未解决时，报告必须列出冲突双方、证据与阻塞的改进项；不得在报告中隐去。

## 6. 泛化与提升门槛

`CONFIRMED_FACT` / `SOURCE_SPECIFIC_EXPERIENCE` → `GENERAL_LESSON` 必须同时满足：

1. 证据 ≥ `E2`；推荐 `E3`（来源侧带测试的修复）。
2. 结论与来源业务模型无关；`applicability.excluded_contexts` 写明反例条件。
3. 已检查 AgentOrch 现有机制是否覆盖：已覆盖时只记录证据或建议补测试，**不得**新增重复规则。
4. 明确映射对象（`suggested_action.target`）与验证方式；没有可执行后续动作的不提升，只入报告。
5. 涉及安全 / 权限 / 锁 / 状态机 / 数据迁移 / 外部依赖 → `HD-03` 人工决策，不得自动提升。
6. 无 `OPEN` 冲突；`decision` 记录决策人与理由。

反向规则：任何 `GENERAL_LESSON` 若被证明只适用于来源，立即按 T12 退回 `SOURCE_SPECIFIC_EXPERIENCE`，并保留历史决策。

## 7. 改进对象与任务生成

只有 `GENERAL_LESSON` 且状态为 `GENERALIZED` 才允许生成改进任务；任务本身遵守核心契约：

- 任务包 / Issue 必须含目标、允许 / 禁路径、逐条验收、负向场景、依赖与验证命令（字段定义见 `docs/orchestration.md` 4.1 与 `templates/task-packet.md`）。
- 任务优先级与依赖由 Master 决定；Lesson 不直接创建 PR，也不自动合并。
- 同一指纹的重复 Lesson 不生成第二个任务；已有覆盖机制时只补测试证据。
- `IMPLEMENTED` 只在对应任务合并并由核心门验收后记录；Lesson 状态不能替代任务状态。

## 8. 保留、删除与隐私

- 来源停用 / 删除 **不删除** Lesson 与决策记录；只停止后续扫描。
- `REJECTED` 记录永久保留（防止同一误报反复出现）。
- 删除 Lesson 只允许在“合成 fixture 误入库”或“含敏感信息”两种情况下执行，并必须记录原因、决策人与脱敏替代内容。
- 任何记录都不得包含来源正文、凭据、个人路径、客户数据。

## 9. 失败语义

| 情况 | 记录 | 结论 |
|---|---|---|
| 证据链接不可达 | `evidence_level=E0_CLAIM`，`notes` 说明 | 只能 `UNVERIFIED_ASSUMPTION` |
| CI 证据缺失 / PR 已关闭但无合并证据 | 标 `NOT_RUN`（报告层） | 不得推断“已修复” |
| 复现失败 | `NEEDS_VERIFICATION` 或 `REJECTED`（需决策） | 不得写成运行时事实 |
| 指纹算法不可用 / 记录缺字段 | 拒绝写入 | 不得退化为“标题匹配”去重 |
| 冲突无法裁决 | `OPEN` + `HD-04` | 停止采纳，报告阻塞项 |
| 来源专属但被误标通用 | 立即 T12 降级 | 不得继续生成任务 |

## 10. 人工决策点

- `HD-03` 提升为通用教训（涉及安全 / 权限 / 锁 / 状态机 / 迁移）。
- `HD-04` 冲突教训裁决。
- 删除含敏感信息的 Lesson（虽不属高风险目录，仍要求记录决策人）。
