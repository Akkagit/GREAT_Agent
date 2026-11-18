# 本地知识库数据库与入库链路

依据用户“检索GitHub相似案例、借鉴过来实现”的指令，执行[已批准知识设计](2026-11-02-conversation-knowledge-design.md)的数据库与入库首批。案例及取舍见[源码核查](../../research/2026-11-12-knowledge-github-cases.md)。

目标：在现有Windows/FastAPI/SQLAlchemy/SQLite/React系统里建立能实际上传、查询入库状态、检索切片并回查原页的本地文献库。继续使用当前checkout，保留所有已有改动；不迁移生产DB或重启现有服务，不调用云端模型。

## 数据和存储

新增knowledge_bases、knowledge_blobs、knowledge_documents、knowledge_versions、knowledge_jobs、knowledge_pages、knowledge_elements、knowledge_chunks八表。blob以SHA256唯一，文档以库+blob唯一；同名不同内容为不同文档，同内容不同名复用。job为持久化队列；每次重解析生成不可变version，成功发布才切document.active_version_id，失败保留旧版。

配置knowledge.root默认project_root/data/knowledge；必须在project_root内，不能与run_root、受保护代码目录相撞。原件位于blobs/<hash前两位>/<hash>.<ext>；只有展示名来自上传文件名。版本派生物和staging按受控UUID管理。源文件不从API任意路径读取。

所有数据库DDL经0006迁移；create_all通过同一FTS helper建立FTS5虚表/触发器。更新历史schema guard，避免把新表当成旧0004基线；未知同名表/FTS冲突拒绝采用。FTS更新与chunk发布同事务。ready/completed_with_warnings均允许词法检索，但返回quality flags；停用立即从搜索和原文读取中排除。历史已发布版本可显式读取，不能将旧引用指向新切片。

## 接口与后台执行

新增/api/v1/knowledge：GET/POST /bases，GET /bases/{id}/documents，POST /bases/{id}/documents?filename=...（原始文件bytes、流式限额，不需要multipart），GET /documents/{id}，PATCH /documents/{id}（enabled），POST /documents/{id}/reparse（parser与request_id），GET /jobs/{id}，POST /bases/{id}/search，GET /documents/{id}/chunks，GET /chunks/{id}，GET /documents/{id}/original。

上传支持PDF/MD/TXT；默认上限50MiB、PDF300页、解析300秒。上传返回202与document/job/duplicate，不把上传成功当可检索完成。搜索仅FTS5 lexical，并返回明确mode。URL只接受受控ID；不接受文件路径、SQL、shell、任意URL或模型名称。

KnowledgeService在API lifespan持有独立OS lock和owner session；构造函数、Solver Worker均不启动队列。每次claim短事务；PDF解析在受控子进程，子进程只写该job的staging JSON，不打开数据库。父进程校验结果并凭owner CAS发布；重启重领前会失效旧owner，超时/失败可见。停止服务终止并回收子进程、把未完成工作重新入队；不会自动启动GREAT。

## 解析与切片

默认无模型pdfplumber基线自动提取文字、页尺寸、原生word/块坐标、可检测表格与图形；章节/阅读顺序是启发式，必须保存warning，不使用Tao论文手工profile。扫描/近空PDF明确提示需要OCR，不发布空索引。公式、表格和图片数值默认未验收。

Docling可选adapter只使用用户显式配置的本地artifacts；依赖或模型不全返回明确失败。保留原生结构化JSON、provenance多页bbox、table结构，不只存Markdown。本批不下载大型模型，也不宣称Docling实文解析已经验收。

文本按章节和段落组合，目标700估算token，完整短段重叠上限100；表格/公式原子块关联邻近解释。page_index从0开始，page_label独立；bbox为PDF points左上原点、x右/y下；所有值有边界/有限数校验。指纹包括blob hash、parser/config版本及chunker版本。文献内容是数据，不能改变工具权限。

## UI与验收

现有顶栏增加独立知识库管理Drawer：建库、上传、状态/失败/质量提示、文档停用/重解析、检索证据、查看切片和PDF页定位。不要显示“向量检索”或“已接入论文问答”；当前聊天能力标记保持诚实。

测试覆盖迁移/FTS、内容hash去重、同名不同文件、重启及迟到结果、失败保留旧版、停用与跨库过滤、乱码/空文件/扫描/超限、恶意文件名和路径穿越、FTS元字符、坐标和质量标记、上传与检索界面。最后用用户Tao PDF通过真实API在隔离DB导入和检索，发布独立验收记录，不修改生产data/great_agent.db。
