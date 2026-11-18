# 已验证 SDK 与 Ant Design 主路径切换验收

日期：2026-11-02。对应实施计划：`docs/superpowers/plans/2026-11-02-validated-sdk-antd-cutover.md`。

**状态：代码主路径替换与验收完成。** 正式模型调用使用 AgentScope 2.0.11，核心界面使用 antd 6.6.8；不是生产部署或完整 Agent 运行时完成声明。

## 本次替换范围

- 后端：正式装配改用 `AgentScopeModelProvider` 和固定 AgentScope 2.0.11 `OpenAIChatModel`，保留现有 ModelProvider 和领域服务合同。旧 provider 名只作兼容导出。
- 前端：正式依赖采用 antd 6.6.8，ConfigProvider 使用中文 locale 和统一 tokens；迁移计划提案、聊天、执行复核、任务监控、历史产物及工作台操作控件。
- 保留：现有分栏行为核心及其键盘/比例存储/窄屏语义、XML 精确纯文本着色、提案和执行状态所有权。
- 后续：完整 Agent/Toolkit/AgentState/AgentRun、会话持久化、聊天 token SSE、检索、多 Agent 仍未由本次切换交付。

## 接入边界

P0 的两项成果原为隔离兼容验证，不是已经部署的预发服务。本轮把 SDK 模型调用和 antd 核心组件切进产品；不把静态 fixture 成功等同于完整产品接入。

`OpenAIChatModel` 使用公开 `response_format` 配置，不调用默认结构化多策略接口。SDK 和 OpenAI 客户端重试均为 0，传输边界只允许一次物理发送。保持原 GLM `max_tokens` 和 thinking 配置；候选正文从 SDK 文本块得到，再经 JSON、Pydantic 和领域校验。

AgentScope 2.0.11 的非流式模型调用会将部分取消转换为 interrupted 终态，因此适配器恢复整次 deadline 和外部取消语义。该版本还丢失 upstream finish_reason/refusal；transport guard 检查这些元数据，以拒绝截断或拒绝响应，避免伪造正常候选。错误和 SDK 日志不得输出密钥、请求内容或供应商正文。

antd Splitter 6.6.8 没有直接传入分隔控件键盘/ARIA 属性的公开入口。本轮保留可键盘调整的分栏核心，替换其操作控件，避免通过内部 DOM 补丁接入。可选 Ant Design X 仅保留隔离验证，不加入产品依赖；原位提案卡、取消与只读交互由 antd 核心组件承载。

## 验证记录

| 检查 | 结果 |
|---|---|
| 新 SDK 合同 RED | 9 failed / 16 passed，32.99 秒；复现旧装配、截断/拒绝接受及正文泄露 |
| SDK 针对回归 | 真实SDK、单请求、终态、取消/清理、代理与日志合同均纳入最终全套通过；HTTP/NO_PROXY/SOCKS专项5/5、deadline焦点复验1/1 |
| 后端完整 pytest | 最终 exit 0，276 passed，196.65 秒；含 SOCKS 依赖和最后的 deadline 测试准备修正 |
| 新产品 provider 真实合成调用 | exit 0；GLM-4.7 / AgentScope2.0.11，1 次调用，11.39 秒；schema有效、候选与合成输入完全一致 |
| 前端完整 Vitest | exit 0，7 files / 66 passed，109.67 秒，无未处理错误 |
| typecheck / lint / build | 最终三项均 exit 0；1513 modules；index-dLZ5DiEI.js，1026.18KB / gzip323.62KB |
| 构建产物浏览器合成流程 | 最终构建 exit 0，16/16 检查通过；含明确启动与停止 |
| 独立评审及修复复验 | 后端、前端、全范围及SOCKS收尾评审通过；确认的代理回归已关闭，无遗留承重P1/P2 |
| 架构图与源码状态一致性 | 21 模块，37 个源码/验收文件及行锚点存在；SVG解析、文字边界、JS语法与视觉复核通过 |

后端在补齐 SOCKS 依赖前的完整回归为 274 passed / 141.73 秒。随后实际 provider 合成冒烟发现：现有环境同时使用 HTTP 与 SOCKS5 代理，缺少 `socksio` 时默认 HTTPX 客户端会在发请求前构造失败。零网络 `httpx.AsyncClient()` 构造可稳定复现 `ImportError`，并非候选 schema 或真实数据问题。本轮补充官方 `httpx[socks]` 依赖及混合代理回归，锁文件仅新增 socksio 1.0.0，原有版本无变化；最终 276 项完整重跑通过，121 个已装包完整性检查通过。[HTTPX 官方 SOCKS 说明](https://www.python-httpx.org/advanced/proxies/#socks)。

补齐后的首轮为 275 passed / 1 failed，唯一失败来自新 SDK 超时测试的 20ms 场景预算：客户端准备阶段已经正确超时、请求数为 0，测试却要求已经进入 transport。将该测试的模型请求预算设为 1 秒、外层保护设为 3 秒，保留 ModelTimeout、请求数 1、上游取消和关闭断言，以确实覆盖 SDK 吞取消后的清理；没有改变产品默认 deadline 或 pytest 全局超时。该场景单独复验 1 passed / 12.86 秒，随后 276 项完整重跑退出 0。

补齐 SOCKS 后，通过新产品 `AgentScopeModelProvider` 的真实合成调用 **退出 0**：一次调用、最多一个物理请求、512 输出 token 上限、45 秒整次 deadline，实际 11.39 秒。返回完整 `ModelPlanProposal`，schema有效且候选与输入合成计划完全相同。保持原有 HTTP/HTTPS/ALL_PROXY 环境，无代理绕过；仅在子进程读取已有用户密钥，没有生产数据库或计算程序操作。[脱敏结果与先前构造期失败证据](2026-11-02-cutover-provider.json)。这项调用验证模型 provider 主路径，不代表完整 Agent/工具循环或真实科研执行已验收。

浏览器验收使用独立的本地内存 API fixture，仅验证已构建前端的真实控件交互。它不导入后端，不访问生产数据库，也不启动模型或外部计算程序。最终 16 项检查实际通过：编辑 Select、待确认门禁、显式接受、窄屏草稿保留且无页面横溢出、XML 全文纯文本、默认确认禁用、切日清空确认、只在明确确认后提交一次；停止弹窗具有正确可访问名称，打开不停止，二次明确确认才提交一次停止请求；没有未捕获浏览器异常。[结构化结果](2026-11-02-cutover-browser.json)、[桌面](assets/2026-11-02-cutover-workspace.png)、[窄屏](assets/2026-11-02-cutover-mobile.png)、[执行复核](assets/2026-11-02-cutover-review.png)。

先前两次浏览器脚本分别遇到未滚动按钮、CDP DOM 节点未序列化为布尔值的验收脚本问题，修正后完整重跑得到 13/13；随后针对最终构建增加停止弹窗检查，最终采纳 16/16 通过的退出 0 结果。

测试环境应使用工作区内 TEMP/TMP；只有实际完成并确认退出码的运行记为通过。前端 UI 库模块较多，必要时使用 Vitest 官方的依赖预打包配置改善 Windows 文件读取成本，不修改业务测试断言或关闭检查。[Vitest 依赖优化](https://vitest.dev/config/deps)、[性能说明](https://vitest.dev/guide/profiling-test-performance)。

前端最终保留真实 antd 依赖优化，Windows 使用单个 VM thread worker 保持文件隔离，其他平台保留 forks；没有提高测试超时或屏蔽业务用例。补充 jsdom 缺少的 matchMedia/ResizeObserver/伪元素测量环境支持。上游测试环境固定 ID 与停止弹窗标题碰撞，通过给 Select 设置明确唯一 ID/标签解决，不改组件私有 DOM。新增数字清空门禁和移动草稿保留回归。正式构建的体积提示仍保留；没有修改原有 React、Vite、Vitest、TypeScript 的锁定版本。

最终后端命令在工作区 TEMP/TMP 中执行，仅关闭无关 pytest 自动插件发现并显式加载现有 asyncio 插件，所有业务测试保持：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
.\.venv\Scripts\python.exe -m pytest -p pytest_asyncio.plugin backend -q --tb=line --basetemp tmp/pytest-sdk-backend-socks-final
```

本轮后端完整日志为 `tmp/sdk-backend-socks-final.log`；前后端实施报告、专项/全范围评审与基线位于 `.codex_work/sdk-antd-cutover-20261102/`。验收期间的源码定位慢、导入诊断访问违规及早期失败结果均保留，没有用未完成运行代替最终通过。

## 使用方式

开发工作区已完成依赖安装与前端构建。其他使用该版本的环境按以下步骤更新；API 进程需要重启以加载新装配：

```powershell
uv sync --locked --link-mode=copy
npm --prefix frontend ci
npm --prefix frontend run build
```

密钥仍由 API 进程从 `GREAT_AGENT_MODEL_API_KEY` 获取；Windows 用户变量不会自动更新已经启动的父进程。旧终端启动 API 时可只将既有用户变量注入该进程，不需要复制或打印密钥：

```powershell
$env:GREAT_AGENT_MODEL_API_KEY = [Environment]::GetEnvironmentVariable('GREAT_AGENT_MODEL_API_KEY', 'User')
uv run great-agent api --config config/local.toml
```

本轮不新增数据库迁移。若环境尚未采用此前 P1 版本，应继续遵循 README 的备份和 `0003_plan_proposals` 升级步骤。执行批准、任务启动及真实程序运行仍通过原有明确确认流程。
