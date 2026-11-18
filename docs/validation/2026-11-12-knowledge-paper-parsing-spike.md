# 首份论文的本地解析、切片与检索样机

日期：2026-11-12。范围：用户指定论文的一次本地验收样机。**已复制原文、解析、生成候选切片与本地词法索引；尚未完成产品上传接口、向量检索及问答接入。**

## 1. 原文与实际存储位置

原文：`D:\Zotero\zotero_data\storage\E8IWL37J\Tao 等 - 2025 - Real-time estimation of multi-frequency phase obse.pdf`。

- 题名：Real-time estimation of multi-frequency phase observable-specific bias for the BDS3 PPP-B2b service。
- 作者：Jun Tao、Gaojian Zhang、Guo Chen、Yihao Jiang、Qile Zhao。
- 期刊：GPS Solutions，2025，29:19；首页可视信息显示在线日期为2024-11-14。出版年、在线年、实验年分别保存，不能用PDF创建日期或参考文献年份替代。
- DOI：10.1007/s10291-024-01776-7。
- 文件大小：5,076,994字节。
- SHA256：`23dbbbcb5681afb6e00508578a16785f1679a01e27c07d39838569eb9aa76702`。
- PDF有16个物理页，论文为1–15页；第16页是出版商条款。条款和重复页眉页脚从默认科研检索中排除，原文件与逐页记录完整保留。

**本次真正已建立的样机目录：**

```text
F:\GREAT_Agent\output\knowledge\tao-2025-acceptance\
  original.pdf                         原文副本，hash与Zotero原件相同
  manifest.json                        文献信息、版本、解析器与指纹、限制
  pages.json                           16页原始文字与带坐标word记录
  blocks.json                          阅读顺序、结构块、来源word IDs及bbox
  reading-order.md                     可直接审阅的抽取文字与区域图
  coverage.json                        实际输出word ID覆盖、遗漏及重复检查
  regions\                            4张表、11个公式、13幅图的原页区域
  chunks-paragraph_baseline-*.jsonl     3档普通段落拼接对照
  chunks-structured-*.jsonl             3档结构化切片对照
  sample.sqlite3                       独立SQLite元数据与FTS5词法索引
  acceptance-questions.json             13道人工阅读后整理的验收题
  retrieval-evaluation.json             检索结果、页定位指标及完整性检查
```

这些资料未发送给云模型或外部存储，Zotero原件未改写。本次不修改`data\great_agent.db`，不重启应用，也不改变GREAT求解任务。

## 2. 当前产品与正式知识库的边界

当前应用的数据库配置为`F:\GREAT_Agent\data\great_agent.db`，但其中的产品知识库功能尚未实现。`runtime/conversation_agent.py`仍返回`knowledge_search=False`和`paper_analysis_ready=False`；API尚无知识上传路由，迁移中也没有knowledge/document/chunk/citation表。

因此，本次可以称为“论文已导入本地解析验收样机”，不能称为“已上传到GREAT_Agent正式知识库”或“聊天已经能引用此文回答”。

沿用[已批准知识库设计](../superpowers/specs/2026-11-02-conversation-knowledge-design.md)，正式接入时建议采用以下布局；这是工程建议，**尚未创建对应产品schema或配置项**：

```text
F:\GREAT_Agent\data\great_agent.db
  knowledge_base / document / document_version / ingestion_job
  chunk / citation / active_generation / FTS5

F:\GREAT_Agent\data\knowledge\
  documents\<document_id>\<version_id>\
    original.pdf
    manifest.json
    parsed\pages.jsonl
    parsed\elements.jsonl
    chunks\<chunk_fingerprint>.jsonl
    previews\...
  vectors\qdrant-local\
  staging\<job_id>\
```

SQLite管理有效状态和版本；文件目录保存不可变原文与派生产物；向量索引可重建。知识库可以包含多个逻辑库，不需要每个实验复制一份论文。实验或对话只绑定允许检索的库/文档范围。

索引目录由单一KnowledgeService所有，受控解析子进程不直接打开向量目录，GREAT Solver Worker不承担论文解析。先在staging构建并校验，再切换active版本；失败保留旧可用版本。最终向量实现及embedding型号仍需容量与召回试验确定，本次没有安装或测试Qdrant。

当前`AppSettings`拒绝未知配置；正式开发时应一起增加并校验`knowledge_root`等字段，不能只往`local.toml`填目录就宣称启用。

## 3. 这篇论文如何解析

本轮使用现有bundled runtime中的pdfplumber和pypdfium2；确切版本记录在`manifest.json`。未安装项目依赖，未运行Docling、OCR、embedding或聊天模型。

这是一篇有文本层的双栏论文，普通整页逐行抽取会混合左右栏；有些页顶部/底部又有跨栏图表。为检验工程数据契约，本轮先目视复核页面，使用**针对这一篇论文的手工版面profile**恢复阅读顺序。不能据此宣称自动解析任意PDF已通过。

- 保留所有物理页的原始文字和word坐标。
- 正文按单栏/双栏与跨栏图表区域排序，保留章节与段落。
- 标记4张表、11个公式、13幅图，保存完整区域图像；特殊块bbox覆盖图表/公式主体，另存文字bbox。
- 参考文献和作者简介保留为back matter，默认不进入科研正文切片。
- 每个块保存实际来源word IDs，检查被选为正文的词是否真正出现在输出块中；这不等价于公式语义正确或图片内文字全部识别。

公式上下标、波浪号、矩阵和分式在扁平文本中存在乱序与残留。除公式块外，公式密集的第3–4页普通段落也标记`needs_formula_review`。表格均标记`needs_table_review`：例如Table2的`m/√hour`在原始抽取中错行，不能把抽出非空文字当作单位解析正确。

图形目前保留区域、图号、可提取图注与标签，**不自动提取图内曲线/柱状图数值**。Fig.8的精确比例需要看面板；正文概述不能替代图中数字。

## 4. 切片研究方法与本次结果

不先冻结“每500字切一刀”。先固定内容类型与来源定位，再比较长度、上下文与召回。

两组对照均从同一版面profile的结构块生成，因此普通段落对照也已经享受了人工版面划分的收益；它不是完全未经处理的原始固定字数baseline。

| 策略 | 目标长度估算 | 切片数 | 本轮英文查询Page-hit@5 |
|---|---:|---:|---:|
| 普通段落拼接 | 400 | 30 | 11/11 |
| 普通段落拼接 | 700 | 16 | 11/11 |
| 普通段落拼接 | 1000 | 11 | 11/11 |
| 章节＋结构元素 | 400 | 68 | 11/11 |
| 章节＋结构元素 | 700 | 65 | 11/11 |
| 章节＋结构元素 | 1000 | 63 | 11/11 |

长度使用可复现的正则计数估算，**不是任何embedding/聊天模型的真实token数**。原子表格及公式上下文可超过目标长度。结构化策略不会为填满长度跨越章节；表格/图形作为完整元素；公式附带邻接解释段落；普通文字以完整短段落重叠，估算上限100。

每个chunk记录`document_id/version_id/chunk_id/source_sha256/text_sha256/section/parent_section_id/block_ids/locations/quality flags`。`locations`可包含多页、多区域；物理页索引从0开始，另存物理页号和印刷页标签。矩形采用PDF points、左上原点、x向右、y向下。

本次可先用`chunks-structured-700.jsonl`的65片作为人工审阅工作集，**没有证据认定700是最佳长度**。所有候选Page-hit@5相同，且单篇论文只有少数页面；长片也更容易碰到正确页。要决定正式配置，必须增加真实模型token、证据完整性、无关内容比例及多论文检索数据。

建议正式实验分三步：

1. **结构质量**：检查公式与定义是否分离、表格标题/双层表头/单位/脚注是否齐全、跨页段落是否能扩展；保留父章节和相邻块供`read_evidence`读取。
2. **检索质量**：对同一组题比较词法、向量和融合检索，调节长度、重叠及父块扩展；按问题检查是否召回真正支持答案的证据，不能只看页码。
3. **回答与引用**：生成答案后检查引用属于本次可访问文档版本、页面和位置可回查；同时检查论据是否真的支持结论。无证据问题应明确无法从文献回答。

正式引用ID由后端根据本轮证据分配，不允许模型自造。论文中的提示性文字仅作为资料内容，不得成为Agent工具调用或执行授权。

## 5. 验收题与检索限制

`acceptance-questions.json`有13题：11题用于内容页定位；1题检查出版/实验年份与条款页；1题是文献没有提供的Galileo四频欧洲高动态数值，供后续拒绝虚构验收。

题目覆盖：PPP-B2b消息的更新间隔/有效期、19参考站/28 rover、式(3)–(8)、Table2处理策略、Fig.8图内数值、精度统计范围、Table3均值与Fig.13分位统计、Table4广播延迟、GPS L5缺失、年份与无证据问题。

本轮使用SQLite FTS5 BM25、查询词OR、前5条结果。英文查询由人工编写，**没有自动中英查询改写**。英文11题全部命中预期页；结构化三组首条均10/11，直接中文查询前5条均9/11。中文查询的少量数字、英文缩写也可能带来偶然页命中，因此这个数字不是中文理解能力指标。

没有计算证据支持率、回答准确率、拒答准确率或中文语义召回率，也没有完成向量检索验收。公式、表格和图像题即使命中正确页面，仍需核对区域内容。样机速度不代表生产并发或容量表现。

两处特别有价值的科学口径验收：

- **Table3**：26.1→3.4分钟是CG模式East列；不能因为摘要使用horizontal措辞，就把原表列标签改为二维水平。Table3的2小时重收敛试验，与每天2–24小时收敛后精度统计不同。
- **Table4**：括号数字是平均接收延迟，单位分钟；90秒广播的0.8不是STD。必须把caption与表头一起作为证据。

## 6. 实际检查与下一步

最新`retrieval-evaluation.json`中的实际检查通过：原文副本hash一致；16物理页/15论文页；15论文页都有可提取文字；按实际块成员ID核对的正文词无遗漏或重复；bbox未越页；28个特殊区域文件存在；第16页未进入科研切片。

这些完整性检查不证明图内所有文字、公式含义、表格行列关系或科研结论已正确识别。

下一开发批次建议按顺序接入：

1. `knowledge_root`配置、文档/版本/入库任务schema、原文hash去重和异步上传状态，先支持此PDF回放；用隔离库完成迁移与失败恢复验收。
2. 采用自动结构解析候选与本次人工复核结果对照，保留页/区域定位；解析、表格/公式质量门槛未过时保留warning，不发布为无条件可用。
3. 本地embedding＋FTS融合、库/文档范围过滤、来源读取接口；使用本题集扩充多论文问题后选定切片参数。
4. 对话注册`search_knowledge/read_evidence`，前端展示引用、页码和原页定位，最后验收图表/公式回答与无证据问题。

解析和索引默认本地。若后续仍使用目前配置的云端GLM生成回答，问题和检索片段会发给该模型；这与本轮完全本地样机的处理范围不同，需要在产品界面说明。要求全离线时还需本地chat模型，不能只把向量库放在F盘就称为全离线。

## 7. 复现

解析脚本：`.codex_work/knowledge-acceptance-20261112/parse_sample.py`。评价脚本：同目录`evaluate_sample.py`。两者只读用户指定原件并重建本次独立样机目录的派生文件。

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'F:\CodexStorage\cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .codex_work/knowledge-acceptance-20261112/parse_sample.py
& 'F:\CodexStorage\cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .codex_work/knowledge-acceptance-20261112/evaluate_sample.py
```

本报告没有把样机脚本注册为产品入口。原始日志、JSON与区域文件在上文目录，尚无产品部署或生产数据迁移。
