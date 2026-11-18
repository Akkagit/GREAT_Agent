# 对话 Markdown 渲染与创建实验：问题定位、修复和验证

日期：2026-11-12

## 1. Markdown 没有显示的原因

截图中的回复已经包含 `**原因：**`、反引号和列表标记，但前端 `frontend/src/components/ChatPanel.tsx` 原先使用 `<p>{item.text}</p>` 输出整段回复。React 会把这些标记当作文本显示，没有 Markdown 解析步骤；单纯修改字体或要求模型继续输出 Markdown 无法解决。

另一个排版问题是 `frontend/src/pages/ExperimentWorkspace.css` 的 `.chat-bubble p`、`.chat-bubble span` 使用后代选择器。接入 Markdown 后，每个内部段落都可能被套上气泡背景和内边距，卡片里的段落也会受影响。

修复：

- 新增共享 `MarkdownContent.tsx`，使用 `react-markdown` 解析助手回复；提案说明 `PlanDiff.tsx` 复用同一组件。
- `remark-gfm` 支持表格、任务列表、删除线；`remark-cjk-friendly/parseOnly` 处理紧邻中文和标点的加粗，例如 `**原因：**当前配置`。
- 把气泡外观限制在直接子元素 `.chat-message-text`，为标题、段落、列表、引用、代码和表格补充独立样式。代码块和宽表格在容器内滚动。
- 用户输入、差异值和 XML 保持原文；助手代码围栏中的内容也按代码展示。
- 关闭原始 HTML，保留渲染库的 URL 协议过滤，不使用 `dangerouslySetInnerHTML`。

入口：`ChatPanel.tsx`、`MarkdownContent.tsx`、`MarkdownContent.css`、`PlanDiff.tsx`、`ExperimentWorkspace.css`。

## 2. 对话无法创建实验的原因

全局对话没有绑定实验，服务端 `_context()` 返回 `None`，模型看到的是 `bound: false`。原来的工具只有能力查询、读取计划、读取任务，以及仅在绑定可修改实验后开放的 `propose_plan_change`，没有创建实验工具。原策略还要求未绑定会话不能修改或执行实验。因此截图中的拒绝是原有能力边界造成的。

验证工具链时还发现一个原有适配问题：当前 AgentScope `FunctionTool` 不能直接把 `ToolResponse` 当作函数返回值解析，最终传给模型的是 SDK 对象的字符串表示。共享返回值函数现已改为框架支持的 JSON 文本；测试直接解析发送给模型的工具消息，覆盖默认配置和能力信息的真实传递。

修复后的流程：

1. 首页全局对话接收创建需求。模型可以通过 `get_experiment_defaults` 读取完整默认配置，再按用户需求构造候选计划。默认基线与首页一致：2024 年 DOY 350、15 站、静态双频浮点方案。策略要求说明默认假设，关键选择不明确时补问。
2. `create_experiment` 调用服务端回调，校验完整 `ExperimentSpec`、日期、测站、并发限制和已登记的配置。此时只暂存通过校验的候选配置。
3. 模型回复成功后，重新校验对话请求所有权，在同一个数据库事务中创建草稿、保存第一版计划和 `experiment_created` 回执。任何一步失败都会回滚。
4. 前端根据服务端结构化回执展示日期、方案、测站及“进入实验”。刷新可恢复卡片，创建后刷新左侧实验列表。进入后使用该实验自己的对话和规划台。
5. 创建后清除过早保存的 SDK 状态，并将真实创建回执纳入后续对话历史，支持追问创建结果和实验编号。
6. HTTP 回包丢失时，前端先读取持久化对话，按原始请求编号恢复结果，避免直接提示重发造成重复草稿。

创建入口仅在首页全局对话开放。现有实验里的对话继续负责咨询、提案和已核对 XML 的执行确认。创建草稿不生成 XML、不排队任务、不启动 GREAT。

示例：在首页对话输入“创建一个 2024 年第 350 天、AHB0 测站的静态双频浮点实验”，创建回执显示后点击“进入实验”。

服务端入口：`runtime/conversation_agent.py`、`services/conversation_service.py`、`services/plan_service.py`、`domain/conversation.py`、`domain/experiment_defaults.py`；配置版和随包版 `SYSTEM.md` 同步更新。

前端入口：`GlobalConversation.tsx`、`ExperimentCreatedCard.tsx`、`App.tsx`、`api/types.ts`。

## 验证记录

- Markdown 回归：修复前 5 项失败，修复后 6 项通过，覆盖中文加粗、列表、表格、代码、HTML/链接处理和用户输入原文。
- 创建服务回归：12 项通过，覆盖持久化、同请求去重、配置校验、取消、超时、写入失败回滚、API 回执序列化和创建后追问。
- 前端完整测试：141 项通过；创建卡片、导航和网络回包丢失恢复包含在内。
- 生产构建和 TypeScript 编译通过；`npm run lint` 通过。构建保留单个 JS 包超过 500 kB 的体积提示，不影响构建成功。
- Chrome 对生产构建的 7 项检查通过：实际字体加粗、列表/代码/表格 DOM、实验列表刷新、回执刷新恢复、进入实验、手机宽度和浏览器异常检查。浏览器使用隔离的合成 API；没有调用生产数据库、模型或解算器。
- 后端完整测试：`python -m pytest backend/tests -q --tb=short`，418 项通过；新旧对话工具的 24 项测试亦单独通过。

截图与机器记录：

- [Markdown 实际显示](assets/2026-11-12-chat-markdown.png)
- [创建回执及实验列表](assets/2026-11-12-chat-created.png)
- [手机对话布局](assets/2026-11-12-chat-mobile.png)
- [浏览器检查 JSON](2026-11-12-chat-browser.json)

## 使用更新

前端 `frontend/dist` 已重新构建。正在运行的后端需要重新启动后才会加载新增服务端代码；随后刷新页面。代码和回归测试已验证，不包含真实模型效果或真实解算的端到端运行。
