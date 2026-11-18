# 统一对话与科研知识库：GitHub 调研依据

调研日期：2026-11-02。范围：仓库主分支、官方文档与本机已锁定的 AgentScope Python 2.0.11 源码。未安装候选组件、上传论文或进行效果/性能跑分。用户尚未确定资料规模和格式；个人本机、数百篇中英混合资料及部分扫描件只是设计假设。

## 1. 产品级参考

选择依据为可追踪的入库/检索/引用实现、后台生命周期、部署复杂度及与当前技术栈的适配，未以 star 数代替质量判断。

| 项目 | 核对到的具体实现 | 建议借鉴 | 不应推断的能力 |
| --- | --- | --- | --- |
| [Kotaemon](https://github.com/Cinnamon/kotaemon) | [文件流水线](https://github.com/Cinnamon/kotaemon/blob/main/libs/ktem/ktem/index/file/pipelines.py) 默认 hybrid、支持 rerank/文件过滤、page_label 同页表格关联；保存原文件 hash 与分块关系 | 论文问答、证据面板、PDF 页引用和解析结果检查 | 强制 reindex 删除旧索引，不是不可变版本切换；[历史感知检索调用](https://github.com/Cinnamon/kotaemon/blob/main/libs/ktem/ktem/reasoning/simple.py) 有注释禁用，不能照抄为完整混合意图路由 |
| [Onyx](https://github.com/onyx-dot-app/onyx) | [聊天上下文设计](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/chat/README.md) 区分临时附件、项目资料和检索结果；证据向模型提供简化编号；[citation processor](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/chat/citation_processor.py) 处理流式引用映射 | 一个输入框、按需工具调用、资料作用域及服务端证据编号 | 不能因此认为其具备本项目 revision/CAS/科研执行审批；当前[compose](https://github.com/onyx-dot-app/onyx/blob/main/deployment/docker_compose/docker-compose.yml) 包含多项后端服务，整体接入成本高 |
| [Dify](https://github.com/langgenius/dify) | [文档模型](https://github.com/langgenius/dify/blob/main/api/models/dataset.py) 保存解析、清理、切块、完成、错误、启用/归档等状态；[检索服务](https://github.com/langgenius/dify/blob/main/api/core/rag/datasource/retrieval_service.py) 提供多种检索与过滤 | 上传后状态、失败重试、文档启停、检索调试 | 文档状态和重索引不自动等于论文不可变版本链；无需为本项目同时引入另一套应用编排平台 |
| [RAGFlow](https://github.com/infiniflow/ragflow) | 当前 main 的 [PDF parser](https://github.com/infiniflow/ragflow/blob/main/internal/deepdoc/parser/pdf/parser.go) 有文字质量判断、OCR、布局/表格处理；[ingestion executor](https://github.com/infiniflow/ragflow/blob/main/internal/ingestion/task/pipeline_executor.go) 有入库完成和禁用文档可见性逻辑 | 复杂论文处理、逐阶段入库、可检索状态管理 | 当前 main 已采用 Go internal 路径，不能引用旧 Python 路径冒充现状；[REST API](https://github.com/infiniflow/ragflow/blob/main/docs/references/http_api_reference.md) 有版本迁移，实际服务接入须锁 release |

截至本次查看，四个仓库未显示归档，但更新频率不同，Kotaemon 的可见更新较少；GitHub 部分历史有缓存，本报告不声称掌握当天绝对最后提交。RAGFlow 当前部署建议起步 4 核/16GB/50GB，模型另计；完整平台并非当前 SQLite 应用的轻量依赖。[部署依据](https://github.com/infiniflow/ragflow#-local-deployment)

许可证同样不同：RAGFlow/Kotaemon 仓库标 Apache-2.0；[Dify LICENSE](https://github.com/langgenius/dify/blob/main/LICENSE) 有附加条件；[Onyx LICENSE](https://github.com/onyx-dot-app/onyx/blob/main/LICENSE) 的普通部分与 ee 目录需分别核对。推荐借鉴架构并使用官方组件接口，实际复制代码前按锁定版本核对许可。

## 2. 解析与检索组件

| 组件 | 核对结果 | 选型判断 |
| --- | --- | --- |
| [Docling](https://github.com/docling-project/docling) | 支持版面、阅读顺序、表格、OCR及公式表示；[DoclingDocument](https://docling-project.github.io/docling/concepts/docling_document/) 保留层次与可用坐标；[安装](https://docling-project.github.io/docling/getting_started/installation/) 支持 Windows/CPU。MIT 代码，模型许可另核对 | 默认解析候选。公式增强需显式配置，CPU 可运行不等于批量 OCR 低延迟；公式识别不等于 GNSS 推导正确。题名/作者/DOI等自动抽取不能当成已可靠完成，须允许人工核对 |
| [MinerU](https://github.com/opendatalab/MinerU) | 当前 [tiers](https://github.com/opendatalab/MinerU/blob/master/docs/en/usage/tiers.md) 按能力分层，部分可 CPU，VLM 部署另计；[许可](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md) 为附加条款版本 | 复杂样本对照候选；按实际版本核对资源和许可，不默认再维护第二套解析模型 |
| [PyMuPDF4LLM](https://github.com/pymupdf/pymupdf4llm) / [Marker](https://github.com/datalab-to/marker) | 前者 AGPL/商业双许可；后者代码和模型权重许可不同，运行依赖也需按当前版本核对 | 备选适配器，不以“轻量/开源”概括全部部署和权重条件 |
| [qdrant-client](https://github.com/qdrant/qdrant-client) | 支持 path 持久化 local 模式；[源码](https://github.com/qdrant/qdrant-client/blob/master/qdrant_client/local/qdrant_local.py) 有目录排他锁、精确搜索和大集合提醒，20,000 points 是提醒阈值而非论文数硬限制 | 本机原型可用，必须单一进程拥有目录。按分块数、内存、延迟升级 Qdrant server；[server 混合查询](https://qdrant.tech/documentation/search/hybrid-queries/) 支持组合与融合 |
| [SQLite FTS5](https://sqlite.org/fts5.html) | 支持全文索引、BM25及 tokenizer；trigram 对短词有局限 | 用已有 SQLite 提供词法召回；中文预分词，保留 PPP/RTK/AR/L1 等原词，不只依赖 trigram |
| [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small) | 多语种、384维，需 query:/passage: 前缀，MIT | CPU 起步候选，GNSS效果尚未评测；不能混用不同 embedding revision/维度 |
| [bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3) | 多语种模型，Apache-2.0 | 可选重排候选，先评测再开启，不承诺未知硬件上的耗时 |

[LlamaIndex](https://github.com/run-llama/llama_index) 的 [BM25](https://developers.llamaindex.ai/python/framework/integrations/retrievers/bm25_retriever/) 与 [引用引擎](https://developers.llamaindex.ai/python/examples/query_engine/citation_query_engine/) 是可参考实现；[Haystack](https://github.com/deepset-ai/haystack) 的 [DocumentJoiner](https://docs.haystack.deepset.ai/docs/documentjoiner) 提供融合模式。当前已有 AgentScope，不默认再引入完整第二套编排框架。LlamaIndex 主仓 README 还说明公司重心调整，采用时应锁版本与必要模块，不能以旧教程判断当前维护安排。

## 3. AgentScope 2.0.11 的实际边界

本机公开主 Agent 名为 `Agent`，不是旧教程的 `ReActAgent`；技能采用 `Toolkit(skills_or_loaders=...)`、`LocalSkillLoader` 与 SkillViewer。技能查看只提供正文，不会自动执行目录中的脚本。[Agent](https://github.com/agentscope-ai/agentscope/blob/v2.0.11/src/agentscope/agent/_agent.py)、[Toolkit](https://github.com/agentscope-ai/agentscope/blob/v2.0.11/src/agentscope/tool/_toolkit.py)、[loader](https://github.com/agentscope-ai/agentscope/blob/v2.0.11/src/agentscope/skill/_local_loader.py)

原生 [KnowledgeBase](https://github.com/agentscope-ai/agentscope/blob/v2.0.11/src/agentscope/rag/_knowledge.py) 与 Parser/Chunker/VectorStore 已有基础能力；[RAGMiddleware](https://github.com/agentscope-ai/agentscope/blob/v2.0.11/src/agentscope/middleware/_rag.py) 有 agentic/static 模式、可选 LLM rerank 和来源编号。其 dense 搜索、来源编号不等于 GNSS 混合召回及最终引用页/段核验；原生 PDFParser 的文本抽取也不能代替 OCR。

本机 app 层还存在知识库服务、lease索引Worker、blob store与 `AsyncSQLAlchemyStorage`。实施前须核对公开接口和现有仓储适配，不能把私有 `_service` 类当成跨版本稳定合同，也不能直接将另一套数据库元数据混入现有 Base。

GLM 现有单次 JSON 提案成功，不代表完整工具闭环已经验证。当前默认 OpenAI formatter 会略过 ThinkingBlock；启用 reasoning 的多轮协议需验证。现有 `thinking=disabled` 可作为初始兼容配置。[GLM-4.7](https://docs.z.ai/guides/llm/glm-4.7)、[thinking 协议](https://docs.z.ai/guides/capabilities/thinking-mode)

SDK 默认重试、结构化输出补救、LLM rerank及上下文压缩可能增加请求。新 Agent run 必须统计全回合实际物理调用、token、工具次数和deadline；不能只设置 max_iters，或让 proposal 工具内部再次调用旧的模型生成路径。

## 4. 本项目结论

主推荐：现有 FastAPI/SQLite/Ant Design 内增量建设 `ConversationService + AgentScope Agent/Toolkit + KnowledgeService`。以 Docling 做论文结构解析，原生RAG提供向量基础，FTS5补词法召回；按评测决定重排和存储部署。统一管理全局规范、经审核技能和资料，但分别加载与授权。完整设计见 [方案](../superpowers/specs/2026-11-02-conversation-knowledge-design.md)。以上为调研与设计，没有新增运行能力或实测效果承诺。
