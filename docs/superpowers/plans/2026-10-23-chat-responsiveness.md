# Chat Responsiveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Track RED/GREEN evidence locally; preserve the existing dirty worktree.

**Goal:** Make approved model proposals responsive without adding output caps or a total request deadline.

**Architecture:** GLM proposal calls explicitly disable thinking. Browser AbortController cancels fetch; the API watches client disconnection and cancels its upstream coroutine. Per-request timings distinguish plan loading, model request (network + generation), validation, and API total; the UI shows elapsed time and the completed breakdown.

**Tech Stack:** Existing Python/FastAPI/httpx and React/TypeScript; no new dependencies.

**Spec:** User-approved chat on 2026-10-23: implement disabling proposal thinking, elapsed time/cancel, and segmented timing; explicitly defer output length and total request timeout.

## Global Constraints

- Keep `F:\GREAT_Agent` as the formal checkout, retain unrelated changes; no commits/push/worktree creation.
- Do not add `max_tokens` or `request_timeout_seconds`. Keep existing connect/read timeouts unchanged.
- Do not start GREAT or call paid GLM during tests. Test synthetic providers only.
- Preserve XML review/explicit execution confirmation, existing experiments, attempts and artifacts.
- Keep credentials and prompt/response content out of timing logs.
- Use the approved blue/white UI tokens and typography; add a compact status area, no layout redesign.

## Review Focus

1. Cancel then immediately resend: stale completion must never overwrite the newer proposal or busy state.
2. Navigating away during generation aborts the request and clears the elapsed timer.
3. Browser disconnect cancels the upstream coroutine without storing a proposal or changing a revision.
4. Error/cancel timing logs must not expose API keys, prompts or provider error text.
5. Non-GLM providers must not receive unsupported thinking parameters; no request gains an output cap/deadline.

### Task 1: Proposal request controls, cancellation and timings

**Files:** `backend/src/great_agent/config.py`, `adapters/openai_compatible.py`, `services/chat_service.py`, `api/chat.py`; new `backend/tests/test_chat_responsiveness.py`.

**Interfaces:** `ModelSettings.thinking: Literal['enabled','disabled'] | None`; GLM defaults disabled, other providers unchanged. `ChatProposal.timings` maps milliseconds to `plan_load_ms`, `model_request_ms`, `validation_ms`, `total_ms`. API returns these plus `api_total_ms` and request ID. Structured INFO logs include safe IDs/outcome/durations only.

- [ ] Write and observe failing transport tests for GLM thinking disabled, explicit opt-in, unchanged non-GLM requests and absent caps/deadlines.
- [ ] Write and observe failing tests for measured stages, safe success/error/cancel logs, response timings and disconnect cleanup with no revision/proposal mutation.
- [ ] Implement the minimal controls, measured service stages and API disconnect watcher with bounded cleanup; do not conflate cancellation with model failure.
- [ ] Verify `uv run pytest backend/tests/test_chat_responsiveness.py -q` is green.

### Task 2: Elapsed/cancel frontend and stale-response protection

**Files:** `frontend/src/api/client.ts`, `api/types.ts`, `components/ChatPanel.tsx`, `pages/ExperimentWorkspace.tsx`, `pages/ExperimentWorkspace.css`; new focused UI/API tests.

**Interfaces:** `api.proposeChat(id, message, signal?)`; chat-only generating state separate from generic busy. During generation show elapsed seconds and enabled cancel button. On completion show request elapsed + backend timings; cancelled/error requests show outcome without creating a proposal.

- [ ] Add RED tests for increasing elapsed display, cancel restoring controls, immediate resend with stale completion ignored, unmount abort, success timing breakdown and API signal forwarding.
- [ ] Implement AbortController and request-identity guard, effect cleanup, compact accessible timing panel using existing tokens.
- [ ] Run complete frontend tests, typecheck, lint and production build; verify no new failure.

### Task 3: Integration, evidence and service activation

- [ ] Run complete backend regression and name the two pre-existing deferred cap/deadline failures; do not remove/skip them.
- [ ] Fresh independent read-only review of this scoped change; one tested fix pass for important findings.
- [ ] Safely update API only, preserving live credentials in memory and leaving Worker unchanged; first check active model/solver requests and project process identities.
- [ ] Verify real local health/assets/history GET only; use isolated synthetic provider for cancellation/timing smoke.
- [ ] Record evidence and limitations; no claimed real-model speedup without a real timed model call.
