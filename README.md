<p align="center">
  <img src="assets/agentorch-logo.svg" alt="AgentOrch — Workflow governance for AI-assisted development" width="560" />
</p>

<p align="center">
  <strong>让多 Agent 协作变成可追踪、可审查、可恢复的工程流程。</strong><br />
  <sub>Governed workflows for AI-assisted software development</sub>
</p>

<p align="center">
  <a href="https://github.com/ctrlshiftaltdel/AgentOrch/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/ctrlshiftaltdel/AgentOrch?display_name=tag" /></a>
  <a href="LICENSE.md"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-16a34a" /></a>
  <a href="https://github.com/ctrlshiftaltdel/AgentOrch/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/ctrlshiftaltdel/AgentOrch/ci.yml?branch=main&label=CI" /></a>
</p>

---

## AgentOrch 是什么？

**AgentOrch 是一套可复用的软件研发工作流治理工具包**：用统一契约、角色提示词、任务模板和轻量命令行工具，把多 Agent / 多会话开发中的任务状态、路径冲突、独立审查和交付证据固定下来。

它关注的是这条流程能否被恢复、审查和验证，而不是替代你正在使用的 Agent：

**需求与设计 → 可验收任务 → 隔离执行 → 独立审查 → Master 决策合并 → 合并后验证。**

| 常见失效 | AgentOrch 的处理方式 |
| --- | --- |
| 多个会话同时修改相同文件 | 用任务范围、路径锁和 Git 分支 / Worktree 管理冲突 |
| 切换会话后忘记真实进度 | 从权威任务载体和 Git 状态恢复，而不是依赖聊天记忆 |
| 执行者自己宣布通过并合并 | 固定 Master、Execution、Reviewer 的职责边界 |
| 只说“测试通过”，没有可复查依据 | 将验收标准映射到证据，并绑定当前 commit SHA |

## 核心能力

- **任务与状态治理**：任务 ID、依赖、允许 / 禁改路径、验收标准、状态流转和审查修复轮次。
- **冲突控制**：本地底座提供数据库范围内的原子路径锁与具名资源锁；GitHub 底座通过登记块和开放 PR 路径检查协调冲突。
- **独立审查与证据门禁**：执行者不自批、不自合；批准绑定具体 head，代码变化后必须在新 head 上重新验证。
- **项目启动工具箱**：可复用的项目配置、设计 / 计划 / 任务包 / 审查模板，以及本地和 GitHub 两套角色操作手册。
- **架构与漂移诊断**：可选的架构契约检查、套件清单与只读漂移报告；不会擅自覆盖下游项目文件。
- **自我完善闭环**：登记经验来源、采集可追溯证据、管理和验证 Lesson、形成改进提案与任务包。
- **安全更新引擎**：校验来源与发行清单，按文件分类更新，并提供备份、回滚和需要人工决策时的停止机制。
- **发布与回归验证**：发行包自检、合成交付场景、指标报告和跨平台 CI。
- **本机记录隔离（Phase D）**：每个终端 / Agent 会话解析独立的 scope，选型与能力记录只以 Markdown 保存在 `.local/agent-orch/<scope-id>/`，不入 Git、不进发行包；无法可靠识别 scope 时安全失败，不回退共享目录。
- **角色发现与匹配（Phase D S2）**：优先使用运行时正式枚举，无法枚举时读取显式注册表；按硬筛选（可调用性、模态、工具、权限、Reviewer 独立性）后接可解释排序，给出 Master / Execution / Reviewer 建议。能力 `UNKNOWN` / `STALE` 或缺少角色时降级人工交接，不自动创建或授权 Agent，选择不等于派发。
- **模型评测与角色专项选型（Phase D S3）**：可选接入 Artificial Analysis 官方 Data API，把官方返回映射为带来源、索引版本、查询时间与新鲜度的供应商无关快照；外部数据只在 S2 硬筛选之后补充角色相关证据与排序。API Key 只从受保护环境变量读取，离线时基础治理不受影响；Reviewer 没有专项证据时标 `UNKNOWN`，不用通用总分冒充。
- **本机角色表现反馈与可解释校准（Phase D S4）**：把 Master / Execution / Reviewer 在**当前 scope** 内的真实任务表现（任务类别、首次验收、返工轮次、合并后回归缺陷、真实可采集的耗时 / 成本；Reviewer 的确认问题、误报与需要独立对照的漏报；Master 的计划遗漏与拆分返工）追加记录到 `.local/agent-orch/<scope-id>/observed-performance.md`。报告只给可解释建议：每项指标带样本量、窗口、任务类别、缺失值与 coverage；样本不足、模型 / Runtime 身份变化或证据过期时标 `UNKNOWN` / `STALE` 且不排名；不自动改提示词、权限或模型配置，也不把聊天记忆、自评、合并成功或 CI 绿灯当质量证据。
- **会话入口接线与端到端回归（Phase D S5）**：`docs/orchestration.md` 的会话入口按「C1–C4 先决 → 实际工具事实 → scope / 记录 → 发现与匹配 → 可选模型数据 → 可选本机反馈」串起 S1–S4，两个底座的 Master / Execution 手册同步同一套角色约束与结果语义；缺少角色、不能真实 spawn、数据过期或本地写入失败时输出具体原因并降级，任何选择都不读作“已派发”。合成回归新增 E 组（E1 正向 + E2–E13 负向），全部使用模拟 Runtime、固定模型目录与临时目录，不访问真实网络 / Key / `.local`。

## 两种协作底座

核心角色、状态语义、审查门和证据标准在两个底座中保持一致；不同的是任务记录与证据的存放位置。

| 底座 | 任务载体 | 适合场景 | 主要边界 |
| --- | --- | --- | --- |
| **<code>local</code>** | SQLite 任务库；本地命令输出和 Git 状态作为证据 | 单人、单机、可离线运行 | 原子锁只在同一数据库与主机范围内成立 |
| **<code>github</code>** | Issue、Draft PR、PR 评论与 CI | 多人协作、跨机器接手、公开可核验的交付 | Issue 登记块和 PR 路径检查是协作性防冲突，不是跨请求原子锁 |

如果你不确定选哪一种，先阅读 [底座选择与迁移说明](docs/README.md)。从 <code>local</code> 迁移到 <code>github</code> 是一次性、单向的授权决策，不能同时双写两套状态。

## 工作流概览

1. **Master** 把目标拆成独立任务，定义范围、依赖、验收标准和验证方式。
2. **Execution** 在隔离的分支 / Worktree 内完成任务并提交逐条证据。
3. **Reviewer / Master** 独立检查差异、当前 head 的验证结果及所有验收标准；执行者不能替自己批准。
4. **Master** 决定是否合并；合并后重新验证，满足标准后才将任务标为 <code>VERIFIED</code>。

<pre><code>需求与设计
   ↓ Master：拆分任务，定义范围与验收标准
隔离执行：分支 / Worktree + 实际验证
   ↓
独立审查：Diff、逐条证据、当前 commit SHA
   ↓ 通过后由 Master 决策合并
合并后重新验证 → VERIFIED</code></pre>

## 自我完善与安全更新

AgentOrch 的自我完善不等于“让模型自由修改仓库”。它有明确的来源注册、证据、教训生命周期和人工决策边界：

- 只从已登记且获准访问的来源中收集证据；无法访问或权限不足时记录阻塞，不绕过权限。
- 区分观察到的问题、已复现 / 验证的事实和可泛化的教训；未经验证的猜测不能直接变成已证实缺陷。
- 只有满足条件、没有未决冲突的通用教训才适合形成改进任务；关键架构、安全或兼容性决策仍需人工批准。
- 更新套件时执行发行来源、清单与文件差异校验；遇到冲突或无法安全自动处理的变化会停止，而不是强行覆盖。

细节见 [自我完善设计与实现计划](docs/self-improvement-and-update-plan.md)、[自我完善操作说明](docs/self-improvement/README.md) 和 [升级与安全更新说明](docs/upgrade.md)。

## 快速开始

**推荐通过 [GitHub Releases](https://github.com/ctrlshiftaltdel/AgentOrch/releases/latest) 获取发行包。** 发行包是面向下游项目的可复用内容；源仓库还包含 AgentOrch 自身的开发记录、契约、审计与发布材料。

1. **新项目**：将发行包解压到项目根目录，按实际情况填写 <code>docs/PROJECT.md</code>，尤其是 <code>edition</code>、允许 / 禁改路径和验证命令。
2. **选择底座**：将 <code>edition</code> 设为 <code>local</code> 或 <code>github</code>，只能有一个权威值。
3. **恢复规则**：先阅读 <code>AGENTS.md</code> 和 <code>docs/orchestration.md</code>，然后使用对应底座的 Master 手册开始工作。
4. **已有项目**：不要直接把发行包解压覆盖到现有仓库；先在项目外解压、盘点同名文件冲突，再按需引入。

完整安装步骤、Agent 话术与命令对照、既有项目接入流程见 **[完整上手指南](docs/README.md)**。

## 开发与验证

仓库脚本使用 Python 标准库，不需要额外的 Python 运行时依赖。开发环境需使用仓库当前支持的 Python 版本；CI 验证矩阵见 [CI 配置](.github/workflows/ci.yml)。在仓库根目录运行：

<pre><code>uv run python scripts/validate_kit.py
uv run python -m unittest discover -s tests
uv run python scripts/run_delivery_scenarios.py --json</code></pre>

发行包的文件清单、隐私扫描、包内回归和可复现构建要求见 [发布标准](release/README.md)。新增的规则或模板应保持项目无关；下游项目的专有决策放在各自的项目配置与任务记录中。

## 文档导航

| 需要了解 | 文档 |
| --- | --- |
| 权限、角色、Git 纪律与公开仓库安全规则 | [AGENTS.md](AGENTS.md) |
| 状态机、证据矩阵、合并门与会话入口 | [核心编排契约](docs/orchestration.md) |
| 快速上手、底座选择与已有项目接入 | [完整用户指南](docs/README.md) |
| 设计目标与原则 | [设计目标](docs/PLAN.md) |
| 阶段计划与当前项目状态 | [阶段计划](docs/implementation-plan.md) |
| 本地协作手册 | [local Master 手册](editions/local/master.md) |
| GitHub 协作手册 | [github Master 手册](editions/github/master.md) |
| 自我完善闭环 | [自我完善文档](docs/self-improvement/README.md) |
| 版本升级、安全更新与回滚 | [升级指南](docs/upgrade.md) |
| 交付指标与证据口径 | [指标说明](docs/metrics.md) |
| 架构契约与检查器 | [架构契约](docs/architecture/architecture-contract.md) |
| 发行包构建和发布规范 | [发布标准](release/README.md) |

## 明确的边界

AgentOrch **不**是 Agent Runtime、模型服务或 AI 网关，也不负责实际运行模型、自动启动子 Agent、替代 Agent 编写业务代码、自动合并 Git 分支，或在下游项目中无授权地同步 / 覆盖文件。实际执行能力来自你选用的 Agent、工具、Git 与 CI 环境；AgentOrch 负责让流程、权限、状态和证据有可核验的约束。

此外，<code>github</code> 底座依赖 GitHub Issue / PR / CI 可用性；私有来源采集需要已有授权。外部平台或运行时未实际验证的能力应保持 <code>UNKNOWN</code> / <code>NOT_RUN</code>，不可仅凭提示词声明当作已具备。

## 版本与许可证

- 当前发行记录与格式版本以 [VERSION.md](VERSION.md) 和 [GitHub Releases](https://github.com/ctrlshiftaltdel/AgentOrch/releases) 为准。
- 原始规则文本、模板和辅助脚本按 [MIT License](LICENSE.md) 提供。
- 问题与建议：通过 [GitHub Issues](https://github.com/ctrlshiftaltdel/AgentOrch/issues) 提交。
