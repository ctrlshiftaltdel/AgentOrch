# ACTIVE-TASK-REGISTRY.md — Issue 内活动任务登记块

每个活动任务在其 Issue 内维护一个 `ORCH-TASK-REGISTRY` 注释块。它是 `github` 底座的活动锁记录：写入成功且通过开放 PR 路径检查，才算取得任务锁。

## 模板

```markdown
<!-- ORCH-TASK-REGISTRY
status: READY
mode: single-session
locked_paths: src/core/, tests/test_core.py
branch: task/123-core-refactor
pr: (none)
runner: (unassigned)
dispatched_at: 2026-01-01T09:00:00Z
updated_at: 2026-01-01T09:00:00Z
head_sha: (none)
blocked_reason: (none)
-->
```

## 字段

| 字段 | 必填 | 说明 |
|------|------|------|
| `status` | 是 | `READY` / `DISPATCHED` / `IN_PROGRESS` / `PR_OPEN` / `REVIEW_READY` / `MASTER_REVIEW` / `APPROVED` / `MERGED` / `VERIFIED` / `BLOCKED` / `NEEDS_HUMAN_DECISION` / `FAILED` / `CANCELED` |
| `mode` | 是 | `single-session`（子代理）、`multi-session`（人工交接）或 `batch`（交付批次，见下节）；用于说明证据从哪里取回 |
| `locked_paths` | 是 | 逗号分隔的允许路径；与 Issue 的“可改路径”完全一致，路径相同或父子重叠即冲突。批次模式下是批次内任务路径的并集 |
| `branch` | 是 | 任务分支名；未创建时写 `(none)`。批次模式下可写集成分支名 |
| `pr` | 是 | PR 编号；未创建时写 `(none)`，创建后立即回填。批次模式下是批次唯一的最终 PR |
| `runner` | 是 | 实际执行者标识（人 / 子代理 ID）；未派发时写 `(unassigned)` |
| `dispatched_at` | 是 | 首次派发时间，ISO 8601 UTC，写入后不再改写 |
| `updated_at` | 是 | 最近一次状态变更时间，ISO 8601 UTC，必须单调递增 |
| `head_sha` | 是 | 当前审查 / 验证对应的完整 commit SHA；无则写 `(none)` |
| `blocked_reason` | 是 | 阻塞原因；正常时写 `(none)`，进入 `BLOCKED` / `NEEDS_HUMAN_DECISION` 时必填 |

## 使用规则

1. 派发前先写入登记块，再列出所有开放 PR 的变更文件并检查与 `locked_paths` 是否相交；相交则整个派发失败。
2. 状态变化时更新 `status`、`updated_at`，必要时更新 `pr` 与 `head_sha`；旧值不保留在同一块里，历史通过 Issue 评论追溯。
3. 只有 `VERIFIED` / `CANCELED` 才把登记块标记为释放（`status` 改为对应值并写 `blocked_reason: (none)`）；`FAILED` 与状态未知保持锁定并写明原因。
4. 扩大 `locked_paths` 必须先在 Issue 评论说明并重新做冲突检查；不得静默修改。
5. 同一 Issue 只允许一个登记块；发现重复块视为状态冲突，停止派发并升级为 `NEEDS_HUMAN_DECISION`。

## 批次模式（`mode: batch`）

- `mode: batch` 表示该 Issue 属于一个**交付批次**：相关任务共用一个集成分支，并在批次结束时只开一个最终 PR / 只做一次合并；批次内每个任务仍保留自己的任务 ID、验收标准与证据，批次只在批次 Issue 上维护一个 `mode: batch` 登记块。
- 批次登记块的 `locked_paths` 是批次内所有任务允许路径的**并集**，开放 PR 冲突检查按该并集执行；`pr` 在最终 PR 创建后填该 PR 编号，逐任务 Draft PR 不是必需项。
- `INTEGRATED` 是批次内部里程碑，不写入本字段的 `status` 取值集合，也不能替代 `VERIFIED`；`status` 仍使用上表的任务状态。
- 释放规则不变：只有 `VERIFIED` / `CANCELED` 才把登记块标记为释放，`FAILED` 与状态未知保持锁定并写 `blocked_reason`。
- 批次阶段、风险分层与合并门的唯一权威是 `kit/orchestration.md`（2.2、4.2 节）；本文件只说明登记块怎么写。
