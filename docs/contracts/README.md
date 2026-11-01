# 契约目录：schema、fixtures 与验证方案（S0，v1）

> 状态：**S0 设计冻结**（Issue #47）。本目录只包含契约、schema 与 fixtures，**不包含**校验脚本、检查器、更新引擎或 CI 改动。未实现的能力不得宣称已支持。
> 语义权威：[`docs/self-improvement/`](../self-improvement/) 与 [`docs/architecture/architecture-contract.md`](../architecture/architecture-contract.md)。本文件只做索引、验证方案与下游依赖登记。

## 1. 目录结构

```text
docs/contracts/
├── README.md                       # 本文件：索引、验证方案、CI 边界、下游依赖、开放事项
├── decision-log.md                 # 契约决策记录（含 `HD-62-B` 的 T10 前置条件）
├── schemas/                        # 四类契约的 JSON Schema 草案（draft 2020-12）
│   ├── source-registry.schema.json
│   ├── lesson.schema.json
│   ├── architecture-contract.schema.json
│   ├── update-manifest.schema.json
│   └── update-report.schema.json
└── fixtures/
    ├── valid/                      # 每类至少一个合法样例
    └── invalid/                    # 每个样例对应一个可判定的非法变体
```

“更新报告 / 清单”是**一类**契约的两个机器产物：`update-manifest`（官方包内 `MANIFEST.json`）与 `update-report`（每次运行报告），因此共 5 个 schema 文件、4 类契约。

## 2. 权威表

| 事实 | 唯一权威 |
|---|---|
| 字段名、必填、类型、枚举、`const`、模式 | `schemas/*.schema.json` |
| 来源信任、游标、启用 / 停用 / 移除 | [`sources-and-trust.md`](../self-improvement/sources-and-trust.md) |
| Lesson 状态、转换、证据等级、去重、冲突 | [`lesson-lifecycle.md`](../self-improvement/lesson-lifecycle.md) |
| 触发语、授权、停止条件、`HD-*` 决策目录 | [`entrypoints-and-decisions.md`](../self-improvement/entrypoints-and-decisions.md) |
| 逐文件分类、项目自有保护、备份 / 回滚、失败关闭 | [`update-safety.md`](../self-improvement/update-safety.md) |
| 威胁、负向测试、剩余风险 | [`threat-model.md`](../self-improvement/threat-model.md) |
| 架构模块、严重级别、豁免、未配置语义 | [`architecture-contract.md`](../architecture/architecture-contract.md) |
| 任务状态机、锁、证据矩阵、合并门 | [`docs/orchestration.md`](../orchestration.md)（只引用） |

## 3. Schema 与 fixtures 对应关系

命名规则：合法样例 `<schema>.valid.json`（可加用途后缀，如 `source-registry-empty.valid.json`）；非法样例 `<schema>.<case>.invalid.json`，一个文件只制造一个主要违规，便于定位。

| Schema | 合法 fixtures | 非法 fixtures |
|---|---|---|
| `source-registry` | `valid/source-registry.valid.json`（两个来源，一启用一停用、一个游标 OK）<br>`valid/source-registry-empty.valid.json`（空来源数组合法） | `invalid/source-registry.access-mode.invalid.json`<br>`invalid/source-registry.cursor-status.invalid.json` |
| `lesson` | `valid/lesson.valid.json`（通用教训，`GENERALIZED`）<br>`valid/lesson.source-specific.valid.json`（来源专属，`VERIFIED`） | `invalid/lesson.verified-without-evidence.invalid.json`<br>`invalid/lesson.rejected-without-decision.invalid.json`<br>`invalid/lesson.unknown-field.invalid.json`<br>`invalid/lesson.source-specific-generalized.invalid.json` |
| `architecture-contract` | `valid/architecture-contract.valid.json`（`ENFORCE`、含一条未过期豁免） | `invalid/architecture-contract.severity.invalid.json`<br>`invalid/architecture-contract.waiver-expiry.invalid.json` |
| `update-manifest` | `valid/update-manifest.valid.json`（字段与现有 `MANIFEST.json` 一致） | `invalid/update-manifest.path-traversal.invalid.json`<br>`invalid/update-manifest.hash-format.invalid.json` |
| `update-report` | `valid/update-report.valid.json`（`PLAN` 模式，全部校验 `PASS`） | `invalid/update-report.applied-with-conflict.invalid.json`<br>`invalid/update-report.rollback-failed.invalid.json` |

每个非法 fixture 的预期错误（实测，由本文件 §4.2 的标准库子集检查器输出）：

| 非法 fixture | 拒绝关键字 | 实测错误位置与信息 |
|---|---|---|
| `source-registry.access-mode.invalid.json` | `enum` | `/sources/0/access_mode`：`public_write` 不在 `public` / `authorized_private` |
| `source-registry.cursor-status.invalid.json` | `oneOf`（`if`/`then` 的必填项） | `/sources/0/scan_cursor`：`OK` 却缺少 `last_successful_scan_at` / `last_scanned_commit` |
| `lesson.verified-without-evidence.invalid.json` | `enum`（`if`/`then`） | `/content_class`：`UNVERIFIED_ASSUMPTION` 不能处于 `VERIFIED` |
| `lesson.rejected-without-decision.invalid.json` | `type`（`REJECTED` 的 `then` 要求决策对象） | `/decision`：`null` 不是合法决策对象 |
| `lesson.unknown-field.invalid.json` | `additionalProperties` | `$`：`raw_source_text` 是未声明字段 |
| `lesson.source-specific-generalized.invalid.json` | `const` | `/content_class`：`GENERALIZED` 要求 `GENERAL_LESSON` |
| `architecture-contract.severity.invalid.json` | `enum` | `/rules/0/severity`：`BLOCKER` 不在 `ERROR` / `WARNING` |
| `architecture-contract.waiver-expiry.invalid.json` | `required` | `/rules/0/waivers/0`：缺少必填 `expires_at` |
| `update-manifest.path-traversal.invalid.json` | `not`（`..` 段） | `/files/0/path`：`../secrets.env` |
| `update-manifest.hash-format.invalid.json` | `pattern` | `/files/0/sha256`：要求 64 位小写十六进制 |
| `update-report.applied-with-conflict.invalid.json` | `const` + `not`/`contains` | `/classification/counts/THREE_WAY_CONFLICT`：`APPLIED` 时冲突计数必须为 0；`files` 亦禁含冲突类 |
| `update-report.rollback-failed.invalid.json` | `const`（`if`/`then`） | `/result`：回滚 `FAILED` 时结果必须为 `BLOCKED` |

非法样例只覆盖**标准 JSON Schema 可判定**的错误。以下语义违规无法用标准关键字表达，由 §5 的语义检查负责，并在 fixture 注释与审查中说明：

- `source_id` / `module_id` / `rule_id` / `waiver_id` / `lesson_id` 的**跨项唯一性**；
- `counts` 之和 == `files` 长度；
- `dependencies` / `boundaries` / `invariants` 中的模块与规则引用必须存在；
- 清单 `category` 必须等于 `scripts/check_drift.py` 的 `classify_upstream(path)`；
- 报告 `release_tag` 与 `version` 的归一化一致性（`v1.6` ≡ `1.6.0`）；
- 游标时间戳单调递增；
- 指纹是否由声明的算法与字段重新计算得到。

## 4. 零第三方依赖验证方案

### 4.1 分层

| 层 | 何时运行 | 依赖 | 判定 |
|---|---|---|---|
| **L0 语法与结构** | 每次改动 / 本地 / CI | Python 标准库 `json` | 每个 schema 与 fixture 能被解析；schema 只使用 draft 2020-12 标准关键字；fixture 命名成对 |
| **L1 零依赖子集校验器** | 后续任务实现并接入（建议 #48 或 #53） | 仅 Python 标准库 | 用仓库内实现的关键字子集（`type` / `required` / `properties` / `additionalProperties` / `enum` / `const` / `pattern` / `minLength` / `maxLength` / `minimum` / `maximum` / `minItems` / `maxItems` / `uniqueItems` / `items` / `contains` / `not` / `allOf` / `anyOf` / `oneOf` / `if`-`then`-`else` / `$ref`）校验全部 fixtures：合法全过、非法全拒 |
| **L2 可选外部验证器** | 仅 CI 的可选 job（不阻断本地离线路径） | 临时安装的第三方工具 | 对同一批 schema / fixtures 交叉验证；失败只作为额外信号，不替代 L1，也不成为运行时依赖 |

本阶段**只交付 L0 可检查的产物与 L1 的实现要求**；L1 校验器本身属于后续任务（不得在本 Issue 新增 `scripts/`）。

### 4.2 本阶段真实验证记录（不声称标准验证器已通过）

在 Issue #47 的分支上执行：

```bash
# 1) 全部 schema 与 fixture 可解析
python3 - <<'PY'
import json, pathlib
root = pathlib.Path('docs/contracts')
files = sorted(root.glob('schemas/*.schema.json')) + sorted(root.glob('fixtures/*/*.json'))
for p in files:
    json.loads(p.read_text(encoding='utf-8'))
print(f'parsed {len(files)} files')
PY
```

2) 标准库子集检查：本任务在本地临时运行一个零依赖的 draft 2020-12 关键字子集检查器，脚本**不提交、路径不入库**；它逐 fixture 验证合法被接受、非法被拒绝。复现方式由 #48 或 #53 落地的 L1 校验器承担（见 §4.1），本仓库不提供该临时脚本。

- 结果：`parsed 24 files`（5 schemas + 19 fixtures），退出码 `0`；临时子集检查器报告 19 个 fixture 全部符合预期（合法被接受、非法被拒绝并打印 §3.2 的位置），退出码 `0`。
- **限制**：该临时检查器是本任务为取证编写的标准库子集实现，**不是**标准 JSON Schema 验证器。环境中没有 `jsonschema` / `check-jsonschema`，且禁止新增依赖，因此“标准验证器的判定”记为 `NOT_RUN`，不得写成已通过。CI 侧可选验证方案见 §4.3。

### 4.3 CI 可选验证器边界

允许（且仅允许）以下形态：

1. 在独立、**可选**的 CI job 中临时安装一个固定版本的 JSON Schema 验证器（如通过 `uvx` / `pipx`），只读取 `docs/contracts/`。
2. 该 job 不参与默认必需检查，不写入项目依赖清单，不被任何仓库脚本 import。
3. 本地与离线路径必须在没有该工具时仍能完成 L0 + L1；缺失时标记 `NOT_RUN`。
4. 可选验证器失败必须在报告中可见；但它不“重新定义”契约，也不能替代 L1 与人工审查。
5. 不得把 `schemas/*.json` 变成运行时加载的可执行配置：它们只是契约文档与 fixture 校验对象。

## 5. 语义检查清单（L1 必须覆盖）

1. `source_id` / `module_id` / `rule_id` / `waiver_id` / `lesson_id` 在各自作用域内唯一。
2. `update-report.classification.counts` 各项之和 == `files` 长度。
3. 架构契约中的模块引用（`boundaries.members`、`invariants.rule_ids`、`forbidden_edge.rule_id`）必须存在。
4. Lesson 的 `duplicate_of` / `conflicts_with.lesson_id` 不得指向自身；`RESOLVED_*` 必须有证据。
5. 清单 `category` 必须与 `scripts/check_drift.py::classify_upstream` 一致；`MANIFEST.json` 不列自身。
6. 报告 `target.release_tag`（`vX.Y` 或 `vX.Y.Z`）与 `target.version` 归一化后必须一致；缺失 patch 按 0。
7. 游标 `last_scan_started_at` / `last_successful_scan_at` 不得倒退；`OK` 之外不得推进水位。
8. Lesson 指纹必须由 §4 算法与 `normalized_fields` 重算一致。
9. `PROJECT_OWNED` / `UNKNOWN_FILE` / `UPSTREAM_REMOVAL` / `UPSTREAM_RENAME` 不得出现 `APPLIED`。
10. Lesson 的 T10 前置条件（`HD-62-B` / B1）：`GENERAL_LESSON` 在 `REGRESSION_WATCH` 观察到回归时，不得直接进入 `NEEDS_VERIFICATION`；必须先执行 T12 语义的 `reclassify`（`content_class` → `SOURCE_SPECIFIC_EXPERIENCE` / `CONFIRMED_FACT`、`scope=source_only`、记录 `decision`），再执行 T10。T12 表格边仍为 `GENERALIZED → VERIFIED`，不得为 `REGRESSION_WATCH` 新增 T12 边；详见 [`lesson-lifecycle.md`](../self-improvement/lesson-lifecycle.md) §3.2.1 与 [`decision-log.md`](decision-log.md)。

这些检查属于后续实现任务；本阶段只冻结“必须检查什么”。第 10 条由 `HD-62-B` 决议，`#50` / `#53` 直接引用本节即可，不得各自改写 T10 语义。

## 6. 下游依赖点

| 任务 | 读取本契约的 | 不得做 |
|---|---|---|
| **#48** 架构契约与检查器 | `architecture-contract.schema.json`、[`architecture-contract.md`](../architecture/architecture-contract.md)（严重级别、报告状态、豁免、未配置语义） | 新增严重级别 / 报告状态；对无契约项目硬失败；改 `docs/PROJECT.md` 之外的契约位置而不记录 |
| **#49** 来源与 Lesson 管线 | `source-registry.schema.json`、`lesson.schema.json`、[`sources-and-trust.md`](../self-improvement/sources-and-trust.md)、[`lesson-lifecycle.md`](../self-improvement/lesson-lifecycle.md) | 在代码里写死来源；新增 Lesson 状态；放宽证据等级；跳过指纹 |
| **#50** 自我完善闭环 | [`entrypoints-and-decisions.md`](../self-improvement/entrypoints-and-decisions.md) 的触发 / 授权 / 停止条件 / `HD-*`；核心契约引用；本文件 §5 第 10 条 | 复制状态机或门禁；自动合并 / 自批；用报告替代证据矩阵；在 T10 场景绕过 `HD-62-B` 的 `reclassify` 前置 |
| **#51** 更新引擎 | [`update-safety.md`](../self-improvement/update-safety.md) 全部分类与失败关闭；`update-manifest` / `update-report` schema | 新增分类；放宽校验；把哈希差异当覆盖许可；静默删除 |
| **#52** 更新入口与发行包 | `update-safety.md` §2、§8 与 `entrypoints-and-decisions.md` §1.2；[`threat-model.md`](../self-improvement/threat-model.md) T-14 | 只把入口放 `docs/`；声称 local 支持 GitHub 自动化 |
| **#53** 合成回归 | `threat-model.md` 的负向测试列、本文件 §3 预期错误、§5 语义清单（含第 10 条） | 把未执行场景写成通过；用真实凭据 / 私有数据；假设 `GENERAL_LESSON` 可跳过 `reclassify` 直接 T10 |
| **#55 / #56** 首批经验 | [`lesson-lifecycle.md`](../self-improvement/lesson-lifecycle.md) 的分类与泛化门槛 | 把来源业务假设写成核心规则 |

依赖顺序与状态以 [`docs/implementation-plan.md`](../implementation-plan.md) 为准，本文件不重复任务状态。

## 7. 开放高风险事项

以下事项在本契约中保持**未决**，下游不得自行猜测；未决期间相关功能不得宣称已完成：

| ID | 事项 | 归属决策 | 阻塞 |
|---|---|---|---|
| `HD-06` | 威胁模型 §4 的剩余风险是否全部接受（T-01、T-03/T-10、T-05、T-07、T-08、T-14、T-15、T-16、T-17） | 项目所有者 | 对应功能“已安全可用”的声明 |
| `HD-14` | 是否为项目启用 `ENFORCE` 硬门禁 | 项目所有者 | #48 的阻断行为 |
| `HD-15` | 是否引入外部工具链（Rust、第三方验证器） | 项目所有者 | 跨语言检查能力 |
| `UNV-1` | 标准库能否完整校验 draft 2020-12（本阶段只保证 fixtures 落在可判定子集） | #48 / #53 实测 | L1 校验器范围 |
| `UNV-2` | GitHub Releases 可访问性、资产命名、tag 序列（如 `v1.1` / `v1.5` / 新版本） | #51 实测 | 真实更新路径 |
| `UNV-3` | `gh` / GitHub API 在 CI 与受限网络的速率限制与可用性 | #49 / #53 实测 | 在线采集 |
| `UNV-4` | Python AST 与 Rust 工具链的实际识别能力 | #48 实测 | 依赖图覆盖率 |
| `UNV-5` | TalkDND / AgentMgr 证据链接可访问性（未复制任何私有内容） | #49 实测 | 首批来源扫描 |

## 8. 变更方式

- 任何 schema 字段、枚举或语义改动都必须新开 Issue 重新冻结，并记录到本项目的 [`decision-log.md`](decision-log.md)（记录格式参考 [`templates/contract-decision-log.md`](../../templates/contract-decision-log.md)）。
- 实现任务不得在实现 PR 中顺手改契约；发现契约缺陷时先停，登记 Issue，再按新版本修订。
- 新增 fixture 必须同时提供合法 / 非法成对样例，并在 §3 登记预期错误。
