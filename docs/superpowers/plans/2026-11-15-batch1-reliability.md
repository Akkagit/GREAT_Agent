# 第一批可靠性建设 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** 完成 P0 状态正确性及第一批调度、备份与离线验证保障。
**Architecture:** 保留模块化单体；SQLite 原子完成 solver 并登记独立分析作业；预览 staging 后 CAS 发布。离线完整快照与隔离验证共同形成可复现交付。
**Tech Stack:** Python 3.11、SQLAlchemy、Alembic、asyncio、FastAPI、PowerShell、React/Vitest。
**Spec:** docs/superpowers/specs/2026-11-15-batch1-reliability-design.md

## Global Constraints

- 使用当前工作区现有改动，不创建丢失未提交基础的新工作树、不重置、不自动提交所有修改。
- 不运行真实 GREAT、外部模型、不修改正式数据库和服务。
- 保留 XML 明确确认、revision CAS、配额、进程身份、输入只读和 run_root 边界。
- 实现前运行行为回归测试确认失败；修复后运行对应测试与完整隔离门禁。

## Review Focus

- solver 成功事务与 analysis enqueue 原子，恢复不依赖聊天或进程消失推断成功。
- 分析绑定固定 attempt，停止/重启不会让旧结果覆盖新 attempt。
- 预览并发发布不回滚别人冻结、不遗留有效但未登记授权。
- 备份内外路径和恢复目标受控，损坏清单不能删除/覆盖科研数据。
- readiness 与验证脚本报告真实状态，不掩盖子命令失败。

## Task 1: P0 preview 原子发布（独立子任务）

Files: services/execution_service.py、services/plan_service.py（必要时）、tests/test_execution_preview.py、tests/test_execution_confirmation.py。
Interface: prepare(...) -> ExecutionPreview 保持；freeze_revision 可增加 session 参数，默认行为保持。
- [x] 写第二日 render 失败、并发 revision/pending 变化、已经冻结重试不被解冻的测试并观察失败。
- [x] staging 全部生成及校验，短事务 CAS 冻结+preview 保存；安全清理自己的未发布文件。
- [x] 执行 preview/confirmation/proposal 回归，提交代码给主代理检查，不执行 Git commit。

## Task 2: P0 持久分析与持续调度（主代理）

Files: db/analysis_models.py、db/analysis_repository.py、db/repositories.py、db/schema.py、alembic/versions/0009_analysis_jobs.py、runtime/analysis_worker.py、runtime/worker.py、services/result_analysis_service.py、api/tasks.py、对应测试。
Interfaces: TaskRepository.complete_solver(claim, files, *, analysis_config) 原子落库；AnalysisJobRepository.enqueue/claim/finish/recover/retry；AnalysisWorker.run_once/run_forever；现有 Worker.run_once 保留一轮可等待完成测试语义，run_forever 使用连续领取。
- [x] 写 solver 分析前已成功、enqueue rollback、analysis 重启/失败重试、固定 attempt、短长任务补槽测试并观察失败。
- [x] 新 migration 和 runtime schema；事务登记 solver 和 analysis job；分析消费者独立执行、恢复与重试 API。
- [x] 连续 solver 调度；维持事件协议、取消和恢复语义，增加 Worker 心跳接口供 Task 4 消费。
- [x] 执行 worker/recovery/scheduler/mock E2E/analysis/migration 回归。

## Task 3: 完整备份恢复（独立子任务）

Files: services/backup_service.py（或同等独立模块）、scripts/local_startup.py、scripts/backup.ps1、scripts/tests/test_backup.py、scripts/tests/test_local_startup.py。
Interfaces: backup/verify/restore functions 可由独立模块 CLI 调用；startup.snapshot(settings, config, destination) 兼容并补全 knowledge 原件。
- [x] 写知识原件恢复、篡改/越界/非空拒绝、活动 worker 拒绝测试并观察失败。
- [x] 完成离线备份/完整性验证/安全新目录恢复；所有递归操作先核对绝对目标。
- [x] 执行启动器及备份隔离测试；提供文档片段给主代理整合，不提交 Git。

## Task 4: 统一验证与诊断（独立子任务 + 主代理集成）

Files: scripts/verify.ps1、scripts/verify.py（如需）、scripts/tests/test_verify.py、.github/workflows/verify.yml、api/diagnostics.py、tests/test_readiness.py（诊断由主代理实现）。
Interfaces: verify 按顺序执行 Python + frontend 检查，构建到 tmp 子目录；生成 command/exit/duration/status 报告。diagnostics 从单独 heartbeat 持久记录及现有 recovery 查询获取事实。
- [x] 写门禁子命令失败传播/隔离构建路径测试并观察失败。
- [x] 实现统一门禁及离线 mock Windows CI，不自动发布；readiness 的 no-worker/stale/healthy/schema mismatch 测试。
- [x] 跑统一检查，记录真实结果和环境阻断；完成 README/验证记录。

## Task 5: 独立审查与交付

- [x] 对全部改动做独立 correctness/security/recovery 审查，修复发现并回归。
- [x] 在干净的临时数据库验证 0008→0009 升级与回退、备份恢复；不碰正式库。
- [x] 汇总交付文件、测试证据、未部署边界与升级命令。
