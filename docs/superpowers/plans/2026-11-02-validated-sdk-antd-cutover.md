# 已验证 SDK 与 Ant Design 主路径替换实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** 将 P0 已验证的 AgentScope 模型 SDK 和 Ant Design 组件接入当前产品主路径，替换直接 HTTP 模型调用和自定义基础控件。

**Architecture:** 保持 FastAPI、领域服务、持久提案、执行批准、SQLite 和 Worker 不变。实现符合既有 `ModelProvider.propose_plan` 的 AgentScope SDK 适配器并接入 CLI 装配。前端用 Ant Design 控件承载原有三栏工作台、计划编辑、提案审批、执行审阅与任务监控。

**Tech Stack:** Python 3.11 / AgentScope ==2.0.11 / HTTPX SOCKS extra / React 19 / Ant Design ==6.6.8。聊天采用 antd Input/TextArea/Button，X 仍为可选隔离验证项。

**Spec:** `docs/superpowers/specs/2026-11-02-agentscope-ant-design-refactor-design.md`。本计划是其中“已验证依赖切换”的独立交付，不把尚未实现的完整 Agent 运行时当作 P0 成果。

## Global Constraints

- 当前用户已明确授权“先把已经预发验证但是还没有替换的进行替换”，直接执行这两项替换。
- 复用当前 `codex/agentscope-p0-p1` 工作分支及既有未提交成果；基线保存于 `.codex_work/sdk-antd-cutover-20261102/baseline`。不重置、不提交混合的既有改动。
- 不调用真实外部计算程序、不升级生产数据库、不自动接受提案、不自动确认执行。
- 模型配置沿用 TOML，密钥沿用进程环境 `GREAT_AGENT_MODEL_API_KEY`；不输出密钥、提示词、供应商响应体。
- 现有 JSON schema / JSON object 配置、GLM thinking 与 max_tokens、整次 deadline、取消传播、错误类型及手工回退语义必须保持。
- 单候选请求禁用 SDK/客户端重试与结构化多策略 fallback，并设置物理请求次数保护；最终候选继续接受 Pydantic 与领域校验。
- SDK interrupted、不完整终态、拒绝、截断和异常响应不得生成可接受提案。
- 保留原有 P1 pending 恢复、基线 UUID、CAS、冻结门禁、用户 confirmed=true、逐 attempt 批准及原子配额。
- 前端保持三栏、宽度持久化、键盘可访问性、小屏区域切换、完整 XML 纯文本审阅。
- 产品等效 `skipLibCheck=true` 沿用；不以关闭类型或关闭测试掩盖问题。
- 本轮不实现 Agent / Toolkit / AgentState / AgentRun 存储、聊天 token SSE、检索、多 Agent 或分布式执行。

## Review Focus

- SDK 默认多策略请求、客户端重试和错误重试造成额外费用或时延。
- 超时/取消后上游连接未关闭，或 SDK interrupted 被当作正常候选。
- UI 控件迁移导致字符串/数字/空值变化、冻结状态可编辑或晚到候选覆盖新实验。
- 三栏切换/尺寸变化卸载聊天或计划编辑器、键盘无法缩放，或恢复错误宽度。
- antd 样式与旧全局 CSS 冲突、Select/Modal 门户可访问性、XML 审阅和确认复选框失效。

## Task 1: AgentScope 模型 SDK 切换

**Files:** 新建 `backend/src/great_agent/adapters/agentscope_model.py` 与相应测试；修改 `pyproject.toml`、`uv.lock`、`backend/src/great_agent/cli.py`，按实际需要更新旧 provider 合同测试。

**Consumes:** `ModelSettings`、`ModelProvider.propose_plan(message, current)`、`ModelPlanProposal` 与现有 provider 错误类型。

**Produces:** `AgentScopeModelProvider(settings, transport=None)`；CLI 只装配新 provider；正式请求通过 `OpenAIChatModel`，候选校验与错误合同保持。

- [x] 写并运行失败合同测试：装配、新 SDK 发送的 JSON mode/schema、GLM 参数、一次物理请求。
- [x] 使用精确锁定 SDK；实现模型适配、异常映射、deadline、取消与 client 清理。
- [x] 覆盖 4xx/429/5xx、不合法 JSON/schema、空/拒绝/截断/interrupted、超时与外部取消。
- [x] 回归现有 chat / responsiveness / proposal 合同；独立评审并修复。

## Task 2: Ant Design 产品组件替换

**Files:** `frontend/package.json`、lock、主题入口、`App`、components、pages、必要 CSS 和前端测试支持。

**Consumes:** 现有组件 props 和 `api/client.ts` 领域合同。

**Produces:** 正式产品 `ConfigProvider` 与 antd 控件；所有现有服务 API 和审批状态所有权保持。

- [x] 锁定正式依赖；保留或补充有意义的用户交互回归，先确认现有迁移目标测试能暴露缺陷。
- [x] 建立统一 tokens 与中文 locale；基础按钮、表单、选项、表格、步骤、状态、Tabs/Descriptions 等替换为 antd。
- [x] 保留当前分栏行为核心（antd Splitter 6.6.8 无公开键盘/ARIA handle 注入口），布局工具按钮替换为 antd；宽度恢复/键盘操作/移动端区域和内容状态回归。
- [x] 聊天使用 antd 核心 Input/TextArea/Button/状态组件；保留原位提案卡、模型生成、取消、readOnly 行为。X 不列为本轮产品依赖。
- [x] 跑前端完整 test / typecheck / lint / build，浏览器验证桌面和窄屏；独立评审并修复。

## Task 3: 联合验收与状态更新

**Files:** README、`docs/validation/2026-11-02-validated-cutover.md`、架构图生成器与输出。

- [x] 完整后端和前端回归，记录真实命令与结果。
- [x] 使用本地合成 fixture 验证已构建界面与新 provider 主路径，不依赖生产 DB 或真实计算程序。
- [x] 广泛独立评审，修复影响用户行为的发现并复验。
- [x] 更新架构图，将已切换模型 SDK 和 antd 标为当前主路径，P0 保留历史验证依据；完整 Agent 目标仍为待实现。
- [x] 记录启动/升级步骤与已知限制，交付代码及验证证据。
