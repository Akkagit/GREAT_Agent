# P2统一对话首批验收

状态：P2.0对话/工具兼容与P2.1统一对话首批完成并更新本地入口。论文上传、解析、索引、证据页引用和后台技能发布管理仍是后续批次；Docling/向量容量样机没有在本轮安装或跑分。

## 用户可见行为

- 首页无需创建实验即可咨询GNSS理论；绑定实验时同一输入框可给解释与修改提案。
- 普通咨询不登记提案、不替换当前pending，也不使已完成的输入检查失效。冻结或已有任务的实验仍可只读咨询。
- 每个实验与全局各有一个默认持久会话；刷新/切换可恢复正文与当前提案状态。同一回合通过client_request_id幂等保存，重放不会重新调用模型。
- 原生AgentScope2.0.11 Agent/Toolkit/FunctionTool/LocalSkillLoader运行；只有受控读取和待确认提案工具，无直接接受、XML或执行工具。
- 接受后自动只读检查、人工生成XML/移动端进入复核、最终明确执行确认保持原有行为。
- 全局规范位于config/agent/SYSTEM.md，5份技能位于config/agent/skills。按需读取，记录内容hash；构建包也有默认资源，历史权限不会从会话快照恢复。

当前理论正文依据模型通用知识，不代表检索了论文或完成专业正确性评测。界面明确标注论文检索尚未启用。正文仍是纯文本；资料证据面板和数学排版在后续知识库批次处理。生命周期SSE已提供，当前前端消息使用完整回合响应，尚非聊天token流。

## 验证结果

| 项目 | 最终结果 |
| --- | --- |
| 完整后端 | 340 passed，91.49秒，exit0 |
| 原生Agent覆盖组 | 15 passed；原生Skill读取、预算、取消、proxy与脱敏 |
| 会话后端覆盖组 | 44 passed；持久化、回放、CAS/lease、断连、迁移与旧接口 |
| 完整前端 | 109 passed，7 files；typecheck/lint/build全exit0 |
| 浏览器 | 最终21/21，exit0，零未捕获异常；隔离合成API |
| 原生运行层审查 | Approved，无P1/P2 |
| 整合审查 | 初轮2个历史合并P2；原实现者一次修复波次后两项均ADDRESSED，复审Approved |
| wheel | 构建成功，zip核对SYSTEM.md+5个SKILL.md均随包分发 |
| 本地入口 | health/首页/最终JS/会话列表接口均200 |

第一次完整后端338项收集早于两个新增用例，随后重新执行最终340项；不是将不同时间的测试结果混称同一次完整验收。首轮浏览器脚本对Ant Design按钮空格的转义有误，修正测试脚本后通过；最终验收另覆盖取消传输后跨区域重挂载、消息不重复及混合解释从恢复占位升级。

### 真实GLM合成验证

直接调用真实原生Agent运行层，未打开用户数据库、未生成XML或启动Worker/求解器；保留原HTTP/HTTPS/ALL_PROXY设置。每轮硬预算4次物理模型请求、6次工具调用、90秒，各层重试0。

| 回合 | 实际行为 | 物理模型请求 / 工具 | 耗时 |
| --- | --- | --- | --- |
| 全局PPP/RTK理论咨询 | 返回正文，无提案/回调 | 2 / 1 | 20.25秒 |
| 解释双频/三频并改为TF | 正文+一个待确认候选，仅频率变化，其他字段保持 | 3 / 3 | 35.61秒 |

合计5次模型请求。这证明当前GLM端点的原生工具闭环和分类输出可用，不是所有GNSS回答正确或性能已优化的证明。只执行这两轮，没有重复真实调用。[合成结果](2026-11-02-p2-conversation-live.json)

## 持久化和上线

新增conversations、conversation_messages、conversation_runs、conversation_events四表，提供0004_conversations迁移。临时数据库验证从0003升级、已有bootstrap表兼容及降级；本机采用既有启动bootstrap新增表，未对原有科研表做破坏式迁移。只读对照启动前备份，9张既有实验/修订/提案/任务/结果/审批表内容全部相同。

启动前备份：data/backups/great_agent-before-p2-conversation-20261102-211139.db。API launcher32952 / server16208，仅API运行，没有启动Solver Worker。模型密钥仅从用户环境传入进程，未输出或保存。

最终构建：index-BhBBe3Ns.js / index-B_dRJBUu.css；先发布hash资源，再原子替换index.html。早期构建CG7ZrHmL曾写入dist，之后修复构建全部暂存在tmp/p2-frontend-dist；以此处最终hash及serving.json为准。依赖与锁文件未改变；原大bundle提示保留。

新API：POST/GET /api/v1/conversations，GET /{id}，POST /{id}/messages，GET /{id}/events。相同请求ID但内容不同或并发活动回合返回409。断连或预算失败记录中断/失败，已保存的有效pending可恢复；不会因此获得执行批准。旧/chat/proposals仍保留，前端没有将普通咨询静默降级为旧JSON提案。

## 证据和剩余范围

- [浏览器结果](2026-11-02-p2-conversation-browser.json)
- [全局咨询](assets/2026-11-02-p2-global-consult.png)
- [正文与提案](assets/2026-11-02-p2-mixed.png)
- [移动端XML复核](assets/2026-11-02-p2-mobile-review.png)
- [取消后恢复解释与卡片](assets/2026-11-02-p2-recovery.png)

首批直接读取受控目录的SYSTEM/技能文件并记录hash；后台发布草稿/数据库active版本切换未实现。资料上传、Docling/OCR、FTS/向量检索、文献来源核验、rerank与容量模型选择继续按P2.2/P2.3推进，不将本轮称为整个知识中心完成。

原始报告、范围基线、修复diff、独立审查、实际命令和日志保留在.codex_work/p2-conversation-20261102。没有提交或推送。
