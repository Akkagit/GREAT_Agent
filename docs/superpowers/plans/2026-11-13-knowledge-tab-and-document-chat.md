# Knowledge Tab and Document Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Execute task-by-task with tests before implementation.

**Goal:** 按已批准预览把文献知识库独立Tab、上传解析、可持久引用的GLM对话接入现有系统，提供真实模式的混合检索与可检查原文来源。

**Architecture:** 保留现有八表和会话表，新增知识binding/trace/citation/index generation/segment五表。独立只读知识引擎使用服务器绑定的版本快照检索；前端三栏页面选择文献、对话和核对来源。

**Tech Stack:** FastAPI/SQLAlchemy/Alembic/SQLite FTS5、AgentScope2.0.11/GLM、ONNX量化多语E5+tokenizers、Qdrant local、React/Ant Design/PDF.js。

**Spec:** `docs/superpowers/specs/2026-11-13-knowledge-tab-and-document-chat-design.md`

## Global Constraints

- 继续当前codex checkout，保留所有前置未提交改动；不自动提交/推送，不替换生产dist或迁移正式库。所有构建使用`tmp/knowledge-chat-dist`。
- 当前问题2000字符、历史6000、证据12000且计数器可用时6000tokens、系统/工具6000；整轮26000字符/可计数时12000tokens；输出2048、4物理请求/6工具/90秒。
- 只注册search_knowledge和read_evidence；不恢复旧opaque SDK state；密钥仅服务器。真实GLM验收仅本轮授权的所选文献证据，限一次成功闭环及必要故障修正。
- Dense权重单独、显式下载并SHA验证，不在上传时隐式下载。内存不足或模型未配置明确显示lexical/degraded，不假称hybrid。
- 原件/解析/索引本地。上传和版本、停用、故障恢复等现有行为继续有效。硬删除和正文编辑不在本批范围。

## Review Focus

- 旧global API不能读/发送knowledge会话；selected空集不能扩大为全库。
- run固定文档版本，重解析不混版本；停用/范围撤销后不再取证或外发相关历史。
- 模型伪造S编号、无证据回答、预算溢出、provider错误和取消不能写入假成功引用。
- snapshot检索需过滤固定document/version而非active指针；embedding子片段能回映不可变父chunk。
- 0004/0006历史采用、reserved冲突、create_all/bootstrap与FTS guard兼容；未知DB不能被静默采用。

### Task 1: 知识会话数据与引用事务

**Files:** create `db/knowledge_chat_models.py`, `db/knowledge_chat_repository.py`, `db/knowledge_chat_schema.py`; tests `test_knowledge_chat_repository.py`。可修改`db/conversation_repository.py`提取claim_in_session和payload事件；主agent负责models注册/schema guard/migration。

**Interfaces:** `KnowledgeChatRepository(sessions)`提供create/list/get conversation、update_binding、claim_turn、safe_history、stage、save_trace、complete_turn、fail_turn、get_citation、events、search_snapshot、read_snapshot_chunk。所有结果为JSON兼容dict；精确契约记ledger。

- [x] 先写真实SQLite测试并观察RED。
- [x] 原子claim与快照、binding revision、幂等replay、引用/消息/run同事务完成，固定版本检索与历史过滤。
- [x] 运行定向测试并独立review，未知引用/跨库/撤销/迟到结果均不能通过。

### Task 2: 受控检索与知识模型引擎

**Files:** create `knowledge/retrieval.py`, `knowledge/embedding.py`, `knowledge/vector_index.py`, `runtime/knowledge_chat_engine.py`; tests `test_knowledge_retrieval.py`, `test_knowledge_chat_engine.py`。模块可细分但独占这些新文件。

**Interfaces:** `KnowledgeRetriever(repository,settings,root)`，async `search(snapshot,query,limit=8)`返回{mode,items,candidates,queries,timings}，`close()`关闭单一向量owner；`KnowledgeChatEngine(model_settings,...)` async `reply(question,history,search_knowledge,read_evidence,on_request)`返回{text,source_ids,answerable,metrics,outbound_messages}。

- [x] 先写snapshot/融合/父子映射/来源ID/预算/只读工具/无证据/provider失败测试，观察RED。
- [x] 实现词法与可配置ONNX E5/Qdrant adapter；原始查询保留，实际mode准确；scope由服务器固定。
- [x] 实现GLM AgentScope引擎、工具和实际payload预算，输出结构可校验，错误脱敏，无隐式重试。
- [x] 小模型资源实测+13题召回诊断；真实模型前先mock完整协议并独立review。

### Task 3: 完整页面及来源核对

**Files:** create `pages/KnowledgeWorkspace.tsx/.css`, `components/KnowledgeChat.tsx`, `KnowledgeSourcePanel.tsx`, `api/knowledgeChat.ts`; 可提取复用`KnowledgeLibrary.tsx`内容。create UI tests。主agent接App路由与依赖，不触碰生产dist。

**Interfaces:** page props `{baseId?:string,chatId?:string,onLocationChange:(baseId?,chatId?)=>void}`；API下文ledger固定；S引用块使用后端源包，PDF.js自托管。

- [x] 先写Tab/上传范围/会话/问答引用/刷新/取消/切换与迟到请求测试，观察RED。
- [x] 按预览实现三栏与两个视图；显示实际检索模式、模型配置/外发范围、解析质量；保留旧实验工作台路由和草稿。
- [x] 正确页码/缩放/旋转坐标与不可用来源；窄屏按需面板，键盘可访问。
- [x] 定向UI、types/lint、隔离构建，独立review。

### Task 4: Service/API/配置/迁移集成

**Files:** create `services/knowledge_chat_service.py`, `api/knowledge_chat.py`, `alembic/versions/0007_knowledge_chat.py`; modify `config.py`, `cli.py`, `app.py`, `api/dependencies.py`, `db/models.py`, `db/schema.py`, `services/conversation_service.py`, `frontend/src/App.tsx`, dependency manifests and examples。

- [x] 先写API端到端与迁移RED；保留global/experiment兼容测试。
- [x] 组合claim→只读工具检索→GLM→引用校验→原子持久化；取消与超时先撤销owner，晚到响应不能提交。
- [x] 新路由、模型profile/连接测试、SSE生命周期、页面元数据/原页访问与模型配置；旧API拒绝knowledge scope。
- [x] 0007迁移及冻结0004基线，API生命周期管理唯一向量client；不在Solver Worker启动RAG循环。

### Task 5: 实文验收与交付

- [x] 所有新增定向、完整后端和前端、types/lint/build；已有native不稳定项保留证据，针对根因处理。
- [x] 隔离DB导入Tao PDF，真实UI/API上传→解析→模型问答→点击原文→刷新→停用/重解析/范围变化。
- [x] 运行获授权的受限真实GLM引用闭环；凭据/网络/内存不足时明确诊断并完成可独立验证的配置与fallback，不能伪造成功。
- [x] 独立最终review，记录模型/检索模式、版本与证据，更新README/验收记录；不自动部署正式服务。
