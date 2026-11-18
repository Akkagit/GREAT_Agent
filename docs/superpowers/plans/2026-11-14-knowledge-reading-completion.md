# Knowledge Reading Completion Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for independent task implementation and root integration.

**Goal:** 补齐复核发现的公式跨页定位、本地通用提取、正式接入与现场验收缺口。

**Architecture:** 增量扩展现有 React 来源选择、受限 PDF 字形转录与公式派生缓存；复用 SQLite 迁移与锁，在备份、隔离验证后切换本机正式应用。

**Tech Stack:** React/TypeScript/Vitest、FastAPI/SQLAlchemy/Alembic/pdfplumber、PowerShell、Chrome CDP。

**Spec:** docs/superpowers/specs/2026-11-14-knowledge-reading-completion-design.md

## Global Constraints

- 复用当前 dirty codex checkout；不提交、推送、删除用户数据或结束未知进程。
- 保留原件/索引、撤权保护与固定版本身份；公式图像不出站，不下载重型模型。
- 机器本地提取不是人工校核；不得对未知符号/结构凑公式。模型标识约定 local_pdf_text_v1，状态保持 machine_transcription。
- 正式数据库与旧 dist 切换前备份；迁移复用现有 WorkerLock/upgrade_runtime_database；不在 API 启动内隐式建表。
- 使用模拟模型自动测试；真实模型验收只发送既有授权范围内的问题和短证据文本。

## Task 1 — Formula page navigation

Own frontend/src/api/knowledgeChat.ts, frontend/src/components/KnowledgeChat.tsx, frontend/src/components/KnowledgeSourcePanel.tsx and relevant frontend tests.
- [x] 新增多页引用公式跳页失败测试，确认 RED。
- [x] 来源选择新增可选 pageIndex；公式按钮携带物理页。重新核验引用后合法页选择；普通引用与异常目标稳定回退。
- [x] 对 local_pdf_text_v1 显示本地提取待核对标识。
- [x] focused Vitest/types/lint 并自审；不构建、不改依赖或启动服务。

## Task 2 — Reliable local startup and frontend release

Own scripts/start.ps1, optional scripts deployment helper, small focused startup tests/docs. Do not mutate runtime database/dist or start services; root performs deployment.
- [x] 明确现有启动缺口并用可执行的隔离测试或预检复现。
- [x] 修复已有 dist 导致源码更新无效；构建先进入阶段目录，成功才替换并保存旧版。
- [x] 显式迁移步骤在启动前运行，复用 CLI 锁及备份；处理非零退出与已有占用；保留历史 runs，启动失败有日志。
- [x] 增加 NoBrowser/配置选项用于自动验收，健康检查后报告成功；不执行 uv sync 或下载，除非显式选择。
- [x] 审查回滚、配置路径、引号与旧进程保护；运行适当验证并自审。

## Task 3 — Conservative local formula transcription

Own backend/src/great_agent/knowledge/formula_recognition.py, new local extraction module/process and related backend tests. Root owns CLI/config/service integration outside these files.
- [x] 先检查真实 PDF 字形与现有公式区域；记录可支持结构和拒绝边界。
- [x] TDD 真实数字 PDF 的多种不同公式、数字/希腊字母、上下标/基本分式等可证明结构，以及未知符号/多行歧义/无文字层拒绝。
- [x] 受限区域本地提取接入默认 helper，人工缓存优先，缓存绑定版本/SHA/页/区域/提取器版本；model=local_pdf_text_v1，status=machine_transcription。
- [x] 扩展恢复校验与来源变化测试；不改原始 evidence，不传出派生 LaTeX，不开启 remote。
- [x] focused pytest；异步测试使用 require_escalated，沙箱内 Windows socketpair 已知阻塞。无真实模型调用。

## Task 4 — Root integration, migration and release

- [x] 校验三项实现接口与独立评审，处理发现的问题。
- [x] 备份正式 DB/runs/配置/dist，保留 SHA/历史行数，先迁移副本并验证。
- [x] 正式迁移、构建切换及启动；验证 HTTP/API、schema、历史记录与资源身份。
- [x] 相关前后端回归、typecheck/lint/build；只针对新失败追加测试。

## Task 5 — Fresh real browser acceptance

- [x] 真实新对话/新问题，捕获实际等待气泡及 SSE 阶段，验证公式原页与公式卡。
- [x] 中英翻译和三类复制、恢复历史、桌面/手机布局及错误日志。
- [x] 独立最终评审、更新 ledger/验证报告与已知覆盖边界，留下可用本地入口。
