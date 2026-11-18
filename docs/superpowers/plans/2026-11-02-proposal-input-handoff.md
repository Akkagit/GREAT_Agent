# Proposal Input Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track steps with checkboxes.

**Goal:** 接受提案后自动检查输入，并在原对话卡继续由人工生成 XML 和进入复核。

**Architecture:** 后端为只读检查增加 revision 身份校验；前端工作区持有唯一检查状态和动作锁，卡片及底部门禁共用。保留执行预览与批准服务的全部权限边界。

**Tech Stack:** FastAPI / Pydantic / SQLite; React / TypeScript / Ant Design 6.6.8 / Vitest.

**Spec:** docs/superpowers/specs/2026-11-02-proposal-input-handoff-design.md

## Global Constraints
- 仅第三项，咨询/skill 路由、模型及依赖不变。
- 接受后仅自动只读检查；XML 生成和实际执行分别人工确认。
- 复用工作区状态与 handler，保留 pending authority、版本、取消及冻结门禁。
- 不启动 Worker、不调用真实模型或求解器，不提交或推送；保留既有未提交工作。
- 使用当前 codex/agentscope-p0-p1 checkout；先备份本轮涉及文件，避免新 checkout 丢失现有主路径改动。

## Review Focus
- 接受幂等返回旧 revision，以及检查期间另一标签页改变 revision。
- 迟到的检查响应覆盖新提案、新检查或另一个实验。
- 检查网络失败被错误当成接受失败。
- 对话卡与底部同时点击导致重复检查或 prepare。
- XML 生成失败后服务端已冻结，界面必须重新读取状态。

## Task 1: Revision-bound read-only validation API
Files: backend/src/great_agent/api/experiments.py; backend/tests/test_input_validation.py (new).
Interface: POST /experiments/{id}/validate accepts optional body {expected_revision_id: UUID, expected_revision: int}. No body remains supported. Response {revision_id: UUID, revision: int, ok: bool, missing: [...]}. Compare current identity before and after preflight; mismatch returns existing revision_conflict 409. No writes or freezing.
- [x] Add failing API tests for bound identity, stale UUID/number, change during checking, side-effect-free missing/present checks and no-body compatibility.
- [x] Implement the minimal API extension and run focused tests.
- [x] Review the scoped baseline diff, resolve findings.

## Task 2: Shared input-check state and chat handoff
Files: frontend/src/pages/ExperimentWorkspace.tsx; frontend/src/components/ProposalCard.tsx; frontend/src/components/ResizableWorkbench.tsx; frontend/src/api/client.ts; frontend/src/api/types.ts; relevant frontend tests/CSS.
Consumes Task 1 frozen contract. validateExperiment(id, {expected_revision_id, expected_revision}) returns revision_id/revision. Auto-check uses the accept response explicitly rather than asynchronous React state. Keep request-generation/owner/revision guards and shared action locking. Card can render an optional handoff slot; accepted revision identity is recorded for relevant card. No duplicate canGenerate state.
- [x] Add failing tests for accept -> exactly one check -> same-card XML action; missing/retry; network failure retains accepted; late results and duplicate actions do not pass gates.
- [x] Implement shared version-bound check and inline status; update existing mocks to real response identity.
- [x] Keep XML manual, reuse preview navigation; refresh authoritative state on prepare error.
- [x] Ensure successful manual XML generation and explicit preview viewing activate the visible center pane on narrow screens, preserving chat drafts and navigation-race guards. First browser pass revealed hidden-center DOM alone was insufficient.
- [x] Run frontend suite, typecheck, lint and build; review scoped diff and resolve findings.

## Task 3: Integrated verification and local serving
Files: docs/validation/2026-11-02-proposal-input-handoff.md; scratch browser harness/evidence.
- [x] Run complete backend and frontend suites and inspect actual exit codes.
- [x] Browser-check built UI with an isolated synthetic API; verify inline flow, missing/retry, mobile visibility and zero automatic run requests.
- [x] Independent final review of this task's baseline diff and verification evidence.
- [x] Rebuild and restart only the owned localhost API, preserve user key via environment without printing it; verify updated assets/health. Never start Worker.
- [x] Complete acceptance notes and checklist; report updated local entry link.
