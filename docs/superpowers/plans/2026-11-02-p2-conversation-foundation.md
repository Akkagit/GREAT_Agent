# P2 Conversation Foundation Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development; follow test-first RED/GREEN and preserve scoped baseline evidence.

**Goal:** 交付P2.0对话兼容与P2.1统一对话首批：真正的AgentScope Agent/Toolkit、普通/混合回答、可恢复会话、全局规范与技能、明确预算和原有提案执行门禁。

**Architecture:** ConversationService owns persistence, turn idempotency and bound domain callbacks; AgentScopeConversationEngine owns native Agent/Toolkit/model lifecycle and budgets; frontend consumes optional proposal blocks. Existing JSON proposal endpoint remains a fallback. PDF/OCR/index/upload and后台技能发布管理 belong next batch; P2.0 document-capacity experiments remain pending until that batch.

**Spec:** docs/superpowers/specs/2026-11-02-conversation-knowledge-design.md (approved by user “按照你的建议可以逐步开展”).

## Global Constraints
- Current Windows local feature checkout codex/agentscope-p0-p1 contains authorized prior uncommitted work; reuse and snapshot it. No commits/pushes, no Worker/solver execution or real plan edits.
- Exact existing AgentScope2.0.11/antd6.6.8 retained. No extra agent framework. No parser/model download in this batch.
- Consultation creates no proposal and does not supersede pending or advance revision. Frozen/task-bearing experiments remain read-only for changes but chat remains enabled.
- Proposal creation only calls deterministic existing services; candidate normalization, full schema/domain/profile/baseline checks stay enabled. No nested propose_plan LLM, no execute/apply/freeze tool.
- API failure text remains fixed/safe; no prompts, bodies or keys logged. Keys only from existing environment.
- Native SDK/tool cancellation, max4 physical requests/max6 tools/90s whole deadline, all retries0, are verified rather than assumed.

## Frozen cross-task interfaces

Runtime module `great_agent.runtime.conversation_agent` exports:
`AgentTurnResult(text: str, proposal: dict | None, state: dict, metrics: dict)` (dataclass) and
`AgentScopeConversationEngine(settings: ModelSettings, *, resource_root: Path, max_model_requests=4, max_tool_calls=6, timeout_seconds=90, transport=None)`.
`async reply(message: str, *, history: list[dict[str,str]], context: dict | None, state: dict | None, propose: Callable[[dict,str], Awaitable[dict]]) -> AgentTurnResult`.
`context` when bound = `{experiment_id, revision_id, revision, spec, read_only, tasks}`. Never let model change those bindings. `propose` returns `{proposal: <existing ProposalResponse dict>|None, no_change: bool}`; no inner model call. Runtime may call it once per turn, result tracked independently of final model prose. No-context runtime has no proposal tool. History roles/text only, bounded. Persisted state must not restore old permissions; on invalid state rebuild from history.

New API (existing /chat/proposals untouched):
- POST /api/v1/conversations `{experiment_id: UUID|null}` -> ConversationDetail. Idempotently ensure the default conversation for that scope (one global, one per experiment for this batch).
- GET /api/v1/conversations -> summaries; GET /api/v1/conversations/{id} -> detail.
- POST /api/v1/conversations/{id}/messages `{message: str, client_request_id: UUID}` -> TurnResponse (complete response, cancellable via HTTP disconnect). Same request ID+same input returns same completed turn; different input409; active other turn409.
- GET /api/v1/conversations/{id}/events -> persisted lifecycle SSE (started/completed/failed/interrupted). Token streaming is deferred; don't describe lifecycle events as token deltas.
- ConversationDetail `{id,experiment_id,title,messages:[Message]}`.
- Message `{id,role:'user'|'assistant',status:'completed'|'failed'|'interrupted',blocks:[{type:'text',text}|{type:'proposal',proposal:<existing ProposalResponse>}],request_id?:string}`.
- TurnResponse `{conversation_id,run_id,request_id,status:'completed',user_message:Message,assistant_message:Message,timings:dict}`.
- Failure existing safe error envelope; retain error.category and request_id. Persist run failure/interruption and visible safe assistant receipt for reload; no fake successful candidate.

Frontend APIs: ensureConversation(experimentId:string|null), getConversation(id), sendConversationMessage(id,message,clientRequestId,signal). Use UUIDs and existing fetch error parsing. Text and optional proposal coexist; don't invalidate pending/input checks merely for consultation.

## Review Focus
- SDK swallows cancellation/errors or performs extra calls through compression/structured fallback.
- Cross-session state leaks stale plan snapshot or persisted tool permissions.
- Duplicate/replayed/concurrent turns create duplicate proposals or corrupt message order.
- Interrupted turn after proposal save must preserve recoverable pending, never authorize execution.
- Frontend reload/cancel/switch races restore stale cards or clear current pending; frozen consultation must remain available.

## Task 1: Native Agent runtime and seeded resources (owner runtime agent)
Files: new runtime/conversation_agent.py and helpers, config/agent/SYSTEM.md + skills/*/SKILL.md, tests/test_conversation_agent.py and isolated compatibility scripts. Do not edit shared API/DB/config/CLI/frontend.
- [x] Add real-SDK MockTransport RED tests for theory text, read-only tools, modification via deterministic callback, mixed output, no-change, budgets/retries, state restoration, cancellation and secret-safe failures.
- [x] Implement native Agent + Toolkit + FunctionTool + LocalSkillLoader from installed2.0.11, no ReActAgent1.x API. Expose only capabilities/plan/tasks reads and bound proposal callback when writable. Skill hashes/seededSYSTEM version go into metrics. Existing knowledge search explicitly unavailable.
- [x] Own HTTP clients safely, preserve env proxies, enforce physical request and whole-run deadline beyond framework max_iters; no automatic retry/compression/structured generation outside budget. Thinkingdisabled initial GLM config.
- [x] Focused GREEN and recorded exact evidence/public API compatibility ADR. No paid calls; root coordinates bounded real smoke.

## Task 2: Durable conversation/service/API integration (owner backend agent)
Files: new db/conversation_models.py, db/conversation_repository.py, domain/conversation.py, services/conversation_service.py, api/conversations.py, migration0004_conversations.py; modify models import registration as needed, dependencies.py/cli.py/app.py/config.py, PlanService no-change guard if needed. No runtime/frontend edits.
- [x] RED tests durable default scopes/history, text-only/no mutation, mixedproposal, no-op preserves pending, frozen chat, idempotency/content mismatch, concurrent active turn, cancel/timeout, interrupted recovery, migration and safe failures.
- [x] Implement frozen API and default-scoped conversations. Agent state is versioned and safe; history separate from plan authorization. Capture snapshot before each turn; callback rechecks read-only/current revision and validates candidate, canonicalizes inherited stationgroup, no empty proposal. Preserve one proposal per run and replay never re-executes runtime.
- [x] SQLite run claim/lease or equivalent durable CAS handles active turns and crash interruption; prevent late callbacks/finish after run lost ownership. Keep legacy endpoint.
- [x] Bootstrap native engine using project config/agent resources. Add explicit conversation config budgets and feature fallback; defaults select new engine when configured. Do not access user DB before root backup/startup.
- [x] Lifecycle SSE with monotonic durable IDs, disconnection cancellation cleanup, safe error envelope. Focused GREEN; root owns final fullsuite.

## Task 3: Unified/persistent frontend (owner frontend agent)
Files: api/client.ts/types.ts, ChatPanel, ExperimentWorkspace, App home and optional new ConversationPanel/hooks, related tests/CSS. No backend/runtime edits.
- [x] RED tests ordinary consult no card/no check invalidation, mixed text+proposal, refresh persisted history, active pending survives consult, readonly/frozen consult, cancel/scope switch races, global consultation without experiment.
- [x] Integrate frozen API; restore authoritative messages and proposals while respecting local drafts/session card recovery. Manual editing still uses existing preview/accept and automatic input check; no auto XML/run. Consult while frozen enabled, proposal generation blocked server-side.
- [x] Update product language to “对话” and neutral progress/send states. Distinguish general explanation from library evidence (library not yet enabled). Avoid fictitious citation UI. Global home can chat without creating experiment.
- [x] Full frontend tests/typecheck/lint/build, report and scoped review; no timeout inflation.

## Task 4: Integration and rollout (root)
- [x] Full backend/ frontend checks and independent scoped review; fix findings through original owners.
- [x] Bounded real GLM synthetic probes cover theory and toolloop/modification, max budget recorded; no user plans/model tools/solver.
- [x] Browser verify global theory, bound mixed proposal, refresh, frozen consult and preserved accept/input/XML gates using isolated synthetic API.
- [x] Backup localDB; apply additive migration/startup registration and restart only owned API; verify new assets/health and conversation endpoints without real user plan modification.
- [x] Update acceptance/design status accurately: paper library/upload/OCR/backend publishing remain later work; snapshot/evidence retained, no commits.
