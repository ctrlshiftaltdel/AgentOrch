# 从旧版升级到 1.3（2026-10）

> 本文件是**仓库文档**，不随发行包发布（`docs/` 下只有随包分发的核心契约 `docs/orchestration.md` 在打包清单内）。发行包使用者看 [README.md](README.md) 第 10 节「从旧版升级（1.1 → 1.3）」，那一节已经包含四条路径的最小可复制步骤；本文是它的完整版：分步清单、报错原文、退出码含义与回退。
> 本文只写「旧文件长什么样、命令怎么改、自检看到什么」，**不重述契约**：
>
> - `edition` 的权威来源、合法值与失败关闭：[README.md](README.md) 6.1 / 6.2
> - 计划分两层（设计目标 / 阶段计划）：[AGENTS.md](../AGENTS.md) 第 8 条
> - 资源锁语义（冲突矩阵、租约与恢复、底座边界）：[docs/design/resource-locks.md](design/resource-locks.md)
> - 清单格式、文件分类与漂移诊断口径：[docs/drift.md](drift.md)
>
> 1.4 起套件命令统一加 `uv run python` 前缀执行；本文记录的是 1.3 时代的实测命令（直接 `python`），语义不变，对照执行时自行加前缀。
> 本文出现的每条命令与每个退出码都在 2026-10-23 于 Windows 10 + Git Bash + Python 3.13 上**真实执行**过，逐条记录见第 9 节。未实测的部分会明写「未实测」。

## 1. 适用对象与迁移路径

适用于还在 `Format version` 1.1 / 1.2、要升到 1.3 的项目，`local` 与 `github` 两种底座都适用（底座差异写在每节的「底座差异」里）。**1.3 → 1.4 的项目只看第 11 节**（根 `PLAN.md` 迁入 `docs/PLAN.md` 与图片规范），第 3–6 节不用重做。

四条路径对应四种旧起点，**命中几条改几条**，彼此独立：

| # | 旧起点 | 现在会撞到什么 | 改哪里 |
| --- | --- | --- | --- |
| 1 | `docs/PROJECT.md` 的 `edition` 是旧格式（多取值、带说明文字、重复声明、无列表符号） | 调度被阻断；`config show` 退出码 1 | `docs/PROJECT.md` 一行 |
| 2 | 根 `PLAN.md` 是「设计目标 + 阶段 + 任务块 + 状态表」混装的台账 | 不报错，但计划分两层后方向与进度读不出唯一答案 | 根 `PLAN.md` + 新增 `docs/` 阶段计划 |
| 3 | 用了具名资源锁，但 `.orchestration/config.json` 没配 `resource_lease_ttl` | 派发失败：`RESOURCE_CONFIG_INVALID` | `.orchestration/config.json` |
| 4 | 旧包没有 `MANIFEST.json` | `check_drift.py` 报 `version-unknown`、退出码 2（不是崩溃） | 登记一份基线清单 |

**没有自动迁移。** 套件不提供迁移脚本，`init` 也不会替你拆分计划（见 4.4），四条都要手工做。迁移只改项目自己的文件，不改套件版本策略（`VERSION.md` 由维护者更新，不是升级动作的一部分）。

## 2. 升级前准备

```bash
python scripts/orchestrator.py backup        # 备份 .orchestration/state.sqlite3
python scripts/orchestrator.py config show   # 记录升级前的生效值（edition 可能已报错）
git rev-parse HEAD                           # 记录升级前 head，回退时要用
```

`backup` 的实测行为：写入 `.orchestration/backups/state-<YYYYMMDD>-<HHMMSS>.sqlite3`，退出码 0。

```text
$ python scripts/orchestrator.py backup
Backup created: <项目根>\.orchestration\backups\state-20261023-011527.sqlite3
# exit 0
```

如果 `config show` 现在就已经退出码 1，先做第 3 节——`edition` 不合法时其余调度命令都不可用，其它三条也验不了。

## 3. 迁移一：`edition` 旧格式行

### 3.1 先判断自己是不是旧格式

```bash
grep -n "edition" docs/PROJECT.md
```

以下四种形态都属于旧格式，都会被拒：

| 形态 | 例子 |
| --- | --- |
| A 取值带说明文字 | ``- `edition`: local（单人本地；需要多人协作时改为 github）`` |
| B 多取值 | ``- `edition`: local / github`` 或 ``local, github`` |
| C 重复声明 | 文件里出现两行 `edition` |
| D 不是列表项 | `edition: local`（没有列表符号），或写在两列表格里（字段名一列、取值一列） |

### 3.2 为什么被拒

1.3 起 `edition` 只在 `docs/PROJECT.md` 里读，且**只认裸取值** `local` / `github`：取值带说明文字或多写几个取值时，路由无法唯一确定底座，所以现在**失败关闭**——报出实际值与文件位置，不再回落到 `local`。`.orchestration/config.json` 里的 `edition` 键不再生效，只作为历史记录显示（`config show` 会标注 `legacy key is ignored`）。

### 3.3 真实报错（四条，形态与退出码都不同）

形态 A（带说明文字）：

```text
$ python scripts/orchestrator.py config show
ERROR: docs/PROJECT.md: key `edition` has unsupported value 'local（单人本地；需要多人协作时改为 github）' in <项目根>\docs/PROJECT.md; allowed values are local, github (write the value alone, with no extra text)
# exit 1
```

形态 B（多取值）：

```text
ERROR: docs/PROJECT.md: key `edition` has unsupported value 'local / github' in <项目根>\docs/PROJECT.md; allowed values are local, github (write the value alone, with no extra text)
# exit 1
```

形态 C（重复声明，会指到行号）：

```text
ERROR: docs/PROJECT.md: key `edition` is declared 2 times (lines 2, 4) in <项目根>\docs/PROJECT.md; keep exactly one declaration
# exit 1
```

形态 D（没有列表符号或写在表格里，报的是"缺键"而不是"值非法"）：

```text
ERROR: docs/PROJECT.md: missing required key `edition` in <项目根>\docs/PROJECT.md; add one line "- `edition`: local" or "- `edition`: github"
# exit 1
```

补充：`config show` 仍会把 `retry_cap` / `local_dir` 表格打到 stdout，`ERROR` 行打到 stderr，`edition` 显示为 `(invalid)`。所以只看 stdout 会以为"没有报错"，**必须看退出码或 stderr**。

### 3.4 改法

```diff
- `edition`: local（单人本地；需要多人协作时改为 github）
+ `edition`: local
```

规则只有四条：**一行**、**以 `-` 或 `*` 开头**、**冒号后只写取值**、**不加引号 / 括号 / 斜杠 / 大小写变体**（`LOCAL`、`Local` 都不合法）。

- 同时把 `.orchestration/config.json` 里残留的 `edition` 删掉（不删也不影响，但会多一行 `legacy key is ignored`）。
- `github` 底座写 ``- `edition`: github``；底座切换是单向、一次性动作，切换规则见 [docs/orchestration.md](orchestration.md) 第 6 节，**不要**为了升级把 `local` 项目写成 `github`。

### 3.5 自检

```bash
python scripts/orchestrator.py config show
```

```text
Config file: <项目根>\.orchestration\config.json
  retry_cap  = 3          (source: config.json)
  local_dir  = .local     (source: config.json)
  edition    = local      (source: docs/PROJECT.md)
Edition is selected only by `edition` in <项目根>\docs/PROJECT.md; config.json holds local CLI runtime parameters (retry_cap, local_dir) and never selects an edition.
Edit .orchestration/config.json to change retry_cap or local_dir; invalid values there fall back to defaults.
# exit 0
```

判据：`edition` 一行**不是** `(invalid)`，`source` 为 `docs/PROJECT.md`，退出码 0。`github` 底座下还会多一行说明（调度命令被拒绝，矩阵见 [README.md](README.md) 6.2），退出码同样是 0。

### 3.6 不要这样做

- 不要写 ``- `edition`: local, github`` 期望"两个底座都支持"——多取值一律拒绝。
- 不要靠 `.orchestration/config.json` 里的 `edition` 生效，它已经不是权威来源。
- 不要在报错后继续跑 `task dispatch` 看看"能不能凑合过"，它只会复述同一个 `EditionError`。

## 4. 迁移二：根 `PLAN.md` 台账 → 设计目标 + `docs/` 阶段计划

### 4.1 判断自己是不是旧台账

```bash
grep -n '^|' PLAN.md
```

命中「任务 / 阶段 / 状态」这类表格行，或文件里还带着任务块字段（`Allowed paths`、`AC-1`、`Depends on`），就是旧台账格式。

```text
3:| 任务 | 阶段 | 状态 |
4:|---|---|---|
5:| TASK-0001 | Phase A | 已完成 |
# exit 0
```

### 4.2 目标形态

计划分两层，权威口径在 [AGENTS.md](../AGENTS.md) 第 8 条，这里只引用不复述：根 `PLAN.md` 只承载设计目标；阶段目标、任务顺序、依赖、任务块与状态搬到 `docs/` 的阶段计划。

1.3 之前只有根 `PLAN.md` 一个计划文件，所以旧项目把两层内容混在一起。升级后：

| 文件 | 只写这些 | 模板 |
| --- | --- | --- |
| 根 `PLAN.md` | 问题与目标用户、设计目标、设计原则、范围与非目标、全局约束、可度量的全局验收 | [templates/plan.md](../templates/plan.md) |
| `docs/` 阶段计划 | 计划头部、当前状态表、阶段（目标 / 交付 / 出口）、任务块、维护规则 | [templates/implementation-plan.md](../templates/implementation-plan.md) |

### 4.3 拆分步骤（手工，四步）

1. 复制阶段计划模板：

   ```bash
   mkdir -p docs
   cp templates/implementation-plan.md docs/IMPLEMENTATION-PLAN.md
   ```

2. 把旧台账里的**阶段目标、任务顺序与依赖、任务块、状态表**搬到 `docs/IMPLEMENTATION-PLAN.md`。状态表从此是唯一落点；已关闭 / 取消的任务保留行并注明结果。

3. 把根 `PLAN.md` 收敛为设计目标：删掉阶段划分、任务清单与状态表，只留 4.2 表里左列六类内容。`templates/plan.md` 的同名章节可以直接对照（`## 问题与目标用户` / `## 设计目标` / `## 设计原则` / `## 范围与非目标` / `## 全局约束` / `## 可度量的全局验收`）。

4. 在根 `PLAN.md` 顶部补一句指向阶段计划的说明（模板里的 `## 计划文件` 一节就是这个用途），避免下一个会话只读到设计目标就以为"没有计划"。

同时删掉项目里可能存在的第二份"计划"备份（例如 `PLAN-old.md`、`docs/PLAN.md` 双写），否则又变成两处状态。

### 4.4 `init` 的确定行为（实测）

`init` 发现根 `PLAN.md` 已存在时**原样保留**，不拆分、不合并、不覆盖：

```text
$ python scripts/orchestrator.py init
...
PLAN.md already present, kept unchanged: <项目根>\PLAN.md
Next: judge task direction from the design goals in the root PLAN.md; templates/plan.md is only the initial copy.
# exit 0
```

实测同一旧台账文件在 `init` 前后内容与表格行数不变（3 行表格 → 3 行表格）。所以**不要指望跑一条命令就把台账拆开**；反过来，升级时也不必担心 `init` 弄坏你的计划文件。

### 4.5 自检

```bash
grep -n '^|' PLAN.md || echo "no table rows: PLAN.md carries no task status table"
```

```text
no table rows: PLAN.md carries no task status table
# exit 0
```

再跑套件自带的两条检查（在套件安装目录，即项目根执行）：

```bash
python scripts/validate_kit.py
python -m unittest discover -s tests
```

两条都应以退出码 0 结束（`validate_kit.py` 打印 `Kit validation passed ...`，`unittest` 打印 `OK`）。人工再确认一遍：`docs/IMPLEMENTATION-PLAN.md` 的「当前状态」表与任务载体（`local` 任务库 / `github` Issue）描述的是同一批任务，没有一边写着 `已完成`、另一边还在 `DISPATCHED`。

### 4.6 不要这样做

- 不要保留"根 `PLAN.md` 写状态、`docs/` 也写状态"的双写：两边冲突时按设计目标停下对齐，代价比现在就拆开高得多。
- 不要把阶段计划放在根目录另起一个名字（`PLAN-2.md`）——按归属它是 `docs/` 下的文件，放在根目录会被当成第二份设计目标。
- 不要直接改 `templates/plan.md` / `templates/implementation-plan.md` 来"适配"自己的项目：模板要保持项目无关，改模板会让下一次升级更难。

## 5. 迁移三：资源锁必须显式配 `resource_lease_ttl`

### 5.1 什么时候会撞上

任务用 `--resources` 声明了具名资源（例如 `db:test/mysql-main:exclusive`），并且声明字符串里**没有**带 `:<ttl>`，`task dispatch` 就会去 `.orchestration/config.json` 读 `resource_lease_ttl`。这个键**没有内置默认值**，没配就拒绝派发。

### 5.2 真实报错

```text
$ python scripts/orchestrator.py task dispatch TASK-0001 --holder "runner:local-01" --evidence "smoke"
ERROR: RESOURCE_CONFIG_INVALID: resource lock configuration is missing or illegal: resource_lease_ttl must be an integer between 1 and 604800 in <项目根>\.orchestration\config.json; add it to .orchestration/config.json (a lease without an explicit ttl or cool-down is refused)
# exit 1
```

同一族报错还有两处，容易被忽略：

```text
# task renew：总是读 config 的 resource_lease_ttl
ERROR: RESOURCE_CONFIG_INVALID: resource lock configuration is missing or illegal: resource_lease_ttl must be an integer between 1 and 604800 in <项目根>\.orchestration\config.json; ...

# task reclaim：总是读 config 的 resource_reclaim_grace
ERROR: RESOURCE_CONFIG_INVALID: resource lock configuration is missing or illegal: resource_reclaim_grace must be an integer between 0 and 86400 in <项目根>\.orchestration\config.json; ...
```

三者都以退出码 1 结束，而且都发生在**写锁之前**：缺 ttl 时派发不会留下半套锁，`task renew` 也不会改动已有租约（实测失败前后 `lease_until` 不变）。

### 5.3 改法

`.orchestration/config.json`（`resource_lease_ttl` 必填才有资源锁能力；`resource_reclaim_grace` 只有 `task reclaim` 需要，建议一起配）：

```json
{
  "retry_cap": 3,
  "local_dir": ".local",
  "resource_lease_ttl": 900,
  "resource_reclaim_grace": 300
}
```

- `resource_lease_ttl`：整数 1–604800（秒）。示例 900 = 15 分钟租约。
- `resource_reclaim_grace`：整数 0–86400（秒）。示例 300 = 过期后 5 分钟冷却，冷却期内 `task reclaim` 一律拒绝。
- 必须是**JSON 整数**：`"900"`（字符串）、`900.0`（浮点）、负数都不合法，会得到同一条 `RESOURCE_CONFIG_INVALID`；ttl 还要求 ≥ 1，而 grace 允许 `0`。
- **未声明资源的任务完全不读这两个键**，所以升级后不配它们也不影响原有任务。

### 5.4 自检

```bash
python scripts/orchestrator.py task dispatch TASK-0001 --holder "runner:local-01" --evidence "smoke"
```

```text
TASK-0001: DISPATCHED; locks acquired for tests
TASK-0001: resource locks GRANTED for db:test/mysql-main:exclusive (holder runner:local-01, fingerprint 799038d52a7d)
Now pass the self-contained task packet to a real Execution Agent. This command does not launch agents.
# exit 0
```

```bash
python scripts/orchestrator.py task resources
```

```text
resource_id	mode	state	lease	holder	task	backend	lock_id	blocking
db:test/mysql-main	exclusive	ACTIVE	lease_until=...	renewed_at=...	holder=runner:local-01	task=TASK-0001	backend=local	lock_id=db:test/mysql-main#exclusive#TASK-0001#0	blocks=exclusive+shared
Total: 1 row(s); generated with the frozen contract docs/design/resource-locks.md.
# exit 0
```

判据：`state=ACTIVE` 且 `lease_until` 与配置的 ttl 相符（示例 900 秒 = 15 分钟）。

### 5.5 底座差异与边界

- **`github` 底座不需要配**这两个键：本地 CLI 在 `github` 下拒绝 `task dispatch` / `renew` / `reclaim`（报 `edition is 'github' ...`，退出码 1），资源占用只以 Issue 登记块协作记录，不走这套租约。
- 声明里自带 ttl（`db:test/mysql-main:exclusive:600`）时，`task dispatch` 不读 config 的 `resource_lease_ttl`；但 `task renew` 仍然读它（实测：config 里没有该键时 `renew` 报 `RESOURCE_CONFIG_INVALID`）。
- 租约到期只是转 `EXPIRED`，**不释放、也不等于空闲**；语义见 [docs/design/resource-locks.md](design/resource-locks.md)。

### 5.6 不要这样做

- 不要把这两个键写进 `docs/PROJECT.md`（那里只管项目底座与门禁口径），也不要在 `config.json` 里写注释（JSON 不接受注释，损坏后 `retry_cap` / `local_dir` 会静默回落默认值）。
- 不要因为"反正没有默认值"就把 ttl 设成 604800 求稳：租约越长，崩掉的任务占着资源的时间越长。
- 不要用 `task unlock --force` 绕过配置错误：`RESOURCE_CONFIG_INVALID` 是配置问题，解锁不会让下一次派发通过。

## 6. 迁移四：旧包没有 `MANIFEST.json`

### 6.1 现象

```bash
python scripts/check_drift.py .
```

```text
status: version-unknown (exit 2)
manifest: absent (no MANIFEST.json)
checked: 0 manifest file(s); missing 0, modified 0, unknown 0, project-profile 0, extensions 0
note: no MANIFEST.json in this project: suite version unknown, no baselines to compare
note: register a baseline: keep the manifest from the kit release you started from (or rebuild one with scripts/build_release.py), the tool never guesses a version
note: this is a diagnosis only; reading and using the project is not blocked
# exit 2
```

**退出码 2 不是崩溃**，意思是"版本未知 / 需要登记基线"。工具不会按文件内容或 `VERSION.md` 猜一个版本，也不会因为这个报告阻止你继续用项目。

### 6.2 退出码含义

| 退出码 | 含义 | 该做什么 |
| --- | --- | --- |
| `0` | clean：核心文件齐全且未被改；`project-profile`（`docs/PROJECT.md` / `PLAN.md` / `DESIGN.md`）差异与本地扩展只列出、不算 drift | 不需要动作 |
| `2` | drift：核心文件缺失或被改、上游目录里有清单不认识的 `unknown` 文件、清单 schema 超出工具支持范围，**或项目没有 `MANIFEST.json`（version-unknown）** | 有人看一眼报告，判断是否本意 |
| `1` | 输入无效或工具错误：项目根不存在 / 不是目录、`MANIFEST.json` 不是合法 JSON 或结构不合法 | 先修输入，这一次诊断没有完成 |

完整口径（分类规则、只读保证）见 [docs/drift.md](drift.md)；面向使用者的摘要见 [README.md](README.md) 8.2。

### 6.3 登记基线（两条来源）

**来源 A：当初那份发行包。** 把安装时那个 zip 里的 `MANIFEST.json` 放到项目根即可——它记录的正是你这棵树的上游基线。zip 名与包内顶层目录名相同：正式 Release 是 `AgentOrch-<X.Y>.zip` / `AgentOrch-<X.Y>/`，日期构建是 `agentorch-<日期>.zip` / `agentorch-<日期>/`。

```bash
# 正式 Release（示例：1.6）
python -m zipfile -e AgentOrch-1.6.zip <解压目录>
cp <解压目录>/AgentOrch-1.6/MANIFEST.json .

# 日期构建
python -m zipfile -e agentorch-<日期>.zip <解压目录>
cp <解压目录>/agentorch-<日期>/MANIFEST.json .
```

**来源 B：在该版本的套件仓库里重建。** 找不到原包时，用与安装版本一致的仓库 checkout 重新构建一次，再从产物里取清单：

```bash
python scripts/build_release.py --date 20261023 --output-dir /tmp/baseline
python -m zipfile -e /tmp/baseline/agentorch-20261023.zip /tmp/baseline-extract
cp /tmp/baseline-extract/agentorch-20261023/MANIFEST.json .
```

实测：

```text
$ python scripts/build_release.py --date 20261023 --output-dir /tmp/baseline
Release self-check passed (checks a-f).
  package files : 48 (47 REQUIRED + MANIFEST.json)
  manifest      : schema 1, suite 'Agent-Orchestration Starter Kit', format 1.3
  zip path      : /tmp/baseline/agentorch-20261023.zip
# exit 0
```

正式 Release 资产（如 `AgentOrch-1.6.zip`）用 `--release-version 1.6` 重建，顶层目录为 `AgentOrch-1.6/`：

```bash
python scripts/build_release.py --release-version 1.6 --output-dir /tmp/baseline
```

参数写 `1.6`，不要写 `v1.6`（`v1.6` 是 tag，不是资产名）；`--date` 与 `--release-version` 互斥，同时给出会直接报错。两种命名模式只改变 zip 名与顶层目录，不改变包内文件集合与 `MANIFEST.json` 的 `format_version`。

注意两点事实：

- **`scripts/build_release.py` 不随发行包发布**：包内 `scripts/` 只有 `check_drift.py`、`orchestrator.py`、`report_metrics.py`、`validate_kit.py`。所以来源 B 只能在仓库 checkout 上做；只有发行包的用户请用来源 A。
- 来源 B 重建的是**那棵树当前的上游文件**。如果这棵树早就被改过，重建出来的基线会把这些改动当成"本来就该这样"，历史漂移就查不出来了。基线要么来自原包，要么来自干净的同版本 checkout。

### 6.4 只读、不覆盖

- `check_drift.py` 本身**只读**：只 `read_bytes` / `read_text` / `os.walk`，没有 `--fix` / `--sync`，也不跟随符号链接。它不会创建、重命名、删除或覆盖项目里的任何文件。
- 第 6.3 节的 `cp` 是**你手动做的登记动作**，不是工具替你改树。想撤销就删掉 `MANIFEST.json`，回到 `version-unknown`。
- 不要把**新版**清单套到**旧**树上：那会把每一个上游文件都报成 `modified`，看起来像"整个套件被改过"。
- 不要为了"让报告变绿"去改核心文件或删本地扩展：报告只描述事实，改文件才改变事实。

### 6.5 自检

登记基线后：

```bash
python scripts/check_drift.py .
```

```text
status: clean (exit 0)
manifest: schema 1 (supported: yes), suite 'Agent-Orchestration Starter Kit', format 1.3
checked: 47 manifest file(s); missing 0, modified 0, unknown 0, project-profile 2, extensions 0
  [project-profile] docs/PROJECT.md - project-profile
  [project-profile] PLAN.md - added locally
note: 2 project-profile file(s) differ from the shipped template, which is expected after a project fills them in
# exit 0
```

`project-profile` 条目**正常**：`docs/PROJECT.md` 与 `PLAN.md` 本来就该被项目填写。

再确认基线真的能检出改动（改一个核心文件再还原）：

```text
status: drift (exit 2)
checked: 47 manifest file(s); missing 0, modified 1, unknown 0, project-profile 2, extensions 0
  [modified] AGENTS.md - core
# exit 2
```

### 6.6 不要这样做

- 不要期望工具"猜一个版本"后自己对齐：没有清单就没有可信基线，这是设计选择，不是缺口。
- 不要把 `unknown`（落在 `editions/`、`templates/`、`scripts/`、`prompts/`、`tests/` 里、清单不认识的文件）当成本地扩展忽略掉：它可能是旧版本残留，值得人工确认。
- 不要在升级过程中顺手"同步"上游文件：套件不做覆盖，需求是差异可解释，不是文件一致。

## 7. 一次跑完的清单

按顺序执行，每步的退出码即判据：

| # | 步骤 | 命令 | 期望退出码 |
| --- | --- | --- | --- |
| 1 | 备份任务库 | `python scripts/orchestrator.py backup` | `0` |
| 2 | 修 `edition` 旧格式 | 编辑 `docs/PROJECT.md`，只留 ``- `edition`: local`` | — |
| 3 | 验证底座 | `python scripts/orchestrator.py config show` | `0`（`edition` 不是 `(invalid)`） |
| 4 | 拆分计划 | `cp templates/implementation-plan.md docs/IMPLEMENTATION-PLAN.md`，把阶段与状态搬过去 | — |
| 5 | 验证根计划无状态表 | `grep -n '^|' PLAN.md` | 无输出（提示行由 `||` 分支打印） |
| 6 | 配资源锁 | 在 `.orchestration/config.json` 加 `resource_lease_ttl`（+ 可选 `resource_reclaim_grace`） | — |
| 7 | 验证派发 | `python scripts/orchestrator.py task dispatch <ID> --holder <可定位ID> --evidence <证据引用>` | `0` |
| 8 | 登记基线 | 见 6.3，把 `MANIFEST.json` 放到项目根 | — |
| 9 | 验证漂移诊断 | `python scripts/check_drift.py .` | `0`（登记基线之前会得到 `2`） |
| 10 | 套件自检 | `python scripts/validate_kit.py` 与 `python -m unittest discover -s tests` | 都是 `0` |

第 7 步只对 `local` 底座有意义；`github` 底座的派发由 Issue / PR 承载，本地 CLI 会明确拒绝。

## 8. 回退

四条迁移都是"文档 + 一个 JSON 键 + 一个基线文件"，逐条可回退，互不牵连。

| 迁移 | 回退动作 | 回退后状态 |
| --- | --- | --- |
| `edition` | `git checkout -- docs/PROJECT.md` | 回到旧格式 = 重新阻断调度。**除非要放弃升级，否则不要回退这一条** |
| 计划分层 | `git checkout -- PLAN.md`，并删除新拆出的 `docs/IMPLEMENTATION-PLAN.md` | 回到单文件台账；任务载体里的状态不受影响（状态本来就不在 `PLAN.md` 里权威） |
| 资源锁 | 从 `.orchestration/config.json` 删除 `resource_lease_ttl` / `resource_reclaim_grace` | 未声明资源的任务照常派发；声明了资源的任务重新报 `RESOURCE_CONFIG_INVALID`。已写入的租约**仍在锁表里**（实测删键后 `task resources` 仍列出 `ACTIVE`），只是 `task renew` 会因缺 ttl 失败——要么补回配置，要么等它过期后按锁语义处置 |
| 基线清单 | `rm MANIFEST.json` | 回到 `version-unknown`、退出码 2；诊断不阻断使用 |

任务库层面的回退（以下**未实测**，属人工操作，动手前先停止派发）：`python scripts/orchestrator.py backup` 生成的 `.orchestration/backups/state-<时间戳>.sqlite3`，在确认没有其它会话在用任务库后，用它替换 `.orchestration/state.sqlite3` 即可回到备份时刻。

**回退的硬边界**：任何回退都不得造成双写——同一个任务不能同时在 `local` 任务库和 GitHub Issue 里维护状态。底座切换与回退规则见 [docs/orchestration.md](orchestration.md) 第 6 节。

## 9. 本轮实测记录

环境：Windows 10（MINGW64 / Git Bash）、Python 3.13、套件 `Format version` 1.3、`main` = `25d1573`。
实验都在系统临时目录里构造旧格式文件完成，**没有改动本仓库自己的 `.orchestration/` 配置**。

| # | 场景 | 命令 | 实测退出码 |
| --- | --- | --- | --- |
| 1 | `edition` 带说明文字 | `orchestrator.py --root <tmp> config show` | `1`（`unsupported value 'local（单人本地…）'`） |
| 2 | `edition` 多取值 `local / github` | 同上 | `1`（`unsupported value 'local / github'`） |
| 3 | `edition` 重复声明两行 | 同上 | `1`（`declared 2 times (lines 2, 4)`） |
| 4 | `edition` 写成 `edition: local`（无列表符号） | 同上 | `1`（`missing required key`） |
| 5 | `edition` 写在表格行里 | 同上 | `1`（`missing required key`） |
| 6 | 修成裸取值 ``- `edition`: local`` | `config show` | `0` |
| 7 | 旧台账 `PLAN.md` + 已有 `templates/plan.md` | `orchestrator.py init` | `0`，打印 `PLAN.md already present, kept unchanged`；文件表格行数仍为 3 |
| 8 | 声明资源但缺 `resource_lease_ttl` | `task dispatch TASK-0001 --holder runner:local-01 --evidence smoke` | `1`（`RESOURCE_CONFIG_INVALID`） |
| 9 | 补齐 ttl=900 + grace=300 后重派 | 同上 | `0`（`locks GRANTED`） |
| 10 | 有 ttl、缺 grace | `task reclaim ...` | `1`（`resource_reclaim_grace must be an integer between 0 and 86400`） |
| 11 | 只剩 grace、缺 ttl | `task renew` / `task resources` | renew `1`（缺 ttl）；resources `0`（原锁仍 `ACTIVE`） |
| 12 | 旧项目无 `MANIFEST.json` | `check_drift.py <项目根>` | `2`（`version-unknown`） |
| 13 | 重建基线包 | `build_release.py --date 20261023 --output-dir /tmp/baseline` | `0`（48 个包内文件，manifest schema 1 / format 1.3） |
| 14 | 解压后的干净包 | `check_drift.py <解压目录>` | `0`（`clean`） |
| 15 | 旧项目放回基线清单 | `check_drift.py .` | `0`（`project-profile 2`，不算 drift） |
| 16 | 篡改 `AGENTS.md` 后再诊断 | `check_drift.py .` | `2`（`[modified] AGENTS.md - core`） |
| 17 | `github` 底座 | `config show` / `task dispatch` | `0`（附拒绝说明）/ `1`（`edition is 'github'`） |
| 18 | README 用 Markdown 链接指向 `docs/upgrade.md` | 在包内跑 `validate_kit.py` | `1`（`broken relative link`，且清单哈希不匹配）——**所以 README 只写路径、不做链接** |
| 19 | ttl 写成 `"900"` / `0` / `900.0` / `-1` | `task dispatch` | 四种都是 `1`（`RESOURCE_CONFIG_INVALID`） |

## 10. 本文不做什么

- 不提供自动迁移脚本，也不改 `init` 的行为：四条迁移都是手工的，可审查、可回退。
- 不猜版本、不覆盖项目文件、不"同步"上游：漂移只报告差异。
- 不重述契约：`edition` 权威、计划分层、资源锁语义、清单分类各自由第 1 节列的文档定义，本文只引用。
- 不改版本策略：`Format version` 由维护者按发布规则更新，升级说明不改变发布与兼容承诺。

## 11. 迁移五（1.4 新增）：根 `PLAN.md` → `docs/PLAN.md` 与图片规范

- **变了什么**：设计目标权威从根 `PLAN.md` 移到 `docs/PLAN.md`——放 `docs/` 是为了同目录配图：图放 `docs/images/`，正文中以相对路径引用（如 `![图 1](images/xxx.png)`）。`init` 改为：`docs/PLAN.md` 缺失且**无**根 `PLAN.md` 时创建 `docs/` 并从 `templates/plan.md` 逐字节复制；存在根 `PLAN.md`（1.3 及更早）时**不会自动迁移**，只打印迁移提示（防数据意外）。`validate_kit.py` 对残留的根 `PLAN.md` 直接报错——根与 docs 并存等于两个设计权威。
- **图片规则**（定义在 `AGENTS.md` 第 8 条，本节只提醒）：**Agent 只在正文明确指向某图时才打开该图，禁止批量读取 `docs/images/`**；**图文双写**——设计意图必须能仅凭文字成立，每个图片引用都要有相邻文字说明；`docs/images/` 随公开仓库公开、不随发行包发布，截图入库前先脱敏。
- **怎么改**（手工两条命令，`init` 不会替你动旧文件；2026-10-23 在 Windows 10 + Git Bash + Git for Windows 实测）：

  ```bash
  mkdir docs && git mv PLAN.md docs/PLAN.md        # 0；docs/ 必须先存在，git mv 不会自建目录
  git commit -m "migrate plan into docs"           # 0；提交迁移（或随下一个提交一起走）
  ```

  然后把 `docs/PLAN.md` 文内指向仓库根文件的相对链接补上 `../`（如 `[AGENTS.md](../AGENTS.md)` → `[AGENTS.md](../AGENTS.md)`）；`python scripts/validate_kit.py` 会逐个指出断链。
- **怎么自检**：

  ```bash
  python scripts/validate_kit.py                   # 0：不再报 legacy root PLAN.md
  python scripts/orchestrator.py init              # 0；输出 kept unchanged: <项目根>\docs\PLAN.md
  python scripts/orchestrator.py task list         # 0；任务库不受迁移影响
  ```
- **底座差异**：`local` / `github` 步骤完全相同——`PLAN.md` 是项目文件，不在任务载体里；`github` 项目迁移后照常在 Issue 登记块中引用 `docs/PLAN.md`。
- **回退**：`git mv docs/PLAN.md PLAN.md` 回到 1.3 布局；但 1.4 的 `validate_kit` 会再报 legacy——回退意味着同时回退套件版本，不要只回退文件。
- **不要这样做**：把根 `PLAN.md` 复制（而非移动）到 `docs/`；迁移后保留两份设计目标；用 init 之外的方式让两份并存。


## 12. 迁移六（1.5 新增）：根目录瘦身 + 文件改名合并

- **变了什么**：根目录只保留 `AGENTS.md`、`LICENSE.md`、`VERSION.md`；`README.md` 移到 `docs/README.md`（GitHub 仍渲染仓库首页）、`ORCHESTRATION-PROJECT.md` 移到 `docs/PROJECT.md`；`AGENT-BOOTSTRAP.md`+`AGENT-ORCHESTRATION.md`+`AGENT-ORCHESTRATION-ACCEPTANCE.md` 三合一为 `kit/orchestration.md`；editions 每底座 6 份合并为 `master.md`、`START-EXECUTION.md` 改名 `execution.md`、github 三个载体模板小写；`templates/` 全部小写（`templates/plan.md` 等）；`prompts/` 两文件并入 `templates/project-charter.md` 与 `templates/review-checklist.md` 后删除目录；全部命令改为 `uv run python` 前缀。
- **怎么改**（本套件自身迁移于 2026-10-23 真实执行，全部 `git mv` 完成；下游照抄即可，含 `mkdir -p kit`）：

  ```bash
  git mv LICENSE.txt LICENSE.md && git mv VERSION.txt VERSION.md
  mkdir -p kit docs
  git mv README.md docs/README.md
  git mv ORCHESTRATION-PROJECT.md docs/PROJECT.md
  git mv AGENT-ORCHESTRATION.md kit/orchestration.md   # 再把 bootstrap/acceptance 内容并入库内
  git rm AGENT-BOOTSTRAP.md AGENT-ORCHESTRATION-ACCEPTANCE.md
  # editions: 6→1 合并 + execution 改名 + github 载体模板小写（git mv 逐个执行）
  # templates: 逐个 git mv 为小写；prompts 两文件内容并入模板后 git rm
  ```

- **怎么自检**：

  ```bash
  uv run python scripts/validate_kit.py     # 0：旧根位置（PLAN/ORCHESTRATION-PROJECT）不存在即通过
  uv run python scripts/orchestrator.py init  # 0：edition 从 docs/PROJECT.md 读取
  uv run python scripts/build_release.py      # 0：包 91 文件（90 REQUIRED + MANIFEST），format 1.6
  uv run python scripts/build_release.py --release-version 1.6  # 0：AgentOrch-1.6.zip，format 1.6
  ```

- **不要这样做**：保留旧根文件副本（validate 会报 legacy）；只改一半路径（validate 链接扫描会逐个指出断链）。

## 13. 迁移七（当前版本新增）：“更新 AgentOrch”安全入口

> 本节是**仓库文档**，不随发行包发布。发行包内的等义操作说明在
> `templates/self-improvement/README.md`；更新安全的语义权威仍是
> [update-safety.md](self-improvement/update-safety.md)，实现细节见
> [update-engine-implementation.md](self-improvement/update-engine-implementation.md)。
> 本节只说明**怎么触发、怎么判读、怎么回退**，不重写分类或信任规则。

### 13.1 触发与不触发

| 说法 | 是否启动更新 | 说明 |
| --- | --- | --- |
| `更新 AgentOrch` / `升级 AgentOrch` | 是 | 路由到更新引擎 |
| `把项目中的 AgentOrch 更新到最新版` | 是 | 同上；同义表述由 `planning.route_request()` 判定 |
| `更新一下文档` / `更新依赖` / `更新这个 Issue` | 否 | 普通请求，不碰套件 |
| `更新 AgentOrch 的文档` / `更新 AgentOrch 版本号` | 先问一次 | 有 `AgentOrch` 指代但对象有歧义，入口返回 `NEEDS_CLARIFICATION`，不默认执行 |
| `AgentOrch 最新版本是多少？` | 否 | 只回答版本问题 |
| `进行自我完善` | 否（另一个闭环） | `SELF_IMPROVEMENT` 路由，不进入更新入口 |

入口只做路由、环境识别与载体适配；它不重写 #51 的来源校验、逐文件分类、备份与回滚语义。默认 `PLAN` 只读；`APPLY` 只在隔离分支 / Worktree 准备变更，不提交、不合并。

### 13.2 先识别，再调用

```bash
python3 -m scripts.update_agentorch.entry --project . \
  --request "更新 AgentOrch" --mode PLAN
```

入口按顺序读取并写入报告：

- `edition`：只从 `docs/PROJECT.md` 读取；缺失 / 非法时 `STOPPED_INVALID_EDITION`，不猜测、不联网。
- 当前版本 / Format：从项目根 `MANIFEST.json` 读取 `format_version` 与 `suite`；没有清单就 `STOPPED_INVALID_BASELINE`。
- 工作区：Git head / branch / 未提交路径；无 Git 时记录 `is_git_repo=false`，不能声称工作区干净。
- 已安装基线：清单 SHA-256 与逐文件哈希；随后才获取并校验官方 Release。

本仓库自身没有 `MANIFEST.json`，因此在本仓库执行上面命令会安全失败，实测退出码见第 14 节；这是失败关闭，不是缺陷。

### 13.3 旧 Format 兼容矩阵（实测范围）

自动更新的前提是项目根有**可解析且 schema 受支持**的 `MANIFEST.json`。`MANIFEST.json` 从 Format 1.3 起才随包提供，因此：

| 已安装 Format | 自动更新是否可判定 | 本任务实测 | 真实已发布包 | 说明 |
| --- | --- | --- | --- | --- |
| `1.7`（当前） | 是 | 合成 / 单元：`tag_matches_format('v1.7.0','1.7')` 为真（`v1.7` 同），v1.7.0 目标包通过 `VERSION_MATCH` 与包校验；历史 `v1.6.x` tag 在 Format 1.7 下不匹配 | `PASS`（2026-10-27） | 当前 Format；真实 `v1.6.3 → 1.7.0` 隔离升级已于 2026-10-27 实测为 `APPLIED`（Format 1.6 → 1.7、`MANIFEST.json` files 92 → 117、117/117 包内文件逐字节一致、项目自有文件 SHA256 不变、备份 `verified=true`、head 前后不变），见 §14.6。入口按 `VERSION_MATCH` 接受 tag `v1.7.0`（major.minor = 1.7），再按单调性比较 **target release `1.7.0`** 与已安装 `format_version`（`1.6` 记作 `1.6.0`）：`1.7.0 > 1.6.0`，因此可判定升级；该判定只说明入口会处理，真实升级证据只覆盖 §14.6 那一次 |
| `1.6`（历史） | 是 | 合成 / 单元：`tag_matches_format('v1.6.3','1.6')` 为真（`v1.6` / `v1.6.1` / `v1.6.2` 同），v1.6 目标包通过 `VERSION_MATCH` 与包校验 | `PASS`（2026-10-27，#113） | 历史 Format；真实 v1.6 包已下载并作为 v1.5 → 1.6 的目标包校验通过。入口按 `VERSION_MATCH` 接受 tag `v1.6.3`（major.minor = 1.6），再按单调性比较 **target release `1.6.3`** 与已安装 `format_version`（`1.6` 记作 `1.6.0`）：`1.6.3 > 1.6.0`，因此可判定升级。真实 `1.6.2` → `1.6.3` 隔离升级已于 2026-10-27 实测为 `APPLIED`（`MANIFEST.json` files 90 → 92、92/92 包内文件逐字节一致、项目自有文件 SHA256 不变、备份 `verified=true`、head 前后不变），见 §14.5；该结果只覆盖这一次升级，不延伸到未实测的旧版本 / 平台 |
| `1.5` | 是 | 合成项目：`APPLIED`，项目自有文件逐字节不变 | `PASS`（2026-10-26，#71） | 真实 v1.5 Release 包（`agentorch-20261023.zip`）→ 真实 v1.6（`AgentOrch-1.6.zip`）：`APPLIED`，Format 1.5 → 1.6，项目自有文件逐字节不变；见 §14.2 |
| `1.4` | 是 | 合成项目：`APPLIED`，根 `PLAN.md` 保持原样 | `NOT_RUN` | 未获取到真实已发布 1.4 资产（本仓库 Release 只有 v1.5 / v1.6，另有 v1.1 tag 但无 Release 资产）；合成目标为 v1.6，根计划按项目自有路径处理 |
| `1.3` | 是（清单存在时） | 合成项目：根 `README.md` 被识别为 `UPSTREAM_REMOVAL`，`STOPPED_CONFLICT` + `HD-09`，文件未删除 | `NOT_RUN` | 未获取到真实已发布 1.3 资产；1.3 根文件在 1.5 布局中改名 / 迁移，入口不自动删除，需人工决策 |
| `1.1` / `1.2` | 否 | 无清单 → `STOPPED_INVALID_BASELINE`，零写入、零网络 | `NOT_RUN` | 无真实发布资产可获取；本轮只用真实 v1.5 包删除 `MANIFEST.json` 复现无基线停止（§14.3 第 1 行），不等于真实 1.1 / 1.2 包；登记基线见第 6 节 |
| 清单损坏 / schema 不支持 | 否 | `STOPPED_INVALID_BASELINE` | `NOT_RUN` | 只读诊断 + 手工迁移（`HD-08`） |

**上表的“本任务实测”只覆盖合成测试；“真实已发布包”一列只记录真实资产实测。** 2026-10-26，#71 在隔离项目上用真实 Release 补齐了 `1.5 → 1.6`、真实下载与网络 / 停止路径（§14.1–§14.3）；2026-10-27，#113 用真实资产补齐了 `1.6.2 → 1.6.3`（§14.5）；同日 #144 用真实资产补齐了 `1.6.3 → 1.7.0`（§14.6）。真实已发布 1.3 / 1.4 包仍为 `NOT_RUN`；签名校验为 `NOT_APPLICABLE`（各 Release 都只有一个 zip 资产，没有 `.sig` / `.asc` / checksum 附件）；未实测平台（大小写不敏感文件系统、Windows 路径、macOS）为 `UNKNOWN`。

**Release 1.6.1（补丁）**：`1.6.0 → 1.6.1` 是**补丁升级，Format 不变**（仍为 `1.6`）；`tag_matches_format('v1.6.1', '1.6')` 为真，`v1.6.1` 仍满足 `VERSION_MATCH`。补丁内容是 #85 的下游包修复（已有 `docs/` 的项目可通过 `validate_kit` 与包内测试），不改变更新引擎的校验 / 分类 / 备份 / 回滚语义，也不改变 `MANIFEST.json` schema。真实 `v1.6.1` 下载与 `1.6.0 → 1.6.1` 升级为 `NOT_RUN`，归发布后验证；发布准备任务只证明构建、包内自检与 1.6.1 包的下游回归。

**Release 1.6.2（补丁）**：`1.6.1 → 1.6.2` 同样是**补丁升级，Format 不变**（仍为 `1.6`）；`tag_matches_format('v1.6.2', '1.6')` 为真（major.minor = 1.6），`v1.6.2` 仍满足 `VERSION_MATCH`。补丁内容是 #93 的 `task add` 重复多值选项修复（`--acceptance` / `--paths` / `--depends` / `--resources` 重复写时不再只保留最后一次），不改变更新引擎的校验 / 分类 / 备份 / 回滚语义，也不改变 `MANIFEST.json` schema 或包内文件集合（91 个 = 90 `REQUIRED` + `MANIFEST.json`）。真实 `v1.6.2` 下载与 `1.6.1 → 1.6.2` 升级为 `NOT_RUN`，归发布后验证；发布准备任务只证明构建、包内自检与 1.6.2 包的下游回归。以 1.6 为已安装基线的升级、真实 1.3 / 1.4 资产仍为 `NOT_RUN` / `UNKNOWN`，不因本补丁改变。

**Release 1.6.3**：`1.6.2 → 1.6.3` 同样是**补丁升级，Format 不变**（仍为 `1.6`）；`tag_matches_format('v1.6.3', '1.6')` 为真（major.minor = 1.6），`v1.6.3` 仍满足 `VERSION_MATCH`。补丁内容是 #99–#104：`task dispatch` 路径锁冲突写入 `CONFLICT_RECORDED`（`data.kind=path`）且不留锁、`task ready` 预检拒绝另记 `PATH_PREFLIGHT_REFUSED`（#99）；`task amend --paths` 在 `BLOCKED` 任务上原子重写路径与锁（#100）；新增只读 `scripts/check_scope.py`（#101）；会话阅读口径、G6 风险深度与 M4 资源冲突来源的文档修正（#102 / #103 / #104）。本补丁不改变更新引擎的校验 / 分类 / 备份 / 回滚语义，也不改变 `MANIFEST.json` schema 或 `format_version`；包内文件集合从 91 个（90 `REQUIRED` + `MANIFEST.json`）增至 93 个（92 + 1）——新增 `scripts/check_scope.py` 与 `tests/test_check_scope.py`，无移动 / 改名 / 删除，更新引擎按“新增上游文件”处理，无需手工搬迁。`v1.6.3` 已于 2026-10-27 发布（[Release v1.6.3](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.6.3)，tag `v1.6.3` → `2adf431`，资产 `AgentOrch-1.6.3.zip`，461,829 bytes，SHA256 `dcce5b24…`，与 GitHub API `assets[].digest` 一致）；真实下载与 `1.6.2 → 1.6.3` 隔离升级结果为 `APPLIED`（§14.5）。真实 1.3 / 1.4 资产与真实 `1.6.0` / `1.6.1` → `1.6.2` 升级仍为 `NOT_RUN` / `UNKNOWN`，不因本补丁改变。

**Release 1.6 的版本对齐**：本轮把 `Format version` 与 release tag 都设为 `1.6`，`v1.6` 包通过包校验第 10 步 `VERSION_MATCH`，可被「更新 AgentOrch」接受（合成测试：`tests/update_agentorch/test_entry_release.py` 的 v1.5 基线 → v1.6 目标为 `APPLIED`，场景 B5 同）。**真实** v1.5 → v1.6 下载与升级已于 2026-10-26 实测（§14.2）：资产 SHA256 `bc0a64e3…` 与 Release 记录一致，结果为 `APPLIED`。真实已发布 1.3 / 1.4 包与以 1.6 为已安装基线的升级仍为 `NOT_RUN`；`1.6.0 → 1.6.1` 的补丁升级同样为 `NOT_RUN`。

**Release 1.7.0**：`1.6.3 → 1.7.0` 是 **Format 1.6 → 1.7 的格式升级**，也是 Phase D 的功能发行；`tag_matches_format('v1.7.0', '1.7')` 为真（major.minor = 1.7），满足 `VERSION_MATCH`。把 Format 留在 `1.6` 会让 `v1.7.0` 因 major.minor 不一致被拒绝，因此本次同时升 Format。包内文件集合从 93 个（92 `REQUIRED` + `MANIFEST.json`）增至 118 个（117 + 1），新增的 25 个 `REQUIRED` 文件全部是 Phase D 的 `scripts/agent_selection/` 资产族与随包测试（`tests/agent_selection/`，含 E1–E13 端到端）；无移动 / 改名 / 删除，`MANIFEST.json` schema 1 不变。更新引擎的校验 / 分类 / 备份 / 回滚语义未改动，升级按“新增上游文件”处理，不需要手工搬迁。`v1.7.0` 已于 2026-10-27 发布（[Release v1.7.0](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.7.0)，tag `v1.7.0` → `ac3e87f`，资产 `AgentOrch-1.7.0.zip`，602,273 bytes，SHA256 `cbc35fcf…`，与 GitHub API `assets[].digest` 一致）；真实下载与 `1.6.3 → 1.7.0` 隔离升级为 `PASS`（`APPLIED`，Format 1.6 → 1.7、`MANIFEST.json` files 92 → 117、117/117 包内文件逐字节一致、项目自有文件 SHA256 不变、备份 `verified=true`、head 前后不变；§14.6）。真实 1.3 / 1.4 资产、真实 `1.6.0` / `1.6.1` → `1.6.2` 升级与真实断网 / 限速 / DNS 仍为 `NOT_RUN` / `UNKNOWN`，不因本版本改变。

### 13.3.1 Release / Format 解耦与旧客户端桥接（#193）

Release 版本与 Format 是两条序列。本仓库更新器现在把第 10 项 `VERSION_MATCH` 交给显式兼容矩阵判断，而不是只比较 tag 前两段：

- **历史规则保留**：tag 的 major.minor 等于 `format_version` 时通过（`v1.7.0` / `v1.7.1` + `1.7`，历史 `v1.6` / `v1.5`）。`versions.tag_matches_format()` 本身**原样保留**，没有放宽。
- **桥接规则**：调用者显式声明的受支持 Format 列表包含目标包 `format_version` 时通过。当前声明为 `versions.DECLARED_SUPPORTED_FORMATS = ('1.7',)`，因此**带有该桥接的更新器**可以接受 `v1.8.0` + Format `1.7`。
- **失败关闭**：缺少或空矩阵、未知 / 非法 Format、非法 tag、清单与包成员不符、版本不前进（降级 / 重装）仍然拒绝。矩阵来自更新器代码，不来自目标包；没有“忽略 `VERSION_MATCH`”的开关。

**已发布的旧客户端仍会拒绝未桥接的 MINOR**：v1.7.0 客户端在安装任何包之前先执行旧的 `tag_matches_format()`，它要求 tag major.minor 等于 Format。`v1.8.0` + Format `1.7` 因此不会被旧客户端接受；必须先有一个**同 Format 的更新器桥接发行**（桥接包自身满足旧规则，例如 tag `v1.7.x` + Format `1.7`）被旧客户端安装，之后才轮到带有桥接的新更新器处理不升 Format 的 MINOR。**本任务只实现并测试桥接，不发布桥接包**；桥接包发布并安装之前，旧客户端对不升 Format 的 MINOR 继续失败关闭。真实旧客户端证据见 §14.7，桥接发行的端到端升级为 `NOT_RUN`。

### 13.4 冲突、删除与重命名

入口（经由 #51 引擎）逐文件分类；以下情况**默认停止并保留项目文件**：

| 情况 | 报告结果 | 默认动作 | 人工决策 |
| --- | --- | --- | --- |
| 本地与上游都改（`THREE_WAY_CONFLICT`） | `STOPPED_CONFLICT` | 保留本地字节，零写入 | `HD-10` |
| 上游删除仍存在的文件（`UPSTREAM_REMOVAL`） | `STOPPED_CONFLICT` | 保留旧文件，不删除 | `HD-09` |
| 上游重命名（字节相同，`UPSTREAM_RENAME`） | `STOPPED_CONFLICT` | 保留旧路径，不创建新路径 | `HD-09` |
| 项目自有 / 未知文件（`PROJECT_OWNED` / `UNKNOWN_FILE`） | 可以 `APPLIED` | 只报告，永不覆盖 | 无需（保持原样） |
| 目标版本不高于已安装版本 | `BLOCKED` | 不降级、不重装 | `HD-16` |

冲突逐文件出现在报告的 `classification.files` 中（含 `baseline_sha256` / `current_sha256` / `target_sha256` 与 `notes`），汇总在 `classification.counts`；没有单独的“冲突列表”字段，分类本身就是证据。

### 13.5 无基线 MANIFEST

没有清单时，入口在**联网之前**停止：

- `baseline.baseline_status = MISSING`；
- `result = STOPPED_INVALID_BASELINE`；
- 报告附只读文件清点与手工迁移提示，不读取哈希差异作为覆盖许可；
- 修复路径见第 6 节（从原发行包取回 `MANIFEST.json`，或在同版本干净 checkout 重建）。

不要为了让入口“能跑”而放一份新版本清单到旧树上：那会把所有上游文件误判为本地修改，分类结果失真。

### 13.6 未提交改动

- `PLAN` 只记录 `workspace.dirty_paths`，不写入、不 stash、不 `checkout --`、不 `clean`。
- `APPLY` 遇到脏工作区且能创建 Git worktree 时，隔离到 `.worktrees/update-<report_id>` 的独立分支，原工作区保持不动。
- 无法隔离时 `STOPPED_DIRTY_WORKTREE` + `HD-12`，不冒险在脏工作区继续。
- 无 Git 项目没有“未提交”概念；引擎仍用备份 + 回滚保护，但无法证明工作区干净，`head_sha` 为 `null`。

### 13.7 网络不可用

官方 Release 获取失败（无网络、DNS、超时、限流、资产缺失）时：

- 报告 `result = BLOCKED`；
- `target.verification.status = NOT_RUN`，所有未执行的检查逐条标 `NOT_RUN`；
- 项目零写入，已安装版本保持不变；
- 不得把 `NOT_RUN` 折算成“已检查通过”，也不得回退到本地 zip / 任意 URL / fork。

真实网络成功路径已于 2026-10-26 实测（§14.1 / §14.2）；失败路径用进程级不可达代理强制连接失败，实测 `BLOCKED` + 全部检查 `NOT_RUN`、零写入（§14.3 第 4 行，属受控模拟，不是真实断网）。

### 13.8 回退与回滚失败

| 阶段 | 成功的证据 | 回退方式 |
| --- | --- | --- |
| `PLAN` | `PLANNED`，项目零写入 | 无需回退 |
| `APPLY` 成功 | `APPLIED`，备份 `verified=true`，写后哈希通过 | 从报告 `backup.location`（项目外临时目录）恢复；或按 Git Diff 回退该分支 / Worktree |
| 写入失败 | `ROLLED_BACK`，恢复后逐路径 presence / sha256 校验通过 | 已自动恢复；人工复核备份与报告 |
| 回滚后校验失败 | `BLOCKED`，`rollback.result=FAILED` | `HD-11`：从备份人工恢复 / 保留现场排查 / 重新安装；**不自动重试** |

回滚只用备份与逐路径哈希，不做 `git reset --hard`，也不覆盖项目自有文件。备份位于项目外系统临时目录，重启后可能被清理；需要长期保留时，在审查完成前把 `backup.location` 复制到项目外可持久位置。真实回滚失败场景本任务 `NOT_RUN`（合成测试覆盖）。

### 13.9 edition 结果形态

- `local`：入口返回任务包 + 分支 / Worktree 交付，`carrier=local_task_database`，`writes=[]`；入口**不写**任务库。用 `scripts/orchestrator.py task add/ready/dispatch` 登记后按 G1–G6 审查。
- `github`：入口返回 Issue 正文 + Draft PR payload，`carrier=issue_and_draft_pr`，`writes=[]`；入口**不调用** `gh`。创建 Issue / Draft PR 并回链，走 `PR_OPEN` → `REVIEW_READY` → G1–G6。
- 两者共用同一份 gate 记录（`requires_review=true`、`self_approval_allowed=false`、`auto_merge=false`、冲突与 `HD-*` 列表、报告契约校验结果）；差异只在载体，不在门禁。`local` 不得假装具备 GitHub 自动化，`github` 不得写本地任务库。

### 13.10 自检与退出门

```bash
python3 -m scripts.update_agentorch.entry --help
python3 -m scripts.update_agentorch.entry --project . \
  --request "更新 AgentOrch" --mode PLAN
python3 scripts/validate_kit.py
python3 -m unittest discover -s tests
python3 scripts/build_release.py
```

前两条只证明入口可导入、路由与失败关闭；真正升级命令的 `APPLY` 必须在有合法基线的项目上由 Master 派发的任务执行，并保留报告、命令、退出码与 head SHA。`build_release.py` 的包内自检覆盖入口与模板（自检 i / j），但不访问网络。

### 13.11 下游项目的 validate_kit 模式

手工迁移（1.1 / 1.2 没有 `MANIFEST.json`，见 §13.3）之后，下游项目会同时拥有自己的 `docs/` 与官方 `MANIFEST.json`。`scripts/validate_kit.py` 按树自动选择严格度，汇总行会打印 `tree mode`：

| 树 | 判定依据 | 仓库文档 | `MANIFEST.json` |
| --- | --- | --- | --- |
| 纯净包 | `docs/` 不存在，或只含随包分发的 `REQUIRED` 文件（当前只有 `docs/orchestration.md`） | 跳过 | 结构 + 分类 + 路径 + `REQUIRED` 集合 + SHA-256 / 大小 |
| 下游项目 | 有 `docs/PROJECT.md`，没有 `docs/upgrade.md` | 只要求 `docs/PROJECT.md` 与 `docs/PLAN.md`（后者仍需“设计目标”章节） | 结构 + 分类 + 路径 + `REQUIRED` 集合 + 列出文件存在；**不比较哈希** |
| 上游 checkout | 有 `docs/upgrade.md` | 全部仓库文档 | 结构 + 分类 + 路径 + `REQUIRED` 集合 + SHA-256 / 大小 |

- **`docs/upgrade.md` 是上游标记**：它只存在于上游仓库，不随包发布。下游项目**不要**创建同名文件；一旦出现，`validate_kit.py` 会按上游 checkout 要求全部仓库文档，并把本地修改当哈希漂移报错。
- **下游放宽的只有哈希**：清单结构非法、分类错误、路径非法、`REQUIRED` 少列 / 多列、清单列出的文件缺失，在三种树下都是失败。
- **本地修改仍要记账**：下游自己的改动由 `python3 scripts/check_drift.py <项目根>` 报告（`modified` / `missing` / `unknown`，退出码 `2`），`validate_kit.py` 不再因这些改动失败。两者分工见 `docs/README.md` 第 7、8 节（仓库文档）。
- **升级前先确认模式**：如果 `validate_kit.py` 报出大量上游文档缺失，先检查树里是否误放了 `docs/upgrade.md`，而不是去补齐上游文档。

## 14. “更新 AgentOrch”入口实测记录

### 14.1 真实 Release 下载（#71，2026-10-26 UTC）

环境：Linux 6.12.113（x86_64）、Python 3.13.5、`github` 底座仓库检出；分支 `task/71-real-release-upgrade`，基线 `main@eac7f8edda56d4a707522fcddf01ac03c882cc7a`。下载目录在仓库外，命令 `gh release download --repo ctrlshiftaltdel/AgentOrch`，真实网络、只读。

| # | 资产 | 发布 URL | 大小（字节） | SHA256（`sha256sum`） | 与 Release 记录 |
| --- | --- | --- | --- | --- | --- |
| 1 | `agentorch-20261023.zip`（v1.5） | `https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.5/agentorch-20261023.zip` | 186438 | `5fa4a91854b6b796334d13a6d916c64fa3002028e2d923d00b345fb3454fdc4b` | 一致 |
| 2 | `AgentOrch-1.6.zip`（v1.6） | `https://github.com/ctrlshiftaltdel/AgentOrch/releases/download/v1.6/AgentOrch-1.6.zip` | 434678 | `bc0a64e35f0d70b937a4a40d923f03638376c97af5d3c55026084d12c04f8d57` | 一致 |
| 3 | 签名 / checksum 附件 | — | — | — | `NOT_APPLICABLE`：两个 Release 均只有一个 zip 资产，无 `.sig` / `.asc` / checksum 文件 |

v1.6 的 SHA256 与 #76 发布记录 `bc0a64e3…` 逐字符一致；两个资产的大小与 SHA256 同时和 GitHub API `assets[].digest` / `size` 互证。

### 14.2 真实 v1.5 → v1.6 隔离升级（#71）

在仓库外建立隔离项目：解压真实 `agentorch-20261023.zip`，补 `docs/PROJECT.md`（`edition: local`）与项目自有 `LOCAL-NOTES.md`（155 字节，`sha256=2feec58a…`），`git init` 并提交（fixture commit 只存在于临时项目，不进入本仓库）。升级命令从仓库检出运行，`--project` 指向临时项目：

| # | 步骤 | 命令 | 退出码 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | PLAN | `python3 -m scripts.update_agentorch.entry --project <tmp> --request "更新 AgentOrch" --mode PLAN` | `0` | `PLANNED`；基线 Format 1.5；目标 tag `v1.6`（报告版本归一 `1.6.0`）/ 资产 `AgentOrch-1.6.zip`；10 项包校验 `VERIFIED`；`UPSTREAM_UNCHANGED=69`、`ALREADY_CURRENT=22`、`PROJECT_OWNED=1`、`UNKNOWN_FILE=1`，冲突 / 删除 / 重命名均为 0 |
| 2 | APPLY | `python3 -m scripts.update_agentorch.entry --project <tmp> --request "更新 AgentOrch" --mode APPLY` | `0` | `APPLIED`；写入 68 个包内文件 + `MANIFEST.json` |

APPLY 后的可核对事实：

- 项目根 `MANIFEST.json` 的 `format_version` 从 1.5 变为 **1.6**，`files` 从 33 变为 90；
- 90 / 90 包内文件与解压后的真实 v1.6 包逐字节相同（逐文件 sha256 比对，0 不匹配）；
- 项目自有文件未被覆盖：`docs/PROJECT.md`（289 字节，`sha256=87574391…`）与 `LOCAL-NOTES.md`（155 字节，`sha256=2feec58a…`）前后 `sha256sum -c` 退出码 `0`；
- 备份与回滚字段可定位：`backup.required=true`、`backup.verified=true`、`backup.location=/tmp/agentorch-update-backup-<随机后缀>`（项目外，12 个被备份文件 + `backup.json`）、`backup.created_at` 有值；`rollback.required=false`、`result=NOT_NEEDED`；
- `head_sha_before == head_sha_after`：引擎不提交、不合并，变更留在临时项目工作区，符合设计。

### 14.3 停止 / 隔离路径（真实资产，#71）

每个场景使用独立副本（都从真实 v1.5 包解压），并在运行前后对树内文件（排除 `.git`）做 sha256 快照对比。

| # | 场景 | 命令 | 退出码 | 结果 | 项目写入 |
| --- | --- | --- | --- | --- | --- |
| 1 | 无基线：删除 `MANIFEST.json` 并提交（树干净） | APPLY | `2` | `STOPPED_INVALID_BASELINE`；`baseline_status=MISSING`；未发出任何 `github:` 命令（零网络） | 零写入（35 个文件快照完全一致） |
| 2 | 脏工作区：修改 `AGENTS.md` 未提交 | APPLY | `0` | 按设计隔离：`workspace.action=ISOLATED_BRANCH`，在 `.worktrees/update-UPD-…` 独立分支上 `APPLIED` / Format 1.6；原工作区 `AGENTS.md` 仍未提交、`MANIFEST.json` 仍为 1.5，原树文件（排除 `.worktrees/`）快照完全一致 | 原工作区未被改写；新增 `.worktrees/` 属隔离动作 |
| 3 | 上游冲突：本地修改 `VERSION.md` 并提交 | APPLY | `2` | `STOPPED_CONFLICT` + `HD-10`；`classification.files` 中 `VERSION.md` 为 `THREE_WAY_CONFLICT`，三方哈希均可定位 | 零写入（36 个文件快照完全一致） |
| 4 | 网络失败（受控模拟） | APPLY，进程级 `HTTPS_PROXY` / `HTTP_PROXY` 指向关闭端口 | `3` | `BLOCKED`；`target.verification.status=NOT_RUN`，10 项检查逐条 `NOT_RUN`；已安装版本不变 | 零写入（36 个文件快照完全一致） |

第 4 行是**受控模拟**：无法在不影响认证与环境的前提下制造真实断网，因此只给该子进程设置指向 `127.0.0.1:9` 的代理强制连接失败；未改动凭据、全局代理或系统网络。它只证明连接失败分支，不等同于真实断网 / 限速 / DNS 故障。

### 14.4 合成记录（#52，保留作为历史基线）

环境：Linux、Python 3.13、`github` 底座、分支 `task/52-update-entry-release`。合成项目与合成 Release 在系统临时目录中构造，未访问真实 Release、未使用凭据、未改动本仓库 `.orchestration/`。

| # | 场景 | 命令 / 测试 | 实测退出码 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | 入口帮助可导入 | `python3 -m scripts.update_agentorch.entry --help` | `0` | 打印用法 |
| 2 | 本仓库无基线，失败关闭 | `python3 -m scripts.update_agentorch.entry --project . --request "更新 AgentOrch" --mode PLAN` | `2` | `STOPPED_INVALID_BASELINE`，零写入、零网络 |
| 3 | 触发语 / 非触发 / 歧义路由 | `python3 -m unittest tests.test_update_entry` | `0` | 全部通过 |
| 4 | 1.5 合成项目升级 | `python3 -m unittest tests.update_agentorch.test_entry_release` | `0` | `APPLIED`，项目自有文件逐字节不变 |
| 5 | 1.4 合成项目升级 | 同上 | `0` | `APPLIED`，根 `PLAN.md` 保持原样 |
| 6 | 1.3 风格根 `README.md` | 同上 | `0` | `STOPPED_CONFLICT`，文件未删除，`HD-09` |
| 7 | 冲突 / 无基线 / 脏工作区 / 网络失败 | 同上 | `0` | 分别 `STOPPED_CONFLICT` / `STOPPED_INVALID_BASELINE` / `STOPPED_DIRTY_WORKTREE` / `BLOCKED` |
| 8 | 真实 GitHub Release 获取 | — | `NOT_RUN` | #52 当时未执行；已由 #71 用 §14.1–§14.3 的真实记录补齐，以那三节为准，本行保留 `NOT_RUN` 不改写为通过 |

合成部分只证明合成测试与失败关闭行为；`NOT_RUN` / `UNKNOWN` 项不得当作已支持。真实部分的 `PASS` 只覆盖 §14.1–§14.3 记录的资产、平台与命令。

### 14.5 真实 v1.6.2 → v1.6.3 隔离升级（#113，2026-10-27 UTC）

环境：Linux x86_64、Python 3.13、`github` 底座仓库检出（基线 `main@2adf4319c5826f97c6c98b8fa401473115f9b76f`）。升级对象是仓库外的隔离项目：解压真实 `AgentOrch-1.6.2.zip`（SHA256 `096e1fba…`，`MANIFEST.json` files 90）作为已安装基线，补项目自有 `docs/PROJECT.md`（`edition: local`）、`docs/PLAN.md` 与 `LOCAL-NOTES.md`，`git init` 并提交（fixture commit 只存在于临时项目，不进入本仓库）。目标为 [Release v1.6.3](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.6.3) 的资产 `AgentOrch-1.6.3.zip`（461,829 bytes，SHA256 `dcce5b242997ccac44ee1dea225ca13c2e9af6583dc6bca3d5bdc35acb046885`，与 GitHub API `assets[].digest` 一致）。升级命令从仓库检出运行，`--project` 指向临时项目：

| # | 步骤 | 命令 | 退出码 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | PLAN | `python3 -m scripts.update_agentorch.entry --project <tmp> --request "更新 AgentOrch" --mode PLAN` | `0` | `PLANNED`；目标 tag `v1.6.3`（报告版本 `1.6.3`）/ 资产 `AgentOrch-1.6.3.zip`；10 项包校验全 `PASS`；`PROJECT_OWNED=2`、`UNKNOWN_FILE=1`，冲突 / 删除 / 重命名均为 0 |
| 2 | APPLY | `python3 -m scripts.update_agentorch.entry --project <tmp> --request "更新 AgentOrch" --mode APPLY` | `0` | `APPLIED`；写入 18 个文件（含 `MANIFEST.json` 与新增的 `scripts/check_scope.py`、`tests/test_check_scope.py`），`ALREADY_CURRENT=75` |

APPLY 后的可核对事实：

- 项目根 `MANIFEST.json` 的 `format_version` 保持 **1.6**（Format 1.6 → 1.6），`files` 从 90 变为 **92**；
- 92 / 92 包内文件与解压后的真实 v1.6.3 包逐字节相同（逐文件 sha256 比对，0 不匹配）；
- 项目自有文件未被覆盖：`docs/PROJECT.md`、`docs/PLAN.md`（`PROJECT_OWNED`）与 `LOCAL-NOTES.md`（`UNKNOWN_FILE`）的 SHA256 前后一致；
- 备份与回滚字段可定位：`backup.required=true`、`backup.verified=true`、`backup.location=/tmp/agentorch-update-backup-<随机后缀>`（项目外）、`backup.created_at` 有值；`rollback.required=false`、`result=NOT_NEEDED`；
- `head_sha_before == head_sha_after`：引擎不提交、不合并，变更留在临时项目工作区，符合设计；
- 升级后项目 `python3 scripts/validate_kit.py` 退出码 `0`（`tree mode downstream`）；项目内 `python3 -m unittest discover -s tests` 675 tests `OK`（skip 19）。

本节只覆盖 `1.6.2 → 1.6.3` 这一次真实升级，不改写 §14.1–§14.4；真实 `1.6.0` / `1.6.1` → `1.6.2`、真实 1.3 / 1.4 资产、真实断网 / 限速 / DNS 与 `HD-11` 仍为 `NOT_RUN` / `UNKNOWN`，不得用本节结果替代。

### 14.6 真实 v1.6.3 → v1.7.0 隔离升级（#144，2026-10-27 UTC）

环境：Linux x86_64、Python 3.13.5、`github` 底座仓库检出（基线 `main@ac3e87f2840eb35e6c0e18205990ad38dbc282aa`）。升级对象是仓库外的隔离项目：解压真实 `AgentOrch-1.6.3.zip`（461,829 bytes，SHA256 `dcce5b242997ccac44ee1dea225ca13c2e9af6583dc6bca3d5bdc35acb046885`，`MANIFEST.json` files 92，Format 1.6）作为已安装基线，补项目自有 `docs/PROJECT.md`（`edition: local`）、`docs/PLAN.md` 与 `LOCAL-NOTES.md`，`git init` 并提交（fixture commit 只存在于临时项目，不进入本仓库）。目标为 [Release v1.7.0](https://github.com/ctrlshiftaltdel/AgentOrch/releases/tag/v1.7.0) 的资产 `AgentOrch-1.7.0.zip`（602,273 bytes，SHA256 `cbc35fcf5ddf2aa8c7c799e52c7f5fa83ec8943a7691e1053155cc85988a4c10`，与 GitHub API `assets[].digest` 一致）。升级命令从仓库检出运行，`--project` 指向临时项目：

| # | 步骤 | 命令 | 退出码 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | PLAN | `python3 -m scripts.update_agentorch.entry --project <tmp> --request "更新 AgentOrch" --mode PLAN` | `0` | `PLANNED`；基线 Format 1.6；目标 tag `v1.7.0`（报告版本 `1.7.0`）/ 资产 `AgentOrch-1.7.0.zip`；10 项包校验全 `PASS`；`UPSTREAM_UNCHANGED=37`、`ALREADY_CURRENT=81`、`PROJECT_OWNED=2`、`UNKNOWN_FILE=1`，冲突 / 删除 / 重命名均为 0 |
| 2 | APPLY | `python3 -m scripts.update_agentorch.entry --project <tmp> --request "更新 AgentOrch" --mode APPLY` | `0` | `APPLIED`；写入 37 个文件（含 `MANIFEST.json` 与新增的 25 个 `REQUIRED` 文件），`ALREADY_CURRENT=81` |

APPLY 后的可核对事实：

- 项目根 `MANIFEST.json` 的 `format_version` 从 **1.6** 变为 **1.7**（Format 1.6 → 1.7），`files` 从 92 变为 **117**；
- 117 / 117 包内文件与解压后的真实 v1.7.0 目标包逐字节相同（逐文件 sha256 比对，0 不匹配）；
- 项目自有文件未被覆盖：`docs/PROJECT.md`、`docs/PLAN.md`（`PROJECT_OWNED`）与 `LOCAL-NOTES.md`（`UNKNOWN_FILE`）的 SHA256 前后一致；
- 备份与回滚字段可定位：`backup.required=true`、`backup.verified=true`、`backup.location=/tmp/agentorch-update-backup-<随机后缀>`（项目外）、`backup.created_at` 有值；`rollback.required=false`、`result=NOT_NEEDED`；
- `head_sha_before == head_sha_after`：引擎不提交、不合并，变更留在临时项目工作区，符合设计；
- 升级后项目 `python3 scripts/validate_kit.py` 退出码 `0`（`tree mode downstream`，117 required）；包内 `python3 -m unittest discover -s tests` 968 tests `OK`（skip 19）。

本节的命令与结果来自 2026-10-27 派发会话的真实运行记录（证据见 Issue #144）；记录发布后文档的同步任务未重跑该升级。本节只覆盖 `1.6.3 → 1.7.0` 这一次真实升级，不改写 §14.1–§14.5；真实 `1.6.0` / `1.6.1` → `1.6.2` 升级、真实 1.3 / 1.4 资产、真实断网 / 限速 / DNS、`HD-11` 与非 GitHub-hosted 平台仍为 `NOT_RUN` / `UNKNOWN`，不得用本节结果替代。

### 14.7 已发布 v1.7.0 旧客户端对不升 Format MINOR 的失败关闭（#193，2026-10-31 UTC）

环境：Linux x86_64、Python 3.12.15（`uv run python`）、`github` 底座仓库检出；下载与运行目录都在仓库外（`/tmp`）。没有打 tag、没有创建 Release、没有改动本仓库 `.orchestration/`。

1. 下载真实已发布资产：`AgentOrch-1.7.0.zip`，602,273 bytes，SHA256 `cbc35fcf5ddf2aa8c7c799e52c7f5fa83ec8943a7691e1053155cc85988a4c10`（与 Release `assets[].digest` 逐字符一致），解压到仓库外隔离目录。
2. 从解压目录导入 `scripts/update_agentorch/`，确认 `engine.__file__` / `package.__file__` 指向该解压目录，且该版本的 `versions` **没有** `format_compatibility`（证明执行的是已发布旧代码，不是本分支的新解析器）。
3. 在仓库外构造合成项目：基线 `MANIFEST.json` 的 `format_version` 为 `1.7`；目标包为合成 zip，清单 `format_version` 也为 `1.7`，内存 `ReleaseInfo` 的 tag 分别为 `v1.8.0`（负向）与 `v1.7.1`（正控），资产摘要按实际字节计算。用旧客户端的 `engine.run_update(..., mode='PLAN', source=<内存源>, edition='github')` 运行。

| # | 旧客户端 | 目标 tag + Format | 结果 | `VERSION_MATCH` | 项目写入 |
| --- | --- | --- | --- | --- | --- |
| 1 | 已发布 v1.7.0 代码 | `v1.8.0` + `1.7` | `STOPPED_UNSAFE_PACKAGE` | `FAIL`，旧客户端原文 `release tag 'v1.8.0' does not match manifest format_version '1.7'` | 零写入（前后逐文件字节快照一致） |
| 2 | 已发布 v1.7.0 代码（正控） | `v1.7.1` + `1.7` | `PLANNED` | `PASS` | 零写入 |

**范围与未验证项**：目标包是合成的，运行方式是 `PLAN` + 内存 Release 源，不是旧客户端到真实 `v1.8.0` Release 的完整 CLI / 网络升级；它也**不是**“旧客户端安装桥接包后成功升级”的证据。桥接发行需要自身仍满足旧规则（同 Format），本任务只实现与测试桥接、**不发布桥接包**，因此“旧客户端安装桥接 → 新更新器升级不升 Format MINOR”的端到端为 `NOT_RUN`。真实旧客户端对不升 Format MINOR 的拒绝已由第 1 行实测。

## 15. 迁移八（本次新增）：核心编排契约从 `kit/` 迁到 `docs/`

- **变了什么**：`kit/orchestration.md` 移到 `docs/orchestration.md`，仍是**唯一可编辑权威**；`kit/` 目录随之消失，发行包里不再有 `kit/`。契约正文的语义（会话入口、状态机、合并门 G1–G6、C1–C4 / C1–C8、证据矩阵）不变，只改位置与指向它的引用。发行包从此只包含 `docs/` 下的这一个文件；其余 `docs/` 内容（设计稿、阶段计划、本文件）仍不打包。
- **怎么改**（下游项目；本套件自身的同一迁移已在本任务执行，全部为 `git mv` + `rmdir kit`）：

  ```bash
  # 1. 先确认旧契约没有本地修改；有修改就先合并 / 备份，不要直接删
  git diff -- kit/orchestration.md
  git rm kit/orchestration.md
  # 2. 重新运行更新入口；引擎随后写入 docs/orchestration.md
  uv run python -m scripts.update_agentorch.entry --project . \
    --request "更新 AgentOrch" --mode APPLY
  ```

- **更新引擎会怎么做**：旧副本还在时，入口以 `STOPPED_CONFLICT` + `HD-09` 停止并零写入，不会把新旧两份并排写出来；`docs/orchestration.md` 已存在且与包内不一致时以 `HD-10` 停止，不覆盖本地文件。旧副本即使未被本地修改也不会被静默删除——删除是明确的人工动作。
- **怎么自检**：

  ```bash
  uv run python scripts/validate_kit.py    # 0：核心契约在 docs/orchestration.md
  test ! -e kit/orchestration.md           # 0：旧路径已不存在
  uv run python scripts/build_release.py   # 0：包内只有 docs/orchestration.md 一个 docs/ 文件
  ```

- **不要这样做**：同时保留 `kit/orchestration.md` 与 `docs/orchestration.md`（两个权威）；把整棵 `docs/` 当成随包内容；只复制旧文件到新路径而不改引用（`validate_kit.py` 的链接扫描会逐个指出断链）。
