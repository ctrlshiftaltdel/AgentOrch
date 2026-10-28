# 交付回归场景（合成）

本文件是 `scripts/run_delivery_scenarios.py` 的场景目录。它回答一个文档回答不了的问题：**套件会不会在它声称要拦的地方真的拦住**。字段与状态语义仍只由 [kit/orchestration.md](../../kit/orchestration.md) 4.1 与第 5 节定义，本文只列场景、注入的缺陷与预期拦截门，不复制契约。

本文件是**仓库内文档**，不随发行包发布（`build_release.py` 禁止包内出现 `docs` 路径段），因此在 `validate_kit.py` 中登记为 `REQUIRED_DOCS` 而不是 `REQUIRED`；缺失或缺章节同样会让校验失败。

## 1. 这套场景回答什么

- 证明 **G1 证据门**、阶段审计分类、edition fail-closed、资源锁与状态机在真实代码路径上会拒绝错误输入，而不只是写在提示词里要求 Agent 自觉。
- 证明**跨模块状态 / 并发 / 恢复**的检查点会拒绝具体的交付缺陷：共享状态漏掉调用点、回滚留下残留、业务回滚删掉锁、部分提交虚报完整回滚、目录错误被吞。对应的验收规则见 [cross-module-state-and-recovery.md](../self-improvement/cross-module-state-and-recovery.md)（R1–R6）；本文件只列场景。
- 证明**架构契约对齐**（AGM-1…AGM-6）在真实检查器上会拦住可静态识别的依赖违规，并在无法静态判定的地方如实退回语义层：核心依赖适配器私有符号、入口绕过共享应用服务、Mock 被标成真实验证、取消 / 超时 / 未知被折叠、能力缺证据仍标 `PASS`；同时证明**未配置契约的项目不会被猜测的规则判失败**。规则见 [agentmgr-contract-rules.md](../architecture/agentmgr-contract-rules.md)；本文件只列场景。
- 证明**自我完善闭环的治理门**在真实管线（`scripts/self_improvement/`）上生效：有合成来源证据的经验要经过验证、重新分类与泛化才能形成任务；来源不可达 / 无权限、提示词注入、重复、冲突、未验证缺陷与硬风险都会在计划或登记之前停下。规则见 [self-improvement-loop.md](../self-improvement/self-improvement-loop.md) 与 [sources-and-trust.md](../self-improvement/sources-and-trust.md)；本文件只列场景。
- 证明**安全更新引擎**（`scripts/update_agentorch/`）在旧版升级、双边冲突、坏清单、路径穿越、未知文件、上游删除、旧版无基线、未提交改动、半更新与回滚失败时要么安全应用、要么零写入停止，并留下可定位报告。语义见 [update-safety.md](../self-improvement/update-safety.md)；本文件只列场景。
- 证明**交付指标报告**不把空输入算成 0、不把缺事件算成零时长、不给每行丢掉样本量 / 窗口 / 缺失率，也不凭空发明“耦合度 / 内聚度”分数。口径只在 [templates/metrics.md](../../templates/metrics.md)；本文件只验证报告行为。
- 证明 **Phase D 角色选择链路**（S5）把 S1 scope / Markdown 记录、S2 发现与角色匹配、S3 可选模型数据、S4 本机反馈真正串起来时仍然：C1–C4 未通过就停、缺角色 / Reviewer 就停、外部数据过期或不可用不冒充最新、scope 不隔离或写入失败如实报告、任何选择都不会被读成“已派发”。规则见 [agent-role-discovery-and-model-selection.md](../design/agent-role-discovery-and-model-selection.md) §10–§13 与 [kit/orchestration.md](../../kit/orchestration.md) 第一部分的角色选择入口；本文件只列场景。
- 给“该失败的地方失败了”留下可复现的证据：每条场景都输出实际断言值与拦截文本。
- 供套件重大变更时回归：改核心契约、`validate_kit.py`、模板、锁/状态机或发行流程后，必须重跑。

**它不回答什么**：不证明外部产品项目（含本套件的经验来源项目）在本套件下跑通过。所有 fixture 都是本仓库合成的，没有复制任何产品仓库的数据、凭据或受版权文本。

## 2. 正负组与判定口径

| 组 | 覆盖 | 正负构成 |
|---|---|---|
| A 治理 / 授权 | 证据矩阵、授权撤销、审计上下文、重复写、越权自批；自我完善候选：来源证据、不可达 / 无权限、提示词注入、去重、冲突、未验证缺陷、硬风险 | A1 正向；A2–A7 负向；A8 / A15 正向；A9–A14 负向 |
| B 迁移 / 版本 | 旧格式升级、幂等重跑、半迁移、版本混用；安全更新：干净升级、双边冲突、坏清单、路径穿越、未知文件、上游删除、无基线、未提交改动、半更新、回滚失败 | B1 正向；B2–B4 负向；B5 正向；B6–B14 负向 |
| C 跨模块闭环 | 冻结规则接线、所有权、事件屏障、非断言异常；共享状态调用点、回滚完整性、锁生命周期、部分提交、吞错；架构契约对齐（适配器隔离、单一应用服务、证据标签、终局语义、能力声明、依赖方向与循环） | C1 正向；C2–C5 负向；C6 正向；C7–C11 负向；C12 正向；C13–C17 负向；C18 正向；C19 正向；C20 负向 |
| D 编排底座 | edition、资源锁、旧 head、修复轮数、阶段收口；指标诚实性（窗口 / 样本 / 缺失 / 覆盖，空输入不等于 0，不发明分数） | D1 正向；D2–D7 负向（D6 含正向子表）；D8 正向；D9 负向 |
| E 角色选择链路 | S1 scope / 本地记录、S2 发现与角色匹配、S3 可选模型数据、S4 本机反馈的端到端组合；C1–C4 门、缺角色、Reviewer 同模型、模态、外部数据不可用 / 过期、预算、scope 隔离与写入失败 | E1 正向；E2–E13 负向 |

判定口径只有三种结果，不允许“部分通过”：

- `PASS`：预期拦截真的发生（负向组），或合法输入真的通过（正向组）。
- `FAIL`：预期拦截没有发生，或消息与预期门不符。属于场景失败。
- `ERROR`：场景代码抛出了预期之外的非断言异常。**记录堆栈后继续跑下一个场景**，不会被吞掉，也不会被算作通过。

正向控制组的存在是硬要求：没有它，“所有场景都失败”会被误读成“门禁有效”。

## 3. 场景矩阵

「证据」列写该场景实际留下什么；「清理」列写它怎么消失。

| ID | 组 | 类型 | 前置状态 | 注入缺陷 | 预期拦截门 | 证据与清理 |
|---|---|---|---|---|---|---|
| A1 | A | 正向 | 显式 `edition: local` 的临时项目；一份合法任务包 | 无（控制组） | G1/G5：任务包 0 命中；`PLANNED → VERIFIED` 且 `approved_sha` 绑定当前 head | 命中列表、任务状态、剩余锁；临时项目随沙箱删除 |
| A2 | A | 负向 | AC-2 的证据行状态为 `FAIL`（授权已撤销） | AC-2 仍标记为已满足 | G1：已满足的 AC 带 `FAIL` 证据即拒绝 | 校验器命中文本；不写状态，沙箱删除 |
| A3 | A | 负向 | 两条 AC，只有一条证据行 | AC-2 没有任何证据映射 | G1：每条 AC 至少一条 evidence item | 命中文本；沙箱删除 |
| A4 | A | 负向 | 声明 `AC-1` 但语句为空 | 空 AC 被当成可检查项 | G1：无可检查语句的 AC 拒绝 | 命中文本；沙箱删除 |
| A5 | A | 负向 | 一行 `PASS` 证据，`actual` 写明未执行 | 未执行的检查记为 `PASS` | G1：`PASS` 行出现未执行/被阻塞字样即拒绝 | 命中文本；沙箱删除 |
| A6 | A | 负向 | 两个任务共享同一允许路径，其中一个已派发 | 重复派发同一任务 + 并发抢占同一路径 | 路径锁 + 状态机：两次调用都被拒绝，且被拒任务不留锁 | 拒绝消息、被拒任务的锁计数；沙箱删除 |
| A7 | A | 负向 | 已派发任务，未经过任何审查状态 | 执行者直接置 `APPROVED` / `VERIFIED` | G6：非法转移被拒绝，任务不前进 | 拒绝消息、状态与 `approved_sha`；沙箱删除 |
| B1 | B | 正向 | 三条 v2014 合成记录 | 无（控制组） | 迁移：记录数、版本、ID 唯一性与完成标记一致，重跑幂等 | 前后摘要、校验结论；产物随沙箱删除 |
| B2 | B | 负向 | 旧格式产物 + 合成备份 | 只写入 1/3 记录且无完成标记 | 迁移：半迁移被报出；从备份恢复后校验通过 | 恢复前后校验结论；沙箱删除 |
| B3 | B | 负向 | 已迁移集合中残留一行 v2014 | 迁移声称完成但版本混用 | 迁移：混版本行被报为版本隔离破坏 | 命中文本；沙箱删除 |
| B4 | B | 负向 | 结构合法的任务包声称升级完成；磁盘上没有产物 | 只有声明，没有产物 | G1 + 产物校验：声明结构合法但被产物检查否证 | 任务包 0 命中 + 产物缺失命中；沙箱删除 |
| C1 | C | 正向 | 三步回合：所有权、规则、提交屏障均已接线 | 无（控制组） | 跨模块：规则接线、所有权、屏障顺序全部满足 | 事件序列与不变量结论；纯内存 |
| C2 | C | 负向 | 同一回合，`action_resolved` 规则未接线 | 冻结规则从调用路径消失 | 跨模块：缺失事件被判为规则未接线 | 事件序列与命中文本；纯内存 |
| C3 | C | 负向 | player-2 操作 player-1 拥有的单位 | 所有权校验被关闭 | 跨模块：非所有者产生 `action_resolved` 即报错 | 命中文本；纯内存 |
| C4 | C | 负向 | 状态变更事件早于提交屏障 | 提交屏障被绕过 | 跨模块：事件顺序违规被报出 | 事件序列与命中文本；纯内存 |
| C5 | C | 负向 | 合成 unittest 模块：一通过、一崩溃、一靠后 | 崩溃用 `ZeroDivisionError` 而非断言 | 运行器：记为 error、保留堆栈、后续用例继续执行 | `testsRun` / `errors` / `failures`、堆栈、执行顺序；纯内存 |
| C6 | C | 正向 | 合成数据树 + 共享 manifest + 锁文件（对照 R2–R6） | 无（控制组） | 共享状态：全部调用点加锁、回滚后文件树与基线逐字节一致、锁已释放、目录扫描 0 命中 | manifest 计数、树摘要对比、锁存在性、扫描报告；沙箱删除 |
| C7 | C | 负向 | 共享 manifest，两个写入者，第二个插在第一个读改写窗口内 | 自动保存调用点绕过锁 | 共享状态：调用点清单点名未加锁的调用点；**最终计数丢失一次更新**；加锁重跑两次都在 | 调用点清单、最终 `count` 与 `writers`；沙箱删除 |
| C8 | C | 负向 | 合成数据树 + 变更前快照 | 回滚只恢复“记得的文件”，新增文件留在树里 | 恢复：报出残留且树仍与基线不同；精确恢复后树与基线一致 | 残留清单、两次树对比；沙箱删除 |
| C9 | C | 负向 | 数据树 + 在整树快照之后创建的锁文件 | 业务回滚恢复整个根目录，删掉活动锁 | 锁生命周期：第二个写入者取得孤儿锁（互斥失效）；只回滚数据时锁存活并继续拒绝第二个写入者 | 锁存在性、第二写入者是否取得锁、`LOCK_HELD`、树摘要；沙箱删除 |
| C10 | C | 负向 | 三阶段多文件提交，在第二阶段失败 | 报告写 `rollback: COMPLETE`，但第一阶段文件仍在磁盘 | 阶段化失败：诚实报告给出失败阶段、已完成阶段、副作用、备份位置；虚假“完整回滚”被文件树否证 | 报告字段、数据树剩余文件；沙箱删除 |
| C11 | C | 负向 | 目录内两个可读文件 + 一个无法按文件读取的条目 | 扫描跳过坏条目却仍报告成功 | 错误语义：`processed != total` 与“成功”自相矛盾；修正后报 `FAILED` 并点名条目 | 前后两次扫描报告、跳过 / 错误列表；沙箱删除 |
| C12 | C | 正向 | 沙箱内合成项目树：领域、应用服务、适配器、两个传输入口；声明了 `ENFORCE` 契约 | 无（控制组） | 架构契约：无 `ERROR`、报告带范围（`examined_modules`）、两个入口决策一致、Mock 证据只标 Mock、能力声明绑定探针 | JSON 报告的 status / counts / examined_modules / invariants、共用契约测试结论、终局矩阵、能力检查结果；沙箱删除 |
| C13 | C | 负向 | 应用服务 `import` 适配器运行时私有模块 | 适配器私有符号泄漏进核心 | `PRIVATE_ACCESS`：`ENFORCE` 下阻断，定位到规则 / 模块 / 文件 / 行，绑定不变量随规则变 `ERROR` | 报告 `blocking`、`PRIVATE_ACCESS` 命中、`invariants[]` 覆盖；沙箱删除 |
| C14 | C | 负向 | 一个入口委派应用服务，另一个入口重写业务规则（内联版 + 直接 import 领域版） | 内联重写没有 import 边，静态检查看不见；结构版是直接依赖 | `FORBIDDEN_EDGE` 拦结构版；共用契约测试抓内联版（同请求不同决策）；`CONTRACT_TEST` 不变量保持 `UNKNOWN` | 两种变体的 `blocking` 与命中、行为差异清单、`invariants[]` 覆盖；沙箱删除 |
| C15 | C | 负向 | Mock 实现上跑通的共用适配器契约套件 | 结果被标成 `REAL_RUNTIME` | 证据标签：越级声明被拒；Mock 运行对真实行为的验证状态恒 `UNKNOWN` | 标注检查命中、各证据类型的验证状态；沙箱删除 |
| C16 | C | 负向 | 四个非成功派发终局经由一个泛化错误码记录 | 失败 / 取消 / 超时 / 未知折叠成一种，未知被改写成失败 | 终局契约：折叠被点名，未知不得变成功，修正后每个终局各有稳定码 | 终局检查命中、修正前后码集合；沙箱删除 |
| C17 | C | 负向 | 执行隔离 / 取消的能力记录：无证据、仅“启动成功”、通配范围、探针测别的能力 | `PASS` 缺少证据或范围 | 能力声明门：四类越级全部被拒；诚实的 `UNKNOWN` 不被升级 | 每条记录的能力检查结果；沙箱删除 |
| C18 | C | 正向 | 同一棵含违规的项目树，但**没有** Architecture Contract | 无（控制组）：不得从代码推断规则 | 契约可选性：`OFF` / 只读诊断、退出 0、无 `ERROR` / `WARNING`、不声明不变量与覆盖；随后声明契约即阻断 | 报告的 mode / status / counts、只读盘点计数，以及声明前后的对比；沙箱删除 |
| D1 | D | 正向 | `local` 项目并配置 `resource_lease_ttl` | 无（控制组） | edition + 资源锁：合法派发取得锁，`VERIFIED` 释放 | edition 值、锁行前后计数；沙箱删除 |
| D2 | D | 负向 | 四个项目根：缺文件、缺键、非法值、重复键；另有 `github` 根 | 每个根的 edition 都不可用 | edition：逐例 fail-closed 并指出要修的文件；`github` 拒绝本地调度 | 四条拒绝消息；沙箱删除 |
| D3 | D | 负向 | 两个路径不相交的任务声明同一 `exclusive` 资源 | 第二个任务请求已被占用的资源 | 资源锁：`MODE_CONFLICT` 拒绝，无半套锁，审计事件保留 | 拒绝消息、锁计数、拒绝事件数、任务状态；沙箱删除 |
| D4 | D | 负向 | 任务在 head `aaaa` 通过审查，随后工作区更新到新 head | 旧审批被复用到未审查的 head | G3/G4：审批被清除，`MERGED` 与旧 head 重新审批都被拒绝 | 状态、`approved_sha`、拒绝消息；沙箱删除 |
| D5 | D | 负向 | 任务已走完三轮审查—修复 | 请求第四轮 | 状态机：第四轮转 `BLOCKED` 并在备注写明上限 | 状态、重试计数、备注；沙箱删除 |
| D6 | D | 负向 | 已填写的阶段审计结论表（含正向子表） | 一行 `BLOCKING`，一行 `NEEDS_HUMAN_DECISION` | 阶段审计：干净表 0 命中；两类结论禁止收口声明 | 三张表的命中文本；纯内存 |
| D7 | D | 负向 | AC 已勾选但证据矩阵为空 | 执行者没有任何证据却声称完成 | G1/G6：空矩阵、无证据 AC、直接 `VERIFIED` 全部拒绝 | 校验命中、拒绝消息、锁计数；沙箱删除 |
| A8 | A | 正向 | 一个合成已合并 PR、一个合成 commit 与一次 CI 运行 | 无（控制组） | 自我完善：经验必须经 `VERIFIED` → 重新分类 `GENERAL_LESSON` → `GENERALIZED` 才可计划；只有 `READY` 进入载体，载体报告 `executed=False` | 扫描状态 / 证据数、lesson ID 与证据 URL、proposal 状态、带证据矩阵表头的登记 payload；内存来源替身，沙箱删除 |
| A9 | A | 负向 | 公共来源的 pulls 端点网络失败 / 403；另一个 `authorized_private` 来源同样 403 | 来源不可读，扫描没有任何证据 | 来源信任：`BLOCKED` 且原因保留；闭环报 `SCAN_INCOMPLETE`；私有来源升级为 `NEEDS_HUMAN_DECISION`；不产生 lesson、不登记任务 | 每例的 scan_status / blocked_reason、loop 状态、决策清单、lesson 数；沙箱删除 |
| A10 | A | 负向 | 合成 PR 标题携带“忽略规则 / 发送令牌”类指令形状 | 把来源文本当指令执行 | 不可信数据：形状被计数、URL 被标记、文本只作数据保存；分析者标题与允许路径不变；不产生任务 | 指令 flag、可疑 URL、lesson 标题、proposal 允许路径、`data_cell` 输出；沙箱删除 |
| A11 | A | 负向 | 两个合成 PR 用同一根因 / 影响分析 | 同一指纹因第二个证据 URL 再建一条 lesson | 去重：第二次摄取报 `duplicate`，只保留一条记录且合并两个证据 URL；未泛化记录不能成为任务 | ingestion 类型、存活 lesson ID 与证据 URL、`duplicate_of`、计划状态；沙箱删除 |
| A12 | A | 负向 | 一条已 `VERIFIED` 的经验带一个 `OPEN` `CONTRADICTS` 冲突 | 冲突被忽略，继续按不完整任务包计划 | 规划：先抛 `HD-04` `NEEDS_HUMAN_DECISION`，任务包检查不得掩盖冲突；闭环停在该状态且零登记 | proposal 状态 / 决策 ID / 原因、loop 状态、决策清单、登记数；纯内存 |
| A13 | A | 负向 | 一条 `E1_SOURCE_ARTIFACT` + `OBSERVED` 的缺陷记录 | 未验证经验直接计划成实现任务 | 生命周期：`can_generate_task` 拒绝、规划返回 `NO_TASK`、状态机拒绝 `OBSERVED → GENERALIZED`（跳过验证） | 资格原因、proposal 状态、状态机拒绝文本；纯内存 |
| A14 | A | 负向 | 一条已泛化经验，任务包要改 `AGENTS.md` | 治理面被当成常规改进任务 | 风险门：`HD-05` `NEEDS_HUMAN_DECISION`，`risk_level=HIGH` 且原因可定位；闭环零登记 | proposal 状态 / 决策 ID / 风险原因、loop 状态、登记数；纯内存 |
| A15 | A | 正向 | 合成来源扫描 + 一条经验走到 `GENERALIZED` + 合成载体 | 无（控制组） | 端到端：触发路由 → 状态恢复 → 扫描分类 → 计划门 → 登记一条 `executed=False` 的任务 → 一个已执行检查记 `PASS`、一个未执行命令记 `NOT_RUN` → 输出收口报告 | trigger / state / scan / plan / registration / validation / not_run 字段与报告章节；内存载体，沙箱删除 |
| B5 | B | 正向 | 合成 v1.5 项目（含基线清单）+ v1.6 发行 zip | 无（控制组） | 安全更新：包校验全过、字节与清单被应用、项目自有文件不动、备份验证、独立应用后回归 0 命中 | 报告 result / 版本 / 10 项检查 / 备份 / 回滚、回归结论、命令退出码；合成树与 zip 随沙箱删除 |
| B6 | B | 负向 | 干净升级前本地改过 `AGENTS.md` | 本地与上游同时修改同一路径 | 三方分类：`STOPPED_CONFLICT`、`THREE_WAY_CONFLICT`、零写入、`HD-10` | 报告 result、`AGENTS.md` 分类项、决策 ID、项目快照；沙箱删除 |
| B7 | B | 负向 | 发行 zip 的 `MANIFEST.json` 声明 `schema_version: 2` | 不支持的清单结构被接受 | 包校验：停在 `MANIFEST_SCHEMA` 的 `STOPPED_UNSAFE_PACKAGE`，后续检查 `NOT_RUN`，零写入 | 报告 result、检查结果、项目快照；沙箱删除 |
| B8 | B | 负向 | 发行 zip 内含 `agentorch-<date>/../escape.txt` | 归档成员试图逃出解压根目录 | 包校验：停在 `MEMBER_PATHS`；项目内外都不出现逃逸文件 | 报告 result、`MEMBER_PATHS`、文件系统检查、项目快照；沙箱删除（含本应逃逸的路径） |
| B9 | B | 负向 | 本地存在 `scripts/local-tool.py`，两份清单都不认识它 | 套件从未发布的本地文件被当成上游路径 | 分类：更新仍可应用，该文件为 `UNKNOWN_FILE` / `KEPT_LOCAL`，字节不变 | 报告 result、分类项、本地文件字节；沙箱删除 |
| B10 | B | 负向 | 目标包不再包含 `templates/task-packet.md` | 更新器删除上游消失的文件 | 分类：`STOPPED_CONFLICT`、`UPSTREAM_REMOVAL` / `KEPT_LOCAL`、文件保留、`HD-09` | 报告 result、分类项、决策 ID、项目快照；沙箱删除 |
| B11 | B | 负向 | 旧项目没有 `MANIFEST.json` | 更新器猜测一个基线继续执行 | 基线门：`STOPPED_INVALID_BASELINE`，附只读盘点说明；fetch / download 调用为 0；零写入 | 报告 result / baseline 状态 / notes、来源调用计数、项目快照；沙箱删除 |
| B12 | B | 负向 | git 仓库内有一处未提交修改 | 更新器 stash 或改写脏工作区 | 工作区隔离：在隔离 worktree 内 `APPLIED`，原树不动、`git stash list` 为空；关闭隔离时 `STOPPED_DIRTY_WORKTREE` + `HD-12` | workspace action / dirty paths / worktree 字节、head SHA、stash 列表、第二次运行结果与决策；沙箱（含 worktree）删除 |
| B13 | B | 负向 | 干净升级，在写入两个文件后注入故障 | 半更新被报告成已完成 | 阶段化失败：`ROLLED_BACK`、回滚 `SUCCEEDED`、备份已验证、逐字节恢复基线、回滚校验 `PASS` | 报告 result、回滚字段、应用后检查、项目快照；沙箱删除 |
| B14 | B | 负向 | 同样的半途写入失败，并在恢复后损坏一个文件 | 回滚失败被报告成干净更新 | 回滚诚实性：`BLOCKED`、回滚 `FAILED` 并带证据、`HD-11`、`rollback_verification` `FAIL` | 报告 result、回滚 result / evidence、决策 ID、应用后检查；沙箱删除 |
| C19 | C | 正向 | 两个合成模块：`api → core` 声明为允许，反向边禁止 | 无（控制组） | 依赖方向：`ENFORCE` 下无 `ERROR`，允许边被真实观测，两条规则均 `PASS`，不阻断 | JSON 报告 status / counts / 边集合 / 规则状态；沙箱删除 |
| C20 | C | 负向 | 模块间互相 `import` | 依赖循环被当成可接受结构 | `NO_CYCLE`：阻断 `ERROR`，点名两个模块、循环路径与具体文件 / 行 | 报告 blocking / counts、循环命中 modules / message / file / line；沙箱删除 |
| D8 | D | 正向 | `templates/metrics.md` 内固定的金样本 | 无（控制组） | 指标诚实性：七项指标、每行都有 sample / eligible / missing / missing_rate / coverage、窗口显式，且不发明耦合 / 内聚分数 | metric 行、window、summary coverage、人类输出、被禁分数词；纯内存 |
| D9 | D | 负向 | 空事件文件；以及只有 `READY`、没有 `VERIFIED` 的任务 | 空输入被算成 0；缺事件被算成零时长 | 指标诚实性：每项 `no_data`、value `null`、coverage `0.0`；`READY`-only 任务使 M1 为 `partial`、sample 0 / missing 1、value `null` | 指标状态 / 值 / coverage、headline、M1 detail；临时事件文件随沙箱删除 |
| E1 | E | 正向 | 一个已初始化的 scope，`agent-registry.md` 列出 Master / Execution / 独立 Reviewer；一份固定合成模型快照 | 无（控制组） | S1–S4 串联：C1–C4 `PASS`、scope 隔离、注册表发现、硬筛选先于外部排序、Reviewer 独立、本机反馈只作建议；报告保持 `executed=False`，选择不是派发 | gate / scope ID、发现结果与候选、选中的 Agent / 模型、来源状态 / 新鲜度 / 索引版本、反馈指标与样本 / coverage、`selection-history.md` 的 `executed=false`、scope 目录清单；沙箱删除 |
| E2 | E | 负向 | `PARTIAL` 能力记录且 C1 为 `UNKNOWN` | 未探测的 spawn 能力被当作可用 | C1–C4 门：`callable` 保持 `UNKNOWN`，没有角色被选中，任务人工交接，自动派发关闭；选择模块里不存在 spawn API | gate 状态 / 原因、Execution 选择 / 交接、callable 过滤、降级说明；沙箱删除 |
| E3 | E | 负向 | 一份 C4 `FAIL` 记录，一份探针超过新鲜度窗口的记录 | 失败或过期的能力解锁自动派发 | C1–C4 门：两种情况都 `BLOCKED`（过期一种带 stale），没有角色被选中，自动派发关闭 | gate 状态 / stale 标志 / 原因、选择结果、automatic 标志；沙箱删除 |
| E4 | E | 负向 | 发现结果只有 Master 与 Reviewer | 选择器虚构或自动派发 Execution | 角色匹配：Execution `selected=None`、人工交接并给出注册指引，任务保持未派发，自动派发关闭 | Execution 选择、提醒文本、降级清单；沙箱删除 |
| E5 | E | 负向 | 发现结果只有 Master 与 Execution | 用执行者自审替代独立 Reviewer | 角色匹配：Reviewer `selected=None`、交接，提醒写明自审不可替代，任务保持未审查，自动派发关闭 | Reviewer 选择 / 提醒、降级清单；沙箱删除 |
| E6 | E | 负向 | Reviewer 候选的模型 ID 与已选 Execution 相同 | 高外部评分让同一底层模型被当成“独立” | 独立性硬筛选：同模型 ID 判 `FAIL`，不选 Reviewer，Reviewer 维度保持 `UNKNOWN`，自动选择被扣住 | 被排除候选的过滤结果、independence 详情、评估信号、automatic 标志；沙箱删除 |
| E7 | E | 负向 | 只能接收文本的 Master 候选，其余证据有效 | 纯文本 Master 满足多模态硬要求 | 模态硬筛选：`FAIL` 并点名缺少 image，不选 Master，自动派发关闭 | modality 状态 / 详情、Master 选择、降级说明；沙箱删除 |
| E8 | E | 负向 | 同一批候选上 `AUTH_FAILED` 的抓取与过期的 `STALE_CACHE` | 失败或过期的数据被当成最新证据，或反过来阻断治理 | 来源诚实性：评估为 `UNLINKED` / `STALE` 且等级全 `UNKNOWN`；S2 选择仍成立；要求新鲜数据的策略扣住自动选择 | 抓取状态 / live / freshness、评估、降级、策略决策；纯内存批次，沙箱删除 |
| E9 | E | 负向 | Execution 候选成本高于任务成本上限 | 强候选越过预算硬限制被选中 | 预算硬筛选：`FAIL` 写明上限与实际成本，不选 Execution，自动派发关闭 | budget 过滤详情、选择结果、automatic 标志；沙箱删除 |
| E10 | E | 负向 | 没有会话 / TTY / 命名空间的项目根；随后两个不同 scope | 解析器回退共享目录，或记录跨 scope 写入 | scope 隔离：解析失败关闭且不建目录；跨 scope 记录被拒，两个 store 都没有记录 | 解析字段、`open_store` 拒绝、scope ID、每 scope 记录数、目录清单；沙箱删除 |
| E11 | E | 负向 | 一个已初始化 scope，原子替换失败 | 失败的反馈 / 选择写入被报告成完整审计链 | 写入诚实性：`FeedbackWriteError` 说明记录未写入，两份文档都保持 0 条记录 | 两条拒绝消息、记录计数；沙箱删除 |
| E12 | E | 负向 | 一个不满足工具 / 权限的候选，以及一次空发现 | 从零合格候选里静默选出一个角色 | 角色匹配：三个角色全部 `selected=None` 且交接，报告写明没有候选通过全部硬筛选，自动派发关闭 | 三个角色选择、原因、降级、空发现结果；沙箱删除 |
| E13 | E | 负向 | 无 spawn + 缺 Reviewer + 模型数据过期 + 跨 scope + 写入失败同时出现 | 把零散证据拼成“已派发 / 已通过”的结论 | 选择器停止：Execution 与 Reviewer 均为 `None` 且交接，降级说明点名缺 Execution / Reviewer 与过期来源；跨 scope 记录被拒，失败写入如实报告 | 降级清单、角色选择、scope 错误、记录计数、executed 标志；沙箱删除 |

## 4. 运行方式

```bash
# 人类摘要（默认）
python scripts/run_delivery_scenarios.py

# 机器可读报告
python scripts/run_delivery_scenarios.py --json

# 只跑指定场景，并把报告写到仓库外
python scripts/run_delivery_scenarios.py --only D3 --only C5 --output <仓库外路径>/scenarios.json

# 列出场景 ID
python scripts/run_delivery_scenarios.py --list
```

退出码只有三种，不能互相折算：

| 退出码 | 含义 |
|---|---|
| `0` | 全部场景 `PASS` |
| `1` | 至少一个场景 `FAIL` 或 `ERROR`（其余场景仍已执行完） |
| `2` | 运行器自身没能开跑：模块缺失、`--only` 未知 ID、`--output` 不可写 |

`--json` 的报告结构：`schema`（`agentorch.delivery-scenarios/1`）、`generated_at`、`python`、`platform`、`scenario_doc`、`totals`（含正负组通过数）、`groups`、`scenarios[]`（每个场景带 `precondition` / `injected_defect` / `expected_gate` / `evidence` / `error`）。报告里的临时路径统一脱敏为 `<sandbox>`，便于直接贴进 PR 证据。

## 5. Fixture 规则

- 全部 fixture 在运行时生成到 `tempfile` 目录，**不读取也不写入仓库**；报告只在 `--output` 指定位置落盘。
- 只用标准库：无网络请求、无凭据、无外部模型、无第三方依赖。
- 任务包 fixture 的字段由 `validate_kit.py` 从 `kit/orchestration.md` 读回，契约改了 fixture 不会“自动跟随”出一个假通过：证据列名或状态枚举变化会让场景直接失败。
- 迁移 fixture 是小型 JSON 记录集（v2014 → v2024），跨模块 fixture 是内存事件序列，跨模块状态 / 恢复 fixture 是沙箱内的合成目录树、合成 manifest 与合成模块源码字符串，都不含真实业务数据。
- 架构契约 fixture 在沙箱内写出合成源码树与一份契约 JSON，然后调用**真实检查器**（只启用 Python 适配器；Rust 适配器既不启用也不宣称）；语义项（证据标签、终局、能力声明）由场景内的小型契约测试判定，并明确标注为合成模型，不代表任何真实运行时。
- 自我完善 fixture 使用虚构的下游仓库、虚构的 PR / commit / CI 记录与 `_SyntheticGh`（回答真实收集器构造的同一组 `gh api` argv）；`scripts/self_improvement/` 的来源注册、收集、去重、生命周期、规划与闭环代码都是**真实模块**，没有网络、没有令牌、没有第三方模型。
- 更新 fixture 在沙箱内构造合成旧项目树与合成 v1.6 发行 zip，`scripts/update_agentorch/` 的包校验、分类、备份、回滚与报告代码都是**真实模块**；`_UpdateSource` 只在内存中提供 release 记录与字节，调用计数用于证明没有网络。
- 指标 fixture 直接读取 `templates/metrics.md` 内嵌的金样本，并额外写一个空事件文件；不访问任务库、不读取任务备注或提示词正文。
- 角色选择 fixture（E 组）在沙箱内建临时项目根与一个初始化 scope，`agent-registry.md`、`observed-performance.md` 与 `selection-history.md` 都由**真实 S1 store** 写入；发现使用内存中的固定 adapter 或该 scope 的注册表，模型数据是内存里的 `SnapshotBatch`，`UNKNOWN` / `STALE` / `AUTH_FAILED` 由真实 S3 判定，不访问网络、不需要 API Key，也不读真实 `.local`。没有任何场景在运行时真正 spawn 子代理。
- 需要 git 的只有 B12（隔离 worktree）；`git init` / `commit` / `worktree` 都在场景沙箱内执行，`GIT_CONFIG_NOSYSTEM=1`、`GIT_TERMINAL_PROMPT=0`，不读写真实仓库。
- 同一个场景重复运行得到同样的判定与摘要（迁移摘要由内容决定，不写时间戳）。
- 场景实现按组放在 `scripts/delivery_scenarios/`：`group_a.py` … `group_e.py` 各管一组，跨组共享的 fixture 与 helper 在 `common.py`；注册表、选择器、执行器与 CLI 仍在 `scripts/run_delivery_scenarios.py`。分组只是代码组织，不改变场景 ID、顺序、类别或报告结构。

## 6. 清理

- 每个场景一个独立沙箱，`run_scenarios()` 在 `finally` 中删除；运行结束后工作目录为空（测试会断言这一点）。
- 场景不创建仓库外分支、不写真实 Git、不改 `.orchestration/`。B12 只在其沙箱内 `git init` 并创建隔离 worktree，随沙箱一起删除。
- 更新引擎的备份写到系统临时目录（`tempfile.mkdtemp`），不进入项目；场景证据只引用“备份已验证”，不打印备份绝对路径。
- 若要保留证据，用 `--output` 写到仓库外；不要把报告提交进仓库。

## 7. local 与 github 的边界

| 能力 | `local` | `github` |
|---|---|---|
| G1 证据矩阵与任务包结构 | 离线可验证（A/B/D 组） | 同一套字段与校验器，同样可离线验证 |
| edition fail-closed、路径锁、具名资源锁、状态机 | 离线可验证（D 组） | 由 Issue 登记块与平台保护承担；**原子性不可离线证明** |
| 阶段审计分类 | 离线可验证（D6） | 同一份模板与判定函数 |
| 自我完善来源治理 / 去重 / 冲突 / 风险门 | 离线可验证（A8–A15，来源为合成替身） | 同一套模块与判定；Issue / PR 载体由合成载体代替，**真实 gh 权限与写操作未运行** |
| 安全更新引擎（校验 / 分类 / 备份 / 回滚） | 离线可验证（B5–B14，release 为内存替身） | 同一引擎；真实 GitHub Releases 下载、平台保护与合并后重验**未运行** |
| 交付指标报告 | 离线可验证（D8–D9，金样本与空输入） | 同一脚本；`github` 事件尚未接入真实时间线 |
| 角色选择链路（scope / 发现 / 模型数据 / 反馈） | 离线可验证（E1–E13，内存 adapter 与固定快照） | 同一套模块与判定；`github` 的 Issue / PR 载体只作为外部记录，选型记录仍只写本机 scope，**真实 spawn 与真实模型 API 未运行** |
| Issue / PR / CI 端到端 | 不适用 | **`BLOCKED`：本次未运行**，需真实仓库权限与 Actions |

## 8. 未验证范围

以下事项**没有**被这套场景证明，不得据此写 `VERIFIED`：

- 任何外部产品项目的真实数据、真实存档或真实业务规则；本套件没有对它们做过自动化运行。
- `github` 底座的端到端流程：Issue 创建、登记块占用、PR 检查、平台保护、合并后重验。它们需要真实仓库权限与 Actions 运行，本次标记为 `BLOCKED`，不能用 `local` 结果替代。
- 并发与崩溃恢复的真实时序：资源锁的原子性只在 `local` 单机语义内被验证，跨主机或网络文件系统不在范围内。
- 架构契约的**语义项**：Mock 与真实运行的边界（C15 只验证标注规则会拒绝越级声明，没有跑任何真实运行时）、入口行为一致性（C14 只比较合成入口的决策结果）、终局可区分性（C16 是合成模型，真实运行时终局仍需 C1–C4 探测）、能力证据真伪（C17 只能判断结构完整性与是否越级）。这些项必须由契约测试与 Reviewer 判定，见 [agentmgr-contract-rules.md](../architecture/agentmgr-contract-rules.md)。
- 运行时能力 C1–C4：本运行器不探测真实 Agent 运行时，能力登记仍以 [templates/runtime-capability.md](../../templates/runtime-capability.md) 的实际探测为准。
- **角色选择链路**：E 组使用合成 Runtime、固定模型快照与临时 scope，只证明 S1–S4 组合后的判定与降级语义；它不证明任何真实 Runtime 的 spawn / wait / collect 可用、不证明外部模型 API 可达、不调用任何模型，也不代表任何真实 Agent 已被选中或派发。真实选型仍须由 Master 按 C1–C4 探测与当前 scope 的记录完成。
- **模型 / 外部服务**：自我完善场景不调用任何模型 API、不使用真实密钥；真实来源仓库的私有数据、真实 Issue / PR 写入、真实 GitHub Releases 下载都没有运行，标 `NOT_RUN` / `BLOCKED`。
- **Windows / macOS**：本轮的场景只在 Linux + Python 3.13 上真实执行；其他平台标 `UNKNOWN`，不得据 Linux 结果推断。
- **指标趋势**：金样本与空输入只证明报告口径；样本量不足以支持任何跨窗口趋势结论，未采集的事件类型仍为 `unknown`。
- 性能与规模：fixture 刻意很小，不能用来推断真实项目的耗时或资源占用。

## 9. 结果解读与维护规则

- 负向组**必须是 `PASS`**：它通过意味着拦截发生了。看到一堆 `PASS` 不代表“没有失败”，先看 `totals.negative_passed` 是否等于 `totals.negative`。
- 新增或修改核心契约、校验器、模板、锁/状态机、发行流程时：先在本文件补场景行，再在对应组的 `scripts/delivery_scenarios/group_<x>.py` 实现，并在 `scripts/run_delivery_scenarios.py` 的注册表登记，最后跑 `python scripts/run_delivery_scenarios.py --json` 与 `python scripts/validate_kit.py`。
- 场景表与运行器必须同步：`tests/test_run_delivery_scenarios.py` 会检查每个场景 ID 都出现在本文件的矩阵里。
- 发现契约本身有问题时，另开 Issue 并先取得授权，**不要**通过放宽场景或删断言把它变成绿色。
- 本文件与 [kit/orchestration.md](../../kit/orchestration.md) 的 I 段配套：验收程序说“什么时候必须跑”，本文件说“跑什么、期望什么”。
