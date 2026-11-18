# GREAT Agent

GREAT Agent 是一个仅绑定本机的 Windows Web Agent：把自然语言或表单修改转换成可审阅、可冻结的 PPP-FLT 计划，按日期展开为可恢复任务，并在隔离输出目录中运行 GREAT_PVT。输入 GNSS 数据始终只读；生成的 XML、日志和 `.flt` 只写入配置的 `run_root`。

## 科研分析工作区（2026-11-18）

第三个顶部 Tab“科研分析”按课题关联实验结果、源码和文献，支持连续追问、针对具体回答/假设追问、查看原始证据、记录反例与用户更正，以及保存不可变分析报告。从实验进度或图片页“带入科研分析”会绑定当前选择的具体日期任务和 attempt，不会自动发送问题或启动模型。

研究助手只使用注册的读取工具。修改位置、伪代码和差异均为建议，不会应用补丁、执行脚本/测试、重新后处理、改实验计划或运行 GREAT。固定比较工具只读取已有 analysis JSON；缺少指标或比较口径不兼容时明确返回缺项。

新数据库版本为 **0010**，研究会话、材料快照、模型调用、证据、检查点和报告保存在独立 research 表中。升级已有正式环境前，停止 API、Solver Worker 和 Research Worker，完整备份后迁移，再按现有启动流程重建和启动；开发验证不会自动升级正式数据库或替换正式前端。

本机已于 2026-11-18 经用户授权完成备份、0010 迁移和正式启用，当前运行 API 与科研 Worker，求解器 Worker 保持关闭。试用入口为 <http://127.0.0.1:8765/?workspace=research>；备份位置、服务身份与只读验收见[启用记录](docs/validation/2026-11-18-research-production-enablement.md)。

```powershell
.\scripts\stop.ps1
# 先按“数据安全和备份”完成实际备份，再运行：
uv run great-agent migrate --config config/local.toml
.\scripts\start.ps1
```

普通 `start.ps1` 现在同时管理 API、Solver Worker 和独立 `research-worker`；`-ApiOnly` 仅启动 API，研究请求仍可保存排队，需另启研究执行器才会处理。手动开发入口为：

```powershell
uv run great-agent research-worker --config config/local.toml
```

`[research] enabled = false` 可关闭该后端能力。默认研究模型继承主模型地址/密钥，但使用独立的推理、输出和任务预算；也可通过 `[research.model]` 单独配置。密钥只来自 `GREAT_AGENT_RESEARCH_MODEL_API_KEY`，缺省时回退主模型环境密钥，不使用 TOML 中的密钥。参见 `config/app.example.toml`。

`[research.code_roots]` 配置可读源码目录，界面通过注册来源 ID 和相对文件名选择，不能任意读取主机路径。代码首版逐文件选择，最多 200 个文件、单文件 1 MiB、每份快照 8 MiB；不会运行 Git 或源代码，提交标识来自只读 Git 元信息。源码当前版本与历史运行程序的关联默认未知。文献固定具体版本，具备可用的现成向量索引时使用混合检索，否则明确降级为词法检索，不隐式建立新文献聊天或重建索引。

每轮冻结材料和模型配置，材料更新仅影响后续轮。切换 Tab、课题、刷新或事件流断开不取消任务；停止通过显式服务端取消。已保存模型响应和工具结果可在恢复时重放；结果未知的调用不会自动重发。报告保留旧证据，后续追问不覆写历史报告。草稿与待恢复请求仅存当前浏览器标签页的 sessionStorage，材料、已发送问题与结果由服务端持久化。

设计与实施范围见 [科研分析设计](docs/superpowers/specs/2026-11-18-research-analysis-design.md) 和 [实施计划](docs/superpowers/plans/2026-11-18-research-analysis.md)。

## 准备环境

需要 Windows 10/11、PowerShell 7、Python 3.11、[uv](https://docs.astral.sh/uv/) 和 Node.js。仓库不包含 GREAT 可执行文件、模板、GNSS 观测数据或精密产品；使用者须按相应许可证自行准备。

```powershell
uv sync
npm --prefix frontend install
Copy-Item config\app.example.toml config\local.toml
```

编辑被 Git 忽略的 `config/local.toml`，确认项目、运行目录、数据库、GREAT_PVT、XML 模板和只读输入根目录。模型密钥只放在进程环境中，不写入 TOML，也不会进入浏览器：

```powershell
$env:GREAT_AGENT_MODEL_API_KEY = '<your-key>'
```

默认示例使用智谱通用 API：`base_url = 'https://open.bigmodel.cn/api/paas/v4'`、`name = 'glm-4.7'`，环境变量中应填写智谱的标准 API Key。`[model]` 中设置 `response_format = 'json_object'`，项目会将计划 schema 放入提示词并在本地严格校验返回结果；其他支持 JSON Schema 的服务可配置为 `json_schema`。修改配置或密钥后需重启 API 服务。

### 提案等待、取消与耗时

GLM-4.7 系列的提案请求默认发送 `thinking = {"type": "disabled"}`，避免简单计划编辑额外进行深度思考。可在 `[model]` 配置 `thinking = 'enabled'` 手动恢复；该字段不会发送给其他模型。此设置只影响提案，不改变 GREAT 解算和人工确认。

发送提案后显示等待秒数，可点击“取消生成”；切换实验或离开工作台也会中断等待，只切换中间视图不会中断对话。前端忽略已取消请求的迟到响应，后端在发现断开后取消上游 HTTP 协程，不会接受计划或启动解算。取消不能保证模型服务商立即停止计算或退回已经发生的费用。

成功后可展开“耗时明细”，查看读取计划、模型请求（包含网络、排队和生成）、本地校验、API 总耗时及浏览器往返。总耗时包含子阶段，不能将所有行相加；这里没有单独测量模型首 token 或思考耗时。请求编号对应 API 日志中的 `chat_proposal_stage` / `chat_proposal_timing` / `chat_request_timing`，成功、失败、超时与取消都记录安全的 ID、阶段、状态和毫秒数，不记录 Key、对话内容、模型返回内容或供应商错误正文。

`max_tokens` 限制提案输出长度，`request_timeout_seconds` 是覆盖连接、排队、生成和响应读取的整次请求总时限；它与单次连接/读取空闲超时不同。默认总时限为 180 秒，超时后恢复工作台控件且不保存提案。没有自动重试或额外的模型探测请求。

API 控制台、访问和提案阶段日志同时写入 `runs/_service_logs/api.log`，单文件达到 5 MiB 后轮转，保留 5 份历史文件。生产启动脚本即使隐藏后台窗口也可以从该文件定位请求所处阶段；修改模型或超时配置后需要重启 API 服务。

对话报错时先复制页面的请求编号，再按编号查日志（包含轮转文件）：

```powershell
Select-String -Path 'F:\GREAT_Agent\runs\_service_logs\api.log*' -SimpleMatch '<请求编号>'
```

`conversation_stage` 记录失败阶段、异常类型、安全诊断码与上游 HTTP 状态（若可用），不记录密钥、消息或供应商响应正文。先区分 `execution_confirmation`（执行校验）、`model_request`（模型请求）、`proposal_validation`（提案校验）和 `persistence`（保存回执）；页面的通用 provider 提示本身不能证明供应商故障。`missing_api_key` 表示 API 进程缺少密钥，即使用户环境中已有配置也应检查启动时是否继承。`start.ps1` 优先使用现有进程变量，缺失时从用户环境加载。

## 启动和停止

开发模式会分别打开 API、worker 和 Vite 日志窗口：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1
```

本地生产模式先独立构建并验证前端，必要时完整备份并在副本上试迁移，再切换构建、后台启动一个 API 和一个 worker；健康、readiness 与页面校验通过后，才把所启动进程的 PID 写入 `data/pids`：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\stop.ps1
```

服务只监听 `127.0.0.1:8765`。停止脚本逐个核对 PID 的命令行；它不会按进程名递归终止 Python、Node 或其他进程。

更新源码后，在服务停止时运行 `start.ps1` 会重新构建；显式 `-NoBuild` 才复用已验证的现有前端。已有服务运行时不要重复启动 API/Worker，切换后在浏览器按 Ctrl+F5。

## 网页中的执行复核

正式页面是同一实验的三栏工作台：左侧是真实历史/进行中实验卡片，中间通过“参数解析 / XML 预览 / 图片预览 / 实验进度”切换内容，右侧始终保留模型对话和内嵌参数修改卡片。点击历史实验只读取计划、任务和产物，不重跑。

两条分隔线可拖拽调整宽度，也可聚焦后用左右方向键调整（Shift 加大步长）；比例保存在本机浏览器，可“恢复默认布局”。窄屏通过“实验 / 工作区 / 对话”切换区域。已发送会话、提案及执行记录由 SQLite 持久保存，刷新后恢复；未发送草稿和未保存卡片仍只在页面内存。不会把对话或 Key 存入 localStorage。

中间的参数解析展示当前已接受版本；“手动调整参数”在右侧生成交互卡片。编辑模型卡片后，接受前会重新请求后端校验并使用新的提案 ID，不会沿用旧提案授权。新预览与历史 XML 均采用黑底语法高亮，历史 XML 保持原始文本、只读且不附带执行按钮。图片按真实日期任务、attempt 和已登记文件选择，不显示模拟产物；运行完成后进入“图片预览”读取自动绘图结果。

1. 对话或卡片生成修改提案，审核差异并接受。存在未接受的提案或未保存卡片修改时，不能检查旧计划或生成执行配置。
2. 点击“检查输入”，按缺失报告补齐文件。检查通过不会启动 GREAT。
3. 点击“生成 XML 并预览”：计划冻结，为每个日期生成独立 XML 快照，但不创建解算任务。冻结计划不能原地修改，需要修改时新建实验。
4. 在执行复核页逐日核对 XML 全文、实际程序绝对路径与 SHA256、模板、准确参数数组、输入报告、工作目录和输出目录。
5. 勾选“我已核对 XML、执行程序、输入文件和输出目录”，再点击“确认并开始解算”；也可保持本次 XML 预览打开，在右侧对话明确发送“确认并开始解算”或“XML 我看了没啥问题，现在你可以执行了”。两种确认复用相同校验与入队流程，成功后进入实验进度。计划冻结只禁止修改，不阻止确认执行。
6. 失败或中断任务的“重试”会先生成下一 attempt 的预览，再经同样确认；旧日志和结果不会覆盖。停止实验会取消等待任务，并通过 SQLite 请求让独立 Worker 终止当前进程。

未确认的预览有效期为 30 分钟；已确认的任务不会因排队超过 30 分钟失去授权。确认时及启动前检查冻结版本、程序/模板 SHA256、XML 字节、输入路径/大小/修改时间。任何不一致都会拒绝执行并提示重新复核。同一确认请求重发不会重复建任务。

对话确认只作用于当前实验中打开的执行预览，不会自动挑选其他 XML。普通咨询、“XML 没问题”、“可以执行吗”以及否定、条件、引用或同时修改计划的消息都不会触发执行；复杂表达可单独发送“确认并开始解算”。入队与对话回执原子保存，模型回复本身不构成执行授权。取消等待不会撤销已经提交的确认，可在实验进度核对任务状态。

生成 XML、准备重试或确认提交后，即使旧请求较晚返回，也不会将已经切换的实验/视图跳回去。已经明确授权的服务端任务不因浏览器切换而撤销，可从历史卡片重新进入监控。

预览原件保存在 `runs/<experiment>/previews/<preview_id>/<year_DOY>/config.xml`；真正执行时将同一份字节保存为 `tasks/<year_DOY>/attempts/<number>/config.xml`，不重新渲染。确认与逐 attempt 授权持久化到 SQLite，API 和 Worker 无需共享内存。

## Mock 与真实冒烟门禁

完整 mock 流程由自动化测试运行 Python mock solver，不需要 GREAT 二进制或研究数据：

```powershell
uv run pytest backend/tests/test_mock_e2e.py -v
```

真实案例固定为 2024 DOY 350、默认 15 站、静态双频浮点解、并发 1。首先只做预检；该命令生成隔离 XML 并报告参数和输入，但不会启动 GREAT：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\real-smoke.ps1 -PreflightOnly
```

旧 `-Execute` 和 CLI `smoke` 自动执行入口已关闭，避免绕过网页的实际 XML 复核与逐次确认。请从网页执行上述流程；不要把预检成功或上午已批准的 DOY 350 冒烟视为其他任务的授权。

## 位置分析和自动绘图

静态 DF Float 日任务成功产生所有测站的 FLT 后，worker 自动生成位置分析与图表，并登记到该 attempt 的“结果文件”列表。每个日期、每次执行只生成三张总览图：`enu.png`（全部测站的 E/N/U 全天序列）、`convergence.png`（各站收敛时间）和 `fix_rate.png`（各站固定率）。产物在 `attempts/0001/analysis`，同时保留每站 ENU CSV、`analysis.json` 和 `summary.csv`。分析失败时记录 `analysis_failed` 事件，保留解算状态与原始结果，便于单独重做后处理。

ENU 总览参考 `F:\GREAT_Python\plot\pppflt_new\ppp_enu_plot.py`：E/N/U 各自独立一行、叠加当前日期的全部测站，300 DPI，全天概览沿用 ±0.3 m 显示范围；CSV 和统计不裁剪离群值。汇总表保留 RMS_E/N/U、STD_E/N/U、FixRate(%) 和 Conv(min)；STD 使用总体标准差，固定率读取原生 FLT 的 AmbStatus（第 17 列），不使用其后的 Quality 列。缺少模糊度状态时固定率为空值，图中标为 `N/A`，与有效的 0% 区分。

“图片预览”仅提供“全局 ENU / 收敛时间 / 固定率”三项，依据后端标记的汇总图类型筛选，历史同名 CDF 和单站图片不进入预览。当前不再自动生成单站细图、首小时放大图或 CDF；已有历史产物保留归档。全部绘图脚本的进一步优化仍安排在知识库建立完成之后，见[后续优化待做清单](docs/backlog.md)。

2026-10-22 确认的临时口径：各站参考坐标取同一天静态双频浮点解的最后一个有效历元 ECEF 坐标；用 WGS84 椭球大地纬度转换 ENU。水平误差 `sqrt(E²+N²) <= 0.2 m`，垂直误差 `abs(U) <= 0.4 m`，连续同时满足 600 秒才算收敛。收敛时间记录该连续窗口的起点，起算时刻为计划中的 GPS 当日零点，缺失历元会重置窗口。未收敛用 JSON `null`、CSV 空值和图中的 `N/C` 表示。此参考只用于相对稳定性分析，不等同于外部真值精度。

顶层配置 `convergence_hold_seconds = 600` 控制自动后处理的持续时长；重做已有结果时可用 `--hold-seconds` 覆盖。下面的命令只读取已有 FLT，不启动 GREAT：

```powershell
uv run great-agent analyze --config config/local.toml --experiment-id 476c79c3-5452-4967-afc7-0bafad975436 --hold-seconds 600
```

分析 TF 或固定解时，通过 `--reference-experiment-id <DF_FLOAT_EXPERIMENT_UUID>` 指向成功的静态 DF Float 实验，按日期和测站匹配同一份参考；尚未选择参考时，worker 记录 `analysis_skipped`。参考路径、文件 SHA256、参考历元及 XYZ 会写入 JSON，避免把不同方案各自的末历元误当成共同真值。重复后处理会更新同一路径的结果登记，不增加重复文件记录。

XML 生成会清除输入节点注释后残留的旧路径，观测路径每站一行，`filter` 每个属性一行。已执行 attempt 中的 XML 保持原样；重新预检可在 `runs/preflight/2024_350/config.xml` 查看修正版。

## 数据安全和备份

- `observation_root` 与 `product_root` 只被读取；适配器不修改或复制源数据。
- 所有运行产物必须位于 `run_root`；越界结果会被拒绝并记录安全事件。
- 计划 revision、任务和 attempt 状态保存在 `data/great_agent.db`；运行快照、XML、日志和结果保存在 `runs`。
- 停止 API 与 worker 后，使用 `scripts/backup.ps1` 同时备份数据库、`runs`、配置与 `knowledge.root`，包括原 PDF 和人工核对公式缓存。数据库已包含切片和翻译；向量索引可重建。仅复制数据库和 `runs` 不能恢复知识原页。
- `config/local.toml`、`data`、`runs`、密钥、日志和 `.flt` 均被 Git 忽略。

## 故障排查

| 类别 | 表现 | 处理 |
|---|---|---|
| model | `model_proposal_error`、超时或 schema 错误 | 检查模型 URL、名称、环境变量和网络；仍可用卡片手工编辑。 |
| preflight | 输入列表出现 missing/empty | 按报告的日期、测站和路径补齐只读数据；不要绕过冻结门禁。 |
| XML | `XmlValidationError` | 检查模板节点是否完整以及配置的模板身份；生成 XML 位于运行目录。 |
| launch | `launch_error` | 检查 GREAT_PVT 路径、执行权限和杀毒软件拦截。 |
| solver | `solver_exit_error` | 查看对应 attempt 的 `solver.log`、退出码和冻结计划。 |
| output | `missing_output_error` | 检查 15 个测站结果命名、非空性以及模板输出根；已有日志会保留。 |
| interrupted | 重启后任务标记 `interrupted` | 确认旧 PID 不再活动后，从仪表盘重试；不要手工改 SQLite 状态。 |

## 验证

统一隔离门禁不迁移正式数据库、不替换正式前端、不调用外部模型：

```powershell
.\scripts\verify.ps1 -Report output\verification\report.json
```

门禁包含 `backend/tests`、`scripts/tests`、前端 test/typecheck/lint/build，失败即停止。报告保留真实退出码、耗时、日志、工作区源码与锁文件 SHA256、Git 身份以及隔离构建指纹；如果检查期间源码变化，要求重新运行。构建在独立临时目录中完成并清理。Windows CI 运行相同入口，Python 测试限制为 loopback/mock 连接。

### 第一批可靠性更新（2026-11-15）

新数据库版本为 **0009**，新增 `analysis_jobs` 和 `worker_heartbeats`。XML 在 staging 全部生成并校验后，短事务重新核对 revision/待处理提案/任务状态，再一起冻结和登记预览。生成失败不会锁死可编辑计划；进程硬退出留下的无登记目录没有执行授权。

本机正式数据库和页面已在2026-11-15经用户授权完成切换，历史数据校验不变、Worker与知识执行器就绪；见[正式切换记录](docs/validation/2026-11-15-batch1-production-cutover.md)。

GREAT 正常退出且原始产物检查通过后，解算成功、原始文件和分析作业在同一事务保存。生产 Worker 持续补领空槽，独立消费分析队列；分析失败或重启不把解算成功改成中断。网页在选中的成功 attempt 分析失败时提供“重试后处理”，只重试相同分析作业，不启动 GREAT。历史无分析作业的结果仍可通过已有 `analyze` CLI 重分析。

分析线程关闭时会等待实际写入结束，即使收到重复取消；短暂数据库错误只重试完成回执。静态 DF Float 保持现有自动三图；其他场景仍需明确选择参考，未在本批改变数值口径。

`GET /api/v1/health` 表示 API 存活；`GET /api/v1/readiness` 检查 schema 和最近 10 秒的 Solver Worker 心跳，未就绪返回 503。API-only 启动使用 `/api/v1/readiness?require_worker=false`。`/api/v1/diagnostics` 提供恢复占槽数、分析队列与知识执行器状态，不启动任务。

正式升级前先停止 API/Worker、完整备份，再迁移；本批代码验收只操作临时数据。以下是操作入口示例，备份与恢复目标必须按实际环境选择：

```powershell
.\scripts\backup.ps1 -Operation backup -Config config\local.toml -Destination F:\GREAT_Backups\2026-11-15
.\scripts\backup.ps1 -Operation verify -Backup F:\GREAT_Backups\2026-11-15
uv run great-agent migrate --config config/local.toml
# 恢复演练：只允许新的空目录，不覆盖现有项目。
.\scripts\backup.ps1 -Operation restore -Backup F:\GREAT_Backups\2026-11-15 -Destination F:\GREAT_Recovered\2026-11-15
```

恢复保留 `original-config.toml`，生成指向恢复目录的 `config.toml`，重定位历史结果和日志路径；不会启动服务。GREAT 可执行文件、只读 GNSS 产品、外部模型与代码环境须另外保留。旧执行预览的授权快照不改写，恢复后执行须重新复核；已有日志与引用保持历史身份。损坏清单、链接/重解析点、路径重叠、活动执行器和非空恢复目标均拒绝，失败残留目录保留以供核查。

### Worker 恢复与实验监控更新（2026-11-12）

此版本新增进程身份、恢复状态与实验事件表，数据库迁移为 `0005`。已有服务应在空闲时段停止 API/Worker，同点备份数据库和 `runs`，再针对实际配置运行：

```powershell
uv run great-agent migrate --config config/local.toml
```

该命令使用 `config/local.toml` 的 `database_path`，另存数据库快照，并在升级前核验未记录 Alembic 版本的历史 bootstrap 表结构。未知结构或同名但不兼容的表/字段会被拒绝，不会盲目写入版本号。原有科研表、审批与 attempt 产物保留。正在持锁的 Solver Worker 会阻止迁移。

构建前端后再启动 API/Worker。代码验证期间的前端产物在 `tmp/worker-monitor-dist`，没有替换正式的 `frontend/dist` 或重启服务。

每份数据库仅允许一个 Solver Worker 实例，仍可按原有配额同时运行多个 GREAT。Worker 重启后持续观察遗留进程；确认退出才收敛任务状态并释放槽位。身份不明会显示原因并保留槽位，可在运行页重新检测；停止遗留进程须核对程序、参数和创建时间。恢复不自动重跑，重试继续需要新 XML 复核与明确确认。

工作台的进度和图片页共享一条实验监控流，断线退避并从游标续传。分析完成、失败或跳过会独立显示；图片晚到或重算会刷新对应 attempt，手动选择的历史 attempt 保持不变。

还原数据库备份后，在 API/Worker 停止时旋转事件日志代际，再重新启动和刷新页面：

```powershell
uv run great-agent monitor-reset --config config/local.toml
```

详情见[实施与验证记录](docs/validation/2026-11-12-worker-recovery-and-monitor.md)。

## 文献知识库与带引用对话

顶栏“文献知识库”是独立工作区，包含“文献对话 / 文档与解析”。可建库、上传PDF/Markdown/TXT、查看入库状态与切片、停用和重解析；在所选文献范围与服务器配置的模型对话，点击回答引用核对固定版本、实际摘录及PDF原页高亮。工作区切换保留实验状态与对话草稿。文件接收后返回持久化任务；成功或带质量提示的版本发布后才进入检索。重解析失败保留此前版本。

数据库沿用配置中的`database_path`，新增八张`knowledge_*`关系表和SQLite FTS5；默认原件目录为`<project_root>/data/knowledge`，按SHA256保存不可覆盖的原件。同库同内容去重，同名不同内容分别登记。知识执行器由API生命周期持有，独立于GREAT Worker。

正式切换时先停止API/Worker，备份数据库与runs，再执行：

```powershell
uv sync
uv run great-agent migrate --config config/local.toml
```

前端仍按项目原有构建/启动流程更新。本批开发验收构建位于`tmp/knowledge-chat-dist`，不会自动替换正式入口。新迁移`0007`新增会话范围、检索轨迹、引用与索引版本五表；升级到head后重启API。不要手工创建空SQLite或执行零散DDL。

`config/app.example.toml`的`[knowledge]`可配置存储目录、50MiB上传上限、300页上限和解析超时。默认解析采用本地文字提取，不需要云模型；复杂双栏、表格、公式和图形数值会明确提示回原页核对。Docling适配器只接受显式本地模型目录，依赖/模型未准备时任务明确失败，不自动下载或静默回退。

检索保留FTS5与GNSS术语扩展，可显式启用本地多语E5 ONNX和Qdrant进行混合检索；模型资源须单独准备，索引未就绪、资源或内存不足时明确降级为关键词检索。`[knowledge]`配置项见示例文件。GLM回答使用现有`[model]`与服务端`GREAT_AGENT_MODEL_API_KEY`，仅接收问题、允许的历史与选取证据，不上传原PDF。知识会话只有检索和读取证据权限。

实施和验收、路径与使用步骤见[引用对话集成记录](docs/validation/2026-11-13-knowledge-chat-integration.md)。

案例取舍见[GitHub源码核查](docs/research/2026-11-12-knowledge-github-cases.md)，技术设计见[数据库与入库设计](docs/superpowers/specs/2026-11-12-knowledge-ingestion-design.md)。

## AgentScope / Ant Design 重构进度

后续需求统一记录在[后续优化待做清单](docs/backlog.md)。当前绘图先精简为三张总览图，全部脚本的进一步整理安排在知识库建立完成之后。

首批P2已接入 **AgentScope2.0.11原生Agent/Toolkit、全局规范与按需技能、统一咨询/提案和SQLite持久会话**。首页可直接咨询GNSS，冻结实验仍可只读聊天；普通咨询不会产生提案或替换pending。该批历史验收为后端340、前端109、浏览器21项，另有两轮真实GLM合成工具闭环；详见 [P2统一对话首批验收](docs/validation/2026-11-02-p2-conversation-foundation.md)。后续独立知识工作区已支持文献入库、可选混合检索与带引用对话；实验主会话的文献检索与参数证据关联尚待后续批次。

P0 的依赖与 SDK 探针位于 `scripts/compatibility`，保留为独立兼容验证依据。正式产品已接入精确锁定的 **AgentScope 2.0.11 模型 SDK** 和 **antd 6.6.8**；最新验证结果见 [主路径切换记录](docs/validation/2026-11-02-validated-cutover.md)。P0 的原始版本、离线矩阵、上游声明问题见 [历史验证记录](docs/validation/2026-11-02-p0-compatibility.md)。

2026-11-02 P0 补测：从用户变量加载 `GREAT_AGENT_MODEL_API_KEY` 后，配置模型 `glm-4.7` 的两种 token 字段工具流与 JSON-object 基线三项通过，共 3 次真实请求、live 阶段 11.234 秒。本次切换另经新 provider 完成一次受限真实合成调用，11.39 秒返回有效且与输入一致的完整候选；当前环境的 HTTP/SOCKS 混合代理由正式 `httpx[socks]` 依赖支持。这些结果都不等同于完整 Agent 或真实业务执行验收。

旧提案API仍装配 `AgentScopeModelProvider`，通过SDK返回完整JSON候选并维持单次物理发送；旧provider名仅作兼容导出。新对话主路径使用 `AgentScopeConversationEngine`，按回合限制模型请求、工具次数和deadline，并通过确定性服务形成可选提案，不嵌套旧模型生成。聊天token SSE仍未接入，已提供的是持久回合生命周期事件。

工作台的计划编辑、提案审批、聊天输入、执行复核、任务监控和产物控件采用 antd，入口使用中文 ConfigProvider 与统一 tokens。分栏继续保留原键盘调整和宽度存储实现，XML 保持精确纯文本；可选 Ant Design X 暂未加入产品依赖。安装新版本须更新锁定依赖、重建前端并重启 API 进程；步骤见切换记录。[SDK/Ant Design切换时架构快照](docs/architecture/2026-11-02-technical-architecture.html) 可查看当时状态与源码依据；原生Agent和会话的最新状态以P2验收为准。

P1已将登记的计划提案保存到SQLite；本轮P2另持久保存全局/各实验默认会话、消息与run状态，刷新可恢复正文和当前提案状态。未发送草稿及未保存手工卡片仍只在浏览器内存。候选、接受和冻结共用日期、并发策略及profile校验。目前仅注册真实默认配置 `local_default`、`great_local`、`pppflt_base`，未知ID会被拒绝。

每份提案绑定生成时的 revision UUID。编辑已有提案必须继承原基线，计划被其他页面更新后不能通过更换数字版本继续接受旧候选。存在待处理提案时，服务端也会以 `409 pending_proposal` 拒绝冻结；页面会恢复待确认卡片，先接受或放弃后才能生成 XML。

Worker 在 SQLite 原子事务中同时遵守全局 `max_concurrency` 和冻结计划的 `concurrency`。达到某实验上限时可领取其他实验的任务；终态释放配额。该变更不授予执行权限，解算和重试仍要求原有 XML 复核与逐次确认。

P1迁移为 `0003_plan_proposals.py`，P2另提供 `0004_conversations.py`。部署前备份数据库和runs，再按现有迁移状态升级；本机本轮已在备份后通过既有bootstrap新增会话表，9张原科研表内容保持不变。迁移升降级和已有bootstrap表兼容在临时数据库验证。升级前端后刷新页面；旧手工提案请求缺少 `base_revision_id` 会返回422，编辑已登记候选还须传 `source_proposal_id`。

P0 真实供应商验证需在已配置 `GREAT_AGENT_MODEL_API_KEY` 的进程中显式运行，不要把密钥提交到仓库或聊天：

```powershell
$env:GREAT_AGENT_MODEL_API_KEY = [Environment]::GetEnvironmentVariable('GREAT_AGENT_MODEL_API_KEY', 'User')
& .\tmp\p0-python\Scripts\python.exe .\scripts\compatibility\probe.py --live --config .\config\local.toml --output .\tmp\p0-live-user-key.json
```

若独立环境尚未建立，先按 `scripts/compatibility/README.md` 安装锁定依赖。探针只发送合成数据，不执行工具或 GREAT；缺少密钥时真实请求为 0，命令以非零状态报告尚未验证。P0/P1 总验收见 [实施记录](docs/validation/2026-11-02-p0-p1.md)。
