# AVE B 阶段只读影子 Gate Planner 契约（冻结版）

> **状态：FROZEN / 已冻结。** 本文是 Issue #186 的 B 阶段子契约定稿，由项目所有者书面冻结并落地实现。
> 冻结来源：[Issue #186 评论 6486678110](https://github.com/ctrlshiftaltdel/AgentOrch/issues/186#issuecomment-6486678110)。
> 实现范围只有 plan-only 影子 CLI；父级提案 [adaptive-verification-and-quality-gates.md](adaptive-verification-and-quality-gates.md) 仍是 `PROPOSED / NOT IMPLEMENTED`，本文不改写它。
> 本文件不复制 G1–G6 的通过条件；门语义的唯一权威是 `docs/orchestration.md`（#151 未 `VERIFIED` 前，见第 7 节）。
> 输出不是 PASS、不是审批、不是 READY、不是 `VERIFIED`、不是授权、不是跳过指令。

本文件落地审查报告六条冻结清单（B1–B3、M4、M3/M1、M5/M6），并吸收 M2 与 N1–N5。上一版中「可复核来源证明已覆盖相关高危检查、即可排除 L3」的分支已删除。

父级提案是 [#199](https://github.com/ctrlshiftaltdel/AgentOrch/issues/199) 与 [PR #190](https://github.com/ctrlshiftaltdel/AgentOrch/pull/190)，文本在 `docs/design/adaptive-verification-and-quality-gates.md`，继续保持 `PROPOSED / NOT IMPLEMENTED`。Owner 澄清的区分要求见该 Issue 评论（`docs/implementation-plan.md` §15.4 所引 [#186 评论](https://github.com/ctrlshiftaltdel/AgentOrch/issues/186#issuecomment-6473153904)）：独立 Reviewer / 身份证据，与 C1–C4 探针状态，是两类事实。

**冻结状态。** Owner 已书面冻结本子契约（来源见上）；本文件即 #186 范围内生效的执行规范，实现证据与本文一起接受 #186 验收。

父级仍是 `PROPOSED`。本子契约里已经写明的条款在 #186 范围内生效；本子契约没有写明的父级条款不得实现。父级 §5 的 `PASS_EXECUTED` 与 `PASS_EQUIVALENCE_ATTESTED` 不进入本工具。

## 1. 目标与不可变边界

#186 只交付 B 阶段的 L0–L3 风险分类、版本化输入 / 输出，以及隔离的 plan-only 影子 CLI。CLI 在调用者给出的任务快照上并列展示「调用者检查快照」和「影子候选」。它不执行任何检查。影子候选在 Owner 书面确认减检对照表之前等于该快照，反事实差异为空。候选集合不替代现行集合。

始终保留、且本工具不改写的现行规则：

- G1–G6 的语义只存在于当时有效的编排契约（本阶段路径见第 7 节）
- 每条 AC 的当前 head 证据、当前四平台 CI
- `docs/PROJECT.md` 所载合并后快速测试、完整回归、`python scripts/orchestrator.py backup`、静态检查
- Reviewer 独立性、安全 / 权限 / 锁 / 状态机、三轮修复上限
- 两底座相同的治理语义

三轮修复上限只作为现行规则被保留。本 Planner 不实现该上限，也不改对应代码。

shadow 输出的固定标记是 `plan_usability: not_a_verification_result` 与 `mode: shadow_only`。该输出不是审批、不是 READY、不是 `VERIFIED`、不是授权、不是跳过指令。本工具不改变 Required Checks、CI、`docs/PROJECT.md` 的现行验证命令、`docs/orchestration.md` 或 `scripts/orchestrator.py` 的门禁行为，不写任务状态。

生产门禁切换仍属于 #188，须 A–C `VERIFIED` 之后另行取得 Owner 明确授权。#187 承担影响面测试选择、旧 SHA 等价性与证据复用。#176 承担拓扑推荐与 Agent 派发。本 Planner 不选角色、不派发。

标准库约束：实现只使用 Python 3 标准库，不引入第三方依赖，不新建数据库或网络服务。

### 1.1 #185 依赖与未知项（不宣布状态）

本契约不宣布 #185 已 `VERIFIED`，也不把「同步 #185 已 VERIFIED」列入 #186 的编辑范围。

B 的依赖以**派发当时**的 GitHub Issue #185 与 `docs/implementation-plan.md` 为准。二者不一致则停下：不在本任务中修改该计划文件，不把 #185 改写为 `VERIFIED`。起草本契约时，仓库 HEAD 的 §15.2.4 把 #185 登记为 `OPEN` / `PLANNED` / 未 READY，并把 #186 登记为因「#185 未 `VERIFIED`」而未就绪。这只说明当时读到的计划文本。它不是对 GitHub 实时状态的宣布。§15.3.1 的硬依赖是 `#185 → #186`。该计划文件另有既有编辑串行（§15.3.3 的 `#149 → #162 → #151 → #152`）。#186 默认不进入这条串行。

在两处权威都把 #185 记为 `VERIFIED` 之前，下列事项是本工具的未知项。它们只升档，不降级，也不允许把父级 §1 的实验比例写成已验证结果。此处不使用未在仓库中定义的「M1–M7」代号。

| `unknown_item` | 引用 | 规则 |
|---|---|---|
| `baseline_measurement_unverified` | 父级 §9.A 出口（可审计基线、冲突表、是否值得实施 B）；`docs/implementation-plan.md` §15.2.4 | 不得把该出口当作已测量事实。父级 §1 的 L0/L1 ≥40%、L2 ≥20%、重复测试 ≥30% 仍是非承诺实验目标 |
| `sample_floor_unverified` | 父级 §9.A「≥20 个代表性任务 / CI 执行事件」 | 本工具不核对该样本数，也不声称样本已足够 |
| `mandatory_check_audit_unfrozen` | 父级 §9.A 对当前必须运行的检查及 G 门映射的审计 | 本契约不内置基线检查条数。输出永远带 `mandatory_baseline_unfrozen`，不得声称现行集合已核对 |
| `cost_metrics_uncollected` | 父级 §8 的采集字段 | 这些字段不是本工具输入。快照若携带 `suite_seconds`、`total_cycle_seconds`、`review_rounds`、`reviewer_time`、`token_usage_if_reported`、`CI_retries`、`regression_escape`、`severity`、`equivalence_rate`、`tests_selected`、`tests_total`、`tests_executed`、`baseline_sha`，按禁止字段处理，退出码 2，不生成计划 |

## 2. 风险级别与计算规则

级别公式：

`classification.level = max(path_floor, semantic_floor, uncertainty_floor, project_floor)`

然后执行高危后处理（第 2.2 节）。路径 floor 不能单独把语义 floor 压低。`project_floor` 只作为下限。检查有没有跑过、调用者是否声称「高危检查已覆盖」，都不参与定级。

| 级别 | 风险示例 |
|---|---|
| L0 | 纯说明文字 / 错字；不改变可执行示例、指令、政策、清单或契约 |
| L1 | 局部变更，无跨模块行为或接口影响 |
| L2 | 接口、多模块、集成，或提示词 / 角色选型中不涉及授权的部分 |
| L3 | 状态机、锁、权限、安全、Reviewer 身份、派发、迁移 / 升级、发布、证据门禁，或其他高危 / 不可逆行为 |

单元格是风险示例。影子候选的对照在附录 D，不在本表。

### 2.1 计算顺序

对结构有效的输入，按下面的顺序计算。任一步都不得把已经得到的 floor 改小。

1. **路径。** 按附录 A 的路径谓词取 `path_floor`（多条命中取最大）并记下全部命中的 `rule_id`。本工具不读取 diff 正文。高危路径是否只改一行，都不降低该 floor：路径命中即成立。
2. **正向语义。** 只根据附录 A 的正向 `signal_id` 取 `semantic_floor`。负向 `signal_id` 不在这一步参与。
3. **项目下限。** `project_floor` 来自输入，只能抬高 `max`，不能单独成为降级理由。
4. **不确定性。** 初始化 `uncertainty_floor`。下列情况把它抬到至少 L3，并写入 `unknown_items` 与 `rule_id`：
   - 任意调用者来源：本工具不核验来源，故 `R-UNVERIFIED-SOURCE` 在每一份计划上成立。不存在可关闭它的输入字段。
   - 基线未冻结：`R-BASELINE-UNFROZEN`（第 5 节）。
   - `dynamic_dependencies = unknown`：`R-DYN-UNKNOWN`。
   - 输入里的 `uncertainties` 非空：`R-DECLARED-UNCERTAINTY`。
   - 语义审阅的 `reviewed_sha` 与 `head_sha` 不一致、为 null，或 `stale = true`：`R-STALE-REVIEW`。
   - `profile.as_of_sha` 与 `head_sha` 不一致：`R-STALE-PROFILE`。
   - 任务要求独立 Reviewer：`R-IDENTITY-UNPROVEN`（第 3 节）。
5. **负向自述。** `no_high_risk_impact`、`semantic_review_complete`、`high_risk_checks_already_covered`、`self_reported_l0` 只追加 `unknown_items` 项 `negative_self_report` 与规则 `R-NEGATIVE-NO-DOWNGRADE`。它们不排除 L3，也不压低任一 floor。
6. **取 max。** 得到合成级别。
7. **高危后处理。** 若附录 A 标记为高危的规则已经命中，再执行 `level = max(level, L3)`，并记下 `R-HIGH-AFTER-MAX`。这一步在 `max` 之后，实现不得在此之前用覆盖声明把 `semantic_floor` 先降下去。

因此，在本工具能够独立核验来源之前，每一份计划的最终级别至少是 L3。L0 / L1 / L2 仍然是必须计算并输出的谓词 floor，夹具分别断言它们。最终级别至少为 L3，是因为来源未核验，不是因为路径表失效。

检查覆盖不参与上述任一步。本工具不实施人工覆盖。父级 §4 所写的「谁批准覆盖」在 B 阶段的含义是：本工具不接受、也不记录快照里的批准字段。`override_approver`、`approved_by`、`waiver`、`checks_cover_high_risk`、`source_verified`、`declared_risk_level` 都是禁止字段（附录 C，退出码 2）。

### 2.2 不完整输入与动态依赖

两类不完整要分开。

**结构无效（没有计划）。** 缺少或类型错误的必需字段、非法 `edition`、不是 40 位小写十六进制的 SHA、非法相对路径、未知字段、禁止字段、空的 AC 或空的变更路径：退出码 2，stdout 只有诊断 JSON，没有 `classification`，没有候选集合。

**结构有效但证据不完整（有计划，但不是合格）。** 动态依赖为 `unknown`、来源未经本工具核验、审阅或 profile 与 `head_sha` 不一致、声明了不确定项、或资料不足以判断高危谓词是否命中：仍然输出影子 JSON，但

- `closure` 为 `open`
- 退出码为 3（本 CLI 不使用退出码 0）
- `classification.level` 至少为 L3
- `checks.shadow_candidates` 的 `check_id` 集合与调用者快照相同，不得更短
- `checks.counterfactual_diff` 为空数组，不得列出移除项
- `unknown_items` 列出对应原因

`dynamic_dependencies = known` 或 `not_applicable` 只是调用者声明。本工具不据此声称持有完整影响图，也不据此压低 floor。影响图属于 #187。

## 3. 身份未证明与 C1–C4 观察

两类字段互斥。身份写入 `identity`。探针写入 `observations.runtime_probe`。同一事实不得同时出现在两边。探针观察不得抬高或压低 `path_floor` / `semantic_floor` / `uncertainty_floor` / `project_floor`，不得删除影子候选，不得满足身份要求。

### 3.1 独立 Reviewer（失败关闭）

本工具不证明 Reviewer 身份。#170 的身份来源、核验方式与失效规则尚未冻结（`docs/implementation-plan.md` §15.2.6 / §15.5）。在 #170 冻结出可机器核验的证据形态、并且 Owner 把该形态书面纳入本契约之前，快照中的身份布尔、模型名、alias、温度、Prompt 哈希和缓存结论都不是证据。

这些键若出现在输入里，属于禁止字段，退出码 2，不生成计划：`identity_ok`、`reviewer_identity_proven`、`model_name`、`model_alias`、`temperature`、`prompt_hash`、`cached_review_ok`。禁止字段不进入 `unknown_items`，以避免把自述洗进一份退出码 3 的计划。

`requires_independent_reviewer` 是必需布尔，省略则退出码 2。取值为 `true` 时：

- `identity.requirement = required`
- `identity.assessment = unproven`（输出枚举没有 `proven`）
- 追加 `R-IDENTITY-UNPROVEN`；`uncertainty_floor` 至少 L3；高危后处理之后最终级别至少 L3
- 影子候选不得短于调用者快照；`counterfactual_diff` 为空
- `closure = open`，退出码 3
- 不得把该结论写进 `observations.runtime_probe`
- 该计划的 `plan_usability` 仍是 `not_a_verification_result`。退出码 3 与 `unproven` 都不是合格

取值为 `false` 时：`identity.requirement = not_required`，`identity.assessment = not_assessed`。这只表示本任务快照没有要求本工具处理独立 Reviewer，不表示身份已经成立，也不表示 L0/L1 可以取消独立 Reviewer。角色选择仍属于 #176 与现行契约。

### 3.2 C1–C4 只做观察

Owner 澄清保留如下，并落到字段上。

无法满足任务所要求的独立 Reviewer / 身份证据，走第 3.1 节。探针缺失、未执行、过期，以及未证明为宿主工具客观不存在的探针失败，只进入 `observations.runtime_probe`。真实调用失败、权限、锁、安全与 G1–G6 使用第 3.4 节的独立字段，不并入探针对象。

输入用本契约自己的 `caller_code`，以免把证据矩阵或能力记录的词写进影子 JSON：

| 调用者要表达的探针事实（设计说明；这些矩阵 / 能力词不进入输出） | 输入 `caller_code` |
|---|---|
| 缺失 | `missing` |
| 未执行 | `not_executed` |
| 过期 | `stale` |
| 探针失败且未证明工具客观不存在 | `unproven_fail` |

输出原样保留上述 `caller_code`，并固定：

```json
{
  "effect": "current_rules_unchanged",
  "refs": [
    "docs/orchestration.md",
    "editions/local/master.md",
    "editions/github/master.md"
  ]
}
```

Owner 澄清里的两句话同时保留，并且落在不同层上。

- 区分：探针缺失、`UNKNOWN`、`STALE`，或未证明宿主工具客观不存在的探针 `FAIL`，按所有者已批准的未来方向只作告警。本工具不单凭这些探针状态输出「禁止尝试」或「允许尝试」。真实调用失败、权限、锁、安全与 G1–G6 另行判定（第 3.4 节）。
- 现行契约：在 #174 那项独立变更落地之前，自动派发仍按现行规则执行。`docs/orchestration.md` 角色选择入口第 1 步，以及 `editions/local/master.md` 与 `editions/github/master.md` 的「先决 C1–C4」，仍然是：C1–C4 任一项不是 `PASS`，或记录为 `UNKNOWN` / `STALE` 或已过期，就停止自动派发，改走人工交接。`docs/implementation-plan.md` §14.4、§15.2.6 与 §15.5 写明 #174 的「以风险提示为主」尚未生效。本契约不把该未来方向写成现行派发规则，也不把「不单凭探针状态禁止获授权尝试」写成影子输出里的许可字段。

本工具对探针的唯一动作是观察。禁止尝试与允许尝试都由本工具之外的现行规则处理；影子 JSON 里两边都不出现。

禁止出现的派发许可字段（出现在输入里则退出码 2；输出 schema 不包含它们）：`attempt_allowed`、`dispatch_ok`、`may_dispatch`、`unlock`、`handover_authorized`、`skip`。

探针条目不改变四个 floor。与不含探针的孪生夹具相比，`classification.level` 与四个 floor 相同。

### 3.3 已声明宿主工具不存在

已证明宿主工具客观不存在，与「未证明客观缺失的探针失败」分开。

输入字段 `host_tool_absence_claims`：`[{ "probe_id": "C1"|"C2"|"C3"|"C4", "evidence_ref": "<引用>" }]`。该字段可选；一旦出现，每一项必须同时有 `probe_id` 与 `evidence_ref`。

输出写到 `observations.runtime_probe.tool_absence_claims`，并带 `level_effect: none` 与 `dispatch_effect: none`。本工具不核验「已经证明」，所以输出里没有「Planner 已证明工具不存在」的字段。该声明不并入 `identity`，不改变级别，不增删影子候选，不产生派发或解锁字段。派发与否仍由第 3.2 节引用的现行规则决定，不由本工具决定。

### 3.4 真实调用失败、权限、锁、安全、G1–G6

这些条件不进入 `observations.runtime_probe`，也不进入 `identity`。

输入必需对象 `non_probe_conditions` 的四个布尔：`call_failure_observed`、`permission_constraint`、`lock_constraint`、`security_constraint`。值为 `true` 时记下对应规则，语义 floor 至少 L3，并触发 `R-HIGH-AFTER-MAX`：

| 字段 | `rule_id` |
|---|---|
| `call_failure_observed` | `R-NONPROBE-CALL` |
| `permission_constraint` | `R-NONPROBE-PERMISSION` |
| `lock_constraint` | `R-NONPROBE-LOCK` |
| `security_constraint` | `R-NONPROBE-SECURITY` |

值为 `false` 只表示快照没有报告该条件。由于来源未核验，`false` 不能证明条件不存在，也不能压低 floor。

G1–G6 始终六门都在输出中，不由这些布尔打开或关闭。门的通过条件不在本工具内判断。

## 4. 输入与信任模型

Planner 读取一份版本化 JSON 快照（只读文件参数）。调用者 / Master 准备快照。Planner 不请求 GitHub，不读 Issue API，不访问 `.orchestration/` 或 SQLite，不调用 `scripts/orchestrator.py`，不执行 git、测试、CI 或网络请求。它不宣称已经核验外部引用。输出携带调用者给的 `source_ref`；引用是否成立仍由 Master 按治理规则复核。

输入文本永不作为 shell 命令执行。

路径字符约束与 `scripts/orchestrator.py` 的 `normalize_path` 对齐，但是本工具把该约束重写在自己的解析里，不导入 orchestrator：拒绝空路径、绝对路径、`..`、`.` 路径分量，以及含星号、问号、方括号的字符串。非法路径是退出码 2 的 `path_invalid`，不是风险降级。

### 4.1 信任

- 带来源的正向高危语义信号只升档。
- 负向声明、无高危影响、语义审阅完成、以及任何本工具不能独立核验的来源，只形成未知项并抬高 `uncertainty_floor`。
- 不得把执行者自述或 `semantic_review_complete` 当成高危条件已被排除。
- 输入不接受可直接覆盖分类结果的级别、合格标记或「无影响 / 低风险」自述。
- Planner 无法认证调用者或来源身份。快照里的「可复核来源」字符串不等于 Master 已经复核，故不能用来排除 L3。

### 4.2 profile 白名单

`profile.checks[]` 只允许三个字段：`check_id`、`scope`、`source_ref`。

结果类字段在写入输出前剥离，不回显：`status`、`actual`、`expected`、`notes`、`result`、`passed`、`pass`、`ok`、`qualified`、`success`、`green`、`approved`、`ready`、`verified`、`may_merge`、`exit_code`、`conclusion`。剥离后追加未知项 `profile_result_field_stripped`，`closure` 保持 `open`，退出码 3。输出中不得留下被剥离的值。

除此之外的未知字段，退出码 2。

输出中的检查列表字段名是 `checks.caller_snapshot`。每项只含白名单字段。对象带常量 `authority: unverified_snapshot`，表示「调用者快照，未经本工具核验为项目权威」。本契约不内置 #185 的基线条数。基线缺失时不得把该快照叫作现行必需检查，也不得声称已经与 `docs/PROJECT.md` 或 `docs/orchestration.md` 核对过。快照比将来的基线更短时，处理方式已经由「基线未冻结 → 一律 `mandatory_baseline_unfrozen` + `closure: open`」覆盖：本阶段没有基线数字可用来补齐条目，禁止编造缺失的 `check_id`。

双底座：只改变 `edition` 时，`classification.level`、`rule_ids` 与 `unknown_items` 必须相同。`local` 与 `github` 各自展示自己输入里的 `caller_snapshot`，不能用一边削减另一边。`edition` 本身不参与 floor。

## 5. 输出、合格不变量与 CLI

机器可读结果只写 stdout，且永远是 JSON。stderr 必须为空。不提供输出文件、状态写入或网络选项。诊断也写 stdout，不写 stderr。

### 5.1 合格不变量

每一份 stdout JSON（含退出码 2 的诊断）满足：

1. 顶层 `plan_usability` 恒为 `not_a_verification_result`。
2. 计划对象的任何字段都不得表示检查、AC、门或身份已合格。
3. 整份 JSON 的键名，以及任意字符串值里的完整词（大小写不敏感），不得是下列词：`PASS`、`FAIL`、`NOT_RUN`、`UNKNOWN`、`BLOCKED`、`NOT_APPLICABLE`、`PASS_EXECUTED`、`PASS_EQUIVALENCE_ATTESTED`、`approved`、`ready`、`verified`、`may_merge`、`ok`、`passed`、`qualified`、`success`、`green`、`attempt_allowed`、`dispatch_ok`、`may_dispatch`、`skip`、`identity_ok`、`declared_risk_level`。测试对整份输出做该扫描。
4. 证据矩阵五个 `status` 的唯一权威仍是 `docs/orchestration.md` 4.1。`UNKNOWN` 在现行契约中用于能力记录，不在这五个值里。本工具既不输出这五个值，也不输出 `UNKNOWN`，从而不建立第二套矩阵状态。
5. Owner 要求的 `FAIL`、`NOT_RUN`、`UNKNOWN`、`BLOCKED`，以及证据缺失，不得以退出码 0、同义词或回显 `status` 表示合格。本 CLI **不使用退出码 0**。退出码 0 在本契约中没有「计划已产出」或「合格」的含义。
6. `not_applied` 只允许出现在尚未生效的对照说明中；当前 schema 把 `counterfactual_diff` 限制为空，因此输出里不出现移除行。空差异在证据矩阵中的含义是「没有一条可被拿去少跑的检查」，不是一条新的 `status`。
7. `ac_map` 的允许字段只有 `ac_id`、`matched_rule_ids`、`gate_ids`、`check_ids`。没有通过类字段。`gate_ids` 恒为 G1–G6 六项。`check_ids` 只复制调用者快照中的 id。
8. 禁止虚构 `PASS`：profile 剥离、禁用词扫描、以及输出枚举中不存在合格值，三条一起执行。

需要表达「影子计划没有被应用」时，只用 `mode: shadow_only`、`closure: open` 与空的 `counterfactual_diff`。这些标记在证据矩阵里对应「不得折算为通过」。它们不是新的 status 权威。

### 5.2 输出主体

退出码 3 时，stdout 是 `schema_version = verification-planner-output/1` 的对象，字段以附录 B 为准。最少包括：planner 与 schema 版本、`task_ref`、`edition`、两个 SHA、`plan_usability`、`mode`、`closure`、`closure_reasons`、`identity`、`policy`、`classification`（最终级别、四个 floor、`rule_ids`、`unknown_items`）、`gates`、`ac_map`、`checks.caller_snapshot`、`checks.shadow_candidates`、`checks.counterfactual_diff`、`observations`。

`classification.rule_ids` 是理由。另外保留输入中的 `source_ref`，不另写散文结论，以免禁用词扫描失效。

### 5.3 CLI

```text
python scripts/verification_planner/cli.py --input PATH
```

只接受这一个参数。未知参数、缺少参数：退出码 2，诊断码 `usage`。`PATH` 只读。进程写集合必须为空。

本 CLI 不是 CI required check，不进入 G4，不承担门禁职责。把退出码映射成矩阵状态的包装脚本不属于本契约；由于本 CLI 不返回 0，该包装也不能从退出码得到合格。

## 6. 验收场景与夹具

每个场景只引用附录 A–D 的字段，并绑定附录 E 的夹具名与预期值。场景之间的预期以附录 E 为准。

| 场景 | 夹具 |
|---|---|
| 谓词 floor 覆盖 L0、L1、L2、L3 | `fx_l0_plain_doc`、`fx_l1_local`、`fx_l2_cross`、`fx_l3_orchestrator` |
| 可执行文档 / 契约语义不得成为 L0 | `fx_doc_executable`、`fx_contradiction_max` |
| 升档单调 | `fx_monotonic_before`、`fx_monotonic_after` |
| 负向自述与「覆盖即可排除 L3」不能降级 | `fx_negative_cannot_exclude`、`fx_coverage_forbidden` |
| 结果字段不回显、不虚构合格 | `fx_profile_status_stripped` |
| 结构拒绝 | `fx_bad_sha`、`fx_unknown_field`、`fx_forbidden_declared_risk`、`fx_identity_field_forbidden` |
| 身份未证明，且与探针互斥 | `fx_identity_required` |
| C1–C4 观察不改变 floor、不发出派发许可 | `fx_probe_observation` |
| 工具不存在声明单独记录 | `fx_tool_absent` |
| 真实锁 / 权限类条件不被探针吞掉 | `fx_nonprobe_lock` |
| 过期审阅、过期 profile | `fx_stale_review`、`fx_stale_profile` |
| 动态依赖未知 | `fx_dynamic_unknown` |
| 双底座分类一致；短快照不能自称已核对 | `fx_edition_local`、`fx_edition_github`、`fx_short_snapshot` |
| 六门只引用路径 | `fx_gates_present` |
| 分类一致性报告 | 测试 `test_classification_consistency_report` |
| 进程写集合为空 | 测试 `test_process_write_set_empty` |

全量验证仍按 `docs/PROJECT.md` 的全部现行命令、完整 unittest，以及当前 PR head 的四平台 CI。不得跳过、删除或减弱已有测试或断言。合并后仍执行该文件已有的合并后命令。本工具的新测试是追加，不替换这些命令。

分类一致性报告由测试在内存中生成：每个夹具一行，`actual_level` 必须等于附录 E 的 `classification.level`（退出码 2 的夹具没有级别，行内 `has_classification` 为 false）。报告的 `report_role` 为 `classification_consistency`，`plan_usability` 为 `not_a_verification_result`。报告不是门禁结论。Planner 不把该报告写入仓库。

## 7. G1–G6 与 #150 / #151

门的唯一语义来源是当时有效的编排契约路径。本工具只保存稳定门 ID（G1、G2、G3、G4、G5、G6）和来源路径。

- #151 未 `VERIFIED` 之前，来源路径保持 `docs/orchestration.md`。仓库 HEAD 的 `docs/implementation-plan.md` §15.2.4 把 #151 登记为 `OPEN` / `PLANNED` / 未 READY，目标路径是 `docs/orchestration.md`。
- #151 迁移合并并且其状态在 GitHub 与 `docs/implementation-plan.md` 上都成为 `VERIFIED` 之后，只更新本契约里的引用路径。本工具不重写门语义，运行时也不自动切换路径。
- 禁止在 Planner 源码、`scripts/verification_planner/schema.json`、子契约定稿 `docs/design/verification-planner-b-contract.md`、测试夹具里复制门的通过条件。夹具只断言 `gate_id` 与 `source_path`。
- #150 仍未 `VERIFIED`（同一节登记为 `OPEN` / `PLANNED`）。本子契约不宣称自己是文件责任地图、门禁权威或阶段状态权威。

六门在任何 `edition`、任何级别下都出现，不由等级挑选或删除。

## 8. 提议文件范围

任务允许路径使用目录前缀比较（与 `scripts/orchestrator.py` 的 `overlaps` 一致），不使用通配符。`normalize_path` 会拒绝含星号、问号或方括号的路径。

目录前缀：

- `scripts/verification_planner`
- `tests/fixtures/verification_planner`

具体文件：

- `scripts/verification_planner/__init__.py`
- `scripts/verification_planner/classify.py`
- `scripts/verification_planner/cli.py`
- `scripts/verification_planner/schema.json`
- `tests/test_verification_planner.py`
- `docs/design/verification-planner-b-contract.md`
- 附录 E 每个夹具各两个文件：`tests/fixtures/verification_planner/<夹具名>.json` 与 `tests/fixtures/verification_planner/<夹具名>.expected.json`

`docs/design/verification-planner-b-contract.md` 已按 Owner 书面冻结的文本落地。落地文本不得复制 G1–G6 的通过条件，也不得把父级文稿改成已实现。

### 8.1 发行清单与静态检查

本阶段这些文件是仓库内工具，**不进入** `scripts/validate_kit.py` 的 `REQUIRED`。因此不修改 `scripts/validate_kit.py`，不把它列入路径。`scripts/build_release.py` 只打包 `REQUIRED`；`release/README.md` 所写的包内集合（`REQUIRED` 加构建生成的 `MANIFEST.json`）不包含本工具。包内自检与 Kit zip **因此不覆盖**这些模块。这是本阶段的明确选择，不是遗漏。

静态语法不修改 `docs/PROJECT.md` 的现行命令（仍是 `python -m py_compile scripts/orchestrator.py scripts/validate_kit.py`）。该命令不覆盖新文件。语法证据由 `tests/test_verification_planner.py` 用标准库 `importlib` 按文件路径加载 `classify.py` 与 `cli.py`：加载成功即语法通过。该导入不替代 `py_compile`。

`docs/PROJECT.md` 与 `docs/implementation-plan.md` 默认都不在本任务路径里。前者若只为新目录追加允许路径，仍会改到 G2 所读的项目范围文本；本阶段不改。新文件的任务允许路径以本节为准，不自动成为项目常设允许路径。后者见第 1.1 节：计划文本与 GitHub 不一致时停下，而不是在本任务里改状态行。

不改 `docs/orchestration.md`、`scripts/orchestrator.py`、`.github/workflows/` 下的工作流、Required Checks、状态机、锁、权限或生产 gate。#188 仍需独立 Owner 授权。

### 8.2 写集合

Planner 进程的写集合为空：不写项目路径，不写 `.orchestration/`，不写输出文件，不写 SQLite，不访问网络。测试框架的临时目录不算 Planner 写入。测试可以在临时目录放置输入夹具；Planner 只读该路径，并只向 stdout 写 JSON。

## 9. 职责边界

- **#176：** Task Risk / Context / Independence 可与本工具共享输入事实。拓扑推荐、派发和验证风险是三份独立输出。本 Planner 不选择角色，不派发 Agent，不输出拓扑建议。禁止字段 `topology_advice`（退出码 2）。
- **#187：** 影响面测试选择、旧 SHA 等价性、证据复用不在本范围。动态依赖未知时只按 `R-DYN-UNKNOWN` 升档，不声称完整影响图。
- **#188：** 任何会改变现行 required check 的决定都不在本范围。附录 D 在 Owner 书面确认之前差异为空，就是为了避免在影子格式里做门禁政策。父级 §11.1 把「哪些 required check 可按风险变化」保留为 Owner 决策。
- **#170：** 身份核验方法未冻结前，本契约的身份枚举没有「已证明」取值。
- **#174：** 未来告警方向不是本工具的派发输出。现行 C1–C4 规则按第 3.2 节保持有效。

---

## 附录 A — 硬 `rule_id`

谓词 floor 取命中规则的最大级别。负向自述与覆盖声明对每一行都不得把 floor 压低。不确定性只允许再抬高。标记为高危的规则在第 2.1 节第 7 步之后仍保证最终级别至少 L3。

### A.1 路径

比较使用正斜杠相对路径，不用通配符。

| `rule_id` | 输入谓词 | floor | 高危（max 之后仍至少 L3） |
|---|---|---|---|
| `R-PATH-ORCHESTRATOR` | `path` 等于 `scripts/orchestrator.py` | L3 | 是 |
| `R-PATH-KIT` | `path` 等于 `kit`，或以 `kit/` 开头 | L3 | 是 |
| `R-PATH-CI` | `path` 以 `.github/workflows/` 开头 | L3 | 是 |
| `R-PATH-PROJECT` | `path` 等于 `docs/PROJECT.md` | L3 | 是 |
| `R-PATH-PLAN` | `path` 等于 `docs/PLAN.md` | L3 | 是 |
| `R-PATH-AGENTS` | `path` 等于 `AGENTS.md` | L3 | 是 |
| `R-PATH-EDITION` | `path` 以 `editions/` 开头 | L3 | 是 |
| `R-PATH-TEMPLATE` | `path` 以 `templates/` 开头 | L3 | 是 |
| `R-PATH-RELEASE` | `path` 等于 `VERSION.md`，或以 `release/` 开头 | L3 | 是 |
| `R-PATH-PACKAGE` | `path` 等于 `scripts/validate_kit.py` 或 `scripts/build_release.py` | L3 | 是 |
| `R-DOC-PLAIN` | 每条路径的首段都是 `docs`，且没有任何路径命中本表中 floor 为 L3 的规则，且没有 `R-DOC-EXECUTABLE` / `R-SEM-POLICY` / `R-SEM-CHECKLIST` / `R-SEM-CONTRACT`，且首段种类数为 1 | L0 | 否 |
| `R-LOCAL` | 存在未命中本表中 floor 为 L3 的规则的路径，且首段种类数为 1，且 `R-DOC-PLAIN` 不成立 | L1 | 否 |
| `R-CROSS-MODULE` | 变更路径的首段种类数 ≥ 2 | L2 | 否 |

`docs/PROJECT.md` 同时形如文档路径，只发 `R-PATH-PROJECT`，不发 `R-DOC-PLAIN`。多条路径规则可以同时出现，`path_floor` 取最大。

### A.2 正向语义

| `rule_id` | `signal_id` | floor | 高危 |
|---|---|---|---|
| `R-DOC-EXECUTABLE` | 路径首段为 `docs`，且信号为 `executable_example` 或 `instruction` | L2 | 否（但最终级别不得为 L0；本阶段由 `R-UNVERIFIED-SOURCE` 升到 L3） |
| `R-SEM-EXECUTABLE` | 信号为 `executable_example` 或 `instruction`，且 `R-DOC-EXECUTABLE` 未覆盖该信号 | L2 | 否 |
| `R-SEM-CROSS` | `cross_module` | L2 | 否 |
| `R-SEM-INTERFACE` | `interface_change` | L2 | 否 |
| `R-SEM-POLICY` | `policy` | L3 | 是 |
| `R-SEM-CHECKLIST` | `checklist` | L3 | 是 |
| `R-SEM-CONTRACT` | `contract_text` | L3 | 是 |
| `R-SEM-STATE` | `state_machine` | L3 | 是 |
| `R-SEM-LOCK` | `lock` | L3 | 是 |
| `R-SEM-PERMISSION` | `permission` | L3 | 是 |
| `R-SEM-SECURITY` | `security` | L3 | 是 |
| `R-SEM-REVIEWER` | `reviewer_identity` | L3 | 是 |
| `R-SEM-DISPATCH` | `dispatch` | L3 | 是 |
| `R-SEM-MIGRATION` | `migration_or_upgrade` | L3 | 是 |
| `R-SEM-RELEASE` | `release` | L3 | 是 |
| `R-SEM-EVIDENCE` | `evidence_gate` | L3 | 是 |

未知 `signal_id`：退出码 2，`unknown_field`。矛盾信号（例如路径谓词为 L0 而语义为 L3）只取更高 floor，不平均、不互相抵消。

### A.3 不确定性、身份与非探针条件

| `rule_id` | 输入谓词 | 抬高 | 高危 |
|---|---|---|---|
| `R-UNVERIFIED-SOURCE` | 生成了计划（本工具不能核验来源） | `uncertainty_floor` 至少 L3 | 否（级别效应由 uncertainty 承担） |
| `R-BASELINE-UNFROZEN` | 本契约没有冻结的现行检查基线条数 | 同上 | 否 |
| `R-DYN-UNKNOWN` | `dynamic_dependencies = unknown` | 同上；候选不得短于快照；差异必须为空 | 否 |
| `R-DECLARED-UNCERTAINTY` | `uncertainties` 非空 | 同上 | 否 |
| `R-STALE-REVIEW` | `semantic_review` 与 `head_sha` 不齐或 `stale = true` | 同上 | 否 |
| `R-STALE-PROFILE` | `profile.as_of_sha` 不等于 `head_sha` | 同上 | 否 |
| `R-NEGATIVE-NO-DOWNGRADE` | 出现任一负向 `signal_id` | 不降；追加未知项 `negative_self_report` | 否 |
| `R-IDENTITY-UNPROVEN` | `requires_independent_reviewer = true` | `uncertainty_floor` 至少 L3 | 是 |
| `R-NONPROBE-CALL` | `call_failure_observed = true` | `semantic_floor` 至少 L3 | 是 |
| `R-NONPROBE-PERMISSION` | `permission_constraint = true` | 同上 | 是 |
| `R-NONPROBE-LOCK` | `lock_constraint = true` | 同上 | 是 |
| `R-NONPROBE-SECURITY` | `security_constraint = true` | 同上 | 是 |
| `R-HIGH-AFTER-MAX` | 任一「高危 = 是」的规则已命中 | 最终级别至少 L3 | 本规则就是后处理 |

负向 `signal_id` 只有：`no_high_risk_impact`、`semantic_review_complete`、`high_risk_checks_already_covered`、`self_reported_l0`。

`profile.checks` 不出现在任何谓词里。

## 附录 B — JSON Schema 草稿

草稿为 JSON Schema draft-07。`additionalProperties` 均为 false。实现把 `scripts/verification_planner/schema.json` 作为同一份草稿的机器副本；副本与本文冲突时，以 Owner 冻结后的本文为准，并应先改本文。

### B.1 输入 `verification-planner-input/1`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "verification-planner-input/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "task_ref", "edition", "base_sha", "head_sha",
    "acceptance", "changed_paths", "semantic_signals", "uncertainties",
    "semantic_review", "profile", "project_floor",
    "requires_independent_reviewer", "dynamic_dependencies",
    "non_probe_conditions"
  ],
  "properties": {
    "schema_version": { "const": "verification-planner-input/1" },
    "task_ref": { "type": "string", "minLength": 1, "maxLength": 200 },
    "edition": { "enum": ["local", "github"] },
    "base_sha": { "type": "string", "pattern": "^[0-9a-f]{40}$" },
    "head_sha": { "type": "string", "pattern": "^[0-9a-f]{40}$" },
    "acceptance": {
      "type": "array", "minItems": 1,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["ac_id", "text"],
        "properties": {
          "ac_id": { "type": "string", "pattern": "^[A-Za-z0-9._-]{1,64}$" },
          "text": { "type": "string", "minLength": 1, "maxLength": 500 }
        }
      }
    },
    "changed_paths": {
      "type": "array", "minItems": 1,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["path", "source_ref"],
        "properties": {
          "path": { "type": "string", "minLength": 1, "maxLength": 300 },
          "source_ref": { "type": "string", "pattern": "^[A-Za-z0-9_.:/#-]{1,200}$" }
        }
      }
    },
    "semantic_signals": {
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["signal_id", "source_ref"],
        "properties": {
          "signal_id": {
            "enum": [
              "executable_example", "instruction", "policy", "checklist",
              "contract_text", "cross_module", "interface_change",
              "state_machine", "lock", "permission", "security",
              "reviewer_identity", "dispatch", "migration_or_upgrade",
              "release", "evidence_gate", "no_high_risk_impact",
              "semantic_review_complete", "high_risk_checks_already_covered",
              "self_reported_l0"
            ]
          },
          "source_ref": { "type": "string", "pattern": "^[A-Za-z0-9_.:/#-]{1,200}$" }
        }
      }
    },
    "uncertainties": {
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["item_id", "source_ref"],
        "properties": {
          "item_id": { "type": "string", "pattern": "^[a-z0-9._-]{1,80}$" },
          "source_ref": { "type": "string", "pattern": "^[A-Za-z0-9_.:/#-]{1,200}$" }
        }
      }
    },
    "semantic_review": {
      "type": "object", "additionalProperties": false,
      "required": ["reviewed_sha", "stale"],
      "properties": {
        "reviewed_sha": {
          "anyOf": [
            { "type": "string", "pattern": "^[0-9a-f]{40}$" },
            { "type": "null" }
          ]
        },
        "stale": { "type": "boolean" }
      }
    },
    "profile": {
      "type": "object", "additionalProperties": false,
      "required": ["profile_version", "source_ref", "as_of_sha", "checks"],
      "properties": {
        "profile_version": { "type": "string", "minLength": 1, "maxLength": 80 },
        "source_ref": { "type": "string", "pattern": "^[A-Za-z0-9_.:/#-]{1,200}$" },
        "as_of_sha": { "type": "string", "pattern": "^[0-9a-f]{40}$" },
        "checks": {
          "type": "array", "minItems": 1,
          "items": {
            "type": "object", "additionalProperties": false,
            "required": ["check_id", "scope", "source_ref"],
            "properties": {
              "check_id": { "type": "string", "pattern": "^[a-z0-9][a-z0-9._-]{0,63}$" },
              "scope": { "type": "string", "enum": ["quick", "full", "static", "post_merge", "ci", "doc"] },
              "source_ref": { "type": "string", "pattern": "^[A-Za-z0-9_.:/#-]{1,200}$" }
            }
          }
        }
      }
    },
    "project_floor": { "enum": ["L0", "L1", "L2", "L3"] },
    "requires_independent_reviewer": { "type": "boolean" },
    "dynamic_dependencies": { "enum": ["known", "unknown", "not_applicable"] },
    "non_probe_conditions": {
      "type": "object", "additionalProperties": false,
      "required": [
        "call_failure_observed", "permission_constraint",
        "lock_constraint", "security_constraint"
      ],
      "properties": {
        "call_failure_observed": { "type": "boolean" },
        "permission_constraint": { "type": "boolean" },
        "lock_constraint": { "type": "boolean" },
        "security_constraint": { "type": "boolean" }
      }
    },
    "runtime_probe": {
      "type": "object", "additionalProperties": false,
      "required": ["items"],
      "properties": {
        "items": {
          "type": "array",
          "items": {
            "type": "object", "additionalProperties": false,
            "required": ["probe_id", "caller_code"],
            "properties": {
              "probe_id": { "enum": ["C1", "C2", "C3", "C4"] },
              "caller_code": { "enum": ["missing", "not_executed", "stale", "unproven_fail"] }
            }
          }
        }
      }
    },
    "host_tool_absence_claims": {
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["probe_id", "evidence_ref"],
        "properties": {
          "probe_id": { "enum": ["C1", "C2", "C3", "C4"] },
          "evidence_ref": { "type": "string", "pattern": "^[A-Za-z0-9_.:/#-]{1,200}$" }
        }
      }
    }
  }
}
```

`profile.checks[]` 若带结果类字段，不走「未知字段 → 退出码 2」。解析器先剥离第 4.2 节列出的结果字段，再按上面的白名单校验剩余键。其余未知键仍是退出码 2。

禁止字段出现在任何层级都是退出码 2、诊断码 `forbidden_field`：`declared_risk_level`、`status`（作为试图保留的结果键，在剥离名单中的处理见上句）、`identity_ok`、`reviewer_identity_proven`、`model_name`、`model_alias`、`temperature`、`prompt_hash`、`cached_review_ok`、`attempt_allowed`、`dispatch_ok`、`may_dispatch`、`unlock`、`handover_authorized`、`skip`、`override_approver`、`approved_by`、`waiver`、`checks_cover_high_risk`、`source_verified`、`topology_advice`，以及第 1.1 节列出的父级 §8 度量字段。`status` 若出现在 `profile.checks[]` 内，走剥离而不是退出码 2；出现在其他对象上，走退出码 2。

### B.2 计划输出 `verification-planner-output/1`

退出码 3 专用。`counterfactual_diff.maxItems` 为 0，直到将来的契约修订在 Owner 书面确认附录 D 之后另行打开。本契约没有该修订。

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "verification-planner-output/1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "planner_version", "plan_usability", "mode",
    "task_ref", "edition", "base_sha", "head_sha", "closure",
    "closure_reasons", "identity", "policy", "classification",
    "gates", "ac_map", "checks", "observations"
  ],
  "properties": {
    "schema_version": { "const": "verification-planner-output/1" },
    "planner_version": { "const": "186-draft-2" },
    "plan_usability": { "const": "not_a_verification_result" },
    "mode": { "const": "shadow_only" },
    "task_ref": { "type": "string" },
    "edition": { "enum": ["local", "github"] },
    "base_sha": { "type": "string", "pattern": "^[0-9a-f]{40}$" },
    "head_sha": { "type": "string", "pattern": "^[0-9a-f]{40}$" },
    "closure": { "const": "open" },
    "closure_reasons": {
      "type": "array", "minItems": 1,
      "items": {
        "enum": [
          "source_not_verified", "mandatory_baseline_unfrozen",
          "dynamic_dependency_unknown", "declared_uncertainty",
          "semantic_review_not_current", "profile_not_current",
          "negative_self_report", "identity_unproven",
          "profile_result_field_stripped"
        ]
      }
    },
    "identity": {
      "type": "object", "additionalProperties": false,
      "required": ["requirement", "assessment"],
      "properties": {
        "requirement": { "enum": ["required", "not_required"] },
        "assessment": { "enum": ["unproven", "not_assessed"] }
      }
    },
    "policy": {
      "type": "object", "additionalProperties": false,
      "required": ["effect", "refs"],
      "properties": {
        "effect": { "const": "current_rules_unchanged" },
        "refs": {
          "type": "array", "minItems": 3, "maxItems": 3,
          "items": [
            { "const": "docs/orchestration.md" },
            { "const": "editions/local/master.md" },
            { "const": "editions/github/master.md" }
          ]
        }
      }
    },
    "classification": {
      "type": "object", "additionalProperties": false,
      "required": ["level", "floors", "rule_ids", "unknown_items"],
      "properties": {
        "level": { "const": "L3" },
        "floors": {
          "type": "object", "additionalProperties": false,
          "required": ["path_floor", "semantic_floor", "uncertainty_floor", "project_floor"],
          "properties": {
            "path_floor": { "enum": ["L0", "L1", "L2", "L3"] },
            "semantic_floor": { "enum": ["L0", "L1", "L2", "L3"] },
            "uncertainty_floor": { "const": "L3" },
            "project_floor": { "enum": ["L0", "L1", "L2", "L3"] }
          }
        },
        "rule_ids": { "type": "array", "items": { "type": "string" } },
        "unknown_items": { "type": "array", "items": { "type": "string" } }
      }
    },
    "gates": {
      "type": "array", "minItems": 6, "maxItems": 6,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["gate_id", "source_path"],
        "properties": {
          "gate_id": { "enum": ["G1", "G2", "G3", "G4", "G5", "G6"] },
          "source_path": { "const": "docs/orchestration.md" }
        }
      }
    },
    "ac_map": {
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["ac_id", "matched_rule_ids", "gate_ids", "check_ids"],
        "properties": {
          "ac_id": { "type": "string" },
          "matched_rule_ids": { "type": "array", "items": { "type": "string" } },
          "gate_ids": {
            "type": "array", "minItems": 6, "maxItems": 6,
            "items": { "enum": ["G1", "G2", "G3", "G4", "G5", "G6"] }
          },
          "check_ids": { "type": "array", "items": { "type": "string" } }
        }
      }
    },
    "checks": {
      "type": "object", "additionalProperties": false,
      "required": ["authority", "caller_snapshot", "shadow_candidates", "counterfactual_diff"],
      "properties": {
        "authority": { "const": "unverified_snapshot" },
        "caller_snapshot": { "type": "array" },
        "shadow_candidates": { "type": "array" },
        "counterfactual_diff": { "type": "array", "maxItems": 0 }
      }
    },
    "observations": {
      "type": "object", "additionalProperties": false,
      "required": ["runtime_probe"],
      "properties": {
        "runtime_probe": {
          "type": "object", "additionalProperties": false,
          "required": ["items", "tool_absence_claims"],
          "properties": {
            "items": { "type": "array" },
            "tool_absence_claims": {
              "type": "array",
              "items": {
                "type": "object", "additionalProperties": false,
                "required": ["probe_id", "evidence_ref", "level_effect", "dispatch_effect"],
                "properties": {
                  "probe_id": { "enum": ["C1", "C2", "C3", "C4"] },
                  "evidence_ref": { "type": "string" },
                  "level_effect": { "const": "none" },
                  "dispatch_effect": { "const": "none" }
                }
              }
            }
          }
        }
      }
    }
  }
}
```

本阶段每一份计划的 `closure` 都是 `open`：来源一律未核验，基线一律未冻结。schema 不提供 `closed`。身份要求为 `required` 时，`assessment` 只能是 `unproven`；为 `not_required` 时只能是 `not_assessed`。测试断言这两种组合，并断言 `observations.runtime_probe` 内不出现 `requirement` 或 `assessment`。

`checks.caller_snapshot[]` 与 `checks.shadow_candidates[]` 的元素只含 `check_id`、`scope`、`source_ref`。两者的 `check_id` 序列必须相同。

### B.3 诊断输出 `verification-planner-diagnostic/1`

退出码 2 专用。对象不得包含 `classification`、`checks`、`gates`、`ac_map`、`identity`。

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "verification-planner-diagnostic/1",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "plan_usability", "diagnostic"],
  "properties": {
    "schema_version": { "const": "verification-planner-diagnostic/1" },
    "plan_usability": { "const": "not_a_verification_result" },
    "diagnostic": {
      "type": "object", "additionalProperties": false,
      "required": ["code", "path"],
      "properties": {
        "code": {
          "enum": [
            "schema_invalid", "sha_invalid", "edition_invalid",
            "unknown_field", "forbidden_field", "path_invalid", "usage"
          ]
        },
        "path": { "type": "string" }
      }
    }
  }
}
```

## 附录 C — 退出码分界

| 退出码 | 何时 | stdout | 是否表示合格 |
|---|---|---|---|
| 2 | 结构无效、用法错误、未知字段、禁止字段、非法 SHA、非法 `edition`、非法路径 | 诊断 JSON，无计划 | 否 |
| 3 | 结构有效并写出影子计划。本阶段包括全部「证据不完整、身份未证明、必须保守升档、来源未核验」的计划 | 计划 JSON，`closure = open` | 否。调用方必须读取 `plan_usability` 与 `closure` |
| 0 | 本 CLI 不使用 | 无 | 退出码 0 不是本工具的合格，也不是「计划已写出」 |

其他退出码（含进程异常）不是合格。测试把不是 2 或 3 的退出视为失败。

分界对照：

| 输入问题 | 退出码 | 诊断码或 `closure_reasons` |
|---|---|---|
| SHA 不是 `^[0-9a-f]{40}$` | 2 | `sha_invalid` |
| `edition` 非法 | 2 | `edition_invalid` |
| 未知 JSON 字段 | 2 | `unknown_field` |
| 禁止字段（含试图排除 L3 的覆盖字段、身份自述、派发许可、拓扑建议、父级 §8 度量） | 2 | `forbidden_field` |
| 路径含通配符、`..` 或绝对路径 | 2 | `path_invalid` |
| 参数不是 `--input PATH` | 2 | `usage` |
| `profile.checks[]` 内的结果字段 | 3 | 剥离；`profile_result_field_stripped` |
| 来源未核验（每份计划） | 3 | `source_not_verified` |
| 基线未冻结（每份计划） | 3 | `mandatory_baseline_unfrozen` |
| 动态依赖 `unknown` | 3 | `dynamic_dependency_unknown`；差异仍为空 |
| 身份要求为真 | 3 | `identity_unproven` |
| 审阅 SHA 不齐或 `stale` | 3 | `semantic_review_not_current` |
| profile SHA 不齐 | 3 | `profile_not_current` |
| 负向自述 | 3 | `negative_self_report`；floor 不下降 |

## 附录 D — 影子候选对照表

本表是 `shadow_only`。它不改变现行检查集合，不产生 `skip`，不进入 Required Checks。

Owner 尚未书面确认任何「按级别减少检查」的对照之前，每个级别的生效候选都等于该次输入的 `checks.caller_snapshot`，`counterfactual_diff` 必须为空。确认不能来自快照字段。

| 最终级别 | 生效 `shadow_candidates` | `counterfactual_diff` | 父级 §4 示例（未生效，不得实现为减检或增检） |
|---|---|---|---|
| L0 | 等于本次 `caller_snapshot` 的 `check_id` 序列 | `[]` | 文档校验、引用 / 路径检查、Diff 检视 |
| L1 | 同上 | `[]` | 关联单测、语法 / 格式、变更场景 |
| L2 | 同上 | `[]` | 关联单测、集成 / 负向回归、独立审查 |
| L3 | 同上 | `[]` | 完整相关回归、端到端 / 负向 / 失败关闭、双底座与发布兼容、独立审查、Owner 决策 |

右列只复述父级提案里的例子，说明那些句子**不是**本契约的 `check_id`，也不是可以少跑的清单。本工具不把它们映射成命令。

不完整输入与 `dynamic_dependencies = unknown` 同样遵守「不得短于本次快照、差异不得列出移除项」。因为差异恒为空，移除项不会出现。

## 附录 E — 夹具与预期值

公共输入（下表只写差异；未列出的字段与此相同）：

- `schema_version`: `verification-planner-input/1`
- `task_ref`: `issue-186-fixture`
- `edition`: `local`
- `base_sha`: `1111111111111111111111111111111111111111`
- `head_sha`: `2222222222222222222222222222222222222222`
- `acceptance`: `[{ "ac_id": "AC1", "text": "wording" }]`
- `changed_paths`: `[{ "path": "docs/charter.md", "source_ref": "caller:diff/1" }]`
- `semantic_signals`: `[]`
- `uncertainties`: `[]`
- `semantic_review`: `{ "reviewed_sha": "2222222222222222222222222222222222222222", "stale": false }`
- `profile.profile_version`: `caller-unfrozen`
- `profile.source_ref`: `caller:profile/1`
- `profile.as_of_sha`: 与 `head_sha` 相同
- `profile.checks`: 四项，`check_id` 依次为 `project.validate_kit`（`quick`）、`project.unittest`（`full`）、`project.py_compile`（`static`）、`project.backup`（`post_merge`），`source_ref` 均为 `docs/PROJECT.md#verification-commands`
- `project_floor`: `L0`
- `requires_independent_reviewer`: `false`
- `dynamic_dependencies`: `not_applicable`
- `non_probe_conditions`: 四个布尔都是 `false`

这四个 `check_id` 只是夹具里的调用者标签，对应 `docs/PROJECT.md` 现有命令的称呼。它们不是本工具内置的权威基线。

凡是退出码 3 的夹具，都另外满足第 5.1 节不变量，以及：

- `plan_usability` = `not_a_verification_result`
- `mode` = `shadow_only`
- `closure` = `open`
- `authority` = `unverified_snapshot`
- `gates` 六项且 `source_path` 都是 `docs/orchestration.md`，顺序 G1–G6
- `counterfactual_diff` = `[]`
- `shadow_candidates` 的 `check_id` 序列等于该夹具自己的 `caller_snapshot`
- `unknown_items` 至少含 `source_not_verified` 与 `mandatory_baseline_unfrozen`（对应 `closure_reasons` 同名）
- `policy.effect` = `current_rules_unchanged`
- `identity.assessment` 不是 `unproven`，除非该夹具把 `requires_independent_reviewer` 设为 `true`
- stderr 为空
- 禁用词扫描通过

| 夹具 | 相对公共输入的差异 | 退出码 | 关键预期 |
|---|---|---|---|
| `fx_l0_plain_doc` | 无 | 3 | `path_floor=L0`，`semantic_floor=L0`，`project_floor=L0`，`uncertainty_floor=L3`，`level=L3`；`rule_ids` 含 `R-DOC-PLAIN`、`R-UNVERIFIED-SOURCE`、`R-BASELINE-UNFROZEN`；不含 `R-HIGH-AFTER-MAX`、`R-IDENTITY-UNPROVEN` |
| `fx_l1_local` | 唯一路径 `scripts/report_metrics.py` | 3 | `path_floor=L1`，`rule_ids` 含 `R-LOCAL`；不含 `R-DOC-PLAIN`；`level=L3` |
| `fx_l2_cross` | 路径 `scripts/report_metrics.py` 与 `tests/test_report_metrics_note.py` | 3 | `path_floor=L2`，`rule_ids` 含 `R-CROSS-MODULE`，不含 `R-LOCAL` 与 `R-DOC-PLAIN`；`level=L3`；候选仍等于该夹具快照的四项 |
| `fx_l3_orchestrator` | 唯一路径 `scripts/orchestrator.py` | 3 | `path_floor=L3`；`rule_ids` 含 `R-PATH-ORCHESTRATOR` 与 `R-HIGH-AFTER-MAX`；`level=L3` |
| `fx_doc_executable` | 增加信号 `executable_example`，`source_ref` 为 `caller:sem/1` | 3 | `path_floor=L0`，`semantic_floor=L2`；含 `R-DOC-EXECUTABLE`；不含 `R-DOC-PLAIN`；`level=L3` 且 `level` 不是 L0 |
| `fx_contradiction_max` | 路径仍为 `docs/charter.md`，另加信号 `state_machine` | 3 | `path_floor=L0`，`semantic_floor=L3`；含 `R-SEM-STATE` 与 `R-HIGH-AFTER-MAX`；`level=L3`。两个 floor 只取更高者 |
| `fx_monotonic_before` | 与 `fx_l0_plain_doc` 相同 | 3 | 同 `fx_l0_plain_doc` |
| `fx_monotonic_after` | 在 before 的路径之外再加 `scripts/orchestrator.py` | 3 | `path_floor` 不低于 before；`level` 不低于 before；`rule_ids` 含 `R-PATH-ORCHESTRATOR`；该夹具候选长度等于其快照长度，不短于 before 的候选；两边 `counterfactual_diff` 都为空 |
| `fx_negative_cannot_exclude` | 路径 `scripts/orchestrator.py`；信号含 `no_high_risk_impact`、`semantic_review_complete`、`high_risk_checks_already_covered`、`self_reported_l0` | 3 | `path_floor=L3`，`level=L3`；含 `R-PATH-ORCHESTRATOR`、`R-HIGH-AFTER-MAX`、`R-NEGATIVE-NO-DOWNGRADE`；`closure_reasons` 含 `negative_self_report`；候选仍为四项；差异为空 |
| `fx_coverage_forbidden` | 顶层额外字段 `checks_cover_high_risk: true` | 2 | `diagnostic.code=forbidden_field`；无 `classification` |
| `fx_profile_status_stripped` | 第一项 check 额外带 `"status": "PASS"` | 3 | 输出全文不含 `PASS`，也不含键 `status`；`closure_reasons` 含 `profile_result_field_stripped`；`level=L3`。输入里的该词不得被回显成合格 |
| `fx_bad_sha` | `base_sha` 为 `abc` | 2 | `diagnostic.code=sha_invalid`；无 `classification` |
| `fx_unknown_field` | 顶层 `declared_note: true` | 2 | `diagnostic.code=unknown_field`；无 `classification` |
| `fx_forbidden_declared_risk` | 顶层 `declared_risk_level: L0` | 2 | `diagnostic.code=forbidden_field`；无 `classification` |
| `fx_identity_field_forbidden` | 顶层 `identity_ok: true` | 2 | `diagnostic.code=forbidden_field`；无 `classification` |
| `fx_identity_required` | `requires_independent_reviewer: true`；另加 `runtime_probe.items` 一项 C1/`missing` | 3 | `identity.requirement=required`，`identity.assessment=unproven`；含 `R-IDENTITY-UNPROVEN` 与 `R-HIGH-AFTER-MAX`；`level=L3`；`closure_reasons` 含 `identity_unproven`；探针项仍在 `observations.runtime_probe.items`；该对象内没有 `assessment`；候选仍为四项；差异为空 |
| `fx_probe_observation` | `runtime_probe.items` 为 C1/`missing`、C2/`not_executed`、C3/`stale`、C4/`unproven_fail` | 3 | 四个 floor 与 `level` 都与 `fx_l0_plain_doc` 相同；不含 `R-IDENTITY-UNPROVEN`；`policy.effect=current_rules_unchanged`；输出不含派发许可键；`identity.assessment=not_assessed` |
| `fx_tool_absent` | `host_tool_absence_claims` 一项：C1，`evidence_ref` 为 `caller:tool-absence/1` | 3 | `tool_absence_claims[0].level_effect=none` 且 `dispatch_effect=none`；四个 floor 与 `level` 同 `fx_l0_plain_doc`；`identity` 内不出现该 `evidence_ref`；无派发许可键 |
| `fx_nonprobe_lock` | `lock_constraint: true` | 3 | `rule_ids` 含 `R-NONPROBE-LOCK` 与 `R-HIGH-AFTER-MAX`；`semantic_floor=L3`；该规则不在 `observations.runtime_probe` 内；`level=L3` |
| `fx_stale_review` | `reviewed_sha` 为 `3333333333333333333333333333333333333333` | 3 | `closure_reasons` 含 `semantic_review_not_current`；含 `R-STALE-REVIEW`；`level=L3`；floor 不低于公共情形 |
| `fx_stale_profile` | `profile.as_of_sha` 为 `4444444444444444444444444444444444444444` | 3 | `closure_reasons` 含 `profile_not_current`；含 `R-STALE-PROFILE`；`level=L3` |
| `fx_dynamic_unknown` | `dynamic_dependencies: unknown` | 3 | 含 `R-DYN-UNKNOWN`；`closure_reasons` 含 `dynamic_dependency_unknown`；`shadow_candidates` 长度等于快照长度（4）；`counterfactual_diff=[]`；`level=L3` |
| `fx_edition_local` | 与 `fx_l0_plain_doc` 相同 | 3 | 作为 github 孪生夹具的对照 |
| `fx_edition_github` | 仅 `edition: github` | 3 | `level`、`rule_ids`、`unknown_items` 与 `fx_edition_local` 相同；`edition=github`；快照仍是输入的四项，不是从 local 夹具删减来的另一套 |
| `fx_short_snapshot` | `profile.checks` 只保留 `project.validate_kit` | 3 | `authority=unverified_snapshot`；含 `mandatory_baseline_unfrozen`；候选长度是 1，等于该快照，不短于它；差异为空；输出不把该快照标成已核对的现行集合 |
| `fx_gates_present` | 与 `fx_l0_plain_doc` 相同 | 3 | `gates` 长度 6；`gate_id` 集合为 G1–G6；每项 `source_path` 为 `docs/orchestration.md`；门对象没有通过条件字段 |

`test_classification_consistency_report`：对上表每一行生成内存报告。退出码 3 的行 `actual_level` 等于表中 `level`（未单列 `level` 时为 L3）。退出码 2 的行 `has_classification=false`。报告本身通过第 5.1 节禁用词扫描。

`test_process_write_set_empty`：在临时目录准备输入，记录 Planner 可见的项目根目录文件清单，运行 CLI，再记录清单。差集为空。测试框架自己的临时目录允许变化，且该目录不是项目路径，也不是 `.orchestration/`。

夹具文件名就是上表第一列。每个夹具两个文件，路径见第 8 节。`.expected.json` 至少包含该行的退出码、诊断码（若有）、四个 floor（若有）、`level`（若有）、必须出现的 `rule_id`、必须出现的 `closure_reasons`，以及 `counterfactual_diff` 为空。
