# 文献知识库与引用对话集成验收

最终收尾：2026-11-14（Asia/Shanghai）；本文件及实现计划沿用11月13日设计批次名称。

实现依据：[已确认设计](../superpowers/specs/2026-11-13-knowledge-tab-and-document-chat-design.md)、[开源案例与源码分析](../research/2026-11-13-knowledge-chat-cases.md)。参考 Docling 的结构与页坐标、kotaemon 的检索证据展示、Dify 的文档版本和召回组织方式；本项目沿用现有 FastAPI/React/AgentScope，不另嵌一套产品。

## 已接通的产品链路

顶部“实验工作台 / 文献知识库”工作区切换；知识库内“文献对话 / 文档与解析”。上传 PDF/MD/TXT 后查询真实解析任务、页数、切片和质量警告。选中文献或全部启用文献建立知识会话，调用服务器配置的 GLM。回答引用绑定不可变版本、实际摘录及其 SHA256；点击后核验原件和权限，再用自托管 PDF.js 展示物理页与区域高亮。刷新恢复历史，切 Tab 保留草稿。

解析、原件、FTS 与 ONNX embedding、Qdrant 向量均在本机。云端回答模型接收当前问题、经范围过滤的历史和选取的证据，密钥只在服务端。浏览器不能传任意模型地址或密钥。知识引擎只有 `search_knowledge` 和 `read_evidence`，旧实验聊天入口拒绝知识会话。

## 存储与配置

SQLite 新增迁移 `0007`：知识会话绑定、检索轨迹、消息引用、索引 generation、embedding 子片段五表。SQLite 保留真实正文与出处，Qdrant 是可重建索引。重解析不改旧引用；停用、范围撤销、缺失或损坏的原件会显示来源不可用。

默认路径由 `config/local.toml` 的 `database_path` 与 `[knowledge].root` 决定；未指定 root 时为 `F:\GREAT_Agent\data\knowledge`。本次验收使用 `output/knowledge/kchat-live-20261113/state.db` 和相邻 `knowledge/`，不迁移正式数据库，不替换 `frontend/dist`。

本地 embedding：`multilingual-e5-small`，固定 revision `6a0d452a575215f80b8f66276dd4ee5d504942c6`，384维，ONNX CPU 单线程，实际 tokenizer 分段、attention-mask mean pooling、L2归一化。模型和 tokenizer 位于 `local/models/`，下载时和运行时均核验 SHA256。服务不在上传或启动时联网下载模型。

`config/app.example.toml` 提供 `chat_enabled`、`embedding_enabled`、模型/tokenizer 路径和可用内存阈值。embedding 默认关闭；显式启用后首轮后台建索引，ready 前使用明确标记的关键词降级模式。模型不可用或内存不足仍保留 FTS 检索。聊天凭据仍使用 `GREAT_AGENT_MODEL_API_KEY`。

每轮上限：问题2000字符、历史6000、工具证据12000、整体请求上下文26000字符、输出2048、4次物理模型请求、6次工具调用、90秒。没有模型专用 tokenizer 时 token 数明确标记 estimated，不冒充实际计数。取消、超时、服务关闭和文献撤权先释放运行所有权，迟到结果不能写入成功引用。

## 验证记录

本轮验证结果（正式数据库和运行入口未切换）：

- 新旧 repository 兼容集合63项通过；新增 repository 最终20项通过。
- 配置、0007/历史迁移、runtime schema、CLI/lifespan 兼容集合30项通过。
- Service/API 9项通过；原件损坏、停用和页身份 API 另3项通过。
- 前端 App/知识页面/来源/PDF.js/旧上传 Drawer 定向35项通过；真实 PDF.js6 类型检查、lint、实际 App 隔离构建通过。
- 生产 `frontend/dist/index.html` SHA256 仍为 `CE7CD447347DD54CF97DDC62678BBD1823F0D7BEBAB745D9774283CACB57596B`。

完整回归：后端 **676 passed, 1 skipped**（576.79秒，含此前出现超时的native恢复用例，本轮完整通过）；前端 **246 passed / 18 files**（137.75秒）。完整回归之后的小补丁均有下述定向复测，未把先前全量结果冒充为最后一次代码状态的全量运行。

最终代码集中回归：repository/service/API/安全摘录/engine/retrieval/embedding/实际PDF几何 **113 passed**（41.58秒）。实际Tao表格上下文离线验收另 **1 passed**：模型工具回包里现在同时包含表头、CG_Float/CG_Fixed与26.1/3.4，查询不含参考答案数字。未为该项额外调用云模型。

最后微修验证：Windows模块MIME及wiring **7 passed**；真实计数与原入库repository/API兼容 **61 passed**；前端微修 **79 passed**，类型检查、全src lint、隔离构建均退出0。计数只统计当前已发布版本，未发布为0，失败重解析保留旧计数，不累加历史版本。

最终前端产物：`tmp/knowledge-chat-dist/index.html`指向`assets/index-BQufAwuY.js`和`assets/index-D-FHYj8a.css`，含真实PDF.js懒加载和自托管worker。引用卡片/页码下拉按物理页去重，原始多个bbox保留；手机折叠区不再被flex压为2px。

## 真实模型与可打开的验收入口

最终真实GLM方法问题已完整返回 **HTTP200、answerable=true、3次物理请求、2条可用原文引用**。同request_id重试返回原response且不重复模型请求；GET恢复能找到同一assistant message；原件SHA256仍为`23dbbbcb5681afb6e00508578a16785f1679a01e27c07d39838569eb9aa76702`；引用页带已资格`rotated_media_top_left`矩阵。

- 问题：这篇论文提出了什么方法来增强 BDS3 PPP-B2b 服务？请简要说明，并引用原文证据。
- 文档经新上传API和解析任务导入，16物理页、91通用解析切片；本轮从新知识库`Knowledge chat integration`取证。
- 真实响应与接口验收：`output/knowledge/kchat-live-20261113/live-result.json`；实际供应商答复在同目录`model-responses-836981f0-2865-437e-b33d-086dd37d3a41.json`。
- 集成验收入口：[打开文献知识库](http://127.0.0.1:8877/?workspace=knowledge&knowledge_base=28917aad-2f78-4f62-8620-a0e97f68f614&knowledge_chat=836981f0-2865-437e-b33d-086dd37d3a41)。这是独立测试数据库与新构建，不是正式8765入口。

实际HTTP冒烟 **1 passed**：新首页/JS资源200，服务器模型配置可用，持久引用可用，页矩阵存在，PDF Range请求206且返回原PDF前1024字节；旧全局会话接口拒绝该知识会话。结果：`preview-http-result.json`。该项没有浏览器也没有额外模型请求。

最终真实浏览器 **14项全部通过、errors=[]、脚本退出0**。使用真实运行在8877的新App、已保存GLM回答和原PDF，完成会话恢复、2引用、3栏/桌面无溢出、真实PDF渲染/高亮/摘录、上传解析面板；通过真实文件输入重新上传Tao至独立新库，等待16页/91切片、确认文献可选并实际浏览91个切片；390px手机无横向溢出、折叠入口可见、真实指针能展开。浏览器验收没有发送模型消息。

初期沙箱内Chrome启动超时；在获批的沙箱外执行中已成功运行。真实浏览器曾暴露Windows将`.mjs`返回`text/plain`导致PDF worker拒载，现由app显式登记JavaScript MIME，已有实际HTTP和ASGI回归；目视发现的手机折叠入口被压缩也已修复并复测。一次脚本25秒解析等待不足时，数据库记录任务21.78秒正常完成；现仅该验收步骤等待90秒，产品300秒解析/90秒模型预算均未更改。

浏览器证据位于`output/knowledge/kchat-live-20261113/browser/results.json`，实际截图为`desktop-source.png`、`parsing.png`、`upload-parsing.png`、`mobile.png`。这些不是设计图或fixture截图。32组PDF.js viewport测试仍是另一组独立几何验证。

本地ONNX实测已通过：384维、向量范数0.999999964、106个真实子片段ready；首轮建索引明确`lexical_degraded`，ready后实际`hybrid`。首次建索引和11题诊断共约19.25秒，加载前可用1887MiB，加载后1377MiB（含模型与索引，单次观测，不是峰值内存）。

| 同一篇Tao文献、11道可回答题 | 页命中@1 | @3 | @5 |
|---|---:|---:|---:|
| 原中文 / FTS | 1/11 | 5/11 | 5/11 |
| 原中文 / dense | 2/11 | 3/11 | 5/11 |
| 原中文 / hybrid | 0/11 | 5/11 | 7/11 |
| 等义英文 / hybrid | 8/11 | 11/11 | 11/11 |

原中文hybrid前列排序仍有退化，不应作为中文检索质量达标的依据。等义英文不加入答案提示；这组结果支持在对话中用英文术语辅助检索，但不代表多论文或未见问题的效果。原始诊断在`output/knowledge/kchat-dense-acceptance-20261113/result.json`。

首轮真实 GLM 已调用2次物理请求：模型连续调用3次搜索，前两份工具证据分别4696字符，第三份重复全文使12000字符累计证据预算耗尽，服务安全失败、没有写入成功引用。现已修复：重复来源仅返回已有 S-ID、按完整段落/表行取证，不提高预算。独立审查还发现关闭期间 pending claim 漏取消，现已用真实 SQLite 回归复现并修复。

供应商格式兼容：第三轮provider实际三次HTTP200、最后stop，回答先输出解释，再附唯一合法JSON，旧整串`json.loads`拒收。修复已用保存的三次真实choices离线回放通过；只提取唯一、完整、无重复键和歧义的JSON围栏，围栏外文字不展示，不放宽来源、布尔值和引用编号校验，多对象/多围栏/未知引用继续拒绝。引擎定向31项通过。

表格取证：Table3数字位于ordinal81，曾已召回但被其它证据贪心占满预算挤掉。明确表格查询且首块含数字caption时，先尝试同文档/固定版本/同页的下一个块，最多一块、仍走5200/12000预算；不加入参考答案或固定页码。普通方法/摘要查询排序保持不变。新证据与service定向17项通过。

旋转定位经32组实际PDF校验：Rotate0/90/180/270 × CropBox有无 × 非零/零MediaBox原点 × UserUnit1/2。原算法二次旋转的缺陷已定位；解析子进程将`to_pdf_transform`随不可变版本持久化，前端先映回PDF用户空间，再由PDF.js处理裁剪/旋转/缩放。API不为定位而重新打开PDF解析。旧版本和未实测Docling坐标标为`unverified_top_left`，显示原页但不画未经核验的框；重新解析baseline可补齐。后端新增及解析兼容77项通过、1项环境跳过。

Windows 沙箱会阻塞 asyncio 的 `socketpair/accept`，造成测试无输出；异步测试使用已批准的沙箱外执行。使用短 `--basetemp=tmp/...` 避免 Windows 长路径限制。

## 手工验收步骤

1. 进入“文献知识库”，创建库，上传 Tao PDF，等待任务完成；检查真实16物理页、91个通用解析切片及质量警告。
2. 选中文献，在“文献对话”测试模型连接，然后提问。确认回答下方出现可点击的 S-ID 引用。
3. 打开引用，核对文件、固定版本、摘录、物理页和高亮；调整缩放，并打开完整原文复核数字。
4. 刷新、切工作区、再进入同一会话；历史引用应可恢复，草稿按库/会话保留。
5. 在“文档与解析”停用文献，确认后续检索和原件访问被阻止，旧来源显示不可用。恢复文献后重解析，旧引用仍指向原版本。
6. 使用慢请求测试停止生成和重试：同 request_id 不重复调用模型；已中断请求需新 request_id。切换文献范围不能带出被撤销文献的历史证据。

页命中仅衡量候选召回，不等于证据足以支持结论。表格和公式的 scientific correctness 仍需对原页验收；不要把“引用身份正确”当作“答案数值正确”。跨论文固定评测集和 Evidence Hit@5 目标是后续质量评估，尚不能宣称已达到90%。
