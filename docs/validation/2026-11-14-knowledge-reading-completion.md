# 文献阅读剩余缺口实施与正式验收 — 2026-11-14

用户在复核后明确要求继续实施。本轮已完成代码修复、备份迁移、正式页面切换、真实新对话和引用工具验收；没有提交或推送代码。

## 当前可用入口

- 正式应用：[文献知识库](http://127.0.0.1:8765/?workspace=knowledge&knowledge_base=77cb8d67-a63e-43a1-a3a7-50550d597c4b&knowledge_chat=e2aa4809-e4f7-48e8-8b96-aef9fae64adf)。正式 API PID 44020，Worker PID 38800；最终检查两者均为该配置的真实 Python 服务进程，仍在运行。
- 正式数据库为 `data/great_agent.db`，迁移版本 `0008`，完整性检查通过。原有 13 张表的所有历史记录仍存在且原字段值一致；新增验收对话不计作原历史记录变化。
- 正式前端 66 个文件，index SHA256 `da82da0ef1b062ba29806d5ea7135fb66dd33eda3071c0716cfe123061c39e9d`；HTTP 返回与正式构建一致。

## 完成的改动

1. 公式按钮携带目标物理页。来源面板重新向服务器核验引用后，才在其真实页集合中选择目标；普通引用仍默认第一页，非法目标回退，切换引用时忽略旧请求。新增多页与异步回归。
2. 默认公式 helper 新增本地数字 PDF 字形提取，支持已限定的小数、希腊字母、单字符上下标、简单分式等结构。结果绑定原件 SHA、版本、区域和提取器身份，人工核对缓存优先，机器结果明确标记待核对。真实生成 PDF 的五类样例均正确，提取请求计数为 0，并使用禁止模型请求的传输器验证。
3. 修复 Windows venv 重定向器造成的实际进程/PID 不一致，解析子进程和正式服务使用保留 venv 身份的直接解释器启动。启动失败只清理本次拥有且身份仍一致的进程树，确认退出后才回退页面。
4. 启动器默认先类型检查和阶段构建；已有 dist 不再使更新永久失效。需要迁移时先保存 DB/runs/配置并迁移第二份副本，再显式调用原 CLI。结构已更新则跳过迁移。新增日志、页面与健康检查、不同配置共享资源冲突检测，以及 NoBrowser/NoBuild/ApiOnly 参数；不隐式安装依赖。
5. 真实模型曾返回纯文本“核心结论：”。新增仅针对首个文本块明确标签的视觉强调兜底，不改写原文；引文、代码、普通段落与已有 Markdown 格式不受影响。该真实历史回答在当前页面复验通过。

## 备份与回退

完整备份位于 `output/knowledge/reading-completion-20261114/before-release-20261114T104835Z`：数据库、111 个运行文件（138710729 字节）、配置和原正式前端。先迁移副本并逐表核对原有字段/记录摘要，再执行正式迁移；迁移另保留数据库 `.bak`。新旧前端通过同卷重命名切换，两次旧版本均保留在 `frontend/.releases`，该目录已加入 Git 忽略规则。不得在服务运行时直接覆盖恢复 SQLite 数据库；恢复数据库与 runs 应作为一组操作。

## 验证结果

- 最终完整前端：21 文件，296 项通过；typecheck、lint 通过，最终阶段生产构建通过。保留现有大包提示。
- 知识库、公式、权限、缓存、聊天、迁移、模块 MIME、启动器相关后端回归：387 项通过、1 项跳过。使用短目录 `tmp/krc0`；较长临时目录曾触发 Windows MAX_PATH，初始日志保留，未通过改业务断言掩盖。
- 独立任务和最终审查均通过。审查发现的数字合并、虚线分式、不可见编号、错误字体映射、批注遮挡、粗体向量、缓存来源混淆和进程遗留均已增加拒绝条件及回归。
- 最终 Tao 浏览器 24 项通过；生成公式新对话 7 项通过，字体/PDF 完成绘制后的只读复验 6 项通过，纯文本结论兜底的真实历史回归 6 项通过。均无浏览器运行时错误。
- 最终发布只读审计 15 项全部通过；服务身份另行核验。原 PDF SHA 未改变；递归检查实际出站字符串及嵌套工具 JSON，没有发送完整派生 Tao LaTeX 或图片。

## 真实验收范围与过程

Tao 采用本轮正式环境新建对话；确实通过 UI 发送问题，记录即时等待气泡、SSE 和真实检索阶段。该轮真实模型 3 次物理请求、2 次工具调用，关键词检索命中 29 条、登记 3 条证据。模型解释与公式(10)后的兼容关系一致，精确表达式由原页核对卡显示。公式按钮实际打开物理第4页。

第一轮发现纯文本结论未强调，保留失败截图后修复。第二轮回答使用“钟偏差”，旧断言只匹配“钟差”而误失败；经原文核对，这是等价用词。保留原记录并修正验收词项后，从同一真实已发送轮次继续翻译/复制，没有重发模型问题来迎合断言。最终报告通过 `continuation_from` 指向保留的新发送证据。

中文、英文目标均真实调用配置模型，随后读取命中 v4 缓存；原文、译文、附出处三种复制均用实际 clipboard 写入/readText 对照，按 Windows 换行规范比较。桌面与 390px 手机无页面横向溢出，刷新恢复检索摘要和公式。截图等待数学字体加载与 PDF 绘制完成后保存。

云端验收最初被自动审批以“可能私有文献外发”拦截，调用未执行。随后核对本机 PDF 的 DOI、标题、作者与 [Springer 官方发表记录](https://link.springer.com/article/10.1007/s10291-024-01776-7)，明确目的地为配置的智谱 glm-4.7、仅短文本后，复审获准。PDF 和公式图片一直保留在本机。

## 证据位置

均位于 `output/knowledge/reading-completion-20261114`：

- `audit.json`、`services.json`、`formal-migration.json`、`release-manifest.json`、`frontend-release-history.json`。
- `frontend-regression-final.log`、`backend-regression-short.log`、`frontend-build-final.log`。
- `browser/results.json`、`browser-generic/results.json`、`browser-generic-render/results.json`、`browser-conclusion/results.json`。
- `browser/chat-formula.png`、`browser/formula-original.png`、`browser/translation-copy.png`、`browser/translation-english.png`、`browser/mobile-chat.png`。
- `browser-conclusion/plain-core-conclusion.png`，以及 `browser-generic-render/local-formula-card.png`、`local-formula-source.png`。
- `local-formula-examples.json`、`local-formula-live-chat.json` 与 `plain-conclusion-live-chat.json` 分别绑定对应对话，来源身份已核验。

## 明确边界

这不是通用扫描公式 OCR。本地提取仅接受能够确认字形与位置的有限数字 PDF 结构；复杂矩阵、分离重音、未知字体、自定义字符映射、扫描件等会跳过。Tao 公式(10)使用已有原页人工核对记录，其他复杂公式不凑写。

原 PDF 文字层的双栏混排和公式噪声仍可能进入引用片段，机器翻译会继承这些质量问题。保护数字/数学字面值不等于自动验证全部科学语义；界面继续提供原文、对照、质量提示和原页入口。
