# 本地论文知识库的GitHub案例核查

核查日期：2026-11-12。仅使用项目官方源码与文档。发布、测试和持续维护证明项目已有实际实现，不证明GNSS样本文献的解析准确率。

| 案例 | 可借鉴实现 | 在GREAT_Agent中的选择 |
|---|---|---|
| [Docling](https://github.com/docling-project/docling) | DocumentConverter、结构化JSON、page/bbox provenance、表格cell结构 | 独立parser adapter，保留结构和坐标；需要本地模型时显式配置，不在上传时偷偷下载 |
| [kotaemon](https://github.com/Cinnamon/kotaemon) | SQLAlchemy文件关系、检索范围、来源预览 | 保留现有SQLite和React，重写固定schema；不直接复用会丢失bbox的loader |
| [paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) | 原件checksum、派生归档、版本、异步入库状态和失败可见性 | 借鉴设计，独立实现；不复制GPL源码或引入其服务体系 |
| [RAGFlow](https://github.com/infiniflow/ragflow) | 分页任务、配置摘要、多页位置契约 | 参考数据契约，不整体迁入；核查分支和版本后再看实现 |

## 实际核对的源码

- [Docling converter](https://github.com/docling-project/docling/blob/main/docling/document_converter.py)：本地文件转换、页数与文件大小限制、结构化document输出。
- [Docling provenance](https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/common/reference.py)：page_no、bbox、charspan。
- [Docling表格结构](https://github.com/docling-project/docling-core/blob/main/docling_core/types/doc/items/table/table_data.py)：行列、span和header属性。
- [Docling离线配置](https://docling-project.github.io/docling/usage/advanced_options/)：本地模型artifact路径。代码与模型权重许可需分别处理。
- [kotaemon文件入库](https://github.com/Cinnamon/kotaemon/blob/main/libs/ktem/ktem/index/file/pipelines.py)：存储以hash为基础，但已存在文件判断还涉及filename；本项目必须自行落实数据库内容hash唯一性。
- [kotaemon Docling loader](https://github.com/Cinnamon/kotaemon/blob/main/libs/kotaemon/kotaemon/loaders/docling_loader.py)：扁平化页文本/表格Markdown不满足本项目保留原生bbox的要求。
- [paperless consumer](https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/consumer.py)、[models](https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/models.py)、[consumer测试](https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/tests/test_consumer.py)：参考原件/派生件身份、重复输入、失败与任务状态。
- [RAGFlow v0.27.3任务](https://github.com/infiniflow/ragflow/blob/v0.27.3/api/db/services/task_service.py)、[位置](https://github.com/infiniflow/ragflow/blob/v0.27.3/rag/nlp/__init__.py)：固定历史版本阅读；不将历史Python文件误称当前主线代码。

## 工程结论

第一批建立真正的SQLite知识schema、不可变内容寻址原件、持久化入库任务、版本化page/element/chunk、FTS5、原文回查和管理入口。采用现有Python包可运行的无模型解析基线，显式提示公式/表格/复杂版面未验证；Docling作为可选本地adapter，配置与依赖不足时明确失败，不自动回退为“高质量解析成功”。

向量检索、自动问答、重排与公式/图形数值理解作为独立下一批，不冒用词法检索结果宣称完整RAG已验收。也不将既有单论文手工profile硬编码进通用上传接口。
