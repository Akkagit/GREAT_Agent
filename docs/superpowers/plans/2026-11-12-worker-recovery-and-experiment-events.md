# Worker Recovery and Experiment Events Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans, TDD, and independent final review. Preserve unrelated changes.

**Goal:** Recover orphan GREAT tasks without duplicate execution, and monitor any number of daily tasks with one resumable experiment stream per workspace.

**Architecture:** A single OS-locked worker owns normal execution and an independent recovery loop. Attempt identity and conditional updates protect orphan observation and cancellation. SQLite transaction events feed a consistent monitor snapshot and resumable experiment SSE shared by React views.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy/SQLite WAL, Windows process handles, React/TypeScript/Ant Design.

**Spec:** ../specs/2026-11-12-worker-recovery-and-experiment-events-design.md

## Global Constraints

- Keep the current dirty codex checkout: the approved design depends on substantial uncommitted conversation/execution code. Do not reset, migrate production data, start real GREAT, deploy, or commit unrelated changes.
- One Solver Worker can own multiple concurrent GREAT tasks; lock only the worker CLI, never API construction.
- Recovery never automatically executes, approves, retries, or infers solver success. Retry retains existing XML review and explicit confirmation.
- Use existing dependencies, existing task statuses, nullable historical identity, transactional state/events, 2s recovery, 500ms monitor polls, 15s heartbeat and 0.5–15s jittered backoff.
- Preserve old task SSE compatibility; all new status writers use transaction-local experiment events.

## Review Focus

- Crash after spawn but before identity persistence must not release quota while an unknown process is alive.
- Healthy current-owner tasks in postprocessing must not be mistaken for dead orphan solvers.
- Snapshot/cursor race, repeated events and cross-experiment cursors must not lose or regress state.
- Re-tried tasks and terminal tasks reanalyzed from CLI must update open views.
- Migration must preserve existing approvals/results and fail clearly for old un-migrated databases.

### Task 1: Persistence and recovery runtime

Files: models.py; next Alembic revision; new db/recovery_repository.py, runtime/worker_lock.py, process_identity.py, recovery_service.py; worker.py, process_runner.py, cli.py, api/tasks.py; backend/tests/test_worker_recovery.py and migration tests.

Interfaces: attempt owner_session_id, launch_phase, process_created_at, process_executable, command_sha256, recovery_state/reason, last_checked_at, version. TaskRepository.claim_next accepts worker instance ownership; record_pid accepts optional process identity; lifecycle transitions check active attempt and owner/version. Recovery runs independently of batch gather.

- [x] Write/run failing tests for live orphan then exit, PID reuse/unknown, launch phases, cancellation, lock and stale owner.
- [x] Implement identity/lock, short CAS repository transitions, conservative recovery and safe launch cleanup; expose recovery read/recheck/stop.
- [x] Run focused tests and compatibility worker/quota/execution tests; record real output.

### Task 2: Monitor persistence, events and API

Files: new db/monitor_models.py, db/monitor_repository.py, api/monitor.py; models.py registration, repositories.py event hook, execution_service.py, task_service.py, result_analysis_service.py, app.py; tests/test_experiment_monitor.py.

Interfaces: append_monitor_event(session, task_id, kind, payload, task_event_id=None); MonitorRepository.snapshot(experiment_id), events(experiment_id,cursor,limit=200); GET experiments/{id}/monitor and /events. Cursor v1:epoch:experiment:id. Snapshot tasks include attempts, active_attempt_id, recovery, analysis_status, artifact_version and queued attempt number. Event envelope includes schema_version,event_id,experiment_id,task_id,attempt_id,type,data.

- [x] Write/run failing tests for rollback, global ID gaps, cross-experiment and expired/forward cursor, snapshot race and metadata projection.
- [x] Implement INTEGER AUTOINCREMENT event log, epoch, explicit read BEGIN snapshot, 500ms bounded replay plus 15s heartbeat; no transaction across yields.
- [x] Wire all creation/retry/claim/finish/cancel/recovery/analysis/registration events in the caller transaction, including chat-owned confirmation.
- [x] Verify legacy event/API behavior and migration compatibility.

### Task 3: Shared frontend monitor

Files: new api/monitor.ts and monitoring provider/hook, related tests; api/client.ts/types.ts, ExperimentWorkspace.tsx, RunDashboard.tsx, ArtifactBrowser.tsx.

Interfaces: api.getExperimentMonitor(id,signal), openExperimentEvents(id,cursor,onEvent,onError,signal); shared provider per experiment with tasks, connection state, metadata version and recovery actions. Event type/data and cursor match Task 2; JSON 64-bit positions stay strings and compare as BigInt.

- [x] Write/run failing parser/backoff/projection tests and UI tests pinning one connection, analysis refresh and history selection.
- [x] Implement UTF8/CRLF/CR/multiline parsing, bounded buffers, jittered reconnect, heartbeat timeout, resnapshot, generation cleanup and one workspace connection.
- [x] Remove product per-task event subscriptions, preserve selected-attempt log polling and display recovery/analysis status.
- [x] Run frontend suite, typecheck, lint and build.

### Task 4: Integration, independent review and evidence

- [x] Test controlled process Worker death and restart without actual GREAT; test 34/100 synthetic tasks and actual Chromium/Edge connection count if a supported browser is available.
- [x] Run complete backend/frontend checks in a working test environment; diagnose sandbox issues rather than asserting old results.
- [x] Obtain independent code review; fix material findings and rerun relevant checks.
- [x] Update README/validation/plan progress; list deployment/migration requirements without restarting production services.

Final evidence: backend472 passed159.03s; frontend176 passed120.75s; Chrome/real isolatedAPI9/9; build/typecheck/lint/diff checks passed. See ../../validation/2026-11-12-worker-recovery-and-monitor.md. Working tree preserved; no production migration, service restart, real model/solver run, commit or deployment.
