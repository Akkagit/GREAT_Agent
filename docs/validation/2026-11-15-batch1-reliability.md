# 第一批可靠性建设与验收 — 2026-11-15

范围为用户已选择的第一批 1、2、7、8、9。实现位于现有 `codex/agentscope-p0-p1` 工作区，保留此前未提交的功能。本次没有运行真实 GREAT、调用云模型、迁移正式数据库、替换正式 `frontend/dist` 或重启正式服务。

## 实际交付

- P0：XML 在独立 staging 完成后，短事务 CAS 校验、冻结和登记预览；渲染/落盘/登记失败不锁死可编辑计划，不解冻其他请求的计划。
- P0：solver succeeded、原始结果登记、analysis job 入队同一事务提交；作业固定 task/attempt/revision，分析失败和重启不重跑解算。
- 持续补槽：生产循环在任一任务结束后继续领取；保留数据库全局/实验配额、恢复占槽与进程身份检查。显式 `run_once` 仍等待分析完成，兼容隔离测试与单轮调用。
- 分析消费者独立恢复/重试，终态数据库暂时失败保留结果重试写入；重复取消仍等待线程停止写文件。页面“重试后处理”只作用于选定的失败分析作业，晚到响应不能污染其他实验或 attempt。
- 迁移 `0009`：新增 `analysis_jobs`、`worker_heartbeats`，支持既有版本升级和保守 bootstrap 验证，不重定义历史研究表。
- 完整离线备份、校验、恢复：SQLite、runs、knowledge 全树、配置、哈希与版本清单；保留人工核对公式缓存。Solver/Knowledge 双锁、进程资源检查、非空/重叠/链接/损坏拒绝。恢复只写新空目录，重定位归档路径，不改旧执行授权，不自动启动服务。
- readiness：schema、Solver 心跳、恢复占槽与分析队列诊断；启动器按完整/API-only 模式验证 readiness，同时保留页面、存活和稳定期校验。
- 统一 `scripts/verify.ps1`、Windows CI、隔离构建及 JSON/日志；源码/锁文件/构建指纹和检查期间变化检测。测试剥离模型密钥并限制 Python 外网连接。

## 复现与审查

新增行为回归已观察失败后修复：分析前解算仍 running、长短任务空槽等待、第二日 XML 失败后冻结、pending/revision/attempt 竞态、预览保存失败、原 PDF 未备份、知识执行器持锁、恢复目标覆盖、活 Worker 不 ready、门禁错误传播、指纹缺失、前端重试入口缺失。

另用真实 mock solver 完成原始结果后，在隔离数据库中启动自有分析子进程、待其领取作业后终止该子进程；新消费者恢复同一 job，solver 成功不变，attempt 数仍为 1。

真实进程启动验收：在新的 `.tmp/batch1-process-smoke` 目录和临时 loopback 端口使用正式启动器完成备份/副本试迁移/0009迁移/API+Worker启动。真实 heartbeat readiness 返回200；仅停止本次 Worker 后，心跳过期时 readiness 返回503，API health和API-only readiness仍200。没有提交任务或模型消息，所有自有进程最终已回收。证据：`output/batch1-reliability-20261115/process-smoke.json`。

独立审查发现并修复两个边界：分析终态提交暂时失败卡 running；重复取消提前退出而线程继续写入。两项均有先红后绿回归，独立复核另 2 passed。最终独立审查未发现新的可行动 P0/P1。

专项结果：预览/确认/计划/提案 98 passed；Worker/分析首轮 22 passed；API/readiness/恢复/配额 45 passed；迁移/分析恢复 20 passed；最终分析边界 8 passed；前端新入口与仪表盘 32 passed，typecheck/lint 通过；备份/启动/readiness 41 passed、2 环境跳过；最终门禁脚本边界 16 passed。各专项范围重叠，不能相加作为总测试数。

## 完整门禁

首轮 Python 为 **914 passed、2 skipped**，267.15 秒。首轮运行期间补入启动和指纹测试，因此继续执行稳定版本最终门禁。

稳定版本最终 Python 回归为 **918 passed、2 skipped**，270.63秒；前端 **23文件、320 passed**，72.92秒。typecheck、lint、隔离生产构建均退出0。

最终报告：`output/batch1-reliability-20261115/verification-final.json`，已核对 `status=passed`、`exit_code=0`、`source_unchanged=true`，五项检查退出码均为0，隔离构建目录已清理。被验收工作区源码/锁文件综合SHA256为 `61201f8a448b1054eaf80bebe205d033bc8e88516e9ed54752ddd62e80d594ae`；具体逐文件和构建指纹在报告中。正式前端index SHA256仍为 `7363dddd6955b7ee5dd3ea80b37285f2e04bdb75747d514154075a427c56acac`，未替换。

两项环境跳过为宿主不允许的文件符号链接用例；Windows junction 在正常本机临时 fixture 中实测通过。沙箱中异步通信/临时目录解析曾阻塞或拒绝，正式验收使用获准的正常本机隔离测试，失败尝试不计入通过数。实际远程CI尚未运行，交付包含相同门禁的Windows workflow定义。

## 上线与恢复边界

部署前停止 API/Worker，用完整 backup 工具生成并 verify 快照，再对实际配置迁移到 0009，重新构建/启动。新库启动时自动建表只用于空库；旧库必须迁移。此验收没有执行正式上线。

随后用户明确授权正式数据库与页面切换，已完成正式0009上线及数据/页面验收，见[正式切换记录](2026-11-15-batch1-production-cutover.md)。

恢复包不包含应用代码、外部 GREAT 程序、GNSS 只读输入或本地模型文件，需保留相同环境。restore 的 `original-config.toml` 保留原身份，生成的 `config.toml` 指向恢复目录；启动器仍要求配置根与部署代码位置一致，需在恢复部署时明确安装代码/前端，不能把恢复文件目录直接当完整安装。

历史已确认预览中的绝对路径不改写，恢复根的 RunStorage 校验拒绝旧路径；重新执行须新预览确认。进程硬退出可能留下无登记的 staging/preview 文件，本批不自动扫描删除其他请求或历史目录，它们没有授权。
