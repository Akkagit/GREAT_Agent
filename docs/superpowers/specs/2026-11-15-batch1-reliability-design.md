# 第一批执行可靠性与交付保障

日期：2026-11-15。依据：用户在项目缺口审查后要求按建议批次完成第一批，范围为原清单 1、2、7、8、9，优先处理两个 P0。

## 范围与技术决定

继续使用当前 Windows 本机单用户、FastAPI/SQLite/独立 Worker。复用当前 codex/agentscope-p0-p1 工作区，因为未提交的现有功能是本次改造基础；不得重置或一并提交他人的修改。测试只使用临时目录、数据库、mock solver/provider，不运行真实 GREAT、不调用云模型、不迁移正式数据库或切换正式服务。

1. 解算与分析：退出码为 0 且原始结果检查通过后，在同一 SQLite 事务登记原始文件、solver succeeded 和待执行 analysis job。分析作业绑定 task/attempt/revision、参考和配置，独立领取、失败、恢复和重试；重启仅恢复分析，不重跑 solver。维持现有分析事件/产物监控协议。首次只自动分析现有静态 DF Float，其他场景仍明确 skipped。分析不可悄悄改用不同 attempt 的 FLT。
2. XML 预览：先准备独立 staging 和所有 XML/身份快照，再以短事务重新校验 revision、pending proposal、已有任务，冻结并持久保存 preview。失败不冻结可编辑计划；不得回滚别的请求已提交的冻结；始终保持逐 attempt 明确确认与幂等入队。
3. 持续调度：维护正在运行的 future，任一 solver 完成就补领；数据库全局/实验并发配额及恢复占槽仍为权威。分析占用单独工作循环，不占 solver 槽。取消/关闭须等待已有子进程清理，不遗留无主分析线程。
4. 备份恢复：统一离线 backup/verify/restore，包含 SQLite 一致快照、runs、knowledge 原件（以及其他不可重建持久文件）、配置和校验 manifest。向量索引可以从固定版本重建；备份明确标注重建策略。恢复默认只写入新的空目标目录；非空目标、活动 Worker、越界/符号链接路径、损坏清单均拒绝。验证数据库完整性、外键、文件哈希；不将密钥写入报告。启动迁移前快照复用完整备份逻辑。
5. 交付基线：统一 verify.ps1 覆盖 backend/tests、scripts/tests、前端 test/typecheck/lint/build，构建输出放独立目录；返回真实退出码和机器可读结果。增加不含研究数据/密钥/真实外部调用的 Windows CI。保留 liveness，readiness/diagnostics 展示 schema 和 Solver Worker 心跳/恢复状态，不把 API-only 模式误报为全功能可执行。

## 可替代方案

仅把 succeeded 提前不能保证分析队列原子保存与重启恢复；采用持久 analysis job。Redis/Celery/微服务会增加本机部署成本，本轮不引入。为整个文件生成过程持有数据库事务会放大锁竞争，因此采用 staging 加短事务 CAS。备份只复制 SQLite 无法恢复 PDF/运行产物，因此采用数据库与文件的共同离线快照。

## 验收

- 分析中 Worker 丢失后 solver 成功记录与原始结果保留；分析可恢复/单独重试，GREAT attempt 不增加。
- XML 第二日生成失败不冻结计划；并发改计划/新建 pending proposal/重复准备均不会错误授权。
- 并发 2 时短任务结束可在长任务结束前领取第三个任务，配额和停止恢复测试保持通过。
- 备份恢复到空目录后历史 attempt、PDF 原件与引用可核验；损坏/越界/非空恢复目标被拒绝。
- 一条命令运行完整隔离验证；健康与 readiness 能区分 API 存活、Worker 不可用和健康 Worker。

## 留待后续

数据语义预检、RAG质量优化、文献到参数证据链、跨实验比较、参考基准增强不属于本批。正式环境升级/服务切换需在代码、测试和恢复报告齐备后单独处理。
