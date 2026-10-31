# 文档生命周期规则

> **状态：`ACTIVE`。** 本文件是仓库文件生命周期状态、状态转换，以及新增 / 修改 / 弃用 / 迁移 / 归档 / 删除规则的唯一权威；文件职责、用途标签与分发范围的当前盘点在 [repository-map.md](repository-map.md)。生命周期不改变任务状态机、合并门、锁或证据矩阵——那些规则只在 [`kit/orchestration.md`](../../kit/orchestration.md) 与 [`AGENTS.md`](../../AGENTS.md) 定义。
>
> 本文件不重述设计 Issue #147 的字段定义；在需要文档影响记录时引用该设计的 §2.3，而不是另写一套字段。

## 1. 两个独立维度

- **用途标签**回答“这个文件在规则体系里是什么角色”：`authoritative`、`reference`、`implementation`、`derived-local`、`outdated`。标签定义与当前盘点只在 [repository-map.md](repository-map.md) §2。
- **生命周期状态**回答“这个文件现在是否有效、是否还能驱动工作”：`PROPOSED`、`ACTIVE`、`IMPLEMENTED`、`DROPPED`、`SUPERSEDED`、`ARCHIVED`。定义与转换只在本文件。

两者不可互相替代：`reference` 可以是 `ACTIVE` 或 `ARCHIVED`；`authoritative` 也可以是 `IMPLEMENTED`（一次性计划已完成但现行规则仍有效）；`outdated` 不是生命周期状态，而是需要替代链接与退出条件的用途标签。

## 2. 生命周期状态

| 状态 | 含义 | 能否作为当前依据 | 必要记录 |
|---|---|---|---|
| `PROPOSED` | 提议中，尚未冻结或尚未获准驱动实现。 | 否；只能作为讨论材料。 | 提案 Issue / 设计文档；待决项与冻结条件。 |
| `ACTIVE` | 当前有效，允许按正式任务维护。 | 是。 | 权威位置或引用关系；最后核验基线。 |
| `IMPLEMENTED` | 对应的一次性计划 / 交付已完成，并有验收证据；若仍承担现行规则，继续按 `ACTIVE` 使用。 | 是，但只在其仍有效的范围内。 | 完成证据（Issue / PR / 验证结果）；是否仍为现行权威。 |
| `DROPPED` | 已决定不实施或不再维护。 | 否。 | 原因、决定依据、替代方案或“无替代”的说明。 |
| `SUPERSEDED` | 被新文件 / 新决策取代。 | 否；以替代项为准。 | **替代链接**、生效时间、受影响引用。 |
| `ARCHIVED` | 仅为历史、审计或兼容性追溯保留，已移出默认阅读路径。 | 否，除非明确用于追溯。 | 归档理由、保留价值、替代链接（若有）、退出条件。 |

`IMPLEMENTED` 不等于废弃。一个已实现的设计契约如果仍是现行规则，应保持 `ACTIVE`；只有被替代、被决定不再维护或仅剩历史价值时，才进入 `SUPERSEDED` / `DROPPED` / `ARCHIVED`。

## 3. 状态转换

| 从 | 到 | 触发条件 | 证据 / 批准 |
|---|---|---|---|
| `PROPOSED` | `ACTIVE` | 设计经独立审查、合并并经任务载体确认冻结。 | 合并记录、`VERIFIED` 记录或 Owner 决定；不得只凭文件头自证。 |
| `PROPOSED` | `DROPPED` | 决定不实施。 | 决定 Issue / 评论；理由与替代方案。 |
| `ACTIVE` | `IMPLEMENTED` | 对应一次性交付完成，但文档仍作为实现记录或现行规则保留。 | 完成 Issue / PR / 验证结果；注明是否仍为权威。 |
| `ACTIVE` / `IMPLEMENTED` | `SUPERSEDED` | 出现新文件或新决策，旧文件不再作为当前依据。 | **替代链接**、决定依据、受影响引用清单。 |
| `ACTIVE` / `IMPLEMENTED` | `ARCHIVED` | 仍有审计 / 兼容价值，但已移出默认阅读路径。 | 保留理由、替代链接（若有）、退出条件。 |
| `ACTIVE` / `IMPLEMENTED` | `DROPPED` | 决定不再维护且无替代。 | 决定依据；说明为何无需替代。 |
| `SUPERSEDED` | `ARCHIVED` | 替代项已稳定，旧文件只剩历史价值。 | 替代项链接、最后核验日期、退出条件。 |
| `ARCHIVED` | `DROPPED` | 保留价值消失，决定删除。 | Owner / Master 明确决定；删除前检查见 §9。 |

任何状态变化都必须有任务载体记录；`PROPOSED` 不得靠编辑文件头变成 `ACTIVE`，`IMPLEMENTED` 不得靠“看起来做完了”变成 `ACTIVE`。

## 4. 新增文件

新增文件前先回答四个问题：

1. **它是否定义新规则？** 如果是，必须指定它为唯一权威位置，并检查现有权威文件是否需要改为引用；不得平行建立第二套定义。
2. **它是否只是引用、说明、实现或本机数据？** 按 [repository-map.md](repository-map.md) §2 选 `reference`、`implementation` 或 `derived-local`，并链接到权威位置。
3. **它是否属于关键文件？** 根契约、计划、设计契约、schema、场景目录、发行标准、CI 与验证入口要登记进 [repository-map.md](repository-map.md)；普通源文件按目录归组，不为每个文件另写说明。
4. **它是否进入发行包？** 进入 `REQUIRED` 的路径会同时成为发行包清单；必须评估构建、包内自检、更新引擎与下游兼容影响。`docs/`、`release/` 等路径段，以及 `.orchestration`、`.worktrees`、`.workbuddy` 等运行时 / 忽略目录名不得进入发行包。

新增文件还必须：

- 使用相对链接指向权威文件，不复制状态机、G1–G6、C1–C4、字段枚举或证据矩阵。
- 按设计 Issue #147 §2.3 记录文档影响：`UPDATE` 必须有实际变更；`NO_CHANGE` 与 `NOT_APPLICABLE` 必须给出依据。
- 运行 `uv run python scripts/validate_kit.py`，并手工核对新增路径与链接真实存在。

## 5. 修改文件

1. **先改权威位置，再改引用。** 规则变更只改唯一权威文件；其它文件只更新链接、示例或说明，不复制新规则。
2. **检查影响面。** 至少检查：`AGENTS.md`、`kit/orchestration.md`、两个 `editions/`、相关 `templates/`、`docs/PROJECT.md`、`docs/PLAN.md`、`docs/implementation-plan.md`、`docs/README.md`、发行清单 / 构建 / 更新引擎。实际受影响项写入文档影响记录。
3. **保持分发边界。** 修改 `REQUIRED`、禁止路径、构建脚本或包内自检时，必须同时验证发行包文件集合与下游更新行为；不得只改文档。
4. **更新地图。** 如果职责、权威归属、用途标签、生命周期或分发范围发生变化，同步更新 [repository-map.md](repository-map.md)。
5. **不得夹带无关重排。** 文档修改与任务目标一致；不顺手移动、删除或格式化无关文件。

## 6. 弃用与 `outdated`

### 6.1 什么情况下可以标 `outdated`

一个文件只有同时满足下列条件，才能在 [repository-map.md](repository-map.md) 中标为 `outdated`：

| 条件 | 要求 |
|---|---|
| **替代链接** | 指向替代文件、替代决定（Issue / PR / 决策记录）或明确的“无替代”决定；替代项必须可定位。 |
| **保留理由** | 说明为什么仍保留：审计追溯、旧版本升级兼容、历史决策、法务 / 许可，或其它可核验价值。 |
| **退出条件** | 写明何时可以归档或删除：例如替代项合并并 `VERIFIED`、无当前入口引用、经过一个发布周期、Owner 决定。不能只写“以后再看”。 |
| **决定依据** | 记录谁在哪个 Issue / PR / 决策中批准弃用；不得由执行 Agent 自行推断。 |
| **最后核验日期** | 记录最后一次确认替代关系与引用状态的日期（UTC 或仓库约定的日期格式）。 |
| **移出当前路径** | 默认不再出现在首要阅读入口、当前计划、当前设计索引或当前发布说明中；只能从历史 / 归档入口进入。 |

缺任一项时，只能登记为“候选过期”（见 [repository-map.md](repository-map.md) §9.3），不能标 `outdated`，也不能据此删除或移动文件。

### 6.2 弃用流程

1. **登记候选。** 在 [repository-map.md](repository-map.md) §9.3 写观察、证据、影响与建议；不改变文件状态。
2. **确认替代。** 替代文件 / 决定必须已经存在且可定位；如果替代项本身仍是 `PROPOSED`，不得把旧文件标为 `SUPERSEDED` 或 `outdated`。
3. **移出阅读路径。** 更新导航、索引、计划与链接，使旧文件不再是当前入口；保留历史入口。
4. **记录生命周期。** 按 §2–§3 写入 `SUPERSEDED` 或 `ARCHIVED`，并补齐 §6.1 的六项记录。
5. **验证。** 运行 `uv run python scripts/validate_kit.py`；若文件在 `REQUIRED` 中，先确认它是否应继续随包发布，再修改清单与构建。

### 6.3 `outdated` 与 `IMPLEMENTED` 的区别

- `IMPLEMENTED` 是生命周期：对应一次性计划已完成。它可能仍是现行权威（继续 `ACTIVE` 使用），也可能转为 `SUPERSEDED` / `ARCHIVED`。
- `outdated` 是用途判断：文件已过时或被替代，且满足 §6.1 的退出条件。
- **不得**因为“计划做完了”就把仍定义现行规则的文件标成 `outdated`；也不得因为文件很旧就推断它已过期。

## 7. 迁移文件

迁移（移动 / 改名）必须作为独立任务执行，不能夹带在普通文档修改里：

1. **先盘点引用。** 找出所有相对链接、`REQUIRED`、`MANIFEST.json` / 构建、更新引擎、测试夹具、CI 与下游文档中的旧路径。
2. **使用版本控制移动。** 优先 `git mv`，保留可审查的重命名历史；不得复制出两份可编辑权威。
3. **一次性更新引用。** 所有当前入口、索引、计划、模板、脚本与测试同步更新；旧路径若需兼容，只能作为显式兼容层 / 重定向说明，并标 `SUPERSEDED` + 替代链接，不得长期保留第二份权威。
4. **更新分发链。** 若路径在 `REQUIRED` 中，修改清单、构建、包内自检与更新引擎，并运行 `uv run python scripts/validate_kit.py` 与 `uv run python scripts/build_release.py` 自检。
5. **记录迁移。** 在任务载体与 [repository-map.md](repository-map.md) 记录旧路径、新路径、生效版本、兼容窗口与回退方式。

本任务（#150）**不移动任何文件**；未来的核心契约迁移（GOV-2 / #151）必须按本节单独执行。

## 8. 归档

归档表示“保留可追溯地址，但移出默认阅读路径”：

- 在 [repository-map.md](repository-map.md) 中把生命周期改为 `ARCHIVED`，写明保留理由、替代链接（若有）与退出条件。
- 从当前导航、当前计划、当前设计索引和首要阅读入口移除；保留历史 / 审计入口或任务载体链接。
- 不删除文件内容；不把归档当作“已通过验证”或“已批准删除”。
- 归档文件若仍在 `REQUIRED` 中，不得直接移出仓库；先确认发行包是否需要它，否则会破坏下游兼容。

当前仓库没有独立的归档目录；本任务不新建目录、不移动现有文件。未来需要独立归档位置时，另立任务并同步 `REQUIRED`、构建与更新引擎。

## 9. 删除

删除是不可逆动作，默认需要人工决定：

1. **确认无追溯价值**：没有审计、兼容、法务或历史决策价值；替代项已稳定；不再被当前入口引用。
2. **确认无依赖**：`REQUIRED`、构建、更新引擎、测试、CI、模板与下游升级路径均不再需要。
3. **取得批准**：由项目所有者或 Master 在任务载体中明确批准；执行 Agent 不得自行删除。
4. **更新记录**：在 [repository-map.md](repository-map.md) 与任务载体记录删除原因、替代链接（若有）、影响分析与最后核验日期。
5. **验证**：运行 `uv run python scripts/validate_kit.py`；若涉及发行内容，再运行 `uv run python scripts/build_release.py` 自检。

只因为“文件很长”“看起来过时”或“目录更整齐”不构成删除理由。

## 10. 文档影响记录与验证

- 影响项目目标、规则、行为、接口、文件布局、运行方式、发行内容或迁移的任务，必须按设计 Issue #147 §2.3 为每份受检文档记录 `UPDATE` / `NO_CHANGE` / `NOT_APPLICABLE`；后两者必须写依据。字段定义不在本文件重述。
- 任何新增 / 修改 / 迁移 / 归档 / 删除后，至少运行：
  - `uv run python scripts/validate_kit.py`
  - 手工核对本任务新增或修改的每个相对链接与仓库路径真实存在。
- 涉及发行包路径、`MANIFEST.json`、构建或更新引擎时，追加 `uv run python scripts/build_release.py` 与相关测试；未执行的项目标 `NOT_RUN` / `BLOCKED`，不得写成通过。
- 生命周期状态与用途标签以 [repository-map.md](repository-map.md) 的当前盘点为准；发现地图与文件实际状态不一致时，先登记差异并停下对齐，不在两处各写一套。

## 11. 与任务载体、分发的关系

- 生命周期是**文件治理**，不是任务状态机。任务状态、锁、合并门与 `VERIFIED` 语义只在 [`kit/orchestration.md`](../../kit/orchestration.md) 与 [`AGENTS.md`](../../AGENTS.md) 定义；本文件不新增、不改写任何状态或门禁。
- 文件的 `ACTIVE` / `IMPLEMENTED` / `ARCHIVED` 不等于任务已批准、已合并或已验证；证据仍必须绑定当前 head SHA。
- 分发范围以 [`scripts/validate_kit.py`](../../scripts/validate_kit.py) 的 `REQUIRED` 为单一来源；`scripts/build_release.py` 解析它，`scripts/check_drift.py` 生成清单。生命周期变化若影响分发，必须同时更新这条链并通过发布自检。
- 本文件与 [repository-map.md](repository-map.md) 均属于 `docs/`，`仅仓库`，不随发行包发布；它们不放宽任何现有禁改路径或任务权限。
