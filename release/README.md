# 发布标准（Agent-Orchestration 通用分发版本）

本文件定义本套件"通用分发版本"的构建与发布标准。以后每次建 release，都按本文件执行；流程与脚本以本目录为准，产物本身不入库。

## 1. 用途与命名规范

- 用途：把套件打包成可被任意项目复用的通用分发版本；包内只保留与具体项目无关的可复用产出。
- **两种命名模式，互不组合**：
  - **正式 Release 资产**：`uv run python scripts/build_release.py --release-version 1.6` 产出 `release/AgentOrch-1.6.zip`，zip 顶层目录与资产 stem 一致，即 `AgentOrch-1.6/`。对应 Git tag 为 `v1.6`、资产名为 `AgentOrch-1.6.zip`。参数写 `1.6`，**不要写 `v1.6`**：`v1.6` 是 tag 名而不是资产名，带 `v` 前缀或预发布后缀会被构建直接拒绝。补丁发行把版本号写全：`--release-version 1.6.1` 产出 `AgentOrch-1.6.1.zip` / 顶层目录 `AgentOrch-1.6.1/`，tag 为 `v1.6.1`；补丁号只改变资产名与顶层目录，不改变 Format。
  - **日期构建（默认，保持不变）**：`uv run python scripts/build_release.py`（今天）或 `--date YYYYMMDD` 产出 `release/agentorch-{YYYYMMDD}.zip`，顶层目录 `agentorch-{YYYYMMDD}/`。CI 与 `docs/upgrade.md` 的基线重建继续用这个模式。
  - `--date` 与 `--release-version` **互斥**：同时给出即报错退出（`argparse` 退出码 2），不存在"谁覆盖谁"的隐含优先级；不带任何命名参数时仍按今天日期命名。
- **发布名只决定资产名与顶层目录**：包内文件集合、`MANIFEST.json` schema 与 `format_version` 在两种模式下完全相同。**Release 版本号不是 Format 版本**：`Release 1.6` 发布时与 `Format version` 1.6 数值相同；当前 `Release 1.7.0` 与 `Format 1.7` 的 major.minor 相同、编号仍各自独立。两者仍是两条序列（发行号 vs 格式契约），`MANIFEST.json` 的 `format_version` 由 `VERSION.md` 的 `Format version:` 一行生成；两者的区别与当前发行记录见第 5 节。
- 产物不纳入 git：`.gitignore` 忽略 `release/*.zip`；仓库只保留流程文档、构建脚本与本标准；历史版本记录由 GitHub Releases 承担。`release/README.md` 不入忽略范围。**已发布的历史资产（如 `agentorch-20261023.zip`）不删除、不覆盖、不改写**；新 Release 用新的资产名。
- 产物可随时重建：运行 `uv run python scripts/build_release.py`（默认输出到 `release/`），或按上面的正式 Release 命令指定 `--release-version`。构建是**可复现**的：同一仓库状态下（同一命名模式与同一 top dir），包内文件集合、各文件内容与 zip 的校验和均一致（归档时间戳使用固定值），因此 zip 的校验和可被任何人重建复核。可复现性不依赖检出行尾：构建时文本文件按 LF 归一后打包、二进制文件原样打包（仓库以 `.gitattributes` 统一 LF），故任何检出（含 Windows 上 `core.autocrlf=true`）重建都得到同一校验和。

## 2. 包内容规范

- 只含通用可复用产出；不得包含本仓库特有的演进决策、任务记录或个人环境信息。`docs/` 下**只有**核心契约 `docs/orchestration.md` 随包分发，其余 `docs/` 路径一律拒绝（`build_release.py` 的 `PACKAGE_DOC_PATHS` 白名单）。
- 包内文件集合 == `scripts/validate_kit.py` 中的 `REQUIRED` 列表 **+ 构建时生成的 `MANIFEST.json`**（数量与名称都一致，即 `len(REQUIRED) + 1` 个文件）。`REQUIRED` 是分发内容的唯一来源，构建脚本直接解析它，不再维护第二份清单；`MANIFEST.json` 是唯一不来自 `REQUIRED` 的成员。
- **`MANIFEST.json`（机器可读清单）**：由 `scripts/check_drift.py` 生成，构建脚本导入该模块而不另写一份规则，因此分类只有一处实现。字段固定为：
  - 顶层 `schema_version`（清单格式版本）、`suite`（套件名，取自 `VERSION.md`）、`format_version`（套件格式版本，当前 `1.7`）、`files[]`；
  - 条目 `path`、`category`、`sha256`、`size`（`sha256` 与 `size` 按包内实际字节计算，文本已按 LF 归一）；
  - `category` 只允许四种上游分类：`editions/` 前缀 → `edition`，`templates/` 前缀 → `template`，`docs/PROJECT.md` / `PLAN.md`（旧根位置）/ `docs/PLAN.md`（1.4 起）/ `DESIGN.md` → `project-profile`，其余 → `core`；
  - 清单**不列出自身**（无法嵌入自己的哈希）、**不含时间戳**、条目按路径排序，因此同一仓库状态两次构建得到逐字节相同的清单。
- **`check_drift.py` 的只读边界**：该脚本随包发布，供下游项目做漂移诊断。它只读取（`read_bytes` / `read_text`）、列目录，**从不创建、重命名、删除或写入项目根下的任何文件**；诊断只报告差异与建议，是否对齐由项目所有者决定。
- 发布时 `docs/PROJECT.md` 自动替换为待填模板 `templates/project.md`，即分发包总是随附 FILL_IN 模板，首次使用时填写。
- **自我完善与安全更新资产**：`scripts/self_improvement/`（来源注册表、采集、Lesson、规划与闭环）、`scripts/update_agentorch/`（#51 安全更新引擎与 #52「更新 AgentOrch」自然语言入口 `entry.py`）、`templates/self-improvement/`（提案 / 任务包 / 闭环报告模板、`sources.example.json` 注册表示例与包内操作说明 `README.md`）全部进入 `REQUIRED`，随包发布。包内说明与测试只读模板 / 脚本路径，不读 `docs/`。
- **仓库专属测试（不随包）**：`tests/update_agentorch/test_report.py` 读取冻结 fixture `docs/contracts/fixtures/`，因此保留为 repository-only，不加入 `REQUIRED`；包内仍通过随包发布的 `tests/update_agentorch/`（引擎、分类、依赖、入口合成升级）与 `tests/self_improvement/` 测试已发布入口和引擎。`tests/self_improvement/test_acceptance_report.py` 反过来**随包发布**：它只在存在 `docs/` 的仓库检出时校验场景目录（`skipUnless`，与 `tests/test_run_delivery_scenarios.py` 的既有做法一致），包内的其余断言只读随包发布的 `scripts/` 与 `templates/`，因此发行包里也能真实运行最终场景集与指标口径。
- 明确排除（不得出现在包内）：
  - `docs/`：本仓库自有章程与验收记录；
  - `release/`：发布目录本身（含本文件与历史分发包）；
  - `scripts/build_release.py`：构建工具，仅存在于仓库；
  - `.orchestration/`、`.worktrees/`、`.workbuddy/`：机器本地状态；
  - 历史分发包；
  - 个人路径与用户名、本仓库任务 ID、本仓库专有文档名、凭据痕迹等非必要信息。

## 3. 自检清单（发布前必须全过）

自检内置在 `scripts/build_release.py`，随构建自动执行，任何一项失败即非零退出并逐条列出原因；**没有跳过自检的开关**。

自检 a–l 与命名模式无关：脚本把本次命名决定的顶层目录（`AgentOrch-1.6/` 或 `agentorch-YYYYMMDD/`）传给全部自检，`MANIFEST.json` 的路径解析、包内 `validate_kit.py` 与 `unittest` 都在该目录下进行。命名模式不改变自检项、不删除任何检查，也不降低强度。

- a. 包内容文件集合 == `REQUIRED` + `MANIFEST.json`（数量与名称一致）；解压后 `MANIFEST.json` 必须能解析每条 `path`、`sha256` 与 `size` 与实际文件一致、`category` 只用合法分类，且清单列出的路径集合与 `REQUIRED` 完全一致。
- b. 解压到临时目录后，`scripts/validate_kit.py` 退出码为 0。
- c. 解压目录内 `scripts/validate_kit.py --project-ready` **预期失败**（退出码 1 且输出含 `FILL_IN`）。这是正确行为而非缺陷：发布的是待填模板；若它"通过"了反而要报错。
- d. 解压目录内用构建脚本所在的解释器（`sys.executable`，不是裸 `python`）执行 `python -m unittest discover -s tests`，退出码为 0。
- e. 解压目录内 `scripts/orchestrator.py init` 与 `scripts/orchestrator.py task list` 冒烟退出码为 0（`init` 会在临时目录创建 `.orchestration/`，属预期）。
- f. 非必要信息扫描零命中：对包内每个文本文件扫描个人路径与用户名、本仓库任务 ID、本仓库专有文档名与凭据痕迹；并断言包内不存在 `.orchestration/`、`release/`、`scripts/build_release.py`、`docs/` 等结构残留。命中即失败并打印文件与行号。凭据痕迹分两类：`ghp_`、`github_pat_`、`-----BEGIN`、`AKIA` 等精确子串；以及用精确形状正则匹配的 OpenAI 风格密钥——词边界 + `sk-` + 至少 16 位密钥字符（**不使用裸 `sk-` 子串**，否则会误命中 `task-id`、`risk-based`、`subtask-level` 等普通词，误拦正常发布）。此外，断言包内文本文件不含 CRLF（换行统一为 LF），保证构建对检出行尾不敏感；命中即失败并打印文件。
  - 扫描模式分层：**通用模式内置在脚本**（个人主目录、凭据形状等，任何克隆都适用）；**机器 / 本仓库相关模式放在 `.local/release-scan.json`**（`{"forbidden_patterns": [...]}`，逐条按精确子串匹配）。该文件缺失时跳过本地规则并打印 `extra scan patterns: none`；该文件永不入库、永不打包。
- i. 解压目录内 `python -m scripts.update_agentorch.entry --help` 退出码为 0：证明自然语言入口及其依赖（`scripts/self_improvement/planning.py`、更新引擎）在无 `docs/` 的包内可导入。
- j. 解压目录内用随包发布的 `scripts/self_improvement/sources.py` 校验 `templates/self-improvement/sources.example.json`：必须无 schema 错误，证明包内注册表示例与实际校验器一致。
- k. 解压目录内 `scripts/run_delivery_scenarios.py --json` 退出码为 0，报告 `errors: 0`、`negative_passed == negative`（负向组通过表示拦截真的发生）且至少一个正向控制组通过：证明包内的合成回归场景集没有因内部实现被裁掉而静默失效。
- l. 解压目录内 `scripts/report_metrics.py --gold-sample --since … --until … --json` 退出码为 0，报告含七项指标、显式窗口与每项的样本量 / 缺失率 / coverage；同一脚本对空输入必须报 `no data` 而不是 0：证明指标口径随包发布且未被改写。
- h. 解压目录内 `scripts/architecture/check.py --project <包根> --json` 退出码为 0，且报告为 `enforcement_mode: OFF`、`blocking: false`、`head_sha_source: "unavailable"`：发行包不含契约与 `docs/`，可选架构检查器必须降级为只读诊断，而不是猜模块边界、静默失败或声称已检查。
- g. 结束时打印摘要：包内文件数、zip 路径、zip 大小（字节）与 zip 的校验和（校验和用于复核：同一仓库状态下重建应得到同一值）。

自检失败时，脚本会**删除刚生成的、未通过自检的 zip**，并在失败信息中说明"已删除未通过自检的产物"（若删除失败则如实打印该产物路径），确保 `release/` 不会留下被误当发布物的半成品。

### 3.1 CI 与本标准的覆盖分工

`.github/workflows/ci.yml` 在 `pull_request`→`main`、`push`→`main` 与手动触发时，用同一份步骤定义在三个 GitHub-hosted runner 上执行同一套自检：

| job | runner | Python |
| --- | --- | --- |
| `checks` | `ubuntu-latest` | 3.12 / 3.13 |
| `checks-macos` | `macos-latest` | 3.13 |
| `checks-windows` | `windows-latest` | 3.13 |

- **包内自检在三个平台都真实执行**：`uv run python scripts/build_release.py` 在每个 job 上各跑一次，脚本用 `sys.executable`（不是裸 `python`）在解压目录里执行 `validate_kit.py` 与 `unittest discover -s tests`（自检 b、d），所以"这一平台 / Python 版本拿到的包是否可用"是实机验证的，不是从 Linux 推断的。
- **失败即红**：没有 `continue-on-error`、`|| true` 或跳过开关；产物只写到 runner 的 `release/`（`.gitignore` 已忽略），不提交、不上传、不使用密钥。
- **行尾与可复现性**：Windows 检出不会破坏可复现性 —— 仓库 `.gitattributes` 统一 `eol=lf`，打包前还会把文本文件归一为 LF，自检 f 断言包内文本不含 CRLF；场景沙箱里的 git 仓库也固定 `core.autocrlf=false` / `core.eol=lf`，不依赖 runner 的全局 git 配置。
- **CI 证明什么**：包能构建，且内置自检 a–l 在该平台 / Python 版本上通过 —— 覆盖"打包内容集合 / 包内结构校验 / 发布扫描 / 可复现归档 / 包内合成回归与指标口径"这类回归。
- **CI 不证明什么**：**不证明发布本身**。CI 不校验发布命名（`AgentOrch-<X.Y>.zip` 或 `agentorch-{YYYYMMDD}.zip`）、不生成 GitHub Release、不写发布记录，也不覆盖真实安装器与用户桌面环境（这些平台承诺仍标 `UNKNOWN`）。发布仍按第 4 节由 Master 执行并逐项留证；CI 绿灯不能替代第 4 节的审查记录。

### 3.2 包内自检在三种树下的行为

自检 b / d 在**解压后的纯净包**里执行：包内 `docs/` 只有随包分发的 `docs/orchestration.md`，没有 `docs/PROJECT.md` / `docs/upgrade.md`，因此 `validate_kit.py` 判定为纯净包并跳过仓库专属文档，`MANIFEST.json` 的 SHA-256 与大小严格比较。这是发布物本身的证据，不受下游用法影响。

同一个 `validate_kit.py` 会按树切换模式（汇总行打印 `tree mode`）：

| 树 | 判定依据 | 仓库文档 | `MANIFEST.json` |
| --- | --- | --- | --- |
| 纯净包 | `docs/` 不存在，或只含随包分发的 `REQUIRED` 文件（当前只有 `docs/orchestration.md`） | 跳过 | 结构 + 分类 + 路径 + `REQUIRED` 集合 + SHA-256 / 大小 |
| 下游项目 | 有 `docs/PROJECT.md`，没有 `docs/upgrade.md` | 只要求 `docs/PROJECT.md` 与 `docs/PLAN.md` | 结构 + 分类 + 路径 + `REQUIRED` 集合 + 列出文件存在；不比较哈希 |
| 上游 checkout | 有 `docs/upgrade.md` | 全部仓库文档 | 结构 + 分类 + 路径 + `REQUIRED` 集合 + SHA-256 / 大小 |

- 下游项目解压后补上自己的 `docs/PROJECT.md` 与 `docs/PLAN.md` 即可通过 `validate_kit.py` 与包内 `unittest`；本地修改由 `check_drift.py` 报告，不阻断校验。
- **下游不要创建 `docs/upgrade.md`**：它是上游标记，出现后校验会按上游 checkout 要求全部仓库文档，并把本地修改当哈希漂移报错。
- 下游放宽的只有哈希：清单结构非法、分类错误、路径非法、`REQUIRED` 少列 / 多列、列出文件缺失，在三种树下都失败。完整说明见 `docs/README.md` 第 7 节（仓库文档，不随包发布）。

## 4. 发布流程

发起任何发行前，先按 5.3 完成版本分类与只读发行评估；常设自动发版授权范围当前为空，因此每次真实发行都需要 Owner 明确授权。准备报告不是发布，常设自动发版授权仍为空。

1. 在任务载体登记发布任务（唯一 ID、单一目标、允许路径与验收）。
2. 从最新主分支创建独立分支 / Worktree；不在共享工作区直接构建。
3. 构建：正式 Release 用 `uv run python scripts/build_release.py --release-version 1.6` 生成 `release/AgentOrch-1.6.zip`（补丁发行写全版本号，如 `--release-version 1.6.1` → `release/AgentOrch-1.6.1.zip`）；日期构建仍用 `uv run python scripts/build_release.py`（或 `--date YYYYMMDD`）生成 `release/agentorch-{YYYYMMDD}.zip`。
4. 自检：确认脚本内置 a–l 全部通过并记录退出码；产物路径、字节数与校验和一并留证。CI 虽已在 PR 上跑过同一自检（见 3.1），发布时仍须在本分支上重跑并在发布记录里写入实际输出，不能用 CI 绿灯代替。
5. 可复现性：用同一命令再构建一次到另一个输出目录（例如 `--output-dir /tmp/release-b`），比对两份 zip 的 SHA256 一致。
6. Master 独立审查（逐条对照本文件的自检清单与验收标准）。
7. 合并到主分支；合并后重新运行验证并记录结果。
8. 发布（由 Master 执行，执行代理不自行打 tag / 建 Release）：在合并后的主分支上创建 tag `v1.6`（补丁发行为 `v1.6.1`），把 `release/AgentOrch-1.6.zip`（补丁发行为 `release/AgentOrch-1.6.1.zip`）作为唯一 zip 资产上传，并在发布说明中写入产物 SHA256、本轮能力与未验证项。命令形如 `gh release create v1.6 release/AgentOrch-1.6.zip --title "AgentOrch 1.6" --notes-file <notes>`；**该命令依赖 GitHub 权限与网络，本仓库的本次发布准备任务未执行它（`NOT_RUN`）**，执行前须确认 tag 与资产名未被占用，冲突时停下报告、不覆盖历史版本。
9. 发布后清理本任务分支 / Worktree（独立记账，清理由此与"合并成功"分开记录）。

## 5. 版本：Format version 与对外 Release 版本

`VERSION.md` 里的 `Format version` 是**套件格式契约**版本（决定包结构、模板与必需文件清单的兼容语义）；对外 Release 版本是**发行号**（Git tag 与 GitHub Release 资产名）。两者不是同一个序列，也不互相替代：

- 只有格式真的变化（例如 1.2 → 1.3 新增资源锁载体字段、1.4 → 1.5 根目录瘦身与文件合并、1.5 → 1.6 新增自我完善与安全更新资产）才升 `Format version`，并同步模板、校验清单与 `docs/upgrade.md` 的迁移说明。
- 只增加能力、不改包格式时，`Format version` 保持不变，只升对外 Release 版本。
- `scripts/check_drift.py` 只解析 `VERSION.md` 的 `Format version:` 一行来生成 `MANIFEST.json` 的 `format_version`；`VERSION.md` 里的 `Release version` 行仅作记录，**不参与** Format 解析。本轮两者数值相同（都是 `1.6`），但解析口径不依赖 Release 行，未来也不会因此合并。
- 下游判断兼容性看 `MANIFEST.json` 的 `format_version` 与 Release tag；下载安装看 Release 资产名。更新引擎的 `VERSION_MATCH` 要求 tag 的 major.minor 与 `format_version` 一致——这正是 1.6 同时升 Format 的原因（见 5.2）；当前耦合仍存在，分类与授权边界见 5.3。

### 5.1 发行记录：Release 1.6

- tag：`v1.6`；资产：`AgentOrch-1.6.zip`（顶层目录 `AgentOrch-1.6/`）；`Format version`：`1.6`（包内 `MANIFEST.json` 的 `format_version` 也是 `1.6`）。
- 本轮同时把 `Format version` 从 `1.5` 升到 `1.6`（见 5.2）：更新引擎的 `VERSION_MATCH` 要求 release tag 的 major.minor 等于清单 `format_version`（`scripts/update_agentorch/versions.py` 的 `tag_matches_format()`），`v1.6` + `1.6` 因此可被「更新 AgentOrch」接受。
- 相对 v1.5 的公开能力（均已在本仓库主分支落地，有单元测试 / 包内自检 / CI 覆盖）：
  - 自我完善闭环：来源注册表、采集、Lesson 生命周期、规划与闭环报告；
  - 「更新 AgentOrch」安全更新入口：来源白名单、包校验 10 步、逐文件分类、备份 / 回滚与 `HD-*` 人工决策；
  - 58 个合成回归场景（A–D 组，含正负控制组）；包内文件 91 个（90 `REQUIRED` + `MANIFEST.json`）；
  - 四档 CI（3 个 runner、4 个 OS / Python 组合）：Linux（Python 3.12 / 3.13）、macOS（3.13）、Windows（3.13）；包内自检 a–l 在每一档真实执行。
- **未验证 / 已知限制（不得写成已验证）**：
  - 真实 `v1.5 → 1.6` 旧包升级已由 #71 于 2026-10-26 用真实发布资产验证为 `APPLIED`（`docs/upgrade.md` §14.2）；真实 1.3 / 1.4 与以 1.6 为已安装基线的升级仍为 `NOT_RUN`。合成测试仍作为合成证据保留：“v1.5 基线清单 + v1.6 包 → `APPLIED`”（`tests/update_agentorch/test_entry_release.py` 与场景 B5）。
  - 真实 GitHub Release 的创建、下载与 SHA256 复核依赖网络与权限，发布前为 `NOT_RUN`，由 Master 在发布后补证（Issue 的 AC-4）。
  - macOS / Windows 之外的操作系统与用户桌面环境为 `UNKNOWN`；CI 只覆盖 GitHub-hosted 的三个 runner。
  - 真实 GitHub Release 下载、真实旧项目与回滚失败场景仍为 `NOT_RUN` / `UNKNOWN`；自动更新入口的校验、分类、备份与回滚语义本轮未改动。
- 历史 `v1.5` Release 与其资产保留原样，不删除、不覆盖、不改写。

### 5.1.1 发行记录：Release 1.6.1（补丁）

- tag：`v1.6.1`；资产：`AgentOrch-1.6.1.zip`（顶层目录 `AgentOrch-1.6.1/`）；`Format version`：`1.6`（包内 `MANIFEST.json` 的 `format_version` 也是 `1.6`）。
- 这是 **1.6.0 的补丁发行**，不是 Format 变更：包内文件集合（91 个 = 90 `REQUIRED` + `MANIFEST.json`）、`MANIFEST.json` schema 1 与 `format_version` 均与 v1.6 相同；`VERSION.md` 只改 `Release version`，`Format version` 保持 `1.6`。`tag_matches_format('v1.6.1', '1.6')` 为真，`v1.6.1` 仍满足更新引擎的 `VERSION_MATCH`（major.minor = 1.6）。
- 补丁内容（#85）：修复下游项目已有 `docs/` 时 1.6 包无法通过 `validate_kit` 与包内 `unittest` 的问题——下游模式只要求 `docs/PROJECT.md` 与 `docs/PLAN.md`，不比较清单哈希；同时修正大小写路径残留。本补丁不改变更新引擎的校验 / 分类 / 备份 / 回滚语义，也不改变 `MANIFEST.json` schema。
- 可复现性：同一仓库状态下两次独立构建得到同一 SHA256；构建自检 a–l 与 v1.6 完全一致，没有跳过项。
- **未验证 / 已知限制（不得写成已验证）**：
  - 真实 `v1.6.0 → v1.6.1` 下载与升级为 `NOT_RUN`，归发布后验证；本任务只证明构建、包内自检与 1.6.1 包的下游回归。
  - 真实 GitHub Release 的创建、下载与 SHA256 复核依赖网络与权限，发布前为 `NOT_RUN`，由 Master 在发布后补证。
  - macOS / Windows 之外的操作系统与用户桌面环境为 `UNKNOWN`；CI 只覆盖 GitHub-hosted 的三个 runner。
- 历史 `v1.6` 与 `v1.5` Release 及其资产保留原样，不删除、不覆盖、不改写。

### 5.1.2 发行记录：Release 1.6.2（补丁）

- tag：`v1.6.2`；资产：`AgentOrch-1.6.2.zip`（顶层目录 `AgentOrch-1.6.2/`）；`Format version`：`1.6`（包内 `MANIFEST.json` 的 `format_version` 也是 `1.6`）。
- 这是 **1.6.1 的补丁发行**，不是 Format 变更：包内文件集合（91 个 = 90 `REQUIRED` + `MANIFEST.json`）、`MANIFEST.json` schema 1 与 `format_version` 均与 v1.6.1 / v1.6 相同；`VERSION.md` 只改 `Release version`，`Format version` 保持 `1.6`。`tag_matches_format('v1.6.2', '1.6')` 为真，`v1.6.2` 仍满足更新引擎的 `VERSION_MATCH`（major.minor = 1.6）。
- 补丁内容（#93）：修复 `task add` 重复多值选项静默丢值——`--acceptance` / `--paths` / `--depends` / `--resources` 原先用 `nargs='+'` + `store`，重复写同名选项时只有最后一次生效；现在重复出现与单次多值（`--acceptance "AC-1" --acceptance "AC-2"` 与 `--acceptance "AC-1" "AC-2"`）都会合并保留全部取值。本补丁不改变更新引擎的校验 / 分类 / 备份 / 回滚语义，也不改变 `MANIFEST.json` schema 或包文件集合。
- 可复现性：同一仓库状态下两次独立构建得到同一 SHA256；构建自检 a–l 与 v1.6.1 完全一致，没有跳过项。日期命名（`--date`）仍可用。
- **未验证 / 已知限制（不得写成已验证）**：
  - 真实 `v1.6.1 → v1.6.2` 下载与升级为 `NOT_RUN`，归发布后验证；本任务只证明构建、包内自检与 1.6.2 包的下游回归。
  - 真实 GitHub Release 的创建、下载与 SHA256 复核依赖网络与权限，发布前为 `NOT_RUN`，由 Master 在发布后补证。
  - macOS / Windows 之外的操作系统与用户桌面环境为 `UNKNOWN`；CI 只覆盖 GitHub-hosted 的三个 runner。
- 历史 `v1.6.1`、`v1.6` 与 `v1.5` Release 及其资产保留原样，不删除、不覆盖、不改写。

### 5.1.3 发行记录：Release 1.6.3

- tag：`v1.6.3`；资产：`AgentOrch-1.6.3.zip`（顶层目录 `AgentOrch-1.6.3/`）；`Format version`：`1.6`（包内 `MANIFEST.json` 的 `format_version` 也是 `1.6`）。
- 这是 **1.6.2 的补丁发行**，不是 Format 变更：`VERSION.md` 只改 `Release version`，`Format version` 保持 `1.6`；`MANIFEST.json` schema 1 与 `format_version` 均不变。`tag_matches_format('v1.6.3', '1.6')` 为真，`v1.6.3` 仍满足更新引擎的 `VERSION_MATCH`（major.minor = 1.6）。
- **包内文件集合 93 个（92 `REQUIRED` + `MANIFEST.json`）**：相对 v1.6.2 的 91 个（90 + 1）**新增 2 个 `REQUIRED` 文件**——`scripts/check_scope.py` 与 `tests/test_check_scope.py`（#101）。**没有移动、改名或删除任何文件**，schema 1 与 `format_version` 不变；这是 Format 1.6 内第一次新增 `REQUIRED` 文件，更新引擎按“新增上游文件”处理，不需要手工搬迁。
- 补丁内容（#99–#104，均已在本仓库主分支落地，有单元测试 / 包内自检 / CI 覆盖）：
  - #99：`task dispatch` 因路径锁冲突被拒绝时在 `events` 写入 `CONFLICT_RECORDED`（`data.kind=path`）且不留下任何锁；`task ready` 的预检拒绝另记 `PATH_PREFLIGHT_REFUSED` 供审计，不计入 M4。
  - #100：`task amend --paths` 可在 `BLOCKED`（parked）任务上原子重写允许路径与锁集合，`task show` 保留前后路径。
  - #101：新增只读 `scripts/check_scope.py` 作为 G2 证据——按 `approved_sha` / `head_sha` / `--base` 输出越界路径，退出码 0 范围内 / 2 越界 / 1 无法判定；随包附带 `tests/test_check_scope.py`。
  - #102 / #103 / #104：文档口径——会话必读集合与 `kit/orchestration.md` 第三部分按需读（#102）；G6 风险深度（触发项写最坏后果与额外复核）#103；M4 资源冲突来源改为 `resource_lock_events` 的 `LOCK_DENIED_CONFLICT`（#104）。
  本补丁不改变更新引擎的校验 / 分类 / 备份 / 回滚语义，也不改变状态机与锁语义。
- 可复现性：同一仓库状态下两次独立构建得到同一 SHA256；构建自检 a–l 与 v1.6.2 完全一致，没有跳过项。日期命名（`--date`）仍可用。
- **发布后补证（2026-10-27，#113）**：
  - 真实 `v1.6.2 → v1.6.3` 下载与升级为 `APPLIED`：`PLAN` 的 10 项包校验全 `PASS`，`APPLY` 后 Format 1.6 → 1.6、`MANIFEST.json` files 90 → 92、92/92 包内文件与目标包逐字节一致、项目自有 `docs/PROJECT.md` / `docs/PLAN.md` / `LOCAL-NOTES.md` SHA256 不变、备份 `required=true` / `verified=true`、head 前后不变；升级后项目 `validate_kit` 退出码 0（downstream）、包内 675 tests `OK`（skip 19）。详见仓库文档 `docs/upgrade.md` §14.5。
  - 真实 GitHub Release 已创建并复核：[Release v1.6.3](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.6.3)，tag `v1.6.3` → `2adf431`，资产 `AgentOrch-1.6.3.zip` 461,829 bytes，SHA256 `dcce5b24…` 与 GitHub API `assets[].digest` 一致；主分支 CI run `40471657312` @ `2adf431` 四平台全绿。
- **仍未验证 / 已知限制（不得写成已验证）**：
  - macOS / Windows 之外的操作系统与用户桌面环境为 `UNKNOWN`；CI 只覆盖 GitHub-hosted 的三个 runner。
  - 真实 `v1.6.0 → v1.6.1`、`v1.6.1 → v1.6.2` 升级、真实 1.3 / 1.4 资产升级、真实断网 / 限速 / DNS、`HD-11` 与非 GitHub-hosted 平台仍为 `NOT_RUN` / `UNKNOWN`；本次 `1.6.2 → 1.6.3` 的成功不替代它们。
- 历史 `v1.6.2`、`v1.6.1`、`v1.6` 与 `v1.5` Release 及其资产保留原样，不删除、不覆盖、不改写。

### 5.1.4 发行记录：Release 1.7.0

- tag：`v1.7.0`；资产：`AgentOrch-1.7.0.zip`（顶层目录 `AgentOrch-1.7.0/`）；`Format version`：`1.7`（包内 `MANIFEST.json` 的 `format_version` 也是 `1.7`）。
- 这是 **Phase D 的功能发行**（不是 1.6.x 的补丁）：`VERSION.md` 同时把 `Format version` 从 `1.6` 升到 `1.7`、`Release version` 从 `1.6.3` 升到 `1.7.0`。`tag_matches_format('v1.7.0', '1.7')` 为真；若只升 Release 而把 Format 留在 `1.6`，更新引擎的 `VERSION_MATCH` 会把 `v1.7.0` 判为不匹配并拒绝，因此本次必须同时升 Format。
- 包内文件集合 **118 个（117 `REQUIRED` + `MANIFEST.json`）**：相对 v1.6.3 的 93 个（92 + 1）新增 25 个 `REQUIRED` 文件，全部属于 Phase D 的 `scripts/agent_selection/` 资产族与随包测试。**没有移动、改名或删除任何文件**，`MANIFEST.json` schema 仍为 1；更新引擎按“新增上游文件”处理，不需要手工搬迁。
- Phase D（S0–S5，Epic #119）带来的能力，均已合并到主分支并有单元测试 / 包内自检 / CI 覆盖：
  - S0（#117）设计冻结 → S1（#120）：每终端独立 scope 与 Markdown 私有记录基座（`scripts/agent_selection/scope.py`、`local_store.py`）；scope 解析失败或隔离无法证明时停止写持久记录，不回退共享目录。
  - S2（#121）：Runtime adapter、Agent 发现与角色匹配（`runtime_adapter.py`、`discovery.py`、`roles.py`）；C1–C4 门先于任何排序，显式注册表回退可用，Reviewer 独立性按底层模型 ID 判定，缺角色输出具体原因。
  - S3（#122）：Artificial Analysis 官方数据源与角色专项动态选型（`model_selection.py`、`model_sources/`）；硬筛选先于解释性排序，外部数据不可用 / 过期时如实降级为 `BLOCKED` / `UNKNOWN` / `STALE`。
  - S4（#123）：本机项目反馈与可解释校准（`feedback.py`）；只在当前 scope 内读取，样本不足、身份变化或证据过期时不排名、不自动改配置。
  - S5（#124）：会话入口接线与端到端合成回归（`tests/agent_selection/test_e2e.py`，E1–E13）；合成回归场景从 58 增至 71（A–E 组，13 正向 / 58 负向），使用模拟 Runtime、固定模型快照与临时目录，不访问外部模型服务、不需要 API Key。
- 可复现性：同一仓库状态下两次独立构建得到同一 SHA256；构建自检 a–l 与 v1.6.3 一致（构建脚本未改动），没有跳过项。日期命名（`--date`）仍可用。
- **发布后补证（2026-10-27，#144）**：
  - 真实 `v1.6.3 → 1.7.0` 下载与隔离升级为 `PASS`：`PLAN` 的 10 项包校验全 `PASS`，`APPLY` 后 Format 1.6 → 1.7、`MANIFEST.json` files 92 → 117、117/117 包内文件与下载的 1.7.0 目标包逐字节一致、项目自有 `docs/PROJECT.md` / `docs/PLAN.md` / `LOCAL-NOTES.md` SHA256 前后不变、备份 `required=true` / `verified=true`、`rollback.result=NOT_NEEDED`、head 前后不变；升级后项目 `validate_kit` 退出码 0（downstream，117 required）、包内 `unittest` 968 tests `OK`（skip 19）。详见仓库文档 `docs/upgrade.md` §14.6。
  - 真实 GitHub Release 已创建并复核：[Release v1.7.0](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.7.0)，tag `v1.7.0` → `ac3e87f`，资产 `AgentOrch-1.7.0.zip` 602,273 bytes，SHA256 `cbc35fcf…` 与 GitHub API `assets[].digest` 一致；主分支 CI run `40598836550` @ `ac3e87f` 四平台全绿。
- **仍未验证 / 已知限制（不得写成已验证）**：
  - Phase D 的 E1–E13 是合成证据，不证明真实 Runtime / 模型 API；真实 `1.6.0` / `1.6.1` → `1.6.2` 升级、真实 1.3 / 1.4 资产、真实断网 / 限速 / DNS、`HD-11` 与非 GitHub-hosted 平台仍为 `NOT_RUN` / `UNKNOWN`。
  - macOS / Windows 之外的操作系统与用户桌面环境为 `UNKNOWN`；CI 只覆盖 GitHub-hosted 的三个 runner。
- 历史 `v1.6.3`、`v1.6.2`、`v1.6.1`、`v1.6` 与 `v1.5` Release 及其资产保留原样，不删除、不覆盖、不改写。

### 5.2 Format version 变更

- **1.6.1 / 1.6.2 / 1.6.3 是补丁发行，不属于 Format 变更**：Format 仍为 `1.6`，见 5.1.1 / 5.1.2 / 5.1.3；本节只记录真正的格式版本升级。其中 1.6.3 在 Format 1.6 内新增了 `REQUIRED` 文件（见 5.1.3），但包格式、`MANIFEST.json` schema 与 `format_version` 未变，仍不是 Format 变更。

- **1.7（当前）** 相对 1.6：
  - **包内新增 Phase D 角色选择资产族**：`scripts/agent_selection/`（每终端 scope、Markdown 私有记录、runtime adapter、发现与角色匹配、可选模型数据源与快照、本机反馈）及随包测试 `tests/agent_selection/`（含 E1–E13 端到端）进入 `REQUIRED`。包内文件从 v1.6.3 资产的 93 个（92 `REQUIRED` + `MANIFEST.json`）增至 118 个（117 + 1）。
  - **无文件移动 / 改名 / 删除，无破坏性布局变化**：新增的只是文件与子目录，既有路径、`MANIFEST.json` schema 1 与 `REQUIRED` 中已有条目全部保留；升级按普通“新增上游文件”处理，不需要手工搬迁。
  - **Format 升到 1.7 的原因**：让 `v1.7.0` release tag 与 `MANIFEST.json` 的 `format_version` 一致，满足更新引擎 `VERSION_MATCH`；`VERSION.md` 的 `Release version` 仍独立记录（本次为 `1.7.0`）。
  - 迁移：无强制手工步骤。已安装 1.3 及以后、且有 `MANIFEST.json` 的项目可用「更新 AgentOrch」入口准备升级；真实 `1.6.3 → 1.7.0` 隔离升级为 `PASS`（2026-10-27，`APPLIED`，Format 1.6 → 1.7、files 92 → 117、117/117 逐字节一致、项目自有文件不变、备份 `verified=true`、head 前后不变；见 `docs/upgrade.md` §14.6），该结果只覆盖这一次升级。
- **1.6（历史）** 相对 1.5：
  - **包内新增自我完善与安全更新资产**：`scripts/self_improvement/`（来源注册表、采集、Lesson、规划与闭环）、`scripts/update_agentorch/`（安全更新引擎与「更新 AgentOrch」入口）及其随包测试、`templates/self-improvement/` 模板进入 `REQUIRED`。连同 1.5 发布后合入 Format 1.5 的可选架构契约检查器与相关测试，包内文件从 v1.5 资产的 34 个（33 `REQUIRED` + `MANIFEST.json`）增至 91 个（90 + 1）。
  - **无文件移动 / 改名 / 删除，无破坏性布局变化**：新增的只是文件与子目录，既有路径、`MANIFEST.json` schema 1 与 `REQUIRED` 中已有条目全部保留；升级按普通“新增上游文件”处理，不需要手工搬迁。
  - **Format 升到 1.6 的原因**：让 `v1.6` release tag 与 `MANIFEST.json` 的 `format_version` 一致，满足更新引擎 `VERSION_MATCH`；`VERSION.md` 的 `Release version` 仍独立记录（1.6 发布时数值相同，语义仍分开）。
  - 迁移：无强制手工步骤。已安装 1.3 及以后、且有 `MANIFEST.json` 的项目可用「更新 AgentOrch」入口准备升级；真实 v1.5 → 1.6 旧包升级已由 #71 验证；1.3 / 1.4 等仍为 `NOT_RUN`。
- **1.5** 相对 1.4：
  - **根目录瘦身至 3 文件**：根只保留 `AGENTS.md`、`LICENSE.md`、`VERSION.md`（原 txt→md）；`README.md`→`docs/README.md`（GitHub 仍渲染仓库首页）、`ORCHESTRATION-PROJECT.md`→`docs/PROJECT.md`（配置权威，旧根位置 fail-closed）。
  - **Agent 权威合并**：`AGENT-BOOTSTRAP.md` + `AGENT-ORCHESTRATION.md` + `AGENT-ORCHESTRATION-ACCEPTANCE.md` 三合一为 `kit/orchestration.md`（随包分发）；editions 每底座 6 份合并为 `master.md`（+`execution.md`）；`prompts/` 并入对应模板后删除；全部模板/底座文件小写命名。
  - **REQUIRED 49→33**（包 34 = 33 + MANIFEST）；分类规则同步（`docs/PROJECT.md`/`docs/PLAN.md` 归 project-profile）。
  - **可选 Architecture Contract 检查器（#48）**：新增 `scripts/architecture/`（纯标准库，Python AST + 受工具链门控的 Rust 词法适配器）与 `templates/architecture/` 模板；`REQUIRED` 33→45（包 46）。无契约项目保持 `OFF` 只读诊断，CI 以诊断模式回归，自检 h 验证包内降级行为。
  - **统一 uv**：全部命令 `uv run python` 前缀，CI 加 setup-uv。
  - 迁移步骤见仓库 `docs/upgrade.md` 第 12 节。
- **1.4** 相对 1.3：
  - **设计目标权威迁到 `docs/PLAN.md`**：任务方向以 `docs/PLAN.md` 为准；`init` 在 `docs/PLAN.md` 缺失且无旧根 `PLAN.md` 时创建 `docs/` 并放入模板，发现旧根 `PLAN.md` 时不迁移、只提示 `git mv`；`validate_kit` 对残留根 `PLAN.md` 报错（fail-closed，防两个设计权威并存）。迁移步骤见仓库 `docs/upgrade.md` 第 11 节。
  - **图片协作条款**：设计文档可配图（`docs/images/`，正文相对引用）；Agent 只按正文指引按需查看图片、禁止批量读取；图文双写；公开仓库截图脱敏。`docs/images/` 不随包发布。
  - **统一用 `uv` 运行 Python 脚本**：文档命令、模板验证命令与 CI 全部改为 `uv run python …` 前缀；CI 增加 setup-uv 步骤（`uv run` 会使用 PATH 上的解释器）。
  - **README 用户指引话术化**：启动 / 登记 / 派发 / 收口等说明改为中文自然语言话术（对 Agent 说），话术 → 命令 → 预期证据对照表保留在 README 3.0 节。
- **1.3** 相对 1.2：
  - **资源锁的载体字段**：任务载体新增具名资源锁字段——通用任务包 `declared_resources`（`<resource_id>:<mode>[:<ttl>]`）、`github` 登记块 `locked_resources` 与 `resource_lease_until`、`local` 任务库 `resources_json` 列。字段形状属载体契约，故格式版本升级；语义与冲突矩阵仍只在 `docs/design/resource-locks.md`。
  - **`MANIFEST.json` 与 `check_drift.py`**：包内新增机器可读清单（自检 a 的校验对象），分类与清单生成下移到 `scripts/check_drift.py` 并由构建脚本导入，同时随包提供下游只读漂移诊断。
  - **CI 打包自检**：打包自检纳入 CI（见 3.1），并新增 `scripts/check_drift.py` 的 `py_compile` 语法检查；本文件与 README 的发布说明同步改为"包内容 == `REQUIRED` + `MANIFEST.json`"。
- **1.2**：包内新增 `MANIFEST.json` 与 `scripts/check_drift.py`（本次 1.3 记录将其约束补齐为可执行的清单校验）。

### 5.3 发行分类与授权边界（#192 冻结）

本节是 [主动式 SemVer 发行策略](../docs/design/semver-proactive-release-policy.md) 第 1、2 节的可执行口径，由 #192 冻结。它只冻结**分类规则与授权边界**，不授权任何 tag / Release，也不改变第 3 节自检、第 4 节发布步骤或现行 G1–G6 门禁。

- **判定依据是对外兼容性，不是工程量或文件数**。以最新**已发布** tag 为基准（`git tag --sort=-v:refname` 的第一个），按三档分类：

| 类别 | 对外兼容性 | 例（基于 v1.7.0） |
|---|---|---|
| PATCH | 修复错误、测试 / 文档 / 兼容性修正；公开行为无对外不兼容变化 | v1.7.1 |
| MINOR | 向后兼容的新能力或对外行为扩展 | v1.8.0 |
| MAJOR | 破坏性 API / 套件入口、配置、数据 / 格式、授权语义或升级路径变化 | v2.0.0 |

- **混合变更取最大 bump**：同一发行含多类改动时按 MAJOR > MINOR > PATCH 取最高一级，不得拆成多次低级别发行来规避等级。
- **空发布不允许**：自最新已发布 tag 以来没有已合并且已验证、用户可感知的变更时不得发版，不得为“走流程”创建空 Release。
- **版本基准与一致性检查**：候选版本号相对最新已发布 tag 计算；若该 tag 与 `VERSION.md` 的 `Release version:` 不一致（tag 领先、落后或缺失），停止发行并核对，不得按任一来源擅自推定，也不得重打、改名或覆盖历史 tag。
- **批次边界**：按实际用户可用成果与高风险门禁决定，不是每个 PR 必发；多个改动同时发行时取最高级别 bump。提案中的数量、天数等数值只是建议默认值，不构成门禁。
- **只读发行评估**：Master 在会话恢复、重要 PR 合并后、阶段完成时执行只读评估，列出最新已发布 tag 之后的已合并已验证项、每项兼容性分类、建议 bump、被阻塞或回滚的项。只读表示**不修改 `VERSION.md`、不打 tag、不建 Release、不上传资产**。
- **离线或无权限**：只能产出报告（建议版本、分类依据、缺口、待授权项），如实标 `NOT_RUN` / `BLOCKED`，不得声明已发布，也不得用本地构建冒充 Release。
- **常设自动发版授权范围：空**。当前没有任何 PATCH / MINOR 处于“无需逐次确认即可发版”的常设授权内。没有 Owner 另一次明确授权（写明批次、版本级别与范围）时，**不得打 tag 或创建 Release**；“无需逐次提醒”的意图不折算为授权。MAJOR、破坏性迁移、无法回退或安全授权变化即使获得常规授权，仍须单独提交 Owner 决策。执行代理永远不得自行发版。
- **Release / Format 解耦与旧客户端桥接（#193）**：旧的 `tag_matches_format()` 原样保留，已发布的旧客户端继续在安装前用它失败关闭。新增 `versions.format_compatibility()` 供新更新器使用：除了原有的“tag major.minor == Format”规则，它还接受调用者显式给出且包含目标 `format_version` 的受支持列表；当前声明为 `DECLARED_SUPPORTED_FORMATS = ('1.7',)`。缺少 / 空矩阵、未知 / 非法 Format、非法 tag、清单与包成员不符、降级 / 重装仍失败关闭，没有“忽略 `VERSION_MATCH`”的开关。
- **桥接未发布前，旧客户端仍拒绝不升 Format 的 MINOR**：`v1.8.0` + Format `1.7` 不会被已发布的 v1.7.0 客户端接受。桥接发行必须自身仍满足旧规则（例如 tag `v1.7.x` + Format `1.7`），旧客户端才能安装它，之后才是带有桥接的新更新器处理不升 Format 的 MINOR。**本任务只实现并测试桥接，不发布桥接包**；桥接发行端到端升级为 `NOT_RUN`，详见仓库文档 `docs/upgrade.md` §14.7。

## 6. 声明

以后建 release 就按本文件执行：命名、包内容、自检与流程全部以本文件为准；任何偏离都必须先更新本文件并说明理由。
