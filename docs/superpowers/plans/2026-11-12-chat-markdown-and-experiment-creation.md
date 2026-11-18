# Chat Markdown and Experiment Creation Implementation Plan

> **For agentic workers:** Use systematic debugging, test-driven development and independent final review. Preserve the existing dirty checkout; do not commit unrelated work.

**Goal:** Render assistant Markdown correctly and allow users to create draft experiments from global conversation.

**Architecture:** A shared safe Markdown renderer handles assistant prose. The conversation runtime exposes a server-bound creation callback and a complete default template only in global conversations. The callback stages a validated spec; experiment creation and the durable structured receipt commit together after a successful model turn. Global conversations remain global; the receipt opens the created experiment's own workspace.

**Tech Stack:** React, TypeScript, Ant Design, AgentScope, FastAPI, Pydantic, SQLite/SQLAlchemy.

**Spec:** User request and screenshot in this chat: fix Markdown card rendering, report exact root cause and solution, add conversation experiment creation.

## Constraints and review focus

- Preserve existing edits and execution confirmation requirements. Creating a draft must not start the solver.
- Parse assistant Markdown without enabling arbitrary HTML. User messages remain literal.
- Validate all candidate fields and registered profiles. Never accept IDs or paths from model prose as authority.
- A request replay must not create another experiment. Timeout, cancellation, invalid config or persistence failure must leave no orphan experiment.
- Restore creation cards from persisted history and ignore responses from a disposed view.

## Tasks

- [x] Markdown: reproduce with a failing rendering regression; add renderer and styles; verify bold, lists, code, tables, unsafe links/HTML and literal user text.
- [x] Runtime: test creation tool availability and single-attempt behavior; expose creation defaults and staging callback; update configured and bundled policies.
- [x] Service: test draft creation, replay, validation, timeout/cancellation and atomic rollback; reuse PlanService with caller-owned transaction; append typed experiment_created receipt.
- [x] UI: test creation receipt, workspace navigation and history reload; render persisted cards and refresh experiment sidebar.
- [x] Run relevant full suites, build/type checks, independent review; document diagnosis, solution and actual verification results.

## Decisions

- Reuse the current checkout because the task depends on substantial existing uncommitted conversation work.
- Keep the global conversation unbound after creation; users explicitly open a receipt to continue planning in the experiment workspace.
- Default template matches the existing home page baseline (2024/350, default 15 stations, static DF float); the runtime must explain defaults and preserve supplied values.
- No merge, deployment or commit is part of this request.

## Verification and review

- Backend: 418 passed. Frontend: 141 passed. Build/type compilation and lint passed.
- Browser: 7 checks passed, including visual bold weight, Markdown structures, receipt reload, navigation and mobile overflow.
- Independent review findings resolved: include server receipt in follow-up context; recover committed receipt after lost HTTP response; clarify creation is available globally.
- Runtime wire verification additionally found FunctionTool was stringifying ToolResponse objects. Shared tool output now returns JSON text supported by the installed SDK. Test fixtures use distinct tool call IDs.
- Diagnosis and screenshots: `docs/validation/2026-11-12-chat-markdown-and-creation.md`.
