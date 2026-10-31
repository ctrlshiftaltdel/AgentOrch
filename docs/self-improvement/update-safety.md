# 更新安全与逐文件分类（v1）

> 机器定义：[`update-manifest.schema.json`](../contracts/schemas/update-manifest.schema.json)（官方包清单）与 [`update-report.schema.json`](../contracts/schemas/update-report.schema.json)（每次运行报告）。
> 本文件是**逐文件分类、默认动作、失败关闭与回滚语义的唯一权威**；schema 只列枚举，不重写动作表。
> 信任边界与威胁见 [threat-model.md](threat-model.md)；触发语与决策目录见 [entrypoints-and-decisions.md](entrypoints-and-decisions.md)。

## 1. 范围与非目标

**范围**：从官方 Release 获取套件、校验包、识别旧基线、对“旧上游 / 当前工作区 / 新上游”做逐文件三方分类、准备可审查的更新变更集、备份与回滚，并输出可核验报告。

**非目标**：

- 不实现下载 / 解包 / 分类代码（#51 实现），不在本阶段访问真实 Release。
- 不定义新的任务状态或合并门；更新任务仍走 `docs/orchestration.md`。
- 不自动合并、不自动改主分支、不自动删除上游移除的文件。
- 不把项目自有内容纳入“套件更新”的覆盖范围。

## 2. 官方 Release 信任来源

| 项 | 冻结规则 |
|---|---|
| 来源仓库 | 只接受 `ctrlshiftaltdel/AgentOrch` 的 Releases。常量写入 `target.repository`，不可由项目配置覆盖 |
| 获取方式 | GitHub Releases 的发布资产；不接受任意 URL、Issue 附件、分支构建、本地 zip、镜像站或第三方代理 |
| 通道 | 默认 `stable`；预发布 / draft 必须 `HD-07` 批准并记录理由 |
| 资产名 | `^[A-Za-z0-9._-]+\.zip$`（当前形如 `agentorch-YYYYMMDD.zip`）；无扩展名或含路径的资产拒绝 |
| Tag 形式 | 接受 `v<major>.<minor>` 与 `v<major>.<minor>.<patch>`（本仓库现有 tag 如 `v1.1`、`v1.5` 均合法）。**比较时缺失的 patch 按 `0` 归一**（`v1.6` ≡ `v1.6.0`），报告与显示保留原始 tag |
| 版本单调性 | 目标版本必须 **≥** 已安装版本；降级 / 平级重装必须走 `HD-16`，默认拒绝 |
| 完整性 | 必须能取得资产的 SHA-256 与包内 `MANIFEST.json`；两者缺失或无法核对 → `UNSAFE_PACKAGE` |
| 网络不可用 | 不重试到成功为止；报告 `BLOCKED` / `NOT_RUN`，保留旧版本，不得改用离线猜测包 |

“官方”只由上述仓库常量与 Release 通道判定；Issue 评论里贴出的链接、他人 fork 的 Release、`main` 分支源码包都不构成官方发行包。

## 3. 包校验顺序与失败语义

校验在**项目外临时目录**完成；任一步失败即停止，不写项目文件。

| 顺序 | 检查（`verification.checks[].check`） | 内容 | 失败归类 |
|---|---|---|---|
| 1 | `SOURCE_ALLOWLIST` | 仓库 / 主机 / Release 归属 | `UNSAFE_PACKAGE` |
| 2 | `RELEASE_CHANNEL` | stable / prerelease / draft | `UNSAFE_PACKAGE`（除 `HD-07` 已批准） |
| 3 | `ASSET_SHA256` | 下载字节的 SHA-256 与 Release 记录一致 | `UNSAFE_PACKAGE` |
| 4 | `MANIFEST_SCHEMA` | `MANIFEST.json` 存在、可解析、`schema_version` 受支持 | `UNSAFE_PACKAGE`（清单不可读）/ `INVALID_BASELINE`（旧清单不可读） |
| 5 | `MANIFEST_SHA256` | 清单文件自身哈希与记录一致（可得时） | `UNSAFE_PACKAGE` |
| 6 | `MEMBER_PATHS` | 成员路径无绝对路径、盘符、`..`、反斜杠、空段 | `UNSAFE_PACKAGE` |
| 7 | `SYMLINKS` | 无符号链接 / 硬链接 / 设备文件成员 | `UNSAFE_PACKAGE` |
| 8 | `FILE_SET` | 清单条目集合 == 归档成员集合（允许清单自身不列出） | `UNSAFE_PACKAGE` |
| 9 | `EXPECTED_LAYOUT` | 单一顶层目录、无非预期可执行位、无嵌套归档 | `UNSAFE_PACKAGE` |
| 10 | `VERSION_MATCH` | 清单 `suite` / `format_version` 与 release tag / 资产名可互证 | `UNSAFE_PACKAGE` |

规则：

- **清单不可信时不得继续分类**：`UNSAFE_PACKAGE` 的整次更新停止，不产生任何文件写入。
- 旧基线清单（已安装项目的 `MANIFEST.json`）不可读 / schema 不支持 → `INVALID_BASELINE`：只读盘点 + 手工迁移路线，**不得**把哈希差异当作覆盖许可。
- 所有检查的 `result` 只允许 `PASS` / `FAIL` / `NOT_RUN`；未执行的检查写 `NOT_RUN`，不得折算通过。
- 校验器不得在项目中留下临时文件；解包目录使用系统临时目录并在结束时清理（失败时保留路径写入报告以便取证，但不保留私有内容）。

## 4. 逐文件分类（唯一权威）

分类输入：旧上游清单（`baseline`）、当前工作区文件（`current`）、新发行清单与字节（`target`）。每个文件必须且只能落在以下一类。

| 分类 | 判定 | 默认动作（`decision`） | 可自动应用 | 是否阻断 |
|---|---|---|---|---|
| `UPSTREAM_UNCHANGED` | `current == baseline` 且 `target != baseline` | `APPLIED` | 是（唯一可自动写入的分类） | 否 |
| `ALREADY_CURRENT` | `current == target` | `NOT_APPLICABLE`（无需写入） | — | 否 |
| `LOCAL_MODIFICATION` | `current != baseline` 且 `target == baseline` | `KEPT_LOCAL` | 否 | 否（报告） |
| `THREE_WAY_CONFLICT` | `current != baseline` 且 `target != baseline` 且 `target != current` | `CONFLICT_REPORTED` | 否 | 是（`STOPPED_CONFLICT`，`HD-10`） |
| `PROJECT_OWNED` | 命中项目自有路径规则（§5） | `KEPT_LOCAL` | 否，永不 | 否 |
| `UNKNOWN_FILE` | 当前存在但不在旧清单、且不属于项目自有分类 | `KEPT_LOCAL` | 否 | 否（报告；不得当作可覆盖） |
| `UPSTREAM_REMOVAL` | 在旧清单、不在新清单，且项目内仍存在 | `KEPT_LOCAL` + 提示清理 | 否 | 是（需 `HD-09` 才可删除） |
| `UPSTREAM_RENAME` | 旧清单路径在新清单缺失、但存在同内容 / 同职责的新路径 | `KEPT_LOCAL` + 差异报告 | 否 | 是（`HD-09`） |
| `UNSAFE_PACKAGE` | 包或清单层面的完整性失败（§3） | `BLOCKED` | 否 | 是（整次停止） |
| `INVALID_BASELINE` | 旧基线缺失、不可读或版本不支持 | `BLOCKED`（或只读诊断） | 否 | 是（整次停止 / 手工迁移） |

补充规则：

1. 只有 `UPSTREAM_UNCHANGED` 允许 `APPLIED`；`result=APPLIED` 时不得存在 `THREE_WAY_CONFLICT` / `UPSTREAM_REMOVAL` / `UPSTREAM_RENAME` / `UNSAFE_PACKAGE` / `INVALID_BASELINE`（schema 强制）。
2. `UNKNOWN_FILE` 与 `PROJECT_OWNED` 可以与 `APPLIED` 共存，但只能 `KEPT_LOCAL`。
3. 分类必须**逐文件**记录 `baseline_sha256` / `current_sha256` / `target_sha256`（不适用时 null），不得只给计数。
4. `counts` 各项之和必须等于 `files` 长度；不等即报告损坏。
5. 上游删除 / 重命名默认**不删除、不改名**；清理是独立的人工决定，不是更新的副作用。
6. 分类不依据“哈希不同”单独下结论：必须同时知道旧基线、当前内容与新目标三方关系。

## 5. 项目自有文件保护（永不被更新覆盖）

以下路径 / 类别一律 `PROJECT_OWNED`，即使旧清单声称属于上游：

- 项目配置：`docs/PROJECT.md`、`docs/PLAN.md`、旧根 `ORCHESTRATION-PROJECT.md`、`PLAN.md`、`DESIGN.md`、`docs/DESIGN.md`。
- 项目业务源码与测试：项目 `src/`、`tests/` 等（按项目配置的路径范围；无法判定时按 `UNKNOWN_FILE` 处理）。
- 项目私有文档、扩展、脚本与 CI 配置：`.github/`、`.local/`、`.orchestration/`、`.worktrees/`、项目自有 `docs/**`。
- 未登记文件：不在旧清单、也不在项目自有声明中的文件。

硬规则：

- 更新只允许写“旧清单中存在、且分类为 `UPSTREAM_UNCHANGED`”的路径。
- 项目自有文件在更新前后必须**逐字节不变**；报告必须能证明（哈希对比）。
- 任何“为了让更新成功而删项目文件”的路径都不存在；冲突只能报告。

## 6. 未提交改动处理

1. 开始前必须记录 `git status --short` 与未跟踪文件清单；写入 `workspace.dirty_paths`。
2. **不得**自动 `stash`、`checkout --`、`clean`、丢弃或覆盖未提交改动。
3. 优先在任务专属分支 / Worktree 中应用（`workspace.action=ISOLATED_BRANCH`）；原工作区保持不动。
4. 无法安全隔离（如无 Git、Worktree 创建失败、脏文件位于必须写入的同一路径）→ `STOPPED_DIRTY_WORKTREE`，交 `HD-12`。
5. 脏文件内容不得写入报告或日志（只记录路径与哈希）；避免把用户数据带进公开评论。

## 7. 事务、备份与回滚

### 7.1 备份

- 应用前必须备份：版本、head SHA、工作区状态、旧清单摘要、将被改写的文件（至少 `UPSTREAM_UNCHANGED` 集合）。
- 备份位置必须在项目外或 Git 忽略目录；报告写可定位 `backup.location` 与时间。
- `backup.verified=true` 只在重新读取并核对文件数量 / 哈希后成立；未核对写 `false`。

### 7.2 应用

- 在项目外暂存目录完成下载、解包、校验与分类，先产出计划 / diff，再一次性应用无冲突变更集。
- 应用期间任何校验失败：不保留“半套更新”；能原子替换则替换，不能则从备份恢复并**验证恢复结果**。
- 更新应用与发布 / 合并分离：更新成功不等于已审核、已合并或项目迁移完成。

### 7.3 回滚

- 回滚必须验证：恢复后的文件哈希、`head_sha`、工作区状态与更新前一致（或明确记录差异）。
- 回滚成功 → `rollback.result=SUCCEEDED`，`result=ROLLED_BACK`。
- 回滚失败或回滚后校验仍失败 → `rollback.result=FAILED`，`result=BLOCKED`（schema 强制），并触发 `HD-11`。
- 回滚失败不得继续后续更新；保留现场供排查，不得再自动重试。

## 8. 失败关闭矩阵

| 条件 | `result` | 是否写入项目 | 后续 |
|---|---|---|---|
| 来源非官方 / 资产不可信 / 校验失败 | `STOPPED_UNSAFE_PACKAGE` | 否 | 报告 + 修正来源 |
| 旧基线缺失 / 不支持 | `STOPPED_INVALID_BASELINE` | 否（只读诊断） | 手工迁移 `HD-08` |
| 双边冲突 / 上游删除 / 重命名 | `STOPPED_CONFLICT` | 无冲突部分也只能等决定 | 逐文件 `HD-09` / `HD-10` |
| 脏工作区无法隔离 | `STOPPED_DIRTY_WORKTREE` | 否 | `HD-12` |
| 目标版本低于已安装 | `BLOCKED` | 否 | 独立决定后才允许 |
| 回滚失败 | `BLOCKED` | 视回滚结果 | `HD-11` |
| 网络不可用 / 无法取得 Release | `BLOCKED` + `NOT_RUN` | 否 | 保留旧版本 |
| 计划模式且全部检查通过 | `PLANNED` | 否 | 等待审查 / 应用授权 |
| 无冲突变更集已应用且后置检查通过 | `APPLIED` | 是 | 走 PR / 审查 / 合并门 |

`NOT_RUN` 与 `UNKNOWN` 一律不得解释为成功；报告必须写明未执行原因。

## 9. 报告与证据

每次运行（`PLAN` 或 `APPLY`）都必须产出符合 `update-report.schema.json` 的报告，至少包含：

- 旧 / 新版本、release tag（保留原始 `vX.Y` 或 `vX.Y.Z`）、资产名与 SHA-256、清单 SHA-256。
- 逐文件分类与计数、冲突 / 删除 / 未知文件清单。
- 工作区状态、备份位置、回滚结果。
- 实际命令与退出码、`head_sha_before` / `head_sha_after`。
- 后置检查：规则包结构校验、单测、语法检查、打包自检、漂移诊断、项目自己的验证命令；未执行的写 `NOT_RUN`。
- 人工决策（`HD-*`）与当前状态。

报告不得包含凭据、个人绝对路径、私有文件内容或来源正文。

## 10. 旧版本与无基线迁移

- 旧项目没有 `MANIFEST.json`，或清单 schema 不受支持：**只读诊断**（结构、版本、漂移报告），输出手工迁移步骤；不自动覆盖任何文件。
- 旧 Format（如 1.3 / 1.4）的兼容矩阵与升级说明由 #52 的升级指南维护；本契约只要求：不支持的旧格式失败关闭，不得猜读。
- 迁移不得删除项目自有内容；迁移后必须重新运行项目验证命令并记录退出码。

## 11. 人工决策点（引用目录）

`HD-07` 预发布通道、`HD-08` 无基线 / 旧 Format、`HD-09` 上游删除 / 重命名、`HD-10` 双边冲突、`HD-11` 回滚失败、`HD-12` 脏工作区无法隔离、`HD-15` 新增外部工具链、`HD-16` 降级 / 平级重装。问题、选项与决定人见 [entrypoints-and-decisions.md](entrypoints-and-decisions.md) §5。

## 12. 未验证事项（不得写成已验证）

- 官方 Release 的真实可访问性、资产命名与 tag 序列（本阶段未下载任何真实包）。
- GitHub API / `gh` 在 CI 与受限网络下的行为、速率限制。
- 真实项目的符号链接、大小写敏感文件系统、Windows 路径差异。
- 打包可复现性与 `MANIFEST.json` 生成规则由现有 `scripts/build_release.py` / `scripts/check_drift.py` 决定；本契约只冻结消费侧字段，不修改生成器。
