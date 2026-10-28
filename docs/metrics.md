# 能力登记与交付质量度量

本文说明两件常被混为一谈的事：**这个运行时到底能做什么**（能力登记），以及**这段时间的交付过程表现如何**（交付度量）。两者的定义分别在 [templates/runtime-capability.md](../templates/runtime-capability.md) 与 [templates/metrics.md](../templates/metrics.md)；本文只解释怎么用、边界在哪、哪些数字暂时还算不出来。§7 是 2026-10-28 的 A 阶段只读基线快照（Issue #185），同样只记录观测、不改任何门禁。

两者都不改变状态机、G1–G6、C1–C4 或 `VERIFIED` 的语义。它们只把“已经被观察到的事实”和“已经被记录的事件”整理成可追溯的记录。

## 1. 为什么需要能力登记

C1–C4（[kit/orchestration.md](../kit/orchestration.md) 第 5 节）回答的是“能不能真实 spawn / 隔离 / wait-collect / 区分终局”。但调度还会被更细的边界卡住：

- spawn 返回的句柄能不能再次定位？失败的 spawn 与排队的 spawn 能不能区分？
- 两个实例是否共享工作目录、环境变量、凭据？
- wait 超时后，状态、产物、证据三者还能取回几个？
- 取消之后留下什么（分支、锁、临时文件）？取消会不会被记成“成功”？
- 执行实例能写哪些路径、能跑哪些命令、能不能读 CI 日志或触发运行？
- 工具链与测试环境是否可复现？两个实例会不会抢同一个测试库？

这些答案过去散落在会话里，换个人就得重问一遍。登记的用途是：**任何人在派发前能查到结论与证据，而不是重新猜**。

### 填写规则

1. **一个组合一份记录**：运行时 + 精确版本 + 底座 + 执行模式。版本变了就是新记录，不能在同一份记录里悄悄覆盖。
2. **只有 `PASS` 才代表已验证**。没探测、证据过期、链接失效、结果含糊，都写 `UNKNOWN`。
3. **先降级再重测**。运行时升级、底座切换、证据载体变化时，先把受影响能力改为 `UNKNOWN`，再重新探测；不要一边留着旧的 `PASS` 一边说“正在复测”。
4. **`PASS` 不是授权**。记录“观察到能写文件”不等于允许本次任务写那些文件；允许路径由任务包决定，权限由项目访问控制决定，CI 是否通过由 G4 决定。
5. **不存敏感内容**。用任务 ID / URL / commit SHA 定位证据，不要贴令牌、会话信息或提示词原文。

### 路由影响

C1–C4 任一条不是 `PASS`，就**不能**声称 Master 已真实派发并推进到 `DISPATCHED`；必须降级为人工交接，并在任务记录里写清缺失能力、证据与降级方式。`C5`–`C8` 为 `UNKNOWN` 时仍可走人工交接，但超时、取消与清理责任落在操作者身上，且必须写进 `Limits`。

## 2. 为什么需要交付度量

“这个阶段做得怎么样”通常被回答成印象。度量的作用是把这个问题变成可核对的数字，并且**明确说出这些数字的局限**。七项指标的口径（起止事件、时区、排除项、缺失处理）只在 [templates/metrics.md](../templates/metrics.md) 定义，本文不重述。

### 怎么跑

```bash
# 固定合成样本（含成功、失败、取消、阻塞、重试、缺事件）
python scripts/report_metrics.py --gold-sample --since 2026-01-01 --until 2026-02-01

# 真实 local 任务库（只读打开，不会改动数据库）
python scripts/report_metrics.py .orchestration/state.sqlite3 --since 2026-01-01 --until 2026-02-01

# 机器可读：字段名与顺序稳定
python scripts/report_metrics.py .orchestration/state.sqlite3 --json
```

退出码：`0` 表示报告已产出（包括“无数据”），`2` 表示来源不可读。**“无数据”是结果，不是错误。**

### 三类必须出现的上下文

任何一个被引用的数字都要带：

| 上下文 | 含义 | 缺了会怎样 |
| --- | --- | --- |
| 样本量 | 真正参与计算的任务数 | 2 个样本的中位数会被读成“普遍水平” |
| 统计窗口 | UTC 半开区间 `[start, end)` | 同样的数据换个窗口就是另一个数 |
| 缺失率 | `missing / eligible` | 把“没记录”读成“很快 / 通过 / 无冲突” |

报告另外给出 `coverage = sample / eligible`。覆盖率低时数字仍然打印，但不能拿来做结论。

## 3. 当前数据源能算出什么

| 指标 | `local` 任务库 | `github`（Issue / PR 时间线） |
| --- | --- | --- |
| M1 READY→VERIFIED | 可算（状态事件） | 可算（Issue / PR 状态与评论按同一事件名登记后） |
| M2 首次检查通过率 | **未采集**：任务库不记检查结果 | 需先把 CI 结论登记为 `CHECK_RECORDED` |
| M3 审查修复轮数 | 可算（`MASTER_REVIEW` 退回 + `APPROVAL_INVALIDATED`） | 可算（PR 审查轮次登记后） |
| M4 路径 / 资源冲突 | 可算：`task dispatch` 拒绝路径锁时在 `events` 写入 `CONFLICT_RECORDED`（`data.kind=path`），资源冲突从 `resource_lock_events` 的 `LOCK_DENIED_CONFLICT` 读出；`task ready` 预检拒绝另记 `PATH_PREFLIGHT_REFUSED` 供审计，不计入 M4。旧任务库没有 path 事件时该部分仍为 `unknown` | 需登记被拒绝的派发 |
| M5 阻塞时长 | 可算（`BLOCKED` 区间） | 可算 |
| M6 合并后回归缺陷 | **未采集**（没有回归事件） | 需把回归单登记为 `REGRESSION_REPORTED` |
| M7 端到端验收通过率 | **未采集**（证据矩阵不在任务库里） | 需把验收行登记为 `ACCEPTANCE_RECORDED` |

**这是原型，不是已完成的自动采集。** 未采集的项在报告里显示 `unknown`，不显示 `0`。要在真实项目里把它们变成 `ok`，需要先让对应事件真的被写入任务载体——那是另一个任务，本文不宣称已经做到。

## 4. 读数的四条纪律

1. **不排名**。这些指标描述的是某个项目某段时间的流程，不是 Agent、模型、人或运行时的排行榜。任何“A 比 B 强”的结论都超出了数据能支持的范围。
2. **不跨项目比较**。任务粒度、验收标准、CI 时长都不同，跨项目比较得到的是口径差异，不是质量差异。
3. **不从聊天补齐**。缺少事件就是 `missing`；用会话记忆补一个数字会让整份报告失去可核对性。
4. **口径改动要登记**。改一个起止事件或排除项会改变全部历史数字；先记录日期与理由，再发新报告。

## 5. 与既有契约的关系

| 既有契约 | 本机制的关系 |
| --- | --- |
| C1–C4（第 5 节） | 能力登记是它的记录载体，不新增、不改写契约；`UNKNOWN` 不解除 C1–C4 的要求 |
| 证据矩阵 4.1 | 指标只引用证据；不替代逐条验收，也不能用聚合数字宣称某条 AC 通过 |
| G1–G6 | 度量不参与门禁判定；G4 仍以当前 head 的 CI 为准 |
| 状态机与锁 | 度量只读事件，不写状态、不释放锁、不改 `retries` |

## 6. 已知限制

- 样本量小：十几个任务的项目，任何比率都只是这几周的情况。
- 墙钟时长：等待跨夜与等待审查都算小时，不能换算成“工作量”。
- 观察窗：合并后 14 天内没有回归报告才算“干净”；刚合并的任务记为 `missing`，不记为“无回归”。
- 事件口径依赖载体：`local` 与 `github` 必须以同一组事件名登记，否则两份报告不可比。
- 未采集项：M2 / M6 / M7 在 `local` 上仍是 `unknown`，需要先补齐事件写入；M4 的路径冲突与资源冲突自路径 / 资源事件落地起可采集，但旧任务库没有 path 事件时该部分仍是 `unknown`，不补 `0`。

## 7. A 阶段基线快照：验证成本与门禁审计（2026-10-28，Issue #185）

本节是一次**只读**审计快照，服务于 AVE A 阶段（[设计提案](design/adaptive-verification-and-quality-gates.md) §8 / §9.1，状态仍为 `PROPOSED`）。它做三件事：列出测量时生效的必需检查与 G1–G6 证据映射（§7.2、§7.3），登记可复核的成本基线与逐项来源（§7.4–§7.6），并给出在途冲突表与 go/no-go 建议（§7.7–§7.9）。

**本节不改变任何东西**：不改 M1–M7 的定义（唯一权威是 [templates/metrics.md](../templates/metrics.md)）、不改 required checks / G1–G6 / CI / 状态机 / 锁 / 权限 / Reviewer 身份 / 发布行为，也不改测试强度。本节里的数字**不是 M1–M7**：GitHub 载体上没有任何 `STATUS_CHANGED` / `CHECK_RECORDED` / `ACCEPTANCE_RECORDED` / `REGRESSION_REPORTED` 事件登记，按模板口径 M1–M7 在本底座上仍是 `unknown`（见 §3 表与 §7.6）。下文所有数字都是 GitHub 原生的观测代理，并在每处写明样本量与边界，**不得**被引用成七项交付指标。

### 7.1 快照元数据与复现方法

| 项 | 值 |
| --- | --- |
| 采集时刻 | `2026-10-28T21:47Z`（同一会话内一次性采集；运行 ID、SHA 均为当时值） |
| 统计窗口 | UTC 半开区间 `[2026-10-22T00:00:00Z, 2026-10-28T21:47:00Z)`（约 6.9 天）；窗口终点即采集时刻 |
| 基线 main | `953c194e9b6ba53a150214775ec793678442575b`（`origin/main`，PR #200 合并） |
| 底座 / 载体 | `edition=github`；来源为公开 GitHub Issue / PR / Actions 记录 |
| 不使用的来源 | `.orchestration/state.sqlite3` 是 `local` 底座的只读归档，不是活动任务记录；`scripts/report_metrics.py` 只读 SQLite / fixture，**不消费 GitHub 时间线**（该脚本内无 `github` / `http` 字样，本任务以 grep 复核，命中 0 处），其输出不得冒充本节实测 |
| 环境 | Linux 6.12.113+deb13-amd64 x86_64；Python 3.13.5；uv 0.13.6；gh 2.103.3；单机、单 Owner、单仓库 |
| 任务样本总体 | 窗口内**全部**已合并 PR：n = 80（不抽样） |
| CI 样本总体 | 窗口内**全部** Actions 运行：n = 219（不抽样） |
| 逐项明细样本 | 33 个任务 + 14 个失败运行，选择方法见 §7.5 开头（确定性规则，可复算） |
| 缺失处理 | 未采集 / 不可归因一律写 `UNKNOWN`，不补 `0`（§7.6） |

复现命令（均为本任务在本机真实执行、退出码 0 的命令；GitHub 端计数会随仓库继续推进而变化，复核时以相同窗口过滤）：

```bash
# 已合并 PR 总数（本快照：80）
gh api --paginate "repos/ctrlshiftaltdel/AgentOrch/pulls?state=closed&per_page=100" \
  --jq '[.[] | select(.merged_at != null)] | length'

# Actions 运行总数与结论分布（本快照：219 / success 205 / failure 14）
gh api --paginate "repos/ctrlshiftaltdel/AgentOrch/actions/runs?per_page=100" \
  --jq '.workflow_runs[] | [.id,.created_at,.updated_at,.event,.conclusion,.head_sha,.head_branch] | @tsv'

# 平台强制的 required status checks（本快照：2 个 ubuntu context）
gh api repos/ctrlshiftaltdel/AgentOrch/rulesets/25644959 \
  --jq '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context'

# 单个运行 → 作业级结论与计时（job-minutes 的原始数据）
gh api "repos/ctrlshiftaltdel/AgentOrch/actions/runs/<run_id>/jobs?per_page=100" \
  --jq '.jobs[] | [.run_id,.name,.conclusion,.started_at,(.completed_at // "null"),.run_attempt] | @tsv'

# 检查定义与文档的版本定位（blob 而非可变路径）
git rev-parse 953c194:.github/workflows/ci.yml      # d042171c106c5ff270880a9eadbf03e43aec5297
git rev-parse 953c194:kit/orchestration.md          # c2d451e43efca6b1cd89f700f83c3943dc30ed07
git rev-parse 953c194:templates/metrics.md          # dee47b6b717270b2d112ffa3bf0d0038828f606c
```

`gh run list --limit 500 --json ...` 等价可用，但在本次采集中出现过一次网络 `EOF`（退出码 1，重试后 0）；因此复现以 `gh api` 形式为准。

### 7.2 测量时有效的必需检查及其来源（AC-1）

「必需检查」在三个层次上不是同一个集合，测量时分开记录，避免把其中一层当成全部：

| 层次 | 检查 / 上下文 | 来源（快照版本） | 触发条件 | 平台是否强制 |
| --- | --- | --- | --- | --- |
| 平台强制 required status check | `checks (Python 3.12)` | 仓库 ruleset「Default」(id `25644959`，`enforcement=active`，`target=branch`，`ref_name.include=~DEFAULT_BRANCH`)，采集时读取 | `pull_request` → `main` | **是**：缺失则 PR 不满足合并条件 |
| 平台强制 required status check | `checks (Python 3.13)` | 同上 | 同上 | **是** |
| CI 作业（工作流定义） | `checks (Python 3.12)`、`checks (Python 3.13)`、`checks-macos (Python 3.13)`、`checks-windows (Python 3.13)`（前两个在 `ubuntu-latest`，第三个在 `macos-latest`，第四个在 `windows-latest`；每个作业跑同一组步骤） | `.github/workflows/ci.yml`（blob `d042171c…` @ `953c194`） | `pull_request`→`main`、`push`→`main`、`workflow_dispatch` | 只有前两个在平台层强制；macOS / Windows 的绿由契约 G4 与审查要求覆盖，**不由分支保护强制** |
| 每个作业内的 7 个步骤 | ① `scripts/validate_kit.py` ② `scripts/architecture/check.py --project . --json`（诊断模式） ③ `unittest discover -s tests` ④ `scripts/run_delivery_scenarios.py --json` ⑤ `scripts/report_metrics.py --gold-sample …` ⑥ 语法检查（`py_compile`：10 个显式文件 + `scripts/update_agentorch/*.py` + `scripts/self_improvement/*.py`） ⑦ `scripts/build_release.py` 自检 | 同上 | 同上 | 随所属作业；无 `continue-on-error`、无 `\|\| true` |
| 契约层（本地补充证据） | `uv run python scripts/validate_kit.py`、`uv run python -m unittest discover -s tests`、`python3 -m py_compile scripts/orchestrator.py scripts/validate_kit.py`；合并后另加 `python scripts/orchestrator.py backup` | [docs/PROJECT.md](PROJECT.md)「验证命令」；`docs/PLAN.md` §6 | 任务交付 / 合并后 | 不是平台门，但 `local` 底座必须执行，`github` 底座作为补充证据 |
| 平台其它合并约束（非检查项） | 禁止删除分支、禁止 non-fast-forward、`pull_request` 规则（`required_approving_review_count=0`、`strict_required_status_checks_policy=false`、允许 merge / squash / rebase） | 同一 ruleset | `main` | 是；注意**平台不要求审批数、也不要求分支与 main 同步**，这两条仍由契约（G1 / G3、Reviewer 独立性）承担 |

测量时观察到的两处**文档与配置不一致**（只登记，不在本任务修改）：

1. `docs/PLAN.md` §5 写「CI 为 `ubuntu-latest` + Python 3.12 / 3.13」，但 `ci.yml` 自 `2026-10-26T14:14:29Z` 起同时运行 macOS 与 Windows 作业（首个 4 作业运行是 PR #79 的 head `40337792930`）；该句未同步。仓内 219 个运行中有 81 个只有 2 个 ubuntu 作业、137 个有 4 个作业。
2. 平台 required checks 只包含 2 个 ubuntu context，而 `kit/orchestration.md` 4.1 的 G4 要求「CI 工作流在当前 head SHA 全绿」——即 macOS / Windows 的绿是**契约要求**而非平台强制。两者都有效，但引用时必须说明是哪一种，不能写「四平台是平台必需检查」。

### 7.3 现行 G1–G6 在 GitHub 载体上的证据映射（AC-1）

门禁定义只以 [kit/orchestration.md](../kit/orchestration.md) §4 与 4.1 为准，本节只记录「在 GitHub 载体上这些证据实际长什么样、本次能算到什么程度」。

| 门 | 契约要求（引用） | GitHub 载体上的实际证据 | 本快照可计算性 |
| --- | --- | --- | --- |
| G1 | 每条 AC 都有可定位证据；`NOT_RUN` / `BLOCKED` 不计通过 | Issue 验收清单 + PR 评论里的九列矩阵；载体是**自由文本评论** | PR 评论覆盖 80/80 个已合并 PR，合计 248 条；GitHub 正式 review 记录 **0** 条。评论未被解析成 `ACCEPTANCE_RECORDED` 事件，**逐 AC 通过率 `UNKNOWN`** |
| G2 | 变更路径全部属于允许路径 | PR `changed_files` 计数 + Issue 正文里的允许路径（非机器字段） | `changed_files` 可算（n=80，median 4，max 61）；「是否越界」需人读比对，本快照**未逐 PR 复核** → `UNKNOWN` |
| G3 | 基线最新、无冲突、可合并 | PR `mergeable` / base SHA；平台 `strict_required_status_checks_policy=false` 表示平台**不**要求分支追平 main | 快照时 0 个开放 PR；80/80 已合并。逐 PR 的「base 是否为合并时刻最新 main」未复核 → 部分可观测 |
| G4 | 当前 head 的必需检查全绿 | Actions 运行结论 + 作业结论，run 绑定 `head_sha` | 可算（见 §7.4）：77 个有 CI 的合并 PR 在合并 head 上全部绿；3 个 PR 无任何 CI；1 个 head 的运行**没有产生作业记录** → 该 head `UNKNOWN` |
| G5 | 命令 / 输出 / 退出码与 head 一致 | 每个运行绑定一个 `head_sha`；作业级 710 条记录带计时 | 部分可算：219 个运行对应 219 个唯一 head（不存在同 SHA 重跑）；4 个运行内部有 7 个作业在 `attempt=2` 重跑。**作业日志正文未存档**，逐步输出不可复核 → 部分 `UNKNOWN` |
| G6 | 新依赖 / 迁移 / 权限 / 安全 / 不可逆动作已登记或升级 | PR 风险节 + Issue 决策记录，均为自由文本 | 未做覆盖率统计 → `UNKNOWN` |

### 7.4 成本基线：任务与 CI 普查（AC-2）

**任务普查**（窗口内全部已合并 PR，n=80）：

| 观测量 | 值 | 说明 |
| --- | --- | --- |
| 已合并 PR / 关闭但未合并 PR | 80 / 0 | 窗口内没有「开了又放弃」的 PR，因此「废弃分支成本」在本窗口**不可观测** |
| 合并前存在时长（created→merged） | median 0.16 h（≈9.6 min）；mean 0.68 h；p90 1.36 h；max 11.56 h | PR 是**最后一个**环节的时长，不含 `READY` 前的工作；**不是 M1** |
| 变更文件数 | median 4；mean 7.0；max 61 | `changed_files` |
| 变更行数（add+del） | median 192；mean 818；合计 65 483 | — |
| 提交数 / PR | median 1；mean 1.86；max 7（n=79；PR #92 以 squash 合并、无 merge commit，未计入） | 修复轮次的**下界代理**，不是 M3 |
| head 修订数（每 PR 的 `pull_request` 运行数） | median 1；mean 1.84；max 7 | 运行与 head 一一对应 |
| 有 CI 证据的合并 PR | 77/80（96.25%） | 缺的 3 个是 PR #10 / #11 / #12，合并于 CI 引入（PR #16，`10-22T13:50Z`）之前 → 结构性缺失，不是漏采 |
| 多平台（4 作业）证据覆盖的合并 head | 45/80（56.25%） | 45 个全部在 `2026-10-26T14:38:59Z` 之后合并；32 个只有 2 作业证据，1 个混合 |
| 关闭的非 PR Issue | 84（其中 `status:verified` 60，无该标签 24） | 标签不带时间戳，`Issue closed` 只能当**闭环代理**：60 个 verified Issue 的 created→closed 为 median 1.05 h / mean 5.01 h / max 48.19 h，含规划时间，**不是 M1** |
| 开放的非 PR Issue | 36 | 见 §7.7 |

**CI 普查**（窗口内全部运行，n=219）：

| 观测量 | 值 | 说明 |
| --- | --- | --- |
| 运行数 | 219（`pull_request` 142 / `push`(main) 77） | 窗口内没有 `workflow_dispatch` 触发的运行 |
| 运行结论 | success 205 / failure 14；运行级 `cancelled` 0 | 失败明细见 §7.5 表 S2 |
| 作业记录 | 710（success 688 / failure 20 / cancelled 2）；1 个运行**没有任何作业记录** | 作业数不等于运行数 × 4：平台矩阵随窗口变化 |
| 作业分钟（job-minutes，按 `started_at`→`completed_at` 求和） | 合计 918.5；PR 端 590.9；main push 端 327.5 | 只统计真的启动过的作业；不是计费分钟 |
| 运行墙钟 | median 2.87 min；mean 3.26 min；max 87.10 min | max 即那个无作业记录的运行 |
| 每运行作业分钟（按作业数拆开） | 2 作业运行：mean 0.80（n=81，合计 64.9）；4 作业运行：mean 6.23 / median 6.03（n=137，合计 853.5） | 窗口内 CI 形态从 2 作业变为 4 作业，合并成一个统计量会失真；只给混合值（median 5.07 / mean 4.21）会误导 |
| 每合并任务 CI 成本 | PR 端 ≈ 7.4 job-min + 合并后 main 端 ≈ 4.25 job-min ≈ **11.6 job-min / 任务** | 该均值混合了两个时代；4 作业时代单任务 PR 端实际约 10–40 job-min（见 §7.5 的 S17/S18/S31/S33）。80 个任务、6.9 天，合计 15.3 job-hours |
| 首次 CI 通过率 | 71/77 = **92.21%**（分母只含「至少有 1 次 CI 运行」的合并 PR；3 个无 CI 的 PR 不并入分母，也不记作通过） | 代理值，**不是 M2**：模板要求 `CHECK_RECORDED` 事件，本底座未登记 |
| ≥1 次失败运行的 PR | 6/77 = **7.79%** | 即 8 个中有 1 个不到 |
| 合并 head 上 CI 全绿 | 77/77 = 100% | 每个有 CI 的 PR 合并前最后一次运行都在**合并 head** 上且为 success；`failures` 全部发生在更早的 head |
| 作业重跑（`attempt=2`） | 4 个运行 / 7 个作业（占运行 1.83%） | 其中 1 个重跑后仍失败，随后被新 head 取代 |

**job-minutes 按作业类型分布**（合计 918.5，分母 710 条作业记录）：

| 作业 | job-min | 占比 | 单作业均值 |
| --- | --- | --- | --- |
| `checks-windows (Python 3.13)` | 468.8 | 51.0% | 3.42 min |
| `checks (Python 3.12)` + `checks (Python 3.13)`（ubuntu） | 135.8 + 138.0 = 273.8 | 29.8% | 0.62 / 0.63 min |
| `checks-macos (Python 3.13)` | 175.9 | 19.1% | 1.28 min |

**本机侧检查耗时**（同一任务环境实测，单次样本，不能当作分布）：

| 命令 | 退出码 | 墙钟 |
| --- | --- | --- |
| `uv run python scripts/validate_kit.py` | 0 | 0.19 s（124 个必需文件） |
| `uv run python -m unittest discover -s tests` | 0 | 21.9 s（`Ran 1050 tests`，`OK`） |
| `python3 -m py_compile scripts/orchestrator.py scripts/validate_kit.py` | 0 | 0.10 s |

> `scripts/report_metrics.py --gold-sample --since 2026-01-01 --until 2026-02-01 --json` 在本机同样退出 0，`7/7 metrics with data`、`overall_coverage 0.7732`。那是**合成 fixture 自检**，与本节 GitHub 实测无关，禁止混引（见模板 §Gold sample）。

### 7.5 逐项样本与来源（AC-2）

**选择方法**（确定性、可复算）：在 80 个已合并 PR 按 `merged_at` 升序排列后，取①每第 3 个（第 1、4、7… 位，共 27 个）∪②所有「≥1 次失败运行」的 PR ∪③所有「无任何 CI 运行」的 PR，去重后得 33 个任务（30 个有 CI）。该并集规则同时避免「只抽成功的」与「只抽失败的」两种偏差；CI 失败事件另行全量列出（表 S2，14 条），不抽样。

表 S1 列：`历时 h` = created→merged；`提交` = PR 内的 commit 数（PR #92 除外）；`head 修订` = 该分支的 `pull_request` 运行数（= 不同 head）；`首次 CI` = 该 PR 第一次运行的结论；`合并 head CI` = 合并 head 上最后一次运行是否 success；`CI job-min` = 该 PR 全部运行里实际启动作业的分钟和。`任务 / Issue` 列：`task/<n>-*` 分支取 `<n>`；其它分支按 PR 正文的关联声明取值（PR #10→#1、#33→#32、#40→#39、#42→#41、#54→#47、#159→#169、#168→#161、#182→#176、#190→#184、#196→#191），设计提案 PR 关联到 Epic。

| # | 任务 / Issue | PR | 合并 (UTC) | 历时 h | 文件 | 变更行 | 提交 | head 修订 | 首次 CI | 失败运行 | 合并 head CI | CI job-min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S01 | [#1](https://github.com/ctrlshiftaltdel/AgentOrch/issues/1) | [PR #10](https://github.com/ctrlshiftaltdel/AgentOrch/pull/10) | 10-22 09:07 | 0.74 | 1 | 224 | 1 | 0 | 无 CI | — | 无 CI | 0.0 |
| S02 | [#2](https://github.com/ctrlshiftaltdel/AgentOrch/issues/2) | [PR #11](https://github.com/ctrlshiftaltdel/AgentOrch/pull/11) | 10-22 09:34 | 0.17 | 8 | 1023 | 3 | 0 | 无 CI | — | 无 CI | 0.0 |
| S03 | [#4](https://github.com/ctrlshiftaltdel/AgentOrch/issues/4) | [PR #12](https://github.com/ctrlshiftaltdel/AgentOrch/pull/12) | 10-22 10:32 | 0.12 | 7 | 436 | 2 | 0 | 无 CI | — | 无 CI | 0.0 |
| S04 | [#14](https://github.com/ctrlshiftaltdel/AgentOrch/issues/14) | [PR #16](https://github.com/ctrlshiftaltdel/AgentOrch/pull/16) | 10-22 13:50 | 0.17 | 3 | 66 | 2 | 2 | PASS | — | PASS | 0.5 |
| S05 | [#5](https://github.com/ctrlshiftaltdel/AgentOrch/issues/5) | [PR #21](https://github.com/ctrlshiftaltdel/AgentOrch/pull/21) | 10-22 15:45 | 0.09 | 4 | 477 | 1 | 1 | PASS | — | PASS | 0.2 |
| S06 | [#3](https://github.com/ctrlshiftaltdel/AgentOrch/issues/3) | [PR #24](https://github.com/ctrlshiftaltdel/AgentOrch/pull/24) | 10-22 16:43 | 0.16 | 9 | 534 | 1 | 1 | PASS | — | PASS | 0.5 |
| S07 | [#13](https://github.com/ctrlshiftaltdel/AgentOrch/issues/13) | [PR #27](https://github.com/ctrlshiftaltdel/AgentOrch/pull/27) | 10-22 17:23 | 0.02 | 2 | 602 | 1 | 1 | PASS | — | PASS | 0.8 |
| S08 | [#32](https://github.com/ctrlshiftaltdel/AgentOrch/issues/32) | [PR #33](https://github.com/ctrlshiftaltdel/AgentOrch/pull/33) | 10-23 01:19 | 0.02 | 22 | 310 | 1 | 1 | PASS | — | PASS | 0.9 |
| S09 | [#39](https://github.com/ctrlshiftaltdel/AgentOrch/issues/39) | [PR #40](https://github.com/ctrlshiftaltdel/AgentOrch/pull/40) | 10-23 02:19 | 0.10 | 6 | 171 | 1 | 1 | PASS | — | PASS | 1.1 |
| S10 | [#41](https://github.com/ctrlshiftaltdel/AgentOrch/issues/41) | [PR #42](https://github.com/ctrlshiftaltdel/AgentOrch/pull/42) | 10-23 03:42 | 0.74 | 61 | 2820 | 7 | 7 | FAIL | [39892424347](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/39892424347), [39892885875](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/39892885875), [39893426645](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/39893426645), [39894195638](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/39894195638), [39894465187](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/39894465187), [39895203291](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/39895203291) | PASS | 3.8 |
| S11 | [#47](https://github.com/ctrlshiftaltdel/AgentOrch/issues/47) | [PR #54](https://github.com/ctrlshiftaltdel/AgentOrch/pull/54) | 10-26 03:51 | 11.56 | 2 | 285 | 3 | 2 | PASS | — | PASS | 1.8 |
| S12 | [#48](https://github.com/ctrlshiftaltdel/AgentOrch/issues/48) | [PR #59](https://github.com/ctrlshiftaltdel/AgentOrch/pull/59) | 10-26 04:57 | 0.04 | 20 | 2943 | 1 | 1 | PASS | — | PASS | 1.1 |
| S13 | [#51](https://github.com/ctrlshiftaltdel/AgentOrch/issues/51) | [PR #60](https://github.com/ctrlshiftaltdel/AgentOrch/pull/60) | 10-26 05:49 | 0.65 | 19 | 4337 | 4 | 3 | FAIL | [40272804851](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40272804851) | PASS | 2.7 |
| S14 | [#55](https://github.com/ctrlshiftaltdel/AgentOrch/issues/55) | [PR #63](https://github.com/ctrlshiftaltdel/AgentOrch/pull/63) | 10-26 06:14 | 0.06 | 6 | 919 | 1 | 1 | PASS | — | PASS | 1.1 |
| S15 | [#66](https://github.com/ctrlshiftaltdel/AgentOrch/issues/66) | [PR #67](https://github.com/ctrlshiftaltdel/AgentOrch/pull/67) | 10-26 08:19 | 0.83 | 1 | 15 | 1 | 1 | PASS | — | PASS | 1.1 |
| S16 | [#53](https://github.com/ctrlshiftaltdel/AgentOrch/issues/53) | [PR #70](https://github.com/ctrlshiftaltdel/AgentOrch/pull/70) | 10-26 12:12 | 0.17 | 10 | 1823 | 1 | 1 | PASS | — | PASS | 1.2 |
| S17 | [#77](https://github.com/ctrlshiftaltdel/AgentOrch/issues/77) | [PR #79](https://github.com/ctrlshiftaltdel/AgentOrch/pull/79) | 10-26 14:38 | 0.41 | 8 | 178 | 4 | 4 | FAIL | [40337792930](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40337792930) | PASS | 18.1 |
| S18 | [#74](https://github.com/ctrlshiftaltdel/AgentOrch/issues/74) | [PR #80](https://github.com/ctrlshiftaltdel/AgentOrch/pull/80) | 10-26 15:06 | 0.84 | 6 | 243 | 4 | 4 | FAIL | [40338013319](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40338013319) | PASS | 15.4 |
| S19 | [#83](https://github.com/ctrlshiftaltdel/AgentOrch/issues/83) | [PR #84](https://github.com/ctrlshiftaltdel/AgentOrch/pull/84) | 10-26 16:14 | 0.07 | 1 | 21 | 1 | 1 | PASS | — | PASS | 5.2 |
| S20 | [#89](https://github.com/ctrlshiftaltdel/AgentOrch/issues/89) | [PR #90](https://github.com/ctrlshiftaltdel/AgentOrch/pull/90) | 10-26 23:39 | 0.06 | 1 | 10 | 1 | 1 | PASS | — | PASS | 4.4 |
| S21 | [#95](https://github.com/ctrlshiftaltdel/AgentOrch/issues/95) | [PR #96](https://github.com/ctrlshiftaltdel/AgentOrch/pull/96) | 10-27 06:12 | 0.58 | 5 | 51 | 2 | 2 | PASS | — | PASS | 10.7 |
| S22 | [#100](https://github.com/ctrlshiftaltdel/AgentOrch/issues/100) | [PR #106](https://github.com/ctrlshiftaltdel/AgentOrch/pull/106) | 10-27 06:55 | 0.15 | 2 | 332 | 1 | 2 | PASS | — | PASS | 10.2 |
| S23 | [#99](https://github.com/ctrlshiftaltdel/AgentOrch/issues/99) | [PR #109](https://github.com/ctrlshiftaltdel/AgentOrch/pull/109) | 10-27 07:22 | 0.35 | 6 | 261 | 2 | 2 | PASS | — | PASS | 11.3 |
| S24 | [#113](https://github.com/ctrlshiftaltdel/AgentOrch/issues/113) | [PR #114](https://github.com/ctrlshiftaltdel/AgentOrch/pull/114) | 10-27 08:10 | 0.13 | 5 | 55 | 1 | 1 | PASS | — | PASS | 5.3 |
| S25 | [#125](https://github.com/ctrlshiftaltdel/AgentOrch/issues/125) | [PR #126](https://github.com/ctrlshiftaltdel/AgentOrch/pull/126) | 10-27 12:12 | 0.12 | 1 | 71 | 1 | 1 | PASS | — | PASS | 6.9 |
| S26 | [#121](https://github.com/ctrlshiftaltdel/AgentOrch/issues/121) | [PR #130](https://github.com/ctrlshiftaltdel/AgentOrch/pull/130) | 10-27 14:00 | 0.54 | 20 | 3073 | 3 | 3 | PASS | — | PASS | 21.9 |
| S27 | [#134](https://github.com/ctrlshiftaltdel/AgentOrch/issues/134) | [PR #135](https://github.com/ctrlshiftaltdel/AgentOrch/pull/135) | 10-27 15:27 | 0.10 | 1 | 10 | 1 | 1 | PASS | — | PASS | 7.8 |
| S28 | [#124](https://github.com/ctrlshiftaltdel/AgentOrch/issues/124) | [PR #139](https://github.com/ctrlshiftaltdel/AgentOrch/pull/139) | 10-27 21:08 | 0.19 | 16 | 1741 | 2 | 2 | FAIL | [40582516481](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40582516481) | PASS | 11.0 |
| S29 | [#144](https://github.com/ctrlshiftaltdel/AgentOrch/issues/144) | [PR #145](https://github.com/ctrlshiftaltdel/AgentOrch/pull/145) | 10-27 23:17 | 0.12 | 4 | 65 | 1 | 1 | PASS | — | PASS | 6.8 |
| S30 | [#169](https://github.com/ctrlshiftaltdel/AgentOrch/issues/169) | [PR #159](https://github.com/ctrlshiftaltdel/AgentOrch/pull/159) | 10-28 03:54 | 1.45 | 2 | 73 | 3 | 3 | FAIL | [40621009594](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40621009594), [40622934728](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40622934728) | PASS | 11.2 |
| S31 | [#147](https://github.com/ctrlshiftaltdel/AgentOrch/issues/147) | [PR #148](https://github.com/ctrlshiftaltdel/AgentOrch/pull/148) | 10-28 10:42 | 9.25 | 1 | 434 | 5 | 6 | PASS | — | PASS | 40.0 |
| S32 | [#149](https://github.com/ctrlshiftaltdel/AgentOrch/issues/149) | [PR #175](https://github.com/ctrlshiftaltdel/AgentOrch/pull/175) | 10-28 13:15 | 1.39 | 1 | 94 | 3 | 2 | PASS | — | PASS | 14.0 |
| S33 | [#184](https://github.com/ctrlshiftaltdel/AgentOrch/issues/184) | [PR #190](https://github.com/ctrlshiftaltdel/AgentOrch/pull/190) | 10-28 15:29 | 1.36 | 1 | 119 | 3 | 2 | PASS | — | PASS | 13.7 |

样本合计：33 个任务（30 个有 CI），230.5 job-min，102 条 PR 评论。S01–S03 是本窗口内唯一的 3 个「无 CI 证据」任务（CI 尚未引入）；S10 的 6 次失败中有 1 次是规则校验失败、5 次是单元测试失败。

**表 S2：窗口内全部 CI 失败运行（n=14，不抽样）**

| 运行 | UTC | 分支（任务） | 失败作业 | 失败步骤 | 分类 | 结局 |
|---|---|---|---|---|---|---|
| [39892424347](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/39892424347) | 10-23 02:57 | `task/restructure`（#41） | ubuntu 3.12 + 3.13 | Validate kit structure | 规则校验 | 后续 head 通过 |
| [39892885875](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/39892885875) | 10-23 03:02 | 同上 | ubuntu 3.12 + 3.13 | Run unit tests | 单测 | 后续 head 通过 |
| [39893426645](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/39893426645) | 10-23 03:08 | 同上 | ubuntu 3.12 + 3.13 | Run unit tests | 单测 | 后续 head 通过 |
| [39894195638](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/39894195638) | 10-23 03:17 | 同上 | ubuntu 3.12 + 3.13 | Run unit tests | 单测 | 后续 head 通过 |
| [39894465187](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/39894465187) | 10-23 03:20 | 同上 | ubuntu 3.12 + 3.13 | Run unit tests | 单测 | 后续 head 通过 |
| [39895203291](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/39895203291) | 10-23 03:28 | 同上 | ubuntu 3.12 + 3.13 | Run unit tests | 单测 | 后续 head 通过 |
| [40272804851](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40272804851) | 10-26 05:10 | `task/51-safe-update-engine`（#51） | ubuntu 3.12 + 3.13 | Run unit tests | 单测 | 后续 head 通过 |
| [40337792930](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40337792930) | 10-26 14:14 | `task/77-ci-cross-platform`（#77） | `checks-windows` | Run unit tests | 单测 / **平台专属** | 后续 head 通过 |
| [40338013319](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40338013319) | 10-26 14:15 | `task/74-live-source-scan`（#74） | ubuntu 3.12 + 3.13 | Run unit tests | 单测 | 后续 head 通过 |
| [40582516481](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40582516481) | 10-27 20:56 | `task/124-e2e-integration`（#124） | `checks-macos` + `checks-windows` | Run unit tests | 单测 / **平台专属** | 后续 head 通过 |
| [40621009594](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40621009594) | 10-28 02:27 | `fix/validate-kit-skip-node-modules`（#169） | **无任何作业记录** | — | 基建 / 未启动（根因 `UNKNOWN`） | 该 head 无 CI 结论，被下一个 head 取代 |
| [40622934728](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40622934728) | 10-28 02:46 | 同上 | `checks-windows` | Run unit tests | 单测 / **平台专属**；`attempt=2` 重跑仍红 | 后续 head 通过 |
| [40345979656](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40345979656) | 10-26 15:06 | `main`（合并 PR #80） | `checks-macos` | 无（0 步，`cancelled`） | 基建 / 取消 | 下次合并的 push 运行恢复全绿 |
| [40716525316](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/40716525316) | 10-28 15:29 | `main`（合并 PR #190） | `checks-macos` | 无（0 步，`cancelled`） | 基建 / 取消 | 下次合并的 push 运行恢复全绿 |

分类计数：单元测试 10（其中平台专属 3：Windows 3 次、macOS 1 次，`40582516481` 同时红两者）、规则校验 1、作业未启动 1、平台作业取消 2。两个 `cancelled` 的 macOS 作业都在启动后约 15 分钟被取消且**从未执行任何步骤**，因此 main 的两次「红灯」（10-26 15:06→15:50、10-28 15:29→16:31 之间无成功 push 运行）**不是测试失败，也不是回归**。

### 7.6 缺失、`UNKNOWN` 与不可归因项（AC-2）

| 项 | 状态 | 原因 / 影响 |
| --- | --- | --- |
| M1–M7 七项交付指标 | **全部 `unknown` / 未采集** | GitHub 载体没有登记 `STATUS_CHANGED` / `CHECK_RECORDED` / `ACCEPTANCE_RECORDED` / `REGRESSION_REPORTED` 等事件；本节的比率与时长都是原生观测代理，**不得**回填成 M1–M7。这是本底座的结构性缺口，不是本次采集失败 |
| `READY` → `VERIFIED` 实际耗时 | `UNKNOWN` | Issue 没有状态转换事件，标签无时间戳；`Issue closed` 只是代理（§7.4），含规划期 |
| 审查轮次 / 退回原因（M3） | `UNKNOWN` | GitHub 正式 review 记录 0 条；248 条 PR 评论未被解析为结构化事件。`提交数`、`head 修订数` 只是下界代理 |
| 缺陷逃逸 / 合并后回归（M6） | `UNKNOWN` | 没有 `REGRESSION_REPORTED`；14 天观察窗未满。窗口内**观测到** main CI 测试失败 0 次，但这不等于 0 回归 |
| AC 逐条通过率（M7） | `UNKNOWN` | 证据矩阵以自由文本评论存在，未机读 |
| Token / 模型 / 子代理成本 | `UNKNOWN` | 无 telemetry 采集；本任务不得以聊天或估算补齐 |
| 3 个合并 PR（#10 / #11 / #12）的 G4 证据 | **结构性缺失** | 合并于 CI 引入之前，历史运行不存在 |
| head `6090f0ac`（PR #159）的 G4 | `UNKNOWN` | 对应运行 `40621009594` 结论 failure 但**无作业记录**，无法判定是产品失败还是基建失败 |
| 32 个只有 2 作业证据的合并 PR（另 1 个混合）的多平台结论 | `UNKNOWN` | macOS / Windows 作业在 `10-26T14:14Z` 前不存在，不能倒推为「当时通过」 |
| 「放弃的分支 / 关闭未合并 PR」成本 | 不可观测 | 窗口内 0 个关闭未合并 PR，无法测量该类浪费 |
| 计费成本（美元） | `UNKNOWN` | 私有计费数据不采集；本节只有 job-minutes |

### 7.7 在途 Issue / PR 路径与契约冲突审计（AC-3）

快照事实（同一时刻 `2026-10-28T21:47Z`，main `953c194e…`）：

| 观察项 | 实际 | 来源 / 方法 |
| --- | --- | --- |
| 开放 PR | **0** | `gh pr list --state open` 返回空数组 |
| 活动任务登记块 | **1**：#185（`status: DISPATCHED`，`locked_paths: docs/metrics.md`，`updated_at 2026-10-28T21:38:52Z`） | Issue #185 评论中的 `ORCH-TASK-REGISTRY`；全仓 Issue 搜索 `ORCH-TASK-REGISTRY` 另命中 #170，而 #170 正文自述「没有 status 标签或 ORCH-TASK-REGISTRY，尚未 READY / DISPATCHED，无路径锁」 |
| 有 `status:` 标签的开放 Issue | #185 `status:in-progress`；#162 / #174 `status:blocked`；其余 33 个无状态标签 | `gh issue list --state open --json number,labels`；无标签者按 `PLANNED` / 未 `READY` 记账（依据 `docs/implementation-plan.md` §15） |
| 开放 Issue 总数 | 36（另有 84 个已关闭） | 同上 |
| 本地残留 worktree | 8 个：`147` / `149` / `172` / `183` / `197` / `198` / `199` / `73` | `git worktree list`；对应 PR 均已合并，**不作为活动任务状态**，也不构成路径锁 |

**路径重叠（潜在冲突，非当前锁冲突）**：3 个尚未派发的开放 Issue 把 `docs/metrics.md` 写进允许路径——

| Issue | 允许路径中对 `docs/metrics.md` 的表述 | 当前状态 | 与 #185 的关系 |
| --- | --- | --- | --- |
| [#155](https://github.com/ctrlshiftaltdel/AgentOrch/issues/155) ETR 计算与阈值 | 「`docs/metrics.md`（如新指标定义确需同步）」 | 开放、无状态标签、无 registry、无 PR（未 `READY`） | 语义相邻：它要**新增/同步指标口径**，#185 只加**只读快照**；同文件，需串行 |
| [#156](https://github.com/ctrlshiftaltdel/AgentOrch/issues/156) 三角色互评 | 「`docs/metrics.md`（如确需列出指标口径）」 | 同上 | 同上 |
| [#158](https://github.com/ctrlshiftaltdel/AgentOrch/issues/158) 端到端回归与文档同步 | 「`README.md`、`docs/README.md`、`docs/upgrade.md`、`docs/metrics.md`、`release/README.md`」 | 同上 | 同上 |

结论：截至快照时刻**没有活动锁冲突**（这 3 个 Issue 未被派发、无登记块、无开放 PR、无分支），但 #185 持有 `docs/metrics.md` 期间它们不能派发；#185 合并后 Master 需重新检查这三条的重叠与顺序。

**依赖与「观察 vs 硬依赖」分类**：

| 类别 | 内容 | 证据 |
| --- | --- | --- |
| 产品硬依赖 | 无：没有任务以 `docs/metrics.md` 的存在或内容为前置条件 | Issue #185「功能硬依赖：无」；`docs/implementation-plan.md` §15.2.4 只登记组内顺序 |
| Owner 调度顺序（非硬依赖） | #197 收口后先 #185 → #186，再 #192–#195 | [#197 决定记录](https://github.com/ctrlshiftaltdel/AgentOrch/issues/197#issuecomment-6473530069)；#197 的交付 PR #200 已合并（`953c194`）、Issue #197 已带 `status:verified` 关闭 |
| 组内顺序 | #185 → #186 → #187 → #188 → #189（AVE）；#177–#181、#163–#167、#192–#195 各自串行 | `docs/implementation-plan.md` §15 |
| 开放但无任务包 | 36 个开放 Issue 中 18 个（#146 / #160 / #176 / #177–#181 / #184 / #186–#189 / #191 / #192–#195）正文没有「可改路径」段 | 逐 Issue 正文检查；`OPEN` ≠ 活动执行 |
| 阻塞状态 | #162 / #174 `status:blocked`；均不涉及 `docs/metrics.md` | 标签 + 正文 |
| 契约 / 计划观察（不在本任务修改） | 三份设计提案（AVE / 拓扑 / SemVer）仍标 `PROPOSED`；`docs/implementation-plan.md` 仍把 #185 记作「未 READY（`OPEN` / `PLANNED`）」，而载体已派发 | 文稿头部；计划 §15.2.4 与 Issue #185 registry；属计划同步任务范围 |

### 7.8 瓶颈归因与证据边界（AC-4）

以下每条都写明支撑数据与「能/不能」推出的结论。

1. **重复执行不是这条流水线的主要成本（改写自直觉结论）**。219 个运行对应 **219 个唯一 head**：平台从不重跑已经绿的 head；`attempt=2` 的作业重跑只发生在 4 个运行 / 7 个作业（1.83%）。142 个 PR 运行分布在 80 个任务上（其中 77 个有运行），多数任务（中位数）只跑 1 次；有失败运行的 PR 只有 6/77，12 个失败运行（11 个有作业记录）合计 **16.45 job-min，占 PR 端 590.9 的 2.8%**。→ AVE §1 的「重复执行测试次数下降 ≥30%」在本仓库的**绝对基数很小**：即便把失败运行全部消灭，可回收的计算量也只有这 2.8% 量级。该结论只适用于本窗口、本仓库。
2. **成本集中在平台广度，而不是重复。** Windows 占 51.0%、macOS 占 19.1% 的 job-minutes（合计 70.1%），ubuntu 两个版本合计 29.8%。同时，这 137 个 4 作业运行里，只有 3 个运行出现了「ubuntu 全绿、macOS/Windows 红」的平台专属失败（2.19%）——**即平台作业很贵，但确实抓到过 ubuntu 抓不到的缺陷**（#77 的 Windows、#124 的 macOS+Windows、#169 的 Windows）。→ 缩短验证的最短路径是平台层（例如按风险选择性运行 macOS/Windows），但现有证据**不**支持直接删除这两个平台，也不支持把「平台专属失败率低」当成等价性证明。
3. **失败模式高度集中，且混入基建噪声。** 14 个失败运行里 10 个是单元测试、1 个规则校验、1 个无作业记录、2 个 macOS 作业被取消（0 步、约 15 分钟）。→ 若把「首次通过率」「运行失败率」直接当质量指标，会被 3/14（21.4%）的基建事件污染；这些指标在使用前必须先按失败类别拆分。
4. **任务周期短，墙钟不是当前主要痛点（但无法用 M1 证明）。** PR created→merged 的 median 仅 0.16 h、p90 1.36 h；60 个 `status:verified` Issue 的 created→closed median 1.05 h。单任务 cycle 短意味着「省验证时间」的收益上限也低；同时 M1 未接线，**不能**据此宣称「审查很快」或「阻塞很少」。
5. **可观测性本身是瓶颈。** 七项指标在本底座全部未接线，缺陷逃逸、审查轮次、AC 通过率均不可机读；本快照只能提供 5 类原生代理（任务生命周期、CI 结论、作业计时、head 修订、路径审计）。→ 任何「下降 ≥40% / ≥20% / ≥30%」的目标在当前载体上**不可验证**；先把事件登记接上，是 B 阶段有意义的先决条件，而不是 B 阶段的可选项。
6. **证据边界**：单仓库、单 Owner、`edition=github`、6.9 天、n=80 任务 / 219 运行 / 710 作业记录；没有对照组、没有 A/B、没有历史同类阶段可比较；窗口内 CI 形态本身发生过变化（2 平台 → 4 平台），因此**跨窗口比较必须重新取数**。

### 7.9 go/no-go、适用范围与回退基线（AC-4）

**结论（基于上述证据，不代表任何批准）**

- **go —— 仅限 B 阶段的只读 / 影子范围**：证据足以支撑「做一次只读风险分类 + 影子 Gate Planner，并同时补齐事件登记」，因为（a）当前必需检查、G1–G6 映射与平台强制范围已可按版本定位；（b）原生观测数据完整（80/80 任务、219/219 运行、710 条作业计时），样本量达到 A 阶段 ≥20 的门槛；（c）存在可量化的平台层成本集中（70.1%）。
- **no-go —— D 阶段及任何门禁切换**：证据**不支持**现在缩减任何 required check、平台作业、合并后回归或审查强度。理由：(a) 七项指标全部未接线，无法验证「不增加逃逸缺陷」；(b) 平台专属失败确实存在（3/12 失败运行）；(c) 样本为单仓库单 Owner 6.9 天，无对照组；(d) 本任务不改门禁，也不构成对后续变更的授权。
- **B 阶段的前置条件（建议，不是本任务的变更）**：先把 `CHECK_RECORDED` / `ACCEPTANCE_RECORDED`（以及需要时的 `REGRESSION_REPORTED`）按模板事件名登记到 Issue / PR 载体，使 M2 / M6 / M7 从 `unknown` 变为可算；否则 B 阶段只能产出分类结果，无法产出可比较的基线差。

**适用范围**：本仓库（`ctrlshiftaltdel/AgentOrch`）、`edition=github`、`main @ 953c194e…`、UTC `[2026-10-22T00:00:00Z, 2026-10-28T21:47:00Z)`、80 个已合并任务 / 219 个 CI 运行 / 14 个失败运行。不外推到其他项目、其他底座、其他时间窗，也不外推到「Agent / 模型质量」——这些数字不是排名依据（见 §4 与模板 Reading rules）。

**回退基线（本任务只读，回退 = 撤销一个文档提交）**

| 对象 | 基线值 |
| --- | --- |
| main（快照与审批基线） | `953c194e9b6ba53a150214775ec793678442575b` |
| `docs/metrics.md` | blob `f176733cdb5150dc73ffff5c585a7a3b4d88400f`（本任务只在其后追加 §7） |
| `.github/workflows/ci.yml` | blob `d042171c106c5ff270880a9eadbf03e43aec5297`（4 作业 × 7 步骤） |
| 平台 required checks | ruleset `25644959`：`checks (Python 3.12)`、`checks (Python 3.13)` |
| `kit/orchestration.md`（G1–G6 / 4.1） | blob `c2d451e43efca6b1cd89f700f83c3943dc30ed07` |
| `templates/metrics.md`（M1–M7 定义） | blob `dee47b6b717270b2d112ffa3bf0d0038828f606c` |
| `docs/PROJECT.md`（验证命令） | blob `9797dd2fdbe2c3a0322ccd228c05f5d6fc80529b` |

回退动作仅限「revert 本任务文档 PR 的合并提交」：没有数据迁移、没有运行时状态、没有锁、没有 tag / Release、没有凭据或权限变更。若后续任务要改变任何 required check 或指标定义，必须另立任务并按现行 G6 / Owner 授权流程执行；本节不提供该授权。
