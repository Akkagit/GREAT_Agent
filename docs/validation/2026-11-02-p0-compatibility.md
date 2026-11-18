# 2026-11-02 P0 隔离兼容性验证

> 本文是隔离探针的历史验证记录，保留原始结果与范围。后续产品 SDK / antd 接入见[主路径切换验收](2026-11-02-validated-cutover.md)，两者不等同于完整 Agent 运行时验收。

结论：**P0 隔离验证通过。** 安装、离线 SDK 合同、React19/antd6（含可选 X2）隔离构建及配置模型 `glm-4.7` 的三项真实合成检查均通过。最新 live 共 **3** 次请求，请求阶段 **11.234 秒**，命令退出 0。产品主路径仍保持现状，尚未进入 P2/P3；本结论不等同于产品集成、严格 JSON Schema 或复杂 Agent 工作流已验收。首次缺密钥的零请求结果保留在下文。

## 范围与隔离

- 只新增 `scripts/compatibility/`、探针预算测试和本文；没有修改产品 `pyproject.toml`、`uv.lock` 或 frontend 依赖。
- Python 环境、uv 缓存、Node 安装、构建、JSON 和诊断输出均在 `tmp/p0-*`；两个 fixture 分别保存锁文件。
- 不启动 API/worker，不导入生产 provider，不打开数据库，不调用 GREAT，不访问科研输入。live 仅调用 `AppSettings.load` 读取模型配置，并读取当前进程环境密钥。
- live 预设 3 个合成检查，物理 transport 总预算最多 8 请求，每个输出上限 <=512，整个 live 请求阶段共享 180 秒 deadline；不自动重试、不执行模型工具。无密钥时不会创建网络 transport。

## 实际包与环境

| 项目 | 观察结果 |
|---|---|
| Python | 独立环境 CPython 3.11.9；系统默认 Python 3.13.9 未用于 SDK fixture |
| uv / Node / npm | 0.11.4 / 24.19.0 / 11.17.0 |
| PyPI `agentscope` | 公共 JSON 索引 `latest=2.0.11`、`2.0.11 in releases=true`；实际安装 **2.0.11** |
| Python 依赖 | 锁定 98 项（含 fixture 项）；实际安装 97 个包；OpenAI 3.42.1、httpx 0.28.1、Pydantic 2.14.2 |
| React / ReactDOM | 两个 fixture 均固定 **19.2.8** |
| Ant Design | npm 查询与安装 **6.6.8**；peer `react/react-dom >=18.0.0` |
| Ant Design X | npm 查询与安装 **2.10.0**；peer `antd ^6.1.1`、`react/react-dom >=18.0.0` |
| 类型 / 构建 | TypeScript 6.0.2、Vite 8.3.0、@types/react 19.2.18、@types/react-dom 19.2.7 |

版本证据来自实际公共 [PyPI 包元数据](https://pypi.org/pypi/agentscope/2.0.11/json)、uv 安装输出与锁文件，以及 `npm view`、`npm ls`。本轮没有遇到目标版本不存在；前期版本假设经实际索引和安装核实成立。fixture 不直接复用产品锁文件中的补丁版本，也不据此改动产品锁文件。

## SDK 离线矩阵

运行 `tmp/p0-python/Scripts/python.exe scripts/compatibility/probe.py --output tmp/p0-offline.json`：**14/14 pass，退出 0，最终一次 3.453 秒**。以下请求数均为 MockTransport 内存请求，真实 HTTP 请求为 0。

| 检查 | 结果与证据 |
|---|---|
| tools / forced tool_choice | 单次请求带正确工具 schema 和具名 `tool_choice`；设置 `parallel_tool_calls=false` |
| 参数分片 / 结束 / usage | 两片 JSON 参数被 SDK 合并，最终 JSON 为合成整数；终止 `is_last=true`；usage-only 尾片保留输入 11、输出 5 |
| usage 缺失 | 无 usage 尾片仍正常终止，`usage=None`；不把缺失用量当作 0 |
| SDK max_tokens 参数 | `Parameters(max_tokens=512)` 实际线协议字段是 `max_completion_tokens=512` |
| 供应商 max_tokens | 使用公开接口 `Parameters(max_tokens=None)` + `extra_body={max_tokens:512,...}` 可发 `max_tokens`，不夹带 `max_completion_tokens`；只证明请求形状 |
| thinking | `extra_body.thinking={type:disabled}` 如实传递；不是通用 `thinking_enable` 与供应商 thinking 的等价性声明 |
| 400 / 401 / 422 / 429 / 500 / 503 | 各自恰好 1 次 mock 请求；分别抛出相应 OpenAI HTTP 异常；SDK 与 OpenAI 双层 `max_retries=0` |
| 连接 timeout | 注入 ConnectTimeout，得到 APITimeoutError |
| 总 deadline | 0.05 秒超时取消慢流；流 `aclose` 已完成 |
| 取消 | 合成取消关闭底层流；SDK 可吞掉 CancelledError，返回 interrupted 终止结果，调用者须检查终止原因 |
| 不支持 schema 的默认 fallback | 全部响应 400 时，即使 `max_retries=0`，结构化接口仍会调用 **3 次**策略 |
| 显式结构化 tool_choice | 具名显式 `tool_choice` 绕过策略阶梯，400 只发 **1 次** |

真实安装后发现两处容易沿用旧示例出错的 API：`Msg.content` 必须是内容块列表（或使用 `UserMsg` 工厂），不能直接给 `Msg` 一个字符串；`ToolCallBlock.input` 是原始 JSON 字符串，须在完整结束后解析。初始探针因采用错误消息构造而失败，按安装包源码修正后上述矩阵通过；没有更改框架源码或通过缩减断言隐藏失败。

这些证据验证模型层公开接口和传输/解析行为，不覆盖 Agent 循环、AgentState 持久化、权限中间件或产品事件转换。后续接入仍需各自合同测试。

## 预算安全测试

`backend/tests/test_compatibility_probe.py` 对应用自有探针预算约束做行为验证：第 9 次物理请求被拦截；没有输出上限、上限 513、两个上限中一个超限均不触网；已过 deadline 不触网且关闭 transport。

TDD 证据：先运行时 **5 failed**（探针文件尚不存在），实现后 **5 passed**（0.10 秒）；最终复核 **5 passed**（0.14 秒），两份 Node 主 typecheck 均退出 0，可选 X 严格声明审计仍退出 2。没有为第三方 SDK 实现编写镜像单元测试。

Windows 默认沙箱下，asyncio 在 `socket.socketpair → accept` 初始化本地 self-pipe 时卡住；faulthandler 栈确认该原因。获准本地 loopback 权限后测试立即通过。另一次 cold import 卡顿的栈在 OpenAI/Rich 模块文件加载，预热后探针总耗时降至数秒。两者都没有被归因为供应商失败。诊断进程已结束，未遗留模型调用。

## 首次显式 live 结果

密钥配置前，运行 `tmp/p0-python/Scripts/python.exe scripts/compatibility/probe.py --live --config config/local.toml --output tmp/p0-live.json`：

```json
{
  "package_status": "installed",
  "offline": "14/14 pass",
  "live": {
    "status": "skipped",
    "reason": "GREAT_AGENT_MODEL_API_KEY absent from process environment",
    "requests": 0
  },
  "status": "fail",
  "elapsed_seconds": 1.593
}
```

该命令退出 **1**，表示所请求的 live 验证没有完成；不是离线 SDK 失败，也不能写作供应商通过/失败。仅报告密钥是否存在，不打印或保存其值，不搜索其他凭据。配置文件存在且 AppSettings 加载成功。没有传输科研文件、原始模型响应或用户计划。

## 用户变量配置后的真实补测

用户新建 `GREAT_AGENT_MODEL_API_KEY` 后，核实其位于 Windows **User** 作用域；原工具进程未自动继承新值。本次只将该用户变量注入探针子进程，结束后清除本次 shell 中的副本，没有改写用户变量、其他模型密钥或项目配置。

执行同一探针，结果保存到 `tmp/p0-live-user-key.json`；脱敏持久摘要及原件 SHA256 见 [live 结果](/F:/GREAT_Agent/docs/validation/2026-11-02-p0-live-result.json)。请求配置模型为 `glm-4.7`，AgentScope **2.0.11**；离线 **14/14** 仍通过，live **3/3** 通过，命令退出 **0**。live 请求阶段 **11.234 秒**，整次命令 **25.844 秒**（包含离线及加载）。

| 检查 | 本次结果 |
|---|---|
| SDK 原生 `max_completion_tokens=512` + 流式具名工具调用 | pass；1 次请求；工具名与完整参数 JSON 符合合成预期 |
| 公开 `extra_body.max_tokens=512` + 同一流式工具调用 | pass；1 次请求；不夹带原生 token 字段，合成结果正确 |
| 非流式 `response_format=json_object` 基线 | pass；1 次请求；正文可解析为预期的合成 JSON 对象 |

三项均观察到 `is_last=true`、`interrupted=false`、`usage_present=true`。live 的通过条件检查非 interrupted 与合成结果；结束信号、usage 和真实网络分片未作为独立压力测试断言。严格 JSON Schema、工具 call ID、复杂循环、AgentState、产品事件与科研操作仍需后续专门验证。上述两种 token 字段路径分别通过，不把其中一条的结果外推到另一条。

实际总请求 **3**，没有重试或执行模型返回的工具；输出参数上限 **512**，使用共享 **180 秒** live deadline。此预算不是人民币费用上限。未调用 GREAT、读写科研数据、升级生产数据库，未保存密钥或原始模型响应。

## Node 独立 fixture

| Fixture | 安装/peer | 主 typecheck | Vite build | 附加严格声明审计 |
|---|---|---|---|---|
| React19 + antd6 | pass；安装 88 包，无 peer 冲突 | pass | pass，824.22 KB JS / gzip 260.84 KB | fail：rc image/picker 的 TS2430 |
| React19 + antd6 + X2 | pass；安装 227 包，无 peer 冲突 | pass | pass，927.45 KB JS / gzip 294.37 KB | fail：上述 TS2430，另缺 syntax-highlighter 声明与 type-fest |

主检查采用产品的 `moduleResolution=bundler`、`jsx=react-jsx` 和 **`skipLibCheck=true`**，fixture 另外启用 `strict=true`。附加 `typecheck:strict-declarations` 强制 `skipLibCheck=false`，独立记录第三方声明问题；没有修改第三方源码、手写兜底声明或把审计失败擦除。

具体上游声明问题：`@rc-component/image@1.10.0` 的 `GroupPreviewConfig.imageRender` 与父接口 `current` 可选性不同；`@rc-component/picker@1.12.2` 的 `defaultValue` null 可选性不同；可选 X 引入 `react-syntax-highlighter` 缺对应类型包、mermaid 缺 `type-fest` 类型。若未来要求检查全部上游声明，应另行锁定修复方案。

两个 build 均有模块级 `use client` 指令与 >500 KB chunk 警告；这属于浏览器 bundle 的后续拆包/体验评估项。fixture 展示 Table/Card/Button，X 额外展示 Bubble/Sender；没有验证产品三栏、XML 审阅、取消、浏览器交互或视觉布局。首次 +X 冷构建耗时约 2 分 7 秒，预热后 antd-only 构建 359 毫秒，不能把差异解释成稳定性能基准。

## 可复现命令与证据文件

详尽操作说明在 `scripts/compatibility/README.md`。本轮主要命令：

```powershell
$env:UV_CACHE_DIR = "$PWD/tmp/p0-uv-cache"
$env:UV_PROJECT_ENVIRONMENT = "$PWD/tmp/p0-python"
uv lock --project scripts/compatibility/python --python .venv/Scripts/python.exe --index-url https://pypi.org/simple
uv sync --project scripts/compatibility/python --python .venv/Scripts/python.exe --locked --index-url https://pypi.org/simple
& .venv/Scripts/python.exe -m pytest backend/tests/test_compatibility_probe.py -q
& tmp/p0-python/Scripts/python.exe scripts/compatibility/probe.py --output tmp/p0-offline.json
& tmp/p0-python/Scripts/python.exe scripts/compatibility/probe.py --live --config config/local.toml --output tmp/p0-live.json
$env:GREAT_AGENT_MODEL_API_KEY = [Environment]::GetEnvironmentVariable('GREAT_AGENT_MODEL_API_KEY', 'User')
& tmp/p0-python/Scripts/python.exe scripts/compatibility/probe.py --live --config config/local.toml --output tmp/p0-live-user-key.json
npm view antd version peerDependencies --json
npm view '@ant-design/x' version peerDependencies --json
npm ls --prefix tmp/p0-node react react-dom antd '@ant-design/x' --depth=0
npm run --prefix tmp/p0-node typecheck
npm run --prefix tmp/p0-node build
npm run --prefix tmp/p0-node-antd-only typecheck
npm run --prefix tmp/p0-node-antd-only build
npm run --prefix tmp/p0-node typecheck:strict-declarations
npm run --prefix tmp/p0-node-antd-only typecheck:strict-declarations
```

实际临时证据：`tmp/p0-offline.json`、`tmp/p0-live.json`（首次 skipped）、`tmp/p0-live-user-key.json`（最新 pass）、`tmp/p0-node-strict.log`、`tmp/p0-node-antd-only-strict.log`、`tmp/p0-node-antd-only-build.log`。tmp 文件可清理，不是产品状态；复现依据是版本锁文件、探针源码、文中命令与明确的结果边界。

切换结论：AgentScope 2.0.11 的安装、SDK mock 合同及本次 `glm-4.7` 三条合成 live 路径通过，原密钥关口已关闭。上游第三方严格声明审计问题仍单独保留；P0 的通过支持后续 P2 技术接入评审，不自动切换产品或开始 P2/P3。
