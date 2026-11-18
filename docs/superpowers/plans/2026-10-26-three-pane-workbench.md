# Three-Pane Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline. Do not commit/push or create another worktree.

**Goal:** Implement the approved resizable three-pane preview against the real API with all authorization gates retained.

**Architecture:** App owns routing/history; a single keyed ExperimentWorkspace owns proposal/chat state across center-view changes. ResizableWorkbench provides left/center/right slots. ArtifactBrowser loads one selected task/attempt and actual registered images or archived XML. Existing review and monitor components remain real, not simulated.

**Tech Stack:** Existing React/TypeScript/FastAPI; no dependencies added.

**Spec:** docs/superpowers/specs/2026-10-26-three-pane-workbench-design.md; user-approved prototype three-pane-workbench-v3-dark-xml.html.

## Global Constraints

- Canonical F:\GREAT_Agent; preserve dirty changes, no commit/push/new worktree.
- No real solver/model invocation; existing DOY350 data unchanged.
- Accept edits via validated backend proposal IDs; preview generation never confirms execution.
- Dark XML highlighting must not interpret source as HTML or change text.
- Restore old plan/review/run URLs and support plots; selection never starts work.
- The two existing max_tokens/deadline test failures stay reported, not removed or implemented.

## Review Focus

1. An edited model card must never accept its old proposal ID or lose additional date segments/exclusions.
2. View/experiment changes must not leak stale fetches, proposals, images or confirmations into another experiment.
3. Layout persistence must withstand blocked/corrupt localStorage, small viewports, cancellation/lost pointer capture.
4. Archived XML must be bound to task/attempt ownership and remain read-only; registered image loading must clear errors on selection.
5. Chat cancel/stale response safeguards and explicit execution checkbox reset must survive the restructure.

### Task 1: Layout, dark XML and real artifact primitives

**Files:** new components/ResizableWorkbench.tsx/.css, XmlCode.tsx/.css, ArtifactBrowser.tsx; api/client.ts/types.ts; tests/WorkbenchPrimitives.test.tsx and backend/tests/test_archived_xml.py if needed.

**Interfaces:** ResizableWorkbench({history,center,chat}) owns only widths/mobile pane. XmlCode({content}) renders unchanged text safely. ArtifactBrowser({experimentId,mode:'images'|'xml'}) reads only real tasks/attempt artifacts. Optional GET /tasks/{id}/attempts/{attempt}/xml returns {xml_path,xml_content}.

- [x] RED tests: keyboard resize persists/reset, corrupt storage safe, source XML text preserved/no HTML execution, actual image selection/empty, archived attempt ownership. Image-error recovery and remaining edge-case coverage deferred explicitly in validation record.
- [x] Implement minimal components and safe read-only archived XML endpoint.
- [x] Verify targeted tests GREEN and baseline frontend unchanged.

### Task 2: Unified controller, inline proposal cards and routing

**Files:** App.tsx/.css, pages/ExperimentWorkspace.tsx, components/ChatPanel.tsx, new ProposalCard.tsx and PlanSummary.tsx, tests/ExperimentWorkspace.test.tsx/App.test.tsx.

**Interfaces:** WorkbenchView='plan'|'review'|'plots'|'run'; workspace receives history/current view/navigation and keeps its chat mounted. Inline card edits are re-previewed before acceptance. All center actions use existing execution-preview/confirm APIs.

- [x] RED tests: card inside dialogue, local editing revalidates before acceptance, stale IDs never accepted; view switches preserve chat/pending card; history opens without writes; confirmation still checkbox gated.
- [x] Replace independent approval column with inline cards; center readonly parameters and actual review/images/monitor; preserve cancel/timing and error handling.
- [x] Integrate real history/routing, responsive approved styling, session isolation and safe history refresh.
- [x] Verify complete frontend tests, typecheck, lint and build.

### Task 3: Whole-system verification and delivery

- [x] Complete backend regression; report known two failures by name.
- [x] Independent fresh read-only review, one tested fix pass for significant findings.
- [x] Real browser verification on isolated synthetic full workflow; production GET-only historical images/XML; draggable widths and desktop/mobile screenshots in isolated browser.
- [x] Safely activate built frontend/API only if no real model/solver activity; retain credentials in memory and Worker unchanged.
- [x] Record test/deployment evidence, limitations and decisions; no claimed real numerical/model revalidation.

Completed evidence and remaining Minor items: docs/validation/2026-10-26-three-pane-workbench.md. Frontend47 passed; backend158 passed and2 known deferred failures. No real solver/model invocation, commits or pushes.
