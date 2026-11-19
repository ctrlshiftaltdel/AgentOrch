# AgentOrch 阶段计划（当前推进指针）

> 本文件记录阶段顺序与已知状态；设计目标和边界见 [PLAN.md](PLAN.md)，规则定义见 [编排契约](../kit/orchestration.md)。Issue / PR 是任务状态与证据来源，关闭不等于完成。状态以查询时的载体为准。

## 当前状态与次序（2026-11-19）

| 任务 | 状态 |
|---|---|
| [#230](https://github.com/ctrlshiftaltdel/AgentOrch/issues/230) | **P0，正在实施**：按所有者本次纠偏授权，优先推进计划 → 真实 Agent 执行 → 验证 → 独立审查 → 安全合并 → 恢复 → 下一任务，减少不必要的人工干预。闭环尚未证明交付；不要求先冻结 #229。每个实际环节须有可定位证据。批次交付（#250）是它采用的交付 / 审查 / CI 模式，两者由同一实现批次推进。 |
| [#250](https://github.com/ctrlshiftaltdel/AgentOrch/issues/250) | **P0，正在实施**：批次交付（一个集成分支 / 一个最终 PR）的实现、契约同步、风险分层审查与发行接入；仅在真实端到端证据齐备后记录完成，当前不声称已交付。 |
| [#263](https://github.com/ctrlshiftaltdel/AgentOrch/issues/263) | **文档冻结（#263；合并后生效）**：终止流契约与工作树边界契约见 [契约文档](design/batch-tool-stop-and-worktree-contracts.md)，AC5 仍为 OPEN，S1 / S2 为 PLANNED；不表示标为契约要求未实现的规则已在 `main` 实现。 |
| [#240](https://github.com/ctrlshiftaltdel/AgentOrch/issues/240) | **已关闭**：针对过期 CI 的修复 PR [#241](https://github.com/ctrlshiftaltdel/AgentOrch/pull/241) 已合并，合并提交为 `72e45c803bacd0c15f13ce3db5e30fafb58cdd72`；合并后 CI 运行 [43429593642](https://github.com/ctrlshiftaltdel/AgentOrch/actions/runs/43429593642) 在 Linux 3.12 / 3.13、macOS 与 Windows 均通过。以上事实以该 Issue 的关闭评论为准；平台侧事件顺序与耗时收益未实测，不作断言。 |
| [#179](https://github.com/ctrlshiftaltdel/AgentOrch/issues/179) | **后续，真实运行时待验证**：实际权限拒绝与上下文隔离链路仍需真实证据；夹具或只读交接不能证明实机能力。历史 PR [#231](https://github.com/ctrlshiftaltdel/AgentOrch/pull/231) 已关闭，不表示该链路已交付。 |
| [#181](https://github.com/ctrlshiftaltdel/AgentOrch/issues/181) 与 [DSH](https://github.com/ctrlshiftaltdel/AgentOrch/issues/160) | **延期**：#181 在 #179 后再评估；该整链不阻塞 #230；[#163](https://github.com/ctrlshiftaltdel/AgentOrch/issues/163) 已重标 P2 / 延期，运行时仍阻塞且能力尚未实测，后续工作不得假定已就绪。历史设计及依赖见 [设计文档](design/deepseek-harness-agent-role-tools.md)。 |
| [#154](https://github.com/ctrlshiftaltdel/AgentOrch/issues/154) | 历史文档一致性门任务已关闭；具体结论以任务记录为准，不列入当前优先队列。 |

## 契约冻结与 AC5 切片（#263）

- 新契约：[`docs/design/batch-tool-stop-and-worktree-contracts.md`](design/batch-tool-stop-and-worktree-contracts.md)。它是 #263 的冻结候选；合并前不作权威，也不表示其中 `REQUIRED` 规则已在 `main` 实现。
- `#250` 的 AC5 仍为 **OPEN / 未完成**；#261、#262 的 findings 只是设计输入，均未修复，也未合并。
- 切片指针（均未派发、未实现、未审查）：

  | 切片 | 单一目标 | 状态 |
  |---|---|---|
  | S1 | executor / verifier 生命周期与 attempt 历史 | `PLANNED`（pending） |
  | S2 | observed / integrated head 与 retry / base 语义 | `PLANNED`（pending） |

- S1、S2 必须各自建 Issue、分支 / worktree 与独立审查；不得混入 #262 类工具修复，也不得并行修改同一文件。

## 边界与证据

- 对外部项目操作、实质扩大安全权限、不可逆迁移须单独确认。所有者本轮已授权在本仓库调整治理规则，具体变更以实际代码及审查合并为准；该授权不等同于允许影响外部项目或扩大安全权限。
- 保留真实测试、独立审查、绑定当前 head 的证据和可恢复状态；未执行、未知或失败不得计为通过。合并与合并后验证仍遵循 [编排契约](../kit/orchestration.md) 和 [项目配置](PROJECT.md)。
- 历史耗时、人工干预量、重复测试量与返工量均为 **UNKNOWN**；不据此宣称效率收益。

## 历史追溯

- [#229](https://github.com/ctrlshiftaltdel/AgentOrch/issues/229) 已关闭为取消（not planned），不是完成；其历史提案 PR [#234](https://github.com/ctrlshiftaltdel/AgentOrch/pull/234) 已关闭，保留供追溯，不作为现行权限。
- 早期阶段及发行记录见 [Releases](https://github.com/ctrlshiftaltdel/AgentOrch/releases)、[自我完善计划](self-improvement-and-update-plan.md) 和 [升级记录](upgrade.md)；具体任务事实回查 Issue / PR，不将历史结果冒充当前验证。
