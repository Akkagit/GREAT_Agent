# GREAT Agent

GREAT Agent 是一个仅绑定本机的 Windows Web Agent：把自然语言或表单修改转换成可审阅、可冻结的 PPP-FLT 计划，按日期展开为可恢复任务，并在隔离输出目录中运行 GREAT_PVT。输入 GNSS 数据始终只读；生成的 XML、日志和 `.flt` 只写入配置的 `run_root`。

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

## 启动和停止

开发模式会分别打开 API、worker 和 Vite 日志窗口：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev.ps1
```

本地生产模式会在缺少构建产物时构建前端，后台启动一个 API 和一个 worker，只把所启动进程的 PID 写入 `data/pids`：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\stop.ps1
```

服务只监听 `127.0.0.1:8765`。停止脚本逐个核对 PID 的命令行；它不会按进程名递归终止 Python、Node 或其他进程。

## Mock 与真实冒烟门禁

完整 mock 流程由自动化测试运行 Python mock solver，不需要 GREAT 二进制或研究数据：

```powershell
uv run pytest backend/tests/test_mock_e2e.py -v
```

真实案例固定为 2024 DOY 350、默认 15 站、静态双频浮点解、并发 1。首先只做预检；该命令生成隔离 XML 并报告参数和输入，但不会启动 GREAT：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\real-smoke.ps1 -PreflightOnly
```

只有人工检查冻结计划、XML 路径、参数数组、输入报告和输出目录后，才可显式运行 `-Execute`。不要把预检成功视为执行授权。

## 数据安全和备份

- `observation_root` 与 `product_root` 只被读取；适配器不修改或复制源数据。
- 所有运行产物必须位于 `run_root`；越界结果会被拒绝并记录安全事件。
- 计划 revision、任务和 attempt 状态保存在 `data/great_agent.db`；运行快照、XML、日志和结果保存在 `runs`。
- 停止 API 与 worker 后，定期同时备份 `data/great_agent.db` 和 `runs`。恢复时保持二者来自同一备份时点。
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

```powershell
uv run alembic upgrade head
uv run pytest backend/tests -v
npm --prefix frontend test -- --run
npm --prefix frontend run typecheck
npm --prefix frontend run build
```
