# 科研分析 Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Track steps with checkboxes. User approved the design and explicitly requested implementation; proceed without another approval round.

**Goal:** Deliver a read-only research workspace connecting experiments, code and literature with durable multi-turn questions, evidence, hypotheses and reports.

**Architecture:** Independent research tables and durable worker; server-scoped immutable material snapshots; one bounded AgentScope research model with only read tools. Existing solver and short-chat lifecycles remain intact. Frontend uses the existing AntD three-pane vocabulary.

**Tech Stack:** Python/FastAPI/SQLAlchemy/SQLite/AgentScope/httpx; React/TypeScript/AntD/Vitest. No new required dependency.

**Spec:** `docs/superpowers/specs/2026-11-18-research-analysis-design.md` (approved 2026-11-18).

## Global Constraints

- Research tools cannot write code, execute scripts/tests/solvers, install dependencies, modify plans or approve execution. Saving research records/snapshots is allowed.
- Freeze concrete experiment/task/attempt, code content and literature versions; subsequent changes affect only later turns.
- Only verified, known evidence aliases may be cited; reload prior evidence before using it in a new turn. Summaries and earlier model statements are not evidence.
- One active run per session, default global worker concurrency 1; HTTP disconnect does not cancel. Cancel/complete use fencing and atomic terminal transitions.
- Quick budgets: 4 model requests, 10 tools, 180 seconds, 12k input/4k output token estimates. Deep: 8, 20, 480 seconds, 32k/8k. Disable provider retries.
- Do not migrate a live database, restart production services, replace frontend/dist or make paid model calls for verification.
- Preserve existing dirty working tree. Work in current feature checkout; no unrelated staging/commits. Independent agents own disjoint files.

## Review Focus

- Historical code/current code or attempt/version mismatch: material and tooling tests must retain concrete identities and causal limitations.
- Same aliases reused across rounds: service tests must load old evidence and remap; API links include run identity.
- Cancellation/late completion and worker crash: repository/service tests must prevent state updates and duplicate physical calls.
- Source traversal, secret files, malicious comments or fake citations: material/engine tests must reject access and unsupported claims.
- UI route changes, stale responses and failed HTTP acceptance: component tests must retain the right session and recover the original request/run.

## Shared wire contract

Research API base `/api/v1/research`.

Material selection is an array:

```json
[
  {"kind":"experiment","experiment_id":"uuid","task_id":"uuid","attempt_id":"uuid","role":"baseline"},
  {"kind":"code","source_id":"project","paths":["backend/src/example.py"],"provenance":"unknown"},
  {"kind":"literature","document_id":"uuid","version_id":"uuid"}
]
```

`GET /catalog` returns `{experiments:[{id,title,tasks:[{id,year,doy,attempts:[{id,number,status}]}]}],sources:[{id,name}],documents:[{id,title,base_id,version_id}],profiles:[{id,name,configured,capabilities}]}`.

`POST /sessions` accepts `{title?,goal?,materials?:Selection[],model_profile_id?:"default",mode?:"quick"|"deep"}`.

Session detail: `{id,title,goal,version,material_revision,state_revision,settings:{model_profile_id,mode},materials:Selection[],material_summary:[],active_run_id,messages:[],items:[],reports:[],stage?}`. Messages: `{id,run_id,role,status,text,blocks:[],sequence}`. Answer blocks `{id,type,text,evidence_ids?:string[]}`. Items `{id,kind,text,status,support:[],opposition:[],unknowns?:[],origin_run_id?,revision?}`.

`PATCH /sessions/{id}/materials` accepts `{expected_version,materials}`. Settings endpoint accepts `{expected_version,model_profile_id,mode}`.

`POST /sessions/{id}/runs` accepts `{request_id,expected_version,message,followup_target?:{run_id,message_id?,block_id?,item_id?}}`; returns 202 and Run. Run `{id,session_id,request_id,status,stage,message,snapshot_summary,model_profile_id,mode,result?,usage?,error_code?,retry_of?}`. `GET /runs/{id}`, `POST /runs/{id}/cancel`, `POST /runs/{id}/retry` (body `{request_id}`), `GET /sessions/{id}/events` (SSE), `GET /runs/{id}/evidence/{alias}`, `POST/GET /sessions/{id}/reports`.

Evidence `{id,alias,kind,title,text,locator:{...},sha256,quality_flags:[],available:true}`. Alias types E/C/L + integer, resolved within run. `result` includes `{answer,answer_blocks,observations,hypotheses,suggestions,open_questions,limitations,answer_status,source_ids}`. Suggestions retain text/diff as unexecuted content only.

API errors use `{error:{code,message,details:[]}}`. Optimistic concurrency and same request ID/different payload return 409. Server material paths are never arbitrary browser paths; code source IDs must be registered.

Snapshot internal shape `{id,manifest_sha256,selection:Selection[],materials:[{id,kind,title,locator,content?,...}],limitations:[]}`; implementation may add typed metadata. Do not expose entire captured code in session list/detail responses.

## Task 1: Durable repository and schema

**Owner:** backend persistence agent.

**Files:** create `backend/src/great_agent/research/__init__.py`, `models.py`, `repository.py`, `errors.py`; migration `backend/alembic/versions/0010_research.py`; modify DB model registration and schema revision checks; new repository/migration tests; update only schema-related test version expectations.

**Interfaces:** `ResearchRepository(session_factory)` handles session/material/settings revisions, request idempotency, queue claiming/heartbeat/fencing, evidence/model-call/checkpoint persistence, cancel/finish/fail, events, retry and immutable reports. Public results are JSON dictionaries matching the shared wire contract. Report exact signatures early for root service integration. Errors carry safe `code`, `message`, `status_code`.

- [x] Write failing tests for durable session restore; request replay/conflict; one-active-run; material/settings changes leave old snapshot untouched; owner fencing/cancel races; stale retry; immutable report; migration from 0009.
- [x] Run tests and observe expected failures.
- [x] Implement independent research tables; register metadata; add additive migration; require latest schema without migrating live DB.
- [x] Verify new tests plus runtime/migration regression tests; report exact commands and counts.

## Task 2: Material snapshots and read-only tools

**Owner:** material/tooling agent.

**Files:** create `research/materials.py`, `research/tools.py`; new `backend/tests/test_research_materials.py`, `test_research_tools.py`. Do not modify config, CLI, API or repository.

**Interfaces:** `ResearchMaterials(session_factory, settings, *, knowledge_service=None, retriever=None)` exposes `catalog() -> dict`, `capture(selection:list[dict]) -> dict`, `validate(snapshot:dict) -> None`, `summary(snapshot:dict)->list[dict]`. Read code roots from `settings.research.code_roots` or default `{'project':settings.project_root}`. Repository tables and source access remain server controlled.

`ResearchToolset(materials, snapshot, *, prior_evidence=None)` exposes `definitions() -> list[dict]` (OpenAI function schemas), `async call(name:str,arguments:dict)->dict`, `evidence:list[dict]`. Tools only read server snapshot objects; evidence aliases allocated by toolset. `load_prior_evidence` revalidates source and allocates current-round alias. Persist toolset evidence before further model calls via root service callback.

- [x] Write failing tests covering real temporary experiment artifacts, code snapshots/dirty changes, selected literature version; traversal/secrets/links; missing or changed files; incompatible metrics; prior alias remapping.
- [x] Implement captured material versions and local registered-source code search/read/diff; literature adapters scoped to fixed versions and current availability; metadata/coverage limits.
- [x] Implement fixed existing-metric comparison (no script execution/reprocessing). Include reference/policy identity, units and missing-data semantics.
- [x] Verify all tool/material tests; report exact supported tool schemas and source shapes.

## Task 3: Frontend workspace and experiment handoff

**Owner:** frontend agent.

**Files:** `frontend/src/api/research.ts`, `pages/ResearchWorkspace.tsx/.css`, optional focused research components, research tests; modify `App.tsx`, `ResizableWorkbench` for independent IDs/labels/key, experiment view for selected attempt handoff. Own all frontend changes.

- [x] Write failing integration/component tests for third workspace route; create/bind materials; question and targeted followup; evidence source selection; active run restoration after route change; version conflict/failed submission recovery; report persistence.
- [x] Build three-pane AntD UI using shared API contract; choose materials from catalog, code paths under source IDs, append comparison/literature.
- [x] Preserve draft and selected evidence identity; query accepted run after transport failure rather than resubmitting new request; explicit stop only; poll/SSE state without cancel on unmount.
- [x] Add “带入科研分析” using exact selected experiment/task/attempt, without model auto-send. Preserve existing workbench/knowledge tests.
- [x] Run frontend focused tests, typecheck, lint, isolated build. Report paths and commands.

## Task 4: Research model, service, API and runtime integration

**Owner:** root.

**Files:** create `research/engine.py`, `service.py`, `worker.py`, `api/research.py`; modify `config.py`, `api/dependencies.py`, `app.py`, `cli.py`, startup scripts as necessary; tests `test_research_engine.py`, `test_research_api.py`, `test_research_service.py`.

- [x] Write failing tests with mock upstream model transport for cross-source tools, previous-claim context and reloaded evidence, structured result validation, no arbitrary tools, budgets, no retries and partial/needs_input output.
- [x] Implement bounded AgentScope loop with source-aware system policy, current question/target, recent history and typed research state. Exclude unsupported/failed outputs from canonical memory.
- [x] Implement worker using repository leases and fenced terminal commits, durable request/response checkpoints and safe recovery; separate `research-worker` CLI process. Register model profiles/settings and startup/stop PID management by existing patterns.
- [x] Implement REST/SSE contract, material capture outside DB write transactions, errors and report persistence; start no model call in HTTP request and never in rendering.
- [x] Verify API with temp DB + fake engine/tools; cross-turn alias correctness, settings/material update, cancelled/late response, same-request replay, expired lease and uncertain calls.

## Task 5: Integration and review

- [x] Run offline unified verification (`scripts/verify.py`) after source stabilizes; fix attributable failures and record pre-existing failures accurately.
- [x] Inspect task-specific changes and run fresh independent spec/quality review; address actionable findings with regression tests.
- [x] Validate real frontend against isolated mock research API if browser tooling is available; do not replace production assets.
- [x] Update README/config example and validation report with migration/startup commands, limits and test evidence.

## Preflight interface scan

| Tasks | Shared boundary | Resolution |
| --- | --- | --- |
| 1 / 2 | package and errors | Task 1 owns `__init__`, models/repository/errors; task 2 only materials/tools and local tests |
| 1 / 4 | repository lifecycle | Task 1 sends signatures early; root adapts service to actual repository, retaining fencing/idempotency |
| 2 / 4 | snapshot/tool interface | Explicit constructors/methods above; root handles model persistence callbacks |
| 3 / 4 | REST/types | Shared wire contract is authoritative; root and frontend agent communicate any unavoidable additions |
| 1 / 3 | none | Disjoint files |
| 1 / 2 / 3 / 4 | global safety and tests | All use temp DB/mock model; no production database, model credentials, solver or source edits by research tools |

Ruling: reuse current dirty feature checkout because approved implementation depends on its uncommitted code; preserve it. Independent frontend, persistence and material files are implemented concurrently as authorized by developer multi-agent instructions, with root integration and final fresh review. Do not create unrelated commits from this working tree.
