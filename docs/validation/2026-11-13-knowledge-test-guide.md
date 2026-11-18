# 知识库内容、业务操作与检索效果测试指南

日期：2026-11-13。本指南基于当前实际API。正式服务尚未切换到本批代码；聊天的knowledge_search/paper_analysis_ready仍关闭。

建议分开验收：数据库内容正确 → 业务操作正确 → 检索找到充分证据 → 大模型根据证据回答正确。当前前三项可测试，其中第三项为词法检索；向量与大模型问答尚未接入。

## 1. 先运行现有隔离API验收

在PowerShell中执行：

```powershell
cd F:\GREAT_Agent
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe .codex_work/knowledge-ingestion-20261112/api_acceptance.py
```

脚本使用产品API与真实解析子进程，每次在`output/knowledge/tao-2025-product-acceptance/<随机编号>/`建立新数据库。它不修改正式数据库，不启动GREAT，不调用大模型。依赖用户此前给定的Zotero PDF及已有临时前端构建目录；当前工作区两者均存在。

检查项包括：上传202、解析任务结束、同内容改名去重、原件hash一致、词法查询、Docling未配置时失败保留旧版、停用后不可检索/读取、重新启用、条款页排除及staging清理。它会故意产生一次失败重解析，该失败是测试场景。

输出JSON的checks均为true、脚本exit0才算该次API闭环通过。完整证据为新目录内`api-acceptance.json`及根目录`api-latest.json`。这不是浏览器或大模型问答验收。

如果清理过`tmp/knowledge-ingestion-dist`，先执行隔离构建：

```powershell
npm --prefix frontend run build -- --outDir ../tmp/knowledge-ingestion-dist
```

## 2. 查看数据库里究竟存了什么

目前已保存的样本库：

`F:\GREAT_Agent\output\knowledge\tao-2025-product-acceptance\a528f6c4\state.db`

用SQLite客户端以只读模式打开。SQL适合核查内容；业务API负责维护版本、状态与索引。可执行以下只读查询：

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;

SELECT id, name FROM knowledge_bases;

SELECT id, filename, enabled, status, active_version_id
FROM knowledge_documents;

SELECT status, error_code, error_message
FROM knowledge_jobs ORDER BY created_at;

SELECT version_id, COUNT(*) AS page_count
FROM knowledge_pages GROUP BY version_id;

SELECT version_id, COUNT(*) AS chunk_count
FROM knowledge_chunks GROUP BY version_id;

SELECT page_index + 1 AS physical_page, page_label, role,
       substr(raw_text, 1, 160) AS preview
FROM knowledge_pages ORDER BY version_id, page_index;

SELECT ordinal, kind, section, substr(text, 1, 300) AS preview,
       locations_json, quality_flags_json
FROM knowledge_chunks
WHERE version_id = (
  SELECT active_version_id FROM knowledge_documents
  ORDER BY created_at LIMIT 1
)
ORDER BY ordinal LIMIT 20;
```

本样本的核对点：integrity_check为ok，foreign_key_check无记录；1份文档、16物理页、当前版本91片；第16页role=publisher_terms且原始文字保留，但不存在引用该页的科研切片。91是当前parser/config的结果，后续改变切片规则应重新记录版本，不能只凭数量判断质量。

人工抽查至少包含：摘要、Dataset、式(3)–(8)、Table2、Table3、Table4、Fig8和跨页段落。逐片检查句子顺序、表头/单位、公式定义、页码与bbox。质量标记存在不等于公式和表格已正确转写。

## 3. 当前API支持哪些增删改查

以下路径统一加`/api/v1/knowledge`前缀。需要运行已加载新代码且完成迁移的API；当前正式入口未切换，不能假定8765已经支持这些路径。

| 操作 | 接口与输入 | 应检查的结果 |
|---|---|---|
| 新建库 | POST `/bases`，`{"name":"GNSS测试库"}` | 返回库ID；新库无文档 |
| 增加文献 | POST `/bases/{id}/documents?filename=paper.pdf`，body为原始文件bytes | 202，返回document/job；等待任务结束后才可检索 |
| 内容去重 | 同一文件改名再上传 | duplicate=true，复用同文档；同名但不同内容应新建文档 |
| 查询 | GET `/bases/{id}/documents`、`/documents/{id}`、`/jobs/{id}` | 文件、active版本、最新任务和失败原因相互一致 |
| 查切片 | GET `/documents/{id}/chunks?offset=0&limit=20`、`/chunks/{id}` | text/section/版本/页码/位置/质量可回查 |
| 查原件 | GET `/documents/{id}/original` | 返回字节hash与上传文件一致 |
| 停用 | PATCH `/documents/{id}`，`{"enabled":false}` | 搜索立即排除；原件和chunk读取404；原文件和历史数据仍保留 |
| 重新启用 | 同上，enabled=true | 此前版本恢复可检索，不需重新上传 |
| 重解析 | POST `/documents/{id}/reparse`，`{"parser":"pdfplumber","request_id":"UUID"}` | 新版本成功才切active；失败保留旧版；同request_id幂等 |
| 修改标题/库名/正文 | 尚无接口 | 不能当作已实现CRUD验收 |
| 永久删除/回收站 | 尚无接口 | 停用不等于删除，硬删除及恢复需后续实现 |

重解析不会修改原件。正文改变应作为不同内容上传；当前还没有将修改后PDF替换为同一document新原文版本的业务接口。

现有隔离回归可以运行：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\.venv\Scripts\python.exe -m pytest -p pytest_asyncio.plugin backend/tests/test_knowledge_repository.py backend/tests/test_knowledge_migration.py backend/tests/test_knowledge_parsing.py backend/tests/test_knowledge_api.py -q
```

## 4. 怎样测当前检索效果

调用：

```http
POST /api/v1/knowledge/bases/{base_id}/search
Content-Type: application/json

{"query":"Table 3 CG Float Fixed convergence","limit":5}
```

必须保存实际返回的chunk_id、version_id、正文、位置和排序，检查片段是否充分回答问题。仅“有结果”无法说明相关，命中正确论文或正确页也不能自动判为证据充分。

已有13题位于`output/knowledge/tao-2025-acceptance/acceptance-questions.json`。其中11题为正文内容题；另有出版元信息题和无证据题。旧英文关键词有人工选词及已知结果提示，不能直接作为公平的自然问句检索benchmark。

2026-11-13对a528f6c4验收库作了一次只读探针，另外给中文问题配了不增加已知答案提示的对应英文问句：

| 输入 | Page-hit@1 | Page-hit@3 | Page-hit@5 |
|---|---:|---:|---:|
| 中文原问题 | 1/11 | 5/11 | 5/11 |
| 对应英文问句 | 10/11 | 11/11 | 11/11 |

原始结果在`output/knowledge/tao-2025-product-acceptance/retrieval-baseline-20261113.json`，paired_en为对应英文问句，en保留旧人工关键词诊断对照。数据库以mode=ro打开，未修改文档、任务或索引，也未调用模型。

**这些数字仅指前K片中至少一片包含任一预期页位置。** 单篇论文、11题、没有人工标注gold chunk集合；不能称为Recall@K、问答准确率或模型能力比较。跨页问题只命中一页也可能缺半段证据。

下一步先人工为每题标注“必须回答的事实、支持事实的原文、相关chunk/页/区域集合”，再计算：

- Evidence Hit@K：前K片是否出现足以支持答案的证据；多事实题另外记录事实覆盖率。
- Recall@K：召回的相关片段数 ÷ 人工标注的全部相关片段数；没有相关片段全集就不报该数。
- MRR：每题第一条充分相关证据排名的倒数，再取均值。
- 耗时：记录P50/P95，并同时记录文档范围、parser/chunker配置和topK。

比较切片版本时，chunk ID会变化，应保留按原文段落/区域标注的gold证据并映射到各版本。建议扩充到30–50题、覆盖多篇相近论文，另留未参与调参的固定验收集；多种有效证据不能只标一片就把其他正确结果算错。

## 5. 大模型接入之后怎么验收

当前还没有向量检索或聊天search_knowledge/read_evidence；此时直接在聊天窗口提问不能测试知识库RAG效果。

接入后，对同一题保存完整链路：用户问题 → 实际检索查询/过滤范围 → topK候选 → 最终选用证据 → 模型回答及引用。分开判断：

1. 证据没召回：检查解析、切片、术语/查询改写、embedding与排序。
2. 证据已召回但回答错误：检查模型、提示词、上下文组织与证据使用。
3. 答案碰巧正确但引用不支持：仍判证据使用失败；不能只看答案文字。

使用同一模型及固定生成参数，对比“无检索上下文”与“带检索证据”的回答，防止把模型记忆当成RAG成功。模型作为自动裁判只作辅助；公式、单位、统计口径最终由领域人工核对。

验收题至少包含：

- 普通事实：参考站与rover各做什么；数据年份和范围。
- 表格口径：Table3各列、单位及均值；Table4括号数字含义。
- 公式上下文：式(3)(4)符号与整数性质，公式和定义应一起核对。
- 跨页/跨文献：答案所需多段证据是否齐全。
- 无证据：要求本文未报告的Galileo四频欧洲高动态数值，应说明资料不足，不虚构数值或引用。
- 库范围：切换知识库、限制document_ids、停用文献后，不应继续引用被排除资料。

回答正确性、事实覆盖率、引用支持率、无证据拒答、耗时分别记录，不合并成一个模糊总分。本轮没有运行这部分大模型评估。
