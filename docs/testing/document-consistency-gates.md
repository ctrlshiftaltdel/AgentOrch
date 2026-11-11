# 文档一致性门禁（Issue #154）

> **状态：`ACTIVE`。** 本文件说明 `scripts/validate_kit.py` 中可机器判定的文档一致性检查：检查什么、依据哪份登记、失败时怎么修、明确不检查什么。它只描述检查口径，不新增、不改写任务状态机、合并门、锁或证据矩阵——那些规则只在 [`docs/orchestration.md`](../orchestration.md) 与 [`AGENTS.md`](../../AGENTS.md) 定义。文件责任与生命周期语义分别在 [repository-map.md](../architecture/repository-map.md) 与 [document-lifecycle.md](../architecture/document-lifecycle.md)。

## 1. 适用范围

| 树 | 执行的检查 |
|---|---|
| 上游 checkout（`docs/upgrade.md` 存在） | 全部检查：登记覆盖、旧路径、文档影响、计划状态，以及两种发行规则核对 |
| 下游项目（有 `docs/PROJECT.md`，无 `docs/upgrade.md`） | 文档影响模板；发行规则（仅当仓库自带构建脚本时） |
| 纯净发行包 | 文档影响模板；构建脚本不随包发布，因此没有可比对的发行规则 |

所有检查都只读：不改写文档、不修改 Issue / PR、不写任务库、不访问网络。唯一产物是 stdout 的失败项与“待人工核验”行。

## 2. 关键文件责任 / 路径 / 生命周期登记

- 登记位置：[repository-map.md](../architecture/repository-map.md) §3（顶层目录与根文件）与 §4（`docs/` 逐文件）。检查依据 `路径` / `用途` / `生命周期` 三列。
- **必须登记**：根入口与契约文件（`AGENTS.md`、`LICENSE.md`、`README.md`、`VERSION.md`），以及 `docs/`、`editions/`、`templates/` 下的每个文件。`scripts/` 与 `tests/` 按目录行整体覆盖（[document-lifecycle.md](../architecture/document-lifecycle.md) §4.4：普通源文件按目录归组）。
- **登记方式**：精确路径、目录前缀（以 `/` 结尾）或 glob（如 `docs/contracts/schemas/*.schema.json`）。`mixed` 是索引行，**不构成覆盖**：`docs/` 行只说明“逐文件见 §4”。
- **允许的例外**（必须写在 repository-map.md 的 `例外登记` 一节）：
  - `derived-local` / 私有：`.local/`、`.orchestration/`、`.worktrees/`、`.workbuddy/`、缓存目录；
  - 展示资产：`docs/images/`（按正文引用按需读取，禁止批量读取）；
  - 归档：`ARCHIVED` / `SUPERSEDED` 条目必须给出保留理由与替代项，或显式“无替代”。
- **失败示例**：新增 `docs/design/foo.md` 未登记 → `docs/design/foo.md is not registered ...`；登记了 `docs/gone.md` 但文件不存在 → `... is registered but missing on disk`；`用途` / `生命周期` 用了词表外的值 → `unknown purpose tag` / `unknown lifecycle state`。
- **不要求**：不要求把宿主项目的所有原文件登记进本仓库的上游地图，也不要求修改宿主根 ignore 文件。目录行覆盖 `scripts/`、`tests/` 就是为了这一点。

## 3. Markdown 相对链接与核心路径引用

- 相对链接必须解析到真实文件或目录；外部 URL、`mailto:` 与锚点跳过。代码块与行内代码里的链接语法是示例文本，不当作活动链接。已由既有的链接扫描覆盖，本次不重复实现。
- **旧路径只允许出现在显式迁移 / 兼容登记中**：[document-lifecycle.md](../architecture/document-lifecycle.md) §7.1 的 `旧路径登记` 表给出“旧路径 / 替代路径 / 允许出现的文件”。检查逐文件扫描 Markdown 正文（忽略 fenced code block），出现在未登记文件即失败。
- 登记行本身也要成立：替代路径必须存在，列出的允许文件必须存在，旧路径不得仍存在于磁盘或 `REQUIRED` 中。

## 4. 文档影响记录

- 任务包与审查都使用同一形状（字段定义来自设计 Issue #147 §2.3，本文件不重述字段语义）：

  | path | status | change or reason |
  |---|---|---|
  | `docs/README.md` | `UPDATE` | 说明具体改了什么 |
  | `AGENTS.md` | `NO_CHANGE` | 说明为什么现有内容仍正确 |

- `status` 只允许 `UPDATE`、`NO_CHANGE`、`NOT_APPLICABLE`：
  - `UPDATE` 必须有变更描述，且交付 Diff 中要有对应修改（后者由审查判定，机器只查描述非空）；
  - `NO_CHANGE` / `NOT_APPLICABLE` 必须给理由；`(none)`、`N/A`、空值都算缺失。
- 表头、行数、重复路径、绝对路径或越界路径（`..`）也会被检查。检查入口：`scripts/validate_kit.py --task-packet <文件>` 与包内模板样例。
- 模板自身也必须携带该契约：`templates/task-packet.md` 的字段与表头、`templates/review-checklist.md` 的逐条审查项；删掉任一处即失败。

## 5. 计划状态：只做可证明的检查

[`docs/implementation-plan.md`](../implementation-plan.md) §1 是唯一状态表。检查只覆盖能从文件本身证明的事实：

- 状态表存在（表头同时含 `任务` 与 `状态`）；
- 状态词在已知词表内（`VERIFIED`、`BLOCKED`、`OPEN`、`PLANNED`、`READY` 等；完整取值以 `docs/orchestration.md` 4.1 与计划里的规划词为准）；未知大写词按拼写错误或第二套词表报告；
- 标为 `VERIFIED`（未被“未 / not”否定）的行必须带证据指针：Issue / PR / Release / commit 链接或 40 位 SHA。

以下内容**不由机器判定**，只输出一行 `待人工核验` 摘要，列出依赖 GitHub Issue / PR 实时状态的行：

- Issue 是否真的处于该状态、PR 是否真的已合并、该状态是否已由证据验证；
- 计划里的状态是否应更新。

因此：**不得**把该检查的输出当作“计划已同步”；也**不得**为了让检查通过而修改 Issue 状态或自动改写计划。状态更新仍走任务分支 + PR 与独立审查。

## 6. 发行包规则一致性

`scripts/validate_kit.py` 的 `REQUIRED` 同时是结构必需文件与发行包清单；`scripts/build_release.py` 解析它并由 `scripts/check_drift.py` 生成 `MANIFEST.json`。检查要求四处一致：

- `REQUIRED` 中任何 `docs/` 路径必须在 `build_release.py` 的 `PACKAGE_DOC_PATHS` 白名单内——上游设计、架构、归档与计划文档**不得自动进入发行包**；
- `scripts/build_release.py` 必须列为禁止打包文件，`.orchestration`、`release`、`.worktrees`、`.workbuddy` 必须列为禁止路径段；
- 白名单只能列 `docs/` 文件，且必须都在 `REQUIRED` 中；
- `release/README.md` 必须记录该白名单与 `MANIFEST.json` 规则。

发行自检 (a) 的“包内集合 == `REQUIRED` + `MANIFEST.json`”比较被抽成 `package_set_errors()`，因此“漏包一个文件”和“误包一个文件”可以分别做负向测试；包内 `validate_kit.py` 与 `MANIFEST.json` 自检仍按原强度在解压目录中真实执行。

## 7. 隔离布局

`<宿主根>/agentorch/` 隔离安装没有套件根的 `AGENTS.md`：唯一入口是宿主的 `<宿主根>/AGENTS.md` 受管区块。因此检查**不得**把缺少 `agentorch/AGENTS.md` 当作缺失文件；`AGENTS.md` 也不在隔离清单里，隔离清单是“随包文件集 + `.gitignore`”。普通 checkout 仍要求根 `AGENTS.md`。

## 8. 未覆盖范围（不得当成已检查）

- 文档内容是否真实、是否与代码一致、同一规则是否在第二处重复定义——语义问题仍需审查；
- `UPDATE` 行是否真的有对应 Diff、`NO_CHANGE` 理由是否成立——审查项；
- GitHub Issue / PR 的实时状态与 CI 结果；
- `docs/images/` 的图片内容与脱敏（图片靠人工把关）；
- 宿主项目自有文件的登记完整性与宿主根 ignore 文件。
