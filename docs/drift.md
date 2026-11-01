# 发行包版本清单与下游核心规则漂移诊断

> 本文件只存在于仓库（`docs/` 不随发行包发布）。面向发行包使用者的一节写在 `README.md` 第 8 节；
> 本文件记录设计口径、清单格式、分类规则与边界，供审查与后续维护对照。

## 1. 要解决的问题

项目把套件复制走之后，没有任何办法回答三个问题：

1. 我现在用的是哪一个套件版本？
2. 核心契约（`AGENTS.md`、`docs/orchestration.md`、底座手册、编排脚本）有没有被人改过？
3. 项目自己加的文件，哪些是本地扩展、哪些是套件路径上的野文件？

自动同步会覆盖项目特有政策，因此本任务只做**对照**：发行包带一份确定性清单，下游用只读命令比对，报告差异与建议，
不覆盖、不合并、不迁移。

## 2. 清单格式（`MANIFEST.json`）

```json
{
  "schema_version": 1,
  "suite": "Agent-Orchestration Starter Kit",
  "format_version": "1.2",
  "files": [
    {
      "category": "core",
      "path": "kit/orchestration.md",
      "sha256": "<64 位小写十六进制>",
      "size": 20658
    }
  ]
}
```

- `schema_version`：清单自身的格式版本（当前 `1`），与套件的 `format_version` 分开演进。
- `suite` / `format_version`：构建时从 `VERSION.md` 读取，不在构建脚本里另写一份常量。
- `files[]`：只列出随包文件；按 `path` 升序；同一路径只出现一次；不列出 `MANIFEST.json` 自身（自引用哈希无法内嵌）。
- **没有时间戳，也没有构建机器信息**：出现 `generated_at`、`timestamp`、`built_at`、`date` 等键会被校验直接拒绝。
  这是「同一工作树两次构建字节一致」的前提，也让清单可以进 diff、可以被复核。
- `sha256` / `size`：按**打包字节**计算——文本文件先做 LF 归一（无 NUL 且可按 UTF-8 解码即视为文本，CRLF 折成 LF），
  二进制原样。这样 Windows 上 `core.autocrlf=true` 的检出与 Linux 检出得到同一份清单。

## 3. 分类规则（唯一实现）

分类只有一处实现：`scripts/check_drift.py` 的 `classify_upstream()` / `classify_local()`。
`scripts/build_release.py` 通过导入使用它，不复制规则；`scripts/validate_kit.py` 校验清单时也复用同一模块。
`tests/test_release_drift.py` 直接断言这两条规则，避免出现第二套判定。

| 类别 | 判定 | 用途 |
| --- | --- | --- |
| `core` | 根目录契约与入口（未命中其他规则的随包文件） | 被改动或缺失即 drift |
| `edition` | 以 `editions/` 开头 | 被改动或缺失即 drift |
| `template` | 以 `templates/` 开头 | 被改动或缺失即 drift |
| `project-profile` | `docs/PROJECT.md`、`PLAN.md`、`DESIGN.md` | 安装后本应被填写，单独列出，不算 drift |
| `project-extension` | 清单之外、且不在上游目录内 | 项目自己的文件，单独列出，永不被覆盖 |
| `unknown` | 清单之外、却落在 `editions/`、`templates/`、`scripts/`、`prompts/`、`tests/` 内 | 套件路径上的野文件，单独列出并计为 drift |

`project-extension` 与 `unknown` 的差别是**归属**而不是「是不是本地文件」：前者是项目自己的目录
（`src/`、`docs/`、自建目录），后者占用了套件拥有的命名空间，可能是旧版本残留或本地改写，值得人工确认。
两类都不会被静默忽略。

## 4. 只读诊断

```bash
python scripts/check_drift.py <项目根>
python scripts/check_drift.py <项目根> --json
```

- 人类可读摘要写 stdout（`--json` 时改写到 stderr），JSON 只写 stdout，便于管道消费。
- 报告字段固定：`tool`、`report_schema_version`、`root`、`status`、`exit_code`、`manifest`、`version_span`、
  `summary`、`findings`、`notes`；工具错误时额外带 `error`。字段稳定由测试锁定。
- `findings` 分 `missing`、`modified`、`project_profile`、`unknown`、`extensions` 五组，逐条给出路径与类别；
  `modified` 条目同时带 `expected_sha256` / `actual_sha256` 与两侧大小，便于取证。

### 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | clean：只有项目文件差异（`project-profile`）或本地扩展 |
| `2` | drift：核心文件缺失 / 被改动、`unknown` 文件、清单 schema 超出工具支持范围，或项目没有 `MANIFEST.json`（版本未知） |
| `1` | 输入无效或工具错误：项目根不存在 / 不是目录、`MANIFEST.json` 不是合法 JSON 或结构不合法 |

`2` 是「有差异需要人看」，不是崩溃；`1` 才表示这次诊断没有完成。

## 5. 只读保证

诊断路径只做 `read_bytes` / `read_text`、`os.walk`、`stat`，不创建、不重命名、不删除、不覆盖任何目标项目文件；
没有 `--fix` / `--sync` 开关。测试用「全树内容 + `mtime_ns` 快照前后比对」证明这一点
（`tests/test_release_drift.py` 的 `test_diagnosis_writes_nothing`）。跳过 `.git`、`.orchestration`、`.local`、
`.worktrees`、`.workbuddy`、`__pycache__`、`release`、`node_modules`、`.venv` 等本地/缓存目录，也不跟随符号链接。

## 6. 旧项目（没有 `MANIFEST.json`）

- 报告 `status: version-unknown`、退出码 `2`，说明「版本未知 / 需基线登记」，并提示登记方式：
  保留初始发行包的 `MANIFEST.json`，或用 `scripts/build_release.py` 重新生成一份作为基线。
- **不猜版本**：没有清单就没有可信基线，工具不会按文件内容或 `VERSION.md` 推断套件版本。
- **不阻断读取**：诊断只输出报告，不修改项目，也不要求用户先迁移才能继续使用。

## 7. 构建与校验的联动

- `build_release.py`：先按 `REQUIRED` 收集并归一字节，再用 `check_drift.build_manifest()` 生成清单，清单作为
  `MANIFEST.json` 一起打进 zip；构建自检 a 校验「包内文件集合 == `REQUIRED` + `MANIFEST.json`」，
  并在解压目录里用 `check_drift.validate_manifest()` 校验路径可解析、哈希与大小一致、分类合法、清单路径与
  `REQUIRED` 完全一致。
- `validate_kit.py`：根目录存在 `MANIFEST.json` 时执行同一套清单校验（外加 `REQUIRED` 对账）；仓库根目录没有
  该文件，检查自动跳过，不误报——所以发布前在裸仓库里跑自检仍然通过。
- 归档时间戳固定（1980-01-01），文本 LF 归一，清单无时间戳：同一工作树两次构建的 zip SHA256 必须相同。

## 8. 负例（已被测试覆盖）

- 清单含时间戳 → 结构校验拒绝。
- 清单哈希只覆盖文本、漏掉二进制 → 二进制同样按归一规则计算（含 NUL 即原样），不设文本白名单。
- 把本地扩展自动「修复」→ 不存在该能力；扩展只报告。
- 无清单时猜版本 → 不猜，报 `version-unknown`。
- `--json` 混入人类可读日志 → 摘要强制走 stderr，stdout 只有 JSON。
- 诊断过程在目标项目写临时文件 → 只读路径 + 快照测试。
- 清单条目乱序 / 重复 / 越界（`..`、绝对路径）/ 非法分类 → 结构校验拒绝，退出码 `1`。

## 9. 非目标

- 不做同步、覆盖、自动合并、迁移或版本回滚。
- 不判断「哈希不同是否等于契约冲突」；只报告差异。
- 不改任务状态机、任务语义、底座结构与发布产物之外的仓库内容。
- 不引入第三方依赖。

## 10. 验证命令

```bash
python scripts/validate_kit.py
python -m unittest discover -s tests
python -m py_compile scripts/orchestrator.py scripts/validate_kit.py scripts/check_drift.py scripts/build_release.py
python scripts/build_release.py
python scripts/check_drift.py .
```

最后一条在**裸仓库**（没有 `MANIFEST.json`）上按设计返回退出码 `2` + `version-unknown`，这是「需基线登记」的
正确表现；在发行包解压目录里对干净副本执行应返回 `0`，对篡改过核心文件的副本应返回 `2`。
