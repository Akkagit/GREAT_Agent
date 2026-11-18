# 第一批正式数据库与页面切换 — 2026-11-15

用户在第一批代码与隔离验收完成后明确要求“正式数据库和页面切换”。本次已完成正式切换，不是演练。

## 切换前

- 正式配置 `config/local.toml`，服务地址 `http://127.0.0.1:8765/`，数据库 `data/great_agent.db`，原版本0008。
- 源码/锁文件指纹与已通过918项Python、320项前端和typecheck/lint/build的最终门禁一致。
- 解算任务2个、attempt2个均succeeded；知识入库6个均completed_with_warnings。无pending/running解算或未完成入库。
- 根据正式配置、PID、创建时间和实际exe核对后，停止原API44020和Worker38800；确认退出，再保存停服数据库历史表行数及内容哈希。未使用模糊进程名批量停止。

## 发布与备份

正式执行 `scripts/start.ps1 -Config config/local.toml -NoBrowser -HealthTimeoutSeconds 120`。

发布目录：`frontend/.releases/20261115T042218Z-dd3855d3`。

1. 独立staged-dist构建通过。
2. 完整快照 `runtime-backup` 包含SQLite、runs、knowledge全树和原配置，125个文件；文件哈希、数据库完整性、外键、历史产物及知识原件引用检查通过。独立代理只读复核也通过，备份库确认为0008。
3. 从原始快照另拷trial.sqlite3，副本迁移0009及runtime schema检查通过。
4. 明确CLI迁移正式库至0009；数据库旁另有仅DB的迁移前.bak，不能替代完整runtime-backup。
5. 原前端移至previous-dist，新前端切入frontend/dist。previous-dist完整指纹与切换前一致。
6. 新API13528、Worker26884启动；health、readiness和页面稳定期检查通过，PID记录已更新。

## 正式验收

- 数据库版本0009，API与Solver Worker均就绪，知识执行器正常启动。
- 原有34张数据表的行数及逐行内容哈希与停服快照完全一致，除版本表和新增表外没有历史数据变化。保留2任务、2attempt、90产物登记、6份知识原件及既有会话/引用/翻译。
- 正式frontend/dist与隔离门禁验收的构建指纹完全一致；HTTP页面及66个静态文件逐一返回200且SHA256一致。
- 从正式知识原件接口读取一份既有PDF的前1024字节：HTTP206，application/pdf，正确Content-Range和PDF头；未上传文件或发送模型消息。
- 本次没有提交解算任务、运行真实GREAT或调用云模型。

证据位于 `output/batch1-cutover-20261115/`：before.json、stopped-baseline.json、stopped.json、after.json、pdf-smoke.json、release-summary.json。发布日志位于上述release目录的build.log、migration.log、api.log、worker.log。

## 使用与保留

访问 `http://127.0.0.1:8765/`，已打开页面按Ctrl+F5加载新资源。后处理重试入口只在成功attempt的分析失败且存在对应job时显示；旧成功结果不会自动补建或重跑分析。

完整备份与previous-dist均保留。启动器的自动失败回退针对前端；数据库需要显式完整恢复。本次成功，无需回退。恢复工具只写新空目录并重定位历史路径，不能把该工具重定位后的数据库直接拷回原目录作为原位回退。
