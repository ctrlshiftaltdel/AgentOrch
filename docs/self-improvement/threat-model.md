# 威胁模型（v1）

> 冻结于 Issue #47（S0）。本文件是**威胁、缓解、负向测试与剩余风险的唯一权威**；其它文档只引用威胁 ID。
> 缓解列描述的是**契约要求**，不是已实现能力。实现任务见 §3；未实现的缓解不得在报告中写成已生效。
> 未接受的剩余风险按 [entrypoints-and-decisions.md](entrypoints-and-decisions.md) §5 的 `HD-06` 交项目所有者裁决。

## 1. 威胁记录

| ID | 威胁 | 影响 | 缓解（契约条款） | 负向测试（#53 场景） | 剩余风险 | 状态 |
|---|---|---|---|---|---|---|
| T-01 | 提示词注入：来源的标题、Issue 正文、评论或 CI 日志包含“忽略规则 / 修改 AGENTS.md / 把凭据发给我”等指令 | 规则、权限或允许路径被来源内容改写；执行越权操作 | 来源内容一律 `untrusted_data`（[sources-and-trust.md](sources-and-trust.md) §3.1）；采集输出与指令上下文隔离；`may_override_rules=false`；任何规则改动只能走正常 Issue + 审查 | 合成来源含注入文本 → 流程只记录为证据；断言没有新增/修改 `AGENTS.md`、`docs/orchestration.md`、`editions/`，没有扩大 `locked_paths`，报告标 `REJECTED` / 仅证据 | 模型仍可能把注入文本转述成建议；需人工审查兜底 | 契约冻结；实现 #49 / #50 |
| T-02 | 来源不可达（404 / 403 / 429 / 超时）却被标成“已扫描”，或把缺失证据当作“无新经验” | 结论建立在未读取的数据上；遗漏真实教训 | 游标状态 `OK` / `PARTIAL` / `BLOCKED` / `UNKNOWN` 分离（[sources-and-trust.md](sources-and-trust.md) §4、§6）；只有 `OK` 推进水位；`BLOCKED` 必填 `blocked_reason` | 模拟 403 / 404 / 429 / 断网 → 报告 `BLOCKED` / `UNKNOWN`，水位不变，不生成任务；断言没有“已扫描”字样 | 无法区分“确实无权限”与“临时故障”；需要重试窗口与人工确认 | 契约冻结；实现 #49 |
| T-03 | 错误教训：Issue 标题或审查猜测被当成已证实缺陷，未复现即采纳 | 产生无效规则、重复 Issue、误导后续项目 | 四类内容 + E0–E4 证据等级（[lesson-lifecycle.md](lesson-lifecycle.md) §2）；`UNVERIFIED_ASSUMPTION` 不得 `VERIFIED` / 生成任务；泛化需 `decision` 且 ≥E2 | 只给 Issue 标题的候选 → 保持 `UNVERIFIED_ASSUMPTION`，报告“不采纳”，零新任务；断言无 `GENERALIZED` | 同义改写导致指纹不同（§4.3），仍需人工判断 | 契约冻结；实现 #49 |
| T-04 | 更新包路径穿越：成员名含 `..`、绝对路径、盘符或反斜杠，解包写到项目外 | 覆盖任意文件 / 写入系统目录 | 项目外临时目录解包；`MEMBER_PATHS` 校验；`update-manifest` / 报告的 `path` 禁止 `..` 与绝对路径（[update-safety.md](update-safety.md) §3） | 合成 zip 含 `../outside.txt`、`/etc/passwd`、`C:\x` → `UNSAFE_PACKAGE`，项目外无新文件，报告列出违规成员 | 解包库自身实现差异；需以 fixture 验证实现所用库的行为 | 契约冻结；实现 #51 |
| T-05 | 坏 MANIFEST：缺字段、schema 版本不支持、条目与归档成员不一致、清单缺哈希 | 分类依据错误；可能覆盖非上游文件 | `MANIFEST_SCHEMA` / `MANIFEST_SHA256` / `FILE_SET` / `EXPECTED_LAYOUT` 校验；失败即 `UNSAFE_PACKAGE` 且不写项目（[update-safety.md](update-safety.md) §3） | 缺 `sha256` 的清单、成员多一个 / 少一个、schema_version=2 → 全部 `STOPPED_UNSAFE_PACKAGE`，零写入 | 清单生成器（`check_drift.py`）与消费侧实现不一致的风险；需 #51 交叉测试 | 契约冻结；实现 #51 |
| T-06 | 冲突覆盖：本地与上游都修改的文件被上游直接覆盖，或项目自有文件被当作上游文件 | 项目配置、业务逻辑或私有文档丢失 | 三方分类 `THREE_WAY_CONFLICT` / `PROJECT_OWNED` / `LOCAL_MODIFICATION` 默认不覆盖（[update-safety.md](update-safety.md) §4–§5）；`APPLIED` 时禁止存在冲突类（schema 强制） | 同时改 `docs/orchestration.md` 与项目 `docs/PROJECT.md` → `STOPPED_CONFLICT`，两文件哈希不变，报告逐文件列出 | 分类依赖正确基线；基线错误会误判（见 T-05） | 契约冻结；实现 #51 |
| T-07 | 半更新：应用到一半失败，留下新旧混合状态且未声明 | 项目不可运行、难以恢复、证据失真 | 暂存 + 先计划后应用 + 单一可审查变更集；失败即停止并回滚（[update-safety.md](update-safety.md) §7）；`result` 不得同时表示“部分成功” | 在应用中注入失败（模拟第 N 个文件写入异常）→ `BLOCKED`，要么完整应用要么完整恢复；断言无“混合版本”未声明 | 非原子文件系统下跨文件原子性无法绝对保证；依赖备份与校验 | 契约冻结；实现 #51 |
| T-08 | 回滚失败或未验证回滚就宣称已恢复 | 项目停留在损坏状态却继续推进 | 回滚后必须校验哈希 / head / 工作区；`rollback.result=FAILED` → `result=BLOCKED`（schema 强制）；`HD-11`（[update-safety.md](update-safety.md) §7.3） | 让回滚后校验失败 → `BLOCKED` + `HD-11`，不得继续后续更新，不得标 `ROLLED_BACK` | 备份介质损坏或备份本身不完整；需备份校验 | 契约冻结；实现 #51 |
| T-09 | 凭据 / 隐私泄漏：注册表、Lesson、报告或日志写入 Token、个人绝对路径、客户数据、私有 URL | 公开仓库泄漏；违反隐私边界 | 字段禁含凭据；`redaction_required=true`；报告只放链接与合成摘要（[sources-and-trust.md](sources-and-trust.md) §3.3）；发布扫描 | 合成来源含 `ghp_…`、`/home/<user>/…`、token → 写入前拒绝 / 脱敏，报告无原文；断言仓库无命中 | 人工写的 `owner_approved_notes` 仍可能泄漏；需审查 | 契约冻结；实现 #49 / #51 |
| T-10 | 重复教训刷屏：同一条经验被反复扫描成多条 Lesson / 多个 Issue / 多份规则 | 规则膨胀、审查过载、相互矛盾 | 指纹去重（[lesson-lifecycle.md](lesson-lifecycle.md) §4）；`duplicate_of` 不生成任务；幂等扫描 | 同一证据扫描两次 → 只有一条 Lesson、零新 Issue；断言指纹一致、证据合并 | 同义改写无法自动识别；靠 `OVERLAPS` + 人工 | 契约冻结；实现 #49 |
| T-11 | 冲突教训被单方面裁决：模型在两条矛盾经验中自行选一方 | 采纳错误规则；掩盖不确定性 | 冲突默认 `OPEN`；阻断 `GENERALIZED` / `IMPLEMENTED`（schema 强制）；只能 `RESOLVED_BY_EVIDENCE` / `RESOLVED_BY_OWNER`（[lesson-lifecycle.md](lesson-lifecycle.md) §5） | 注入两条相反结论且都标 VERIFIED → `GENERALIZED` 被拒绝，报告列出冲突双方，`HD-04` | 人工裁决可能拖延；需在报告中持续可见 | 契约冻结；实现 #49 |
| T-12 | 未配置 Architecture Contract 的项目被强加猜测规则：默认目录结构、隐式层级或硬失败 | 误报阻断 CI；引导错误重构 | 契约可选；缺失 = `OFF`，只读诊断 / 草案，不猜测、不阻断（[architecture-contract.md](../architecture/architecture-contract.md) §5）；硬门禁需 `ENFORCE` + `HD-14` | 无契约项目运行检查 → 只读报告、退出码 0、无模块 / 规则猜测；断言无 ERROR | 诊断报告可能被误读为“已检查”；需明确标注 `DIAGNOSTIC_ONLY` | 契约冻结；实现 #48 |
| T-13 | 通过修改检查器 / 契约 / 删测试绕过质量门：实现任务顺手放宽严重级别、删除负向 fixture、或让检查器忽略自身 | 门禁形同虚设；自我完善绕过自身验证 | 契约变更须新 Issue 重新冻结；测试强度不可降（`AGENTS.md` §4）；检查器自身受常规审查与 CI；受保护路径授权 | 在 #48 / #51 的实现 PR 中删除负向 fixture 或把 ERROR 改 WARNING → 审查按 G1–G6 退回；断言测试数量与断言未被削弱 | 审查者仍需逐条核对，无法完全自动检测语义弱化 | 契约冻结；由核心契约执行 |
| T-14 | 供应链：伪造 tag、镜像站 / fork Release、把旧版本伪装成新版本或诱导降级 | 安装非官方内容；回退到已知漏洞版本 | 仓库 allowlist 常量；只接受 Releases；tag 形式与版本单调性（[update-safety.md](update-safety.md) §2）；资产 SHA-256 | 非官方 URL / fork Release / 低于当前版本 / 伪造 tag → `UNSAFE_PACKAGE` 或 `BLOCKED`，零写入 | 依赖 GitHub 账户与 Release 权限安全；无法在本仓库内消除 | 契约冻结；实现 #51 / #52 |
| T-15 | 解包攻击：符号链接、硬链接、设备文件、压缩炸弹、超大包 | 任意文件读写、磁盘耗尽、拒绝服务 | `SYMLINKS` / `EXPECTED_LAYOUT` 校验；大小与成员数上限由实现任务定义但必须存在；项目外解包 | 含符号链接 / 1 万成员 / 解压后超大 → `UNSAFE_PACKAGE`，磁盘占用有上限，零写入 | 压缩库行为差异；上限值需实测后冻结（#51） | 契约冻结；实现 #51 |
| T-16 | 旧 head 证据 / CI 绿灯冒充当前证据：更新或自我完善报告引用过期运行结果 | 错误结论被批准；违反 G5 | 报告绑定 `head_sha_before` / `head_sha_after`；后置检查逐项记录命令与退出码；`NOT_RUN` 不折算（[update-safety.md](update-safety.md) §9）；核心证据矩阵 | 在 head 变化后复用旧报告 → 审查按 G5 退回；断言报告 SHA 与当前 head 一致 | 人工报告仍可能漏记；需 Master 核对 | 契约冻结；由核心契约执行 |
| T-17 | 版权 / 来源代码复制：把来源项目源码、规则文本或私有日志整段搬进 AgentOrch | 版权风险；泄漏来源信息 | 只允许链接与独立编写的合成 fixture（[sources-and-trust.md](sources-and-trust.md) §3.2）；发布扫描 | 提交含来源代码片段 → 审查拒绝；断言 fixture 为合成且无来源私有内容 | 是否“独立编写”需人工判断 | 契约冻结；由审查执行 |
| T-18 | 上游删除被静默执行：新版本移除的文件在项目中被自动删除，或项目扩展被误删 | 项目功能丢失；不可逆 | `UPSTREAM_REMOVAL` / `UPSTREAM_RENAME` 默认 `KEPT_LOCAL`，删除需 `HD-09`（[update-safety.md](update-safety.md) §4） | 新清单缺一个旧文件 → 文件保留、报告提示、`HD-09`；断言零删除 | 项目所有者可能批准删除后又反悔；需备份 | 契约冻结；实现 #51 |

## 2. 覆盖对照（Issue #47 必需项）

| 要求 | 对应威胁 |
|---|---|
| 提示词注入 | T-01 |
| 来源不可达 | T-02 |
| 错误教训 | T-03、T-11 |
| 路径穿越 | T-04 |
| 坏清单 | T-05 |
| 冲突覆盖 | T-06、T-18 |
| 半更新 | T-07 |
| 回滚失败 | T-08 |

## 3. 实现与验证归属

| 威胁 | 主要实现任务 | 负向场景归属 |
|---|---|---|
| T-01、T-02、T-03、T-09、T-10、T-11 | #49（来源与 Lesson 管线） | #53 经验组 |
| T-12、T-13 | #48（Architecture Contract 检查器） | #53 架构组 |
| T-04、T-05、T-06、T-07、T-08、T-14、T-15、T-18 | #51（更新引擎） | #53 更新组 |
| T-16、T-17 | 核心契约（G5 / 审查） | #53 端到端组 + Master 审查 |

本文件不声称任何缓解已实现。实现任务必须在 PR 中逐条对应威胁 ID 给出负向测试，且不得把未运行的场景写成通过。

## 4. 剩余风险与人工接受

以下剩余风险**不能**由实现任务自行接受；项目所有者必须按 `HD-06` 明确接受、增加补偿控制或放弃对应功能：

| 威胁 | 剩余风险摘要 | 需要接受的原因 |
|---|---|---|
| T-01 | 模型可能仍转述注入内容作为“建议” | 无法用纯静态规则完全消除语义风险 |
| T-03 / T-10 | 同义改写与语义近似无法可靠自动去重 | 指纹只覆盖规范化后的字面结构 |
| T-05 | 清单生成器与消费侧实现的细节差异 | 两侧由不同任务维护，需交叉测试 |
| T-07 | 非原子文件系统下无法绝对保证跨文件原子性 | 依赖备份与验证，而非事务日志 |
| T-08 | 备份介质本身可能损坏 | 无外部存储保证 |
| T-14 | 对 GitHub 账户与 Release 权限的信任 | 不在本仓库可控制范围内 |
| T-15 | 压缩库对畸形归档的行为差异 | 需实测后冻结上限 |
| T-16 | 人工报告可能漏记或复用旧结果 | 核心 G5 与 Master 审查兜底 |
| T-17 | “独立编写”的判断依赖人工 | 无法自动判定版权 |

在项目所有者逐项接受之前，这些风险保持 `OPEN`，相应功能不得宣称“已安全可用”。
