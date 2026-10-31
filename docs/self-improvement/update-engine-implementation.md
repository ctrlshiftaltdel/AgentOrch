# 更新引擎实现说明（#51，v1）

> **实现记录，不是冻结契约。** 逐文件分类、失败关闭、信任来源与回滚语义的唯一权威仍是
> [`update-safety.md`](update-safety.md) 与 `docs/contracts/schemas/update-*.schema.json`；
> 威胁与剩余风险见 [`threat-model.md`](threat-model.md)。本文件只说明 #51 实际实现了什么、
> 如何运行、有哪些限制与未验证项，不新增分类、不改写冻结语义。
>
> 状态：核心引擎与合成测试已实现；自然语言入口、发行包 `REQUIRED` / `build_release.py` / CI /
> README 计数与包内测试由 **#52** 接入；真实 GitHub Release 获取为 **`NOT_RUN`**。

## 1. 能力与范围

实现位置：`scripts/update_agentorch/`（纯 Python 标准库，无第三方运行依赖）。

| 能力 | 实现 |
|---|---|
| 官方来源 | 常量 `OFFICIAL_REPOSITORY = 'ctrlshiftaltdel/AgentOrch'`，不可由项目配置覆盖；只读 `https://api.github.com/repos/ctrlshiftaltdel/AgentOrch/releases/latest`，只接受 stable Release 的单一 `.zip` 资产 |
| 版本 | 接受 `vX.Y` / `vX.Y.Z`；比较时缺失 patch 按 0 归一；报告保留原始 tag |
| 包校验 | 按冻结顺序执行 10 项检查，任一项失败即 `UNSAFE_PACKAGE`，项目零写入 |
| 旧基线 | 读取项目 `MANIFEST.json`；缺失 / 不可解析 / schema 不受支持 → `STOPPED_INVALID_BASELINE`，不联网、不写文件 |
| 三方分类 | 旧清单 / 当前工作区 / 新清单逐文件分类，输出 sha256 与决策；只有 `UPSTREAM_UNCHANGED` 可 `APPLIED` |
| 隔离与备份 | 下载 / 解包 / 分类在项目外临时目录；APPLY 前备份；失败回滚并验证；回滚失败 `BLOCKED` |
| 报告 | 每次运行（PLAN / APPLY）产出符合 `update-report.schema.json` 的对象，并由引擎内置校验器自检 |
| 不做什么 | 不自动合并、不写主分支、不删除 / 重命名上游移除文件、不 stash / clean 未提交改动、不验签 |

## 2. 运行流程

```text
inspect workspace (git head/branch/dirty)
  └─ APPLY 且脏工作区 → 在 .worktrees/update-<report_id> 创建独立分支 + worktree
read baseline MANIFEST.json（失败：STOPPED_INVALID_BASELINE，零网络零写入）
fetch / select / download official release asset
verify package（10 项，项目外）
target version <= installed version → BLOCKED + HD-16
解包到临时目录 → 逐文件三方分类
  └─ 冲突 / 删除 / 重命名 → STOPPED_CONFLICT（零写入）
  └─ PLAN → PLANNED（零写入）
APPLY：
  backup（版本、head、工作区、旧清单摘要、将改文件）→ 校验备份
  写 UPSTREAM_UNCHANGED + 包 MANIFEST.json（原子替换）
  写后哈希验证
    └─ 任一失败 → 从备份恢复 → 验证恢复 → ROLLED_BACK / BLOCKED
  APPLIED
```

说明：

- **下载 / 解包 / 分类在项目外**：zip 在内存中校验，解包到系统临时目录，运行结束即清理；
  项目内只发生经过校验的 APPLY 写入。
- **无基线只读诊断**：`STOPPED_INVALID_BASELINE` 的 `baseline.notes` 写入只读文件清点与手工迁移
  提示（`HD-08`），不读取哈希差异作为覆盖许可。
- **APPLY 只写两类路径**：旧清单中存在且 `UPSTREAM_UNCHANGED` 的路径，以及包自身的
  `MANIFEST.json`（作为下一次更新的基线记账；同样经过包校验、备份与写后哈希验证）。
  项目自有 / 未知文件 / 删除 / 重命名 / 冲突永不写入。
- **不提交、不合并**：写入后不 `git add` / `commit` / `merge`；`head_sha_after` 因此通常等于
  `head_sha_before`（变更留在工作区或隔离 worktree 供审查）。
- **隔离目录本身不是更新写入**：脏工作区隔离会在 Git 忽略的 `.worktrees/` 下创建
  `update-<report_id>`（独立分支 + worktree），原工作区文件逐字节不变；即使随后因冲突 /
  校验失败停止，也不会修改或删除原工作区文件。
- **未提交改动**：不执行 `git stash` / `checkout --` / `clean`。有 Git 时优先隔离到
  `.worktrees/update-<report_id>`（`workspace.action=ISOLATED_BRANCH`，原工作区保持不动）；
  无法隔离时 `STOPPED_DIRTY_WORKTREE`。无 Git 项目没有“未提交”概念，引擎照常以备份 + 回滚
  保护，但无法证明工作区干净（`baseline.head_sha=null`，见 §11）。

## 3. 命令与退出码

```bash
# 计划（只读；不写项目文件）
python3 -m scripts.update_agentorch --project <项目根> --mode PLAN

# 应用无冲突变更集（备份 + 回滚保护）
python3 -m scripts.update_agentorch --project <项目根> --mode APPLY

# edition 默认从 <项目根>/docs/PROJECT.md 读取；也可显式指定
python3 -m scripts.update_agentorch --project <项目根> --mode PLAN --edition github
```

`scripts/` 在 `PYTHONPATH` 上时也可用 `python3 -m update_agentorch ...`。CLI **没有** 本地 zip /
任意 URL / 其他仓库参数：信任边界不能由命令行放宽。测试通过注入 `ReleaseSource`（进程内合成
包）驱动同一引擎，不联网。

| 退出码 | 含义 |
|---|---|
| `0` | `PLANNED` / `APPLIED` |
| `2` | `STOPPED_CONFLICT` / `STOPPED_UNSAFE_PACKAGE` / `STOPPED_INVALID_BASELINE` / `STOPPED_DIRTY_WORKTREE` |
| `3` | `BLOCKED` |
| `4` | `ROLLED_BACK` |
| `1` | CLI 配置 / 参数错误，或未能产出报告 |
| `5` | 报告未通过内置契约校验（引擎 bug，报告仍会打印） |

`python3 -m scripts.update_agentorch --help` 已真实执行，退出码 `0`。

## 4. 包校验（冻结顺序）

| # | check | 本实现做什么 | 失败归类 |
|---|---|---|---|
| 1 | `SOURCE_ALLOWLIST` | 仓库常量、release URL 主机与路径、tag 形式、单一 `.zip` 资产名、下载主机（GitHub / `*.githubusercontent.com`） | `UNSAFE_PACKAGE` |
| 2 | `RELEASE_CHANNEL` | `prerelease` / `draft` 拒绝 | `UNSAFE_PACKAGE` |
| 3 | `ASSET_SHA256` | 下载字节 SHA-256 与 Release 记录的 `sha256:<hex>` 比对；记录缺失即失败 | `UNSAFE_PACKAGE` |
| 4 | `MANIFEST_SCHEMA` | 唯一 `<top>/MANIFEST.json`、可解析、schema_version、字段、排序、路径、category 与路径一致 | `UNSAFE_PACKAGE` |
| 5 | `MANIFEST_SHA256` | 清单字节两次读取一致；若 Release 记录提供清单摘要则必须一致 | `UNSAFE_PACKAGE` |
| 6 | `MEMBER_PATHS` | 绝对路径 / 盘符 / 反斜杠 / 空段 / `.` `..` / 成员名大小写重复由 layout 处理；同时执行成员数、单文件、总解压大小与压缩比上限 | `UNSAFE_PACKAGE` |
| 7 | `SYMLINKS` | 拒绝符号链接、设备 / FIFO / 套接字等非普通成员 | `UNSAFE_PACKAGE` |
| 8 | `FILE_SET` | 清单条目集合 == 归档成员集合（允许清单自身不列出），并逐成员核对 `sha256` 与 `size` | `UNSAFE_PACKAGE` |
| 9 | `EXPECTED_LAYOUT` | 单一顶层目录、无嵌套归档、无可执行位、无大小写碰撞、无“同一路径既是文件又是目录” | `UNSAFE_PACKAGE` |
| 10 | `VERSION_MATCH` | tag 的 major.minor == 清单 `format_version`；基线存在时 `suite` 必须一致 | `UNSAFE_PACKAGE` |

补充（冻结规则未变，属实现细节）：

- 失败后未执行的检查写 `NOT_RUN`，不折算通过。
- `FILE_SET` 除集合一致外还核对每个成员的哈希 / 大小；否则“清单校验通过”仍可能写入与清单
  不符的字节，分类结果会失真。
- `MANIFEST_SHA256` 在 GitHub 不提供独立清单摘要时，检查的是“清单字节在读取 — 使用之间保持
  一致”，并在 `detail` 明确写出“没有独立的 release 侧摘要”。可选的 release 记录
  `manifest_sha256` 一旦提供则必须匹配。这是当前能取得的最强校验，**不是签名校验**（§11）。
- 本实现的资源上限（T-15 要求“必须存在”，数值由实现任务定义）：

| 常量 | 值 |
|---|---|
| `max_members` | 2000 |
| `max_total_uncompressed` | 64 MiB |
| `max_member_uncompressed` | 32 MiB |
| `max_compression_ratio` | 1000 |
| `max_manifest_bytes` | 1 MiB |
| `max_path_length` | 512 |

## 5. 三方分类与默认动作

分类与决策严格使用 `update-safety.md` §4 的枚举：

| 分类 | 本实现的判定 | 决策 |
|---|---|---|
| `UPSTREAM_UNCHANGED` | `current == baseline` 且 `target != baseline`；或旧清单无此路径且本地也不存在（新上游文件） | `APPLIED`（PLAN 为 `DEFERRED`） |
| `ALREADY_CURRENT` | `current == target`；或本地与上游都已不存在 | `NOT_APPLICABLE` |
| `LOCAL_MODIFICATION` | `current != baseline` 且 `target == baseline`；包括本地删除而上游未变 | `KEPT_LOCAL` |
| `THREE_WAY_CONFLICT` | 双边修改；或本地删除但上游已改 | `CONFLICT_REPORTED` → `STOPPED_CONFLICT`（`HD-10`） |
| `PROJECT_OWNED` | 命中项目自有路径规则（见下） | `KEPT_LOCAL`，永不写入 |
| `UNKNOWN_FILE` | 本地存在、不在旧清单、不属于项目自有；或上游新增路径与本地未知文件同名 | `KEPT_LOCAL` |
| `UPSTREAM_REMOVAL` | 旧清单有、新清单无、本地仍存在 | `KEPT_LOCAL` → `STOPPED_CONFLICT`（`HD-09`） |
| `UPSTREAM_RENAME` | 旧路径在新清单缺失且存在同字节的新路径；两侧都标 `UPSTREAM_RENAME` | `KEPT_LOCAL` → `STOPPED_CONFLICT`（`HD-09`） |
| `UNSAFE_PACKAGE` / `INVALID_BASELINE` | 由 §4 / 基线检查决定整次运行结果 | `BLOCKED` / `STOPPED_*` |

项目自有路径（永不被覆盖，即使旧清单声称属于上游）：

- 精确路径：`docs/PROJECT.md`、`docs/PLAN.md`、`ORCHESTRATION-PROJECT.md`、`PLAN.md`、
  `DESIGN.md`、`docs/DESIGN.md`
- 目录前缀：`.github/`、`.local/`、`.orchestration/`、`.worktrees/`、`.workbuddy/`、
  `docs/`、`src/`、`release/`

`scripts/`、`tests/`、`docs/orchestration.md`、`templates/`、`editions/` **不是**整体项目自有：旧清单中的文件
按上述规则分类，不在旧清单中的同名路径按 `UNKNOWN_FILE` 保留并报告（符合 §5“无法判定时按
`UNKNOWN_FILE` 处理”）。

其他实现细节：

- 上游重命名只按**字节相同**识别；仅“职责相同”的改名会退化为 `UPSTREAM_REMOVAL` + 新文件，
  仍然阻断（安全失败），不会静默删除。
- 计数 `counts` 之和恒等于 `files` 长度；`MANIFEST.json` 作为一条记账条目参与分类与计数。

## 6. 工作区、备份与回滚

- **脏工作区**：`git status --porcelain=v1 -z --untracked-files=all` 的路径写入
  `workspace.dirty_paths`（只记路径，不记内容）。APPLY 时自动创建
  `.worktrees/update-<report_id>` 独立分支；原工作区（含脏文件）逐字节不动。无法创建时
  `STOPPED_DIRTY_WORKTREE`（`HD-12`）。
- **备份**：位置为项目外系统临时目录 `agentorch-update-backup-*`，包含
  `backup.json`（installed / target 版本、head SHA、dirty paths、旧清单 SHA-256、逐文件
  present / sha256 / size）与被改文件原始字节副本。备份会被重新读取并核对后才标记
  `verified=true`；备份失败在任何写入之前 `BLOCKED`。
- **应用**：逐个文件写同目录临时文件后 `os.replace`；父路径含符号链接或解析后逃出项目根即
  拒绝。写后逐文件哈希验证。
- **回滚**：从备份恢复每个路径（原本不存在的文件删除），清理本次新建的空目录，再逐路径
  核对 presence / sha256。成功 → `ROLLED_BACK`；恢复后校验失败 → `BLOCKED` +
  `rollback.result=FAILED` + `HD-11`，不再自动重试。

## 7. 报告

报告字段与枚举以 `update-report.schema.json` 为准；内置校验器同时实现标准 JSON Schema 难以
表达的语义（计数求和、`APPLIED` 零冲突、回滚失败必须 `BLOCKED`、`NOT_RUN`/`FAIL` 不得出现在
`VERIFIED` 校验中、项目自有 / 未知 / 删除 / 重命名永不为 `APPLIED` 等）。

- 冲突、删除、重命名、项目自有与未知文件逐条出现在 `classification.files`（含
  `baseline_sha256` / `current_sha256` / `target_sha256` 与 `notes`），汇总在
  `classification.counts`；schema 没有单独的“冲突列表”字段，分类即冲突证据。
- `baseline.installed_version` 取已安装清单的 `format_version`（当前 `MANIFEST.json` 不记录
  semver）；`target.version` 是 tag 归一化后的 `x.y.z`，`target.release_tag` 保留原始 tag。
- `commands` 记录真实执行的操作与结果：`git rev-parse` / `git status` /
  `git worktree add`，以及 `github:releases/latest` 与 `github:release-asset <name>`
  （退出码 0 / 1）。命令字符串不含项目绝对路径、凭据或令牌。
- `post_apply_checks`：APPLIED 时记录 `applied_file_hashes=PASS` 与
  “project verification (delegated to #52)”=`NOT_RUN`；未执行项不折算通过。
- `NOT_RUN` / `UNKNOWN` 永远是未验证：网络不可用 → `BLOCKED` + 全部检查 `NOT_RUN`，保留旧版本。

示例命令（真实执行，退出码 `0`）：

```bash
python3 -m unittest tests.update_agentorch
```

## 8. 威胁覆盖（#51 负责项）

| 威胁 | 实现 | 负向测试 |
|---|---|---|
| T-04 路径穿越 | `MEMBER_PATHS` + 解包 containment 二次校验 + 项目外 staging | `test_path_traversal_member_writes_nothing_outside`、`test_absolute_and_drive_and_backslash_members_are_unsafe`、`test_flat_archive_without_single_top_directory_is_unsafe` |
| T-05 坏 MANIFEST | 冻结 schema 校验、文件集合、逐成员哈希、排序 / 自列 / 未知字段 | `test_bad_manifest_writes_nothing`、`test_manifest_*`、`test_file_set_mismatch_is_unsafe`、`test_member_hash_mismatch_is_unsafe` |
| T-06 冲突覆盖 / 项目自有 | 三方分类；`PROJECT_OWNED` 优先于基线；`APPLIED` 零冲突 | `test_two_sided_conflict_stops_with_zero_writes`、`test_project_owned_file_is_never_overwritten_even_if_baseline_lists_it`、`test_clean_upgrade_applies_and_keeps_project_files_byte_identical` |
| T-07 半更新 | 先校验后写入、备份、写后验证、失败恢复并验证 | `test_half_update_rolls_back_and_verifies`、`test_backup_failure_stops_before_any_write` |
| T-08 回滚失败 | 恢复后逐路径校验；失败 `BLOCKED` + `HD-11` | `test_rollback_failure_is_blocked` |
| T-14 供应链 | 仓库常量、stable 通道、tag 形式、资产名、asset SHA-256、版本单调性 | `test_non_official_source_is_rejected`、`test_rejects_prerelease_and_draft`、`test_downgrade_is_blocked`、`test_equal_version_is_blocked_as_a_reinstall`、`test_two_and_three_part_tags_apply` |
| T-15 解包攻击 | 符号链接 / 设备文件拒绝、成员数 / 大小 / 压缩比 / 清单大小上限 | `test_symlink_member_is_unsafe`、`test_member_count_limit_is_enforced`、`test_uncompressed_size_limit_is_enforced`、`test_compression_ratio_limit_is_enforced` |
| T-18 静默删除 | 删除 / 重命名默认 `KEPT_LOCAL` + `HD-09`，零删除 | `test_upstream_removal_blocks_and_keeps_the_file`、`test_upstream_rename_is_reported_and_not_applied` |

T-01 / T-02 / T-03 / T-09 / T-10 / T-11 属 #49，T-12 / T-13 属 #48，本任务不声称覆盖。

## 9. 测试方式

- `tests/update_agentorch/`：合成项目目录 + 内存合成 zip / `MANIFEST.json`；
  逐字节快照用于证明“零写入”和“项目自有文件不变”。
- 不访问真实网络：网络客户端只由 `sources.parse_release_payload` / `select_asset` 的纯函数
  测试覆盖；引擎测试注入 `FakeReleaseSource`，另有测试在 `socket.socket` 被禁止时仍完成
  APPLY，证明引擎不依赖联网。
- 合成 Release 元数据与真实 GitHub 返回同形（`tag_name` / `prerelease` / `draft` /
  `assets[].digest` / `browser_download_url`）。
- 回滚 / 半更新失败通过测试专用的 `FaultPlan` 注入，不进入 CLI。

真实执行记录（Linux，Python 3.13.5，`github` edition，分支
`task/51-safe-update-engine`）：

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python3 scripts/validate_kit.py` | 0 | `Kit validation passed` |
| `python3 -m unittest discover -s tests` | 0 | `Ran 511 tests` / `OK` |
| `python3 -m py_compile scripts/orchestrator.py scripts/validate_kit.py scripts/update_agentorch/*.py` | 0 | 无输出 |
| `python3 scripts/build_release.py` | 0 | 打包自检通过；未修改 `REQUIRED` / 发行清单 |
| `python3 -m unittest tests.update_agentorch` | 0 | `Ran 116 tests` / `OK` |
| `python3 -m scripts.update_agentorch --help` | 0 | 打印用法 |

## 10. 已知限制与未验证项

**`UNV-2` / 真实获取保持 `NOT_RUN`：**

- 真实 GitHub API 可访问性、限流、`/releases/latest` 行为、官方资产命名与 tag 序列均未实测。
- 官方 Release 是否提供资产 `digest` 未实测；缺失时引擎按 `UNSAFE_PACKAGE` 失败关闭。
- 未下载、未验证任何真实官方发行包。

**信任与完整性：**

- **不验签**：没有签名 / 证书校验；只做来源 allowlist、asset SHA-256、清单结构与逐文件哈希。
  因此不能防住“GitHub 账户或 Release 权限被攻破”这一类供应链风险（T-14 剩余风险）。
- 没有独立清单摘要时，`MANIFEST_SHA256` 只证明清单字节稳定，不证明发布方意图。

**平台：**

- 仅在 Linux 上测试；大小写不敏感文件系统、Windows 路径 / 权限、macOS 行为未实测。
- 文本哈希按 LF 归一（与 `scripts/check_drift.py` 相同规则），CRLF 检出的一致性未在真实
  Windows 工作区复现。

**工作区：**

- 无 Git 项目无法证明工作区干净，也无隔离分支；只依赖备份 + 回滚，`baseline.head_sha=null`。
- 非原子文件系统下跨文件原子性无法绝对保证（T-07 剩余风险）；依赖备份与恢复后校验。
- 备份介质本身可能损坏；引擎只验证备份可读且哈希一致。

**分类：**

- 重命名只识别字节完全相同的情况；仅职责相同的改名退化为“删除 + 新文件”，仍然阻断。
- 项目自有 denylist 是固定列表 / 前缀；未覆盖的下游扩展会成为 `UNKNOWN_FILE`（保留并报告），
  不会被覆盖。

**已接入（#52）：**

- 发行包 `REQUIRED`、`build_release.py` 自检（入口可导入、注册表示例可校验）、CI 与
  `docs/README.md` 计数已覆盖 `scripts/update_agentorch/`、`scripts/self_improvement/`、
  `templates/self-improvement/` 与随包测试；自然语言入口为 `scripts/update_agentorch/entry.py`。
- 项目自己的验证命令仍不由引擎执行：`post_apply_checks` 对应项为 `NOT_RUN`。
- T-07 / T-08 / T-15 的完整故障演练归 #53；本任务只覆盖合成负向场景。

## 11. 与 #52 / #53 的接口

- **#52**：调用 `update_agentorch.run_update(...)` 或 CLI，读取报告 JSON；把引擎文件加入
  `REQUIRED` / 打包 / CI；`docs/upgrade.md` 补升级与回退说明。
- **#53**：可直接复用 `tests/update_agentorch/support.py` 的合成项目 / 包 / Release 构造器，
  不需要真实网络、凭据或生产项目。
