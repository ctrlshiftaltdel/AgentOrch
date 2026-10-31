# 契约决策记录（AgentOrch）

> 本文件记录本仓库冻结契约的决策与变更。契约语义权威仍是 [`docs/self-improvement/`](../self-improvement/) 与 `schemas/*.schema.json`；本文件只回答“何时、由谁、基于什么证据冻结或改了什么”，不复制状态机、锁或任务生命周期（见 [`docs/orchestration.md`](../orchestration.md)）。
> 记录格式参考 [`templates/contract-decision-log.md`](../../templates/contract-decision-log.md)。ID 沿用 Issue 决策的 `HD-<issue>-<option>` 形式（模板中的 `DC-<n>` 只是格式示例），全局唯一、不复用。

## 状态取值

| 状态 | 含义 | 是否可改 |
|---|---|---|
| `OPEN` | 尚未决定；未冻结任何语义。行内写明谁决定、阻塞什么。 | 可以：以新行记录决定，并把本行标为 `SUPERSEDED`。 |
| `FROZEN` | 下游任务必须遵守；单方面改写是契约违规，不是重构。 | 只能通过带影响任务与证据的替代行修改。 |
| `REVISITABLE` | 暂时冻结，但已预期在明确触发条件下修改。 | 触发条件满足后通过替代行修改。 |
| `SUPERSEDED` | 已被其它决策替代；保留作历史。 | 否，以替代行为准。 |

## 已冻结决策

| ID | 日期 | 决定人 | 影响任务 | 变更前 | 变更后 | 理由 | 状态 | 证据 |
|---|---|---|---|---|---|---|---|---|
| `HD-62-B` | 2026-10-26 | 项目所有者 | #49、#50、#53、#62 | `lesson-lifecycle.md` §2.1 的 `GENERAL_LESSON` 允许状态不含 `NEEDS_VERIFICATION`，而 §3.2 的 T10 未排除 `GENERAL_LESSON`；按字面写“`REGRESSION_WATCH` 上先执行 T12”不存在该表格边（T12 只从 `GENERALIZED` 出发，#49 也会拒绝）。 | 保持 §2.1 状态表与 T12 表格边（`GENERALIZED → VERIFIED`）不变；T10 对 `GENERAL_LESSON` 增加前置：先按 T12 语义 `reclassify` 撤销泛化（`content_class` → `SOURCE_SPECIFIC_EXPERIENCE` / `CONFIRMED_FACT`、`scope=source_only`、记录 `decision`；状态仍 `REGRESSION_WATCH`），再进入 `NEEDS_VERIFICATION`。示例见 [`lesson-lifecycle.md`](../self-improvement/lesson-lifecycle.md) §3.2.1。 | 与 #49 现有 `reclassify` + T10 行为一致；不改 schema、不改代码，风险最小；保留“通用教训回归先降级、再验证”的保守语义。 | `FROZEN` | Issue #62 决策评论 [#6410056128](https://github.com/ctrlshiftaltdel/AgentOrch/issues/62#issuecomment-6410056128)，B1 细化 [#6410191880](https://github.com/ctrlshiftaltdel/AgentOrch/issues/62#issuecomment-6410191880)；记录基线 `6666e155171a5399926e5bf6e617cb20bd9fc624` |

## 未决事项

（none）

## 可复审事项

（none）

## 已替代记录

（none）

## 记录规则

- ID 全局唯一、不复用、不重排；更正以新行记录，不删除旧行。
- 每行写明决定人与影响任务；“团队”“大家”不是决定人。
- `变更前` / `变更后` 只写契约文字或语义的变化；首次冻结的 `变更前` 写 `(none)`。
- 证据必须是可定位指针（Issue / PR 评论、任务记录）并附记录时的 head SHA；没有证据的决策是 `OPEN`，不是 `FROZEN`。
- 不删除行；过时决策标 `SUPERSEDED` 并保留。
- 冻结项变更必须在同一任务内同步更新契约正文（如 [`lesson-lifecycle.md`](../self-improvement/lesson-lifecycle.md) 与 [`README.md`](README.md)），日志与契约不得互相矛盾。
- `OPEN` 行必须写明解除条件；里程碑收口时仍为 `OPEN` 的事项按 `NEEDS_HUMAN_DECISION` 报告，不得静默丢弃。
