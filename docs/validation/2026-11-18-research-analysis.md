# 科研分析 Tab 实施与验收

后续状态：本机已在用户授权后完成正式启用，见[当前环境启用记录](2026-11-18-research-production-enablement.md)。下文保留启用前的实现与隔离验收记录。

日期：2026-11-18。当前状态：实现、独立复审、真实 API 浏览器联调及最终完整检查已完成；正式环境未切换。

## 已实现的流程

- 第三个顶级工作空间“科研分析”，独立课题历史、材料、对话、研究状态、证据查看和报告。
- 从实验进度/图片页带入当前选中的 experiment/task/attempt，不自动发送问题、不运行实验。
- 关联已有实验统计/日志/XML、注册源码文件、知识库具体文档版本；历史代码可按可信持久化快照与新版本共同选入。
- 一个有预算的 AgentScope 主模型调用十一种只读工具，按需读取三类材料；字段定位、内容哈希、统计口径和质量标记随证据保存。
- 连续追问及针对具体消息块/假设追问；观察、假设、反证、待补充项与用户原话分别保存。摘要/旧回答不自动获得证据资格。
- 旧引用按稳定 evidence ID 重新授权和读取，分配本轮别名；同名 `[C1]` 总是由 run ID 共同定位。
- 显式失败重试、未知接收结果恢复、材料/模型下一轮修订、可取消的独立持久研究队列和检查点。
- 保存有正文、具体轮次与来源按钮的不可变报告；历史 partial/needs_input 的范围与限制在新追问后仍可查看。

## 只读边界

研究模型没有文件写入、脚本执行、导入用户代码、编译、测试、重做后处理、改计划、确认执行或求解器工具。代码差异和修改步骤作为未应用建议展示。固定指标比较只使用已有 JSON 指标，检查参照、日期、采样、单位和统计口径；CSV 可读取，不补造其缺失元数据。

新增代码、索引、会话、证据副本和报告的写入只发生在平台研究存储中。开发验证使用临时数据库/源文件与模拟模型，不代表对真实研究算法做了自动实验。

## 关键实现位置

| 范围 | 文件 |
| --- | --- |
| 研究模型与数值/来源校验 | `backend/src/great_agent/research/engine.py` |
| 材料捕获、授权与版本 | `backend/src/great_agent/research/materials.py` |
| 十一种只读读取/比较工具 | `backend/src/great_agent/research/tools.py` |
| 会话、证据、检查点与研究状态 | `backend/src/great_agent/research/models.py`、`repository.py` |
| 长任务和恢复 | `backend/src/great_agent/research/service.py`、`worker.py` |
| REST/SSE | `backend/src/great_agent/api/research.py` |
| 前端工作空间 | `frontend/src/pages/ResearchWorkspace.tsx` |
| 材料/证据组件 | `frontend/src/components/ResearchMaterials.tsx`、`ResearchEvidencePanel.tsx` |
| 前端类型和请求 | `frontend/src/api/research.ts` |
| 数据库版本 | `backend/alembic/versions/0010_research.py` |

## 已验证的重点边界

- 实际仓库/材料/AgentScope 组合在模拟 HTTP 模型边界下，快速/深入两种模式均连续五轮，同时读取实验、代码和文献，重新加载证据并保留反例。
- 故障恢复重放已保存的模型响应和规范化工具输出，不重复外调；源码正常变动不改变原快照。
- 请求幂等先于会话版本冲突；旧失败轮只能在原研究状态未前进时重试，否则以原问题发起新追问。
- 取消、过期 owner、重复完成和回执丢失不会重复提交回答或研究状态。
- 来源撤销会遮盖相关会话/报告/证据；每次实际外发前重新核查历史依赖，不能从旧回答/研究事项缓存向模型再次发送被撤销内容。
- 用户陈述保留逐字原话和消息身份，不能被模型改写为数值观察；跨数组重复事项更新被拒绝。
- 结构化数值声明通过原始 JSON Pointer、提取指针映射或派生比较行核对值与单位。此机制不等于自动证明任意自然语言结论或数学推导。
- 注册目录、路径越界、链接、密钥内容、未知工具、伪造引用和浏览器提交快照均有拒绝测试。

前后端独立复审的 P1/P2 问题已修正并复审通过。早期并行集成中一次 SQLite 锁错误未在两次独立定向运行复现，没有因此修改全局 SQLite 超时。Windows 沙箱偶发在测试体之前阻塞 asyncio socketpair，已捕获堆栈；需要时仅对隔离、离线验证使用审核后的执行权限。

## 最终验证结果

真实 API 浏览器流程：默认快速模式、显式深入模式均完成两轮定向追问；真实版本快照在夹具源码变动后仍保留原文，实际生成 753 字符报告，刷新保留 4 条消息/2 个轮次/1 份报告。390px 手机布局无横向溢出，浏览器无页面错误或越界请求；临时 API/worker 均已退出。仅模拟供应商 HTTP，数据库/服务/队列/材料读取均使用真实实现。

快速模式联调发现并修复了材料对象字段顺序误判、按字符计 token 导致过早拒绝，以及重复发送逐轮审计引用造成上下文增长的问题。输入预算使用明确标注的混合语言启发式估算，含原生工具 schema 和消息开销；它不保证是任意模型 tokenizer 的硬上界。快速模式保留最近两次对话，深入模式最近八次；持久研究事项、支持/反证 ID、用户原话与明确选定的更早追问仍分别保留。原 12k/32k 预算和调用次数上限不变。

核心 engine/service/API/五轮集成定向检查：37 passed。另有独立新增的 12 项预算临界值、工具结果估算与导航保真检查通过，未修改持久状态。

最终完整检查对应同一份冻结源码：

| 检查 | 最终结果 |
| --- | --- |
| 后端和脚本完整测试 | 1049 passed、3 skipped；exit 0 |
| 前端完整测试 | 25 个文件、359 passed；exit 0 |
| TypeScript | exit 0 |
| ESLint | exit 0 |
| 隔离前端构建 | exit 0，生成 index.html 和资源指纹 |
| 验收前后源码 | SHA-256 一致，`source_unchanged=true` |
| 临时构建清理 | `build_output_removed=true`，目录已不存在 |

报告为 `output/verification/research-20261118-release.json`，源码指纹为 `951dda536b18193b923e736dc8f2d327232bcf0cb4ad4ae69ed12a2c3551d173`。

本次为同一源码的分段恢复门禁：原 `scripts/verify.py` 在前两项测试均以 exit 0 完成后、启动 TypeScript 之前，原子替换 JSON 报告遇到 Windows `WinError 5`，runner 因而退出 1。恢复工具先确认原测试日志、两个通过步骤的完整对象及源码指纹，再沿用原检查实现顺序执行余下三项。最终报告的 `recovery` 字段保存原始异常、恢复命令、检查点/工具/日志哈希；没有把未执行步骤或原 runner 的退出码记作成功。原检查点保存在 `research-20261118-interrupted-checkpoint.json`。早期为修复独立复审/真实联调发现而中止的其他门禁不计为通过证据。

浏览器原始记录与截图保存在 `output/verification/research-ui/`：`research-live-api-quick-final-result.json`（默认快速模式）、`research-live-api-result.json`（深入模式）、`research-live-api-quick-final-evidence.png`、`research-live-api-quick-final-report.png`、`research-live-api-quick-final-mobile.png`。

## 启用现有环境

本轮未迁移正式数据库、重启正式服务或替换 `frontend/dist`。已有环境需在适当维护窗口按现有流程停止 API、Solver Worker 和 Research Worker，完整备份数据库、runs、文献原件与配置，迁移到 0010 后构建并启动。

```powershell
.\scripts\stop.ps1
# 先按 README 完成实际备份，再迁移：
uv run great-agent migrate --config config/local.toml
.\scripts\start.ps1
```

正常启动管理三个进程；`-ApiOnly` 仅启动 API。研究执行器也可单独启动：

```powershell
uv run great-agent research-worker --config config/local.toml
```

代码根目录在 `[research.code_roots]` 注册，浏览器仅提交来源 ID 和相对文件名。`[research.model]` 可独立指定研究模型，密钥只读取环境变量；未指定时继承主模型地址与密钥并使用独立研究预算。实例见 `config/app.example.toml`。

首版限制：每个文本文件最多 1 MiB、200 个文件/8 MiB 快照，摘录最多 200 行/20,000 字符；现成向量索引不可用时明确降级词法检索；静态源码与历史二进制的关联默认未知。真实模型的科研判断质量仍需用实际 GNSS 问题联调评估，本次验证没有付费模型请求。
