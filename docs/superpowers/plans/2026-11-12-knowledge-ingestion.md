# Knowledge Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可用的本地文献数据库、持久化入库链路与检索/来源管理界面。

**Architecture:** 八张SQLAlchemy知识表和SQLite FTS5复用应用数据库；内容寻址原件单独存储。API lifespan拥有独立入库执行器，解析子进程只写staging，父进程短事务发布不可变版本。

**Tech Stack:** FastAPI、SQLAlchemy、Alembic、SQLite FTS5、pdfplumber、可选Docling、React/Ant Design。

**Spec:** `docs/superpowers/specs/2026-11-12-knowledge-ingestion-design.md`

## Global Constraints

- 继续当前checkout，不覆盖已有改动；不提交、不改生产DB、不重启服务。
- 仅本地处理；不调用云模型，不下载大型模型；不启动GREAT。
- 支持PDF/MD/TXT；50MiB、300页、300秒默认限制；切片700估算token/完整短段重叠上限100。
- 仅词法检索，明确quality flags；不宣称向量、自动问答、公式/图片数值理解已完成。

## Review Focus

- 同内容不同名与同名不同内容：内容hash决定身份，库范围不能串。
- 解析中重启、重试和迟到完成：旧owner不能发布，旧可用版本不能丢。
- PDF双栏、跨页公式、表格与扫描件：质量限制可见，空解析不能成功。
- 文件名、ID、原文路径及FTS元字符：不能穿越存储边界或注入查询。
- 旧无版本DB与create_all提前建表：识别合法历史，拒绝保留名冲突。

### Task 1: 持久化知识关系与检索

**Files:** 新增`db/knowledge_models.py`、`db/knowledge_schema.py`、`db/knowledge_repository.py`；测试`backend/tests/test_knowledge_repository.py`。

**Interfaces:** `KnowledgeRepository(session_factory)`提供create/list/get base/document/job、register_upload、queue_reparse、claim/recover/release/fail/publish job、set_enabled、list/get chunks、search。跨模块统一返回JSON兼容dict；发布输入为parser的结构化dict。

- [ ] 先写真实SQLite测试并观察缺失功能失败。
- [ ] 实现八表、内容唯一性、owner/CAS、active version、FTS triggers与受控查询。
- [ ] 验证去重、失败保旧版、停用/跨库、恶意FTS、重启和迟到结果，完成独立审核。

### Task 2: 本地存储、解析与受控进程

**Files:** 新增`knowledge/{storage,parsers,types,parse_worker}.py`和包入口；测试`backend/tests/test_knowledge_parsing.py`。

**Interfaces:** `KnowledgeStorage(root,max_upload_bytes).store_stream`或同步store_bytes返回sha256/size/media_type/relative_path；`original_path(relative_path)`强制root containment。`parse_document(source,media_type,parser_name,max_pages,artifacts_path=None)->ParsedDocument`；`ParsedDocument`含parser、metadata、pages、elements、chunks、warnings。`python -m great_agent.knowledge.parse_worker`按父进程指定路径写JSON。

- [ ] 先写文本/PDF、扫描空页、坐标、图表质量标记与路径测试，观察失败。
- [ ] 实现自动文字基线、结构化Docling adapter、本地模型限制和chunker，不引入单论文profile。
- [ ] 验证真实小PDF与Docling结构fixture，明确未运行的大模型验收，独立审核。

### Task 3: 配置、迁移、API与异步入库

**Files:** 新增`services/knowledge_service.py`、`api/knowledge.py`、`alembic/versions/0006_knowledge.py`；修改`config.py`、`models.py`注册、`schema.py`、`dependencies.py`、`app.py`、`cli.py`、`pyproject.toml/uv.lock`；测试`test_knowledge_api.py/test_knowledge_migration.py`。

**Interfaces:** spec所列/api/v1/knowledge路由；service.start/stop由lifespan调用；与Task1/2约定在ledger固定。

- [ ] 先写API上传→持久任务→检索/来源与旧DB迁移测试，观察失败。
- [ ] 实现安全配置、0006与FTS guard、worker子进程/owner、上传限额、原文响应、生命周期。
- [ ] 验证超时/重启/重复上传/解析失败/旧版本/路径/未知schema，完成独立审核。

### Task 4: 管理入口、实文验收与文档

**Files:** 新增`frontend/src/api/knowledge.ts`、`components/KnowledgeLibrary.tsx`及样式/测试；主agent接入`App.tsx`；更新README与独立validation。

- [ ] 先写建库/上传/状态/检索/来源/停用/异步切换的UI测试，观察失败。
- [ ] 实现独立Drawer内容，原文bytes上传由fetch设置正确Content-Type，不污染现有JSON client。
- [ ] 跑后端、前端、类型/构建/lint检查；隔离DB以真实API导入用户Tao PDF、检查去重、页定位与warnings。
- [ ] 记录证据和剩余边界，独立最终审核；保留改动供审阅，不自动部署。
