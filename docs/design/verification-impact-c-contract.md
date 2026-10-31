# AVE C 阶段只读影响面与证据影子契约（Owner 直接实施）

> **状态：FROZEN / 已由 Owner 要求直接实施。** 本文是 Issue #187 的 C 阶段子契约，落地来源为
> [Issue #187 评论 6537003302](https://github.com/ctrlshiftaltdel/AgentOrch/issues/187#issuecomment-6537003302)。
> 时间：2026-10-31。Owner 在派发登记
> [评论 6537114477](https://github.com/ctrlshiftaltdel/AgentOrch/issues/187#issuecomment-6537114477)
> 中去掉冻结问答，要求按评论 6537003302 **直接实施只读影子**，不改门禁。
>
> 父级提案 [adaptive-verification-and-quality-gates.md](adaptive-verification-and-quality-gates.md)
> 继续保持 `PROPOSED / NOT IMPLEMENTED`；本文不修改它，也不改写 B 阶段子契约。
> 父级 §5 的 `PASS_EXECUTED` 与 `PASS_EQUIVALENCE_ATTESTED` 不进入本工具。
>
> 输出固定标记：`plan_usability: not_a_verification_result`、`mode: shadow_only`、
> `closure: open`。它不是 PASS、不是审批、不是 READY、不是 `VERIFIED`、不是授权、不是跳过指令。
> 新文件不进入 `scripts/validate_kit.py` 的 `REQUIRED` 集合。

## 1. 交付

只读 plan-only 影子 CLI：`python scripts/verification_impact/cli.py --input PATH`。

- 只使用 Python 3 标准库，不引入第三方依赖。
- 不执行测试，不写项目路径，不写 `.orchestration/`，不访问网络。
- JSON 只写 stdout，stderr 保持为空。
- 退出码只允许 `2`（结构拒绝，诊断 JSON）或 `3`（写出影子报告）。不使用 `0`；其他退出码视为失败。
- 结构错误（含未知字段、禁止字段、非法路径、非法 SHA、内置用法错误）返回 `2` 与诊断 JSON，不输出 traceback。
- 要求独立 Reviewer 时，身份评估只能是 `unproven`，不得输出任何身份已证明的结论。
- 实现不得导入 `scripts/agent_selection/`，也不得修改 `scripts/verification_planner/`。

## 2. 影响图

图只来自调用者快照里的节点（`impact_graph.nodes`）和显式边（`impact_graph.edges`）。本工具不做仓库扫描，因此不得声称静态分析已经找全依赖；输出固定带 `repository_scanned: false` 与 `impact.authority: caller_snapshot_only`。

- 每条变更路径必须存在一条显式的 `covers` 边，从该路径的 `changed_path` 节点指向一个 `test` 节点；否则该路径进入 `uncovered_paths`，`coverage_status=incomplete`。
- 空边集合同样不是成功：除逐路径未覆盖外，另记 `empty_mapping`。
- 动态引用缺失 / `unknown`：`coverage_status=incomplete`。空 `dynamic_references` 记 `dynamic_references_absent`，不得当作“没有动态引用”。
- 空映射不是成功；`complete` 只在没有未覆盖路径、没有动态未知且没有高危命中时出现。
- 命中下列高危路径时同样 `coverage_status=incomplete`，不得给出可代替完整集的子集：
  `scripts/orchestrator.py`、`kit/`、`.github/workflows/`、`docs/PROJECT.md`、`docs/PLAN.md`、`AGENTS.md`、`editions/`、`scripts/validate_kit.py`、`scripts/build_release.py`、`release/`。
- 建议动作始终包含调用者给出的完整检查快照（`suggested_checks == caller_snapshot`）。`counterfactual_removed` 必须为 `[]`，`subset_claimed_sufficient` 必须为 `false`。
- 映射到的测试只是观察（`mapped_tests_authority: observation_only`），不得声称子集充分或可替代完整检查集。

## 3. 旧证据

旧 SHA 上的结果不得写成当前 HEAD 重跑，也不得写成 CI context 已通过。每条复用记录都保留 `ran_on_sha` 与 `head_sha`，且 `head_rerun` 固定为 `false`。

`reuse_decision` 只允许：

| 取值 | 含义 |
|---|---|
| `not_reused` | 默认；以及强制 CI、批准类、安全类、未被调用者标为 `cacheable` 的检查 |
| `rejected` | 缺指纹、命令不同、输入 / 依赖变化、环境漂移、被改过的测试、未知检查 |
| `applicable_on_head` | 旧证据在指纹层面可适用于 head；**不是合格、不是 G4、不是 HEAD 重跑** |

`applicable_on_head` 只在同时满足时出现：命令相同、输入哈希相同、依赖指纹相同、环境指纹相同、调用者把该检查标成 `cacheable`，且该检查不是 `ci_required` / 批准类 / 安全类。缺任一指纹即 `rejected`。

- 本阶段默认所有检查都不可缓存（`checks.cache_policy: not_cacheable_by_default`，`policy.cache_default: not_cacheable`）。
- 强制 CI、批准类、安全类即使标了 `cacheable` 也是 `not_reused`。
- 环境漂移、被改过的测试、无法证明输入未变的旧缓存，一律 `rejected`。
- 相同 `evidence_id` 的重复副本可以去掉，保留一份。

## 4. 对照与审查包

- 调用者没给完整套件失败列表时，假阴性是 `UNKNOWN`，不是 `[]`。
- 没给计时时，节约时间是 `UNKNOWN`，不得估算，也不得写成 `0`。
- 给了完整套件失败列表时，必须标出映射外的失败（`out_of_mapping_failures`），并把全部失败保留在审查包中。
- 审查包必须列出每条 AC、每条强制检查、每条未知和每条拒绝复用；可以去掉同一 `evidence_id` 的重复副本，但不得因为摘要提到过就删掉失败或未知项。
- 审查包不减免现行审查门；`review_package.gates_unchanged` 固定为 `true`。

## 5. 身份与边界

- 模型家族自述、别名、温度、Prompt 哈希，以及 #170 注册表字段，都不是本工具的证据；这些字段按禁止字段拒绝（退出码 `2`）。
- 要求独立 Reviewer 时，`identity.requirement=required` 且 `identity.assessment=unproven`；审查包记 `identity:reviewer_unproven`。
- 不导入 `scripts/agent_selection/`，不改 `scripts/verification_planner/`。
- 本工具不改写 G1–G6、Required Checks、CI、`docs/PROJECT.md` 的现行验证命令、`kit/orchestration.md` 或 `scripts/orchestrator.py` 的门禁行为。#188 的生产门禁切换仍须单独授权。

## 6. 允许路径与最低夹具

允许路径：

- `scripts/verification_impact/`
- `tests/test_verification_impact.py`
- `tests/fixtures/verification_impact/`
- `docs/design/verification-impact-c-contract.md`

不改 `docs/PROJECT.md`、`docs/implementation-plan.md`、`docs/PLAN.md`、`kit/`、`scripts/orchestrator.py`、`scripts/validate_kit.py`、`scripts/verification_planner/`、`.github/`、真实 `.orchestration/`。新文件不进入 `REQUIRED`。

最低夹具（每个都有输入 JSON 与 expected JSON）：

| 夹具 | 关键预期 |
|---|---|
| `fx_clean_complete` | 正面对照；`coverage_status=complete`，建议等于快照 |
| `fx_empty_edges` | 空边不得成功；`incomplete`，含 `empty_mapping` |
| `fx_uncovered_path` | 未覆盖路径 `incomplete` |
| `fx_high_risk_path` | `scripts/orchestrator.py` 命中仍 `incomplete`，不给子集 |
| `fx_empty_dynamic` | 空动态引用不是成功 |
| `fx_old_sha_reuse` | 旧 SHA 记 `applicable_on_head`，`head_rerun=false` |
| `fx_ci_not_reused` | 强制 CI / 批准类 / 安全类均 `not_reused` |
| `fx_env_drift` | 环境漂移 `rejected` |
| `fx_modified_test` | 改过的测试拒绝旧证据 |
| `fx_family_self_report` | 家族自述下身份 `unproven` |
| `fx_false_negative_unknown` | 缺完整套件时假阴性 `UNKNOWN`，时间 `UNKNOWN` |
| `fx_mapping_missed_failure` | 完整套件失败时标出映射外失败 |
| `fx_path_invalid_dot` | `docs/./x.md` 为 `path_invalid`，退出码 `2` |
| `fx_task_ref_pass` | `task_ref=PASS` 退出码 `2`，stderr 为空 |
| `fx_identity_field_rejected` | 身份自述字段按禁止字段拒绝 |

## 7. 输出与不宣称事项

影子报告不包含任何结果字段（如 `status`、`result`、`passed`）。所有资格形状的禁用词在写 stdout 前统一扫描；命中即返回 `2` 诊断，绝不把输入回显成合格。

本契约不宣布 #188 已就绪，不把影子报告当作覆盖率证明、等价性证明或 CI 结果。`UNKNOWN` 在本契约中专用于“调用者未提供数据”的缺省标记，不表示检查通过。

## 8. 本任务验收标准（AC）

下列 AC 从评论 6537003302 的可判定要求逐条展开，供证据矩阵引用。

- **AC-1 只读边界**：`python scripts/verification_impact/cli.py --input PATH` 只把 JSON 写到 stdout，stderr 为空；不写项目文件、不写 `.orchestration/`、不联网、不执行测试，只用标准库。
- **AC-2 退出码**：只返回 `2`（结构拒绝，诊断 JSON）或 `3`（影子报告），从不返回 `0`。
- **AC-3 固定标记**：输出含 `plan_usability: not_a_verification_result`、`mode: shadow_only`、`closure: open`；不含 `PASS_EXECUTED` / `PASS_EQUIVALENCE_ATTESTED`，不作为 PASS、审批、READY、`VERIFIED`、授权或跳过指令。
- **AC-4 影响图来源**：只由快照节点与显式边构建，不扫描仓库，输出 `repository_scanned: false`。
- **AC-5 覆盖不完整**：无显式覆盖边的路径、动态引用缺失 / `unknown`、空映射、空动态引用、高危路径均 `coverage_status=incomplete`；空映射不是成功。
- **AC-6 完整快照建议**：建议始终等于调用者完整检查快照；`counterfactual_removed=[]`、`subset_claimed_sufficient=false`；映射测试只作观察。
- **AC-7 旧证据语义**：`reuse_decision` 仅 `not_reused` / `rejected` / `applicable_on_head`；旧 SHA 不写成 HEAD 重跑（`head_rerun=false`）；`applicable_on_head` 不是合格。
- **AC-8 不可复用与拒绝**：强制 CI、批准类、安全类即使 `cacheable` 也 `not_reused`；缺指纹、环境漂移、被改过的测试、无法证明输入未变均 `rejected`。
- **AC-9 对照诚实**：缺完整套件失败列表时假阴性为 `UNKNOWN`；缺计时时时间为 `UNKNOWN`；给完整套件时必须标出映射外失败。
- **AC-10 审查包完整**：列出每条 AC、每条强制检查、每条未知和每条拒绝复用；同一 `evidence_id` 可去重；不删失败或未知项。
- **AC-11 身份边界**：家族自述、别名、温度、Prompt 哈希、#170 字段不作证据（按禁止字段拒绝）；要求独立 Reviewer 时为 `unproven`；不导入 `scripts/agent_selection/`，不改 `scripts/verification_planner/`。
- **AC-12 路径与错误**：`.` / `..` / 绝对路径 / 通配符返回 `2` 与 `path_invalid`；`task_ref=PASS` 返回 `2` 诊断，stderr 为空，无 traceback。
- **AC-13 夹具与测试**：最低夹具均有输入 JSON 与 expected JSON，测试逐项断言；先写失败测试再实现。
- **AC-14 设计稿与 REQUIRED**：评论落地为本文件并标明 Owner 2026-10-31 直接实施；父级保持 `PROPOSED / NOT IMPLEMENTED`；新文件不进入 `REQUIRED`。
- **AC-15 仓库验证**：以下命令全部真实执行并记录退出码，变更只落在允许路径：`unittest tests.test_verification_impact`、`validate_kit.py`、`unittest discover -s tests`、`run_delivery_scenarios.py --json`、`py_compile`、`build_release.py`、`git diff --check`。
