# GREAT Agent Invited Trial Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare a reviewable deployment kit and read-only preflight for invited users without changing the running local instance.

**Architecture:** Linux Caddy/frps gateway forwards authenticated HTTPS requests through a certificate-verified tunnel to a separate Windows trial API on loopback 8766. Windows retains the SQLite database, knowledge files, runs and solver. Domain and cloud procurement remain user-owned prerequisites.

**Tech Stack:** Existing Python 3.11/FastAPI/React stack, Caddy, frp TOML, WinSW XML, systemd.

**Spec:** `docs/superpowers/specs/2026-11-15-invited-trial-deployment-design.md`

## Global Constraints

- Reuse the current checkout on codex/agentscope-p0-p1; only add deployment files and scoped ignore rules, preserving uncommitted user work. No commit, branch switch, dependency installation, service startup, migration, live solver, cloud write or publishing in this task.
- Trial port is 8766; existing 8765 and production data are untouched.
- Single API and single Solver Worker; shared workspace, no per-user resource authorization claim.
- Gateway remote port 18765 is loopback-only; frp authentication and server-certificate verification are mandatory.
- No credentials, exception bodies or HTTP response bodies in preflight reports. Configuration and templates contain placeholders only.
- Independent tasks use disjoint file ownership; final integration review covers their shared paths and ports.

## Review Focus

- Basic Auth protects all paths; unsafe Origin and missing Origin cannot trigger a remote write.
- TLS must verify the relay identity, and the private forwarding port must not bypass Caddy.
- Default checks cannot start work, change data, or send model/network requests.
- Health 200 with readiness 503 or a dead knowledge executor is not a passing probe.
- Templates cannot accidentally point at the production port/database or assume a service account inherits the interactive user's secrets.

---

### Task 1: Deployment templates

**Files:** Create `deploy/gateway/*`, `deploy/windows/*`, `config/trial.example.toml`.

**Interfaces:** Consumes the spec's ports and directories; produces editable examples referenced by the operator guide. Only example files are committed candidates; real configurations and secrets remain untracked.

- [x] Create gateway Caddy/frps/systemd and Windows frpc/WinSW examples using the agreed ports, full authentication, TLS server identity verification and explicit service working directories.
- [x] Create a schema-valid trial configuration with independent DB, runs and knowledge roots and concurrency 1.
- [x] Parse all TOML and XML examples. Use tool-native validation only if tools are already available; report unavailable checks accurately.

### Task 2: Read-only preflight

**Files:** Create `scripts/deploy_preflight.py`, `scripts/tests/test_deploy_preflight.py`.

**Interfaces:** `collect_checks(config_path: Path, *, probe: bool = False, environ: Mapping[str, str] | None = None) -> list[dict]`. CLI `--config PATH [--probe] [--report PATH]`; exit 0 on no failures, exit 1 if any check fails. Warnings do not imply readiness. Emit only check code/status/safe message and aggregate state.

- [x] Write behavior tests and observe RED: missing implementation, static mode never networks, sensitive invalid config not echoed, escaped or production trial resources not accepted, local live probe requires both worker and knowledge readiness, redirects/non-loopback refused.
- [x] Implement minimal static validation and opt-in loopback-only, no-proxy/no-redirect probe. Only explicitly requested report output may be written; no config or application runtime changes.
- [x] Run focused tests and existing script tests; inspect every failure. Run offline backend suite if integration changes require it.

### Task 3: Operator runbook and integration

**Files:** Create `docs/deployment/invited-trial.md`; append narrowly scoped rules to `.gitignore`.

**Interfaces:** Uses templates and preflight CLI from Tasks 1–2. Operator guide must exactly match filenames, ports, env names and working directories.

- [x] Write concrete Chinese procurement/setup/validation/rollback steps for a user with no domain or server. Include current-workspace packaging, TLS SAN/CA lifecycle, Windows service-account environment, initial migration only on trial config, separate service supervision, shared data limits and backup coverage.
- [x] Protect deployed copies, tokens, certificates, private keys, service binaries and logs through scoped ignore rules while retaining example files.
- [x] Independently review all deployment files; fix material gaps, rerun affected checks, record actual validation and remaining external prerequisites.

## Execution record

- User already accepted the architecture and asked to proceed; no repeated design approval is required. No infrastructure exists yet, so this run delivers local preparation only.
- Root owns Task 2 and final integration; template and runbook work may run in parallel on disjoint files. This follows session-level proactive delegation requirements while preserving user work.
- Preflight tests observed RED before implementation (15 missing-implementation failures), then GREEN (15 passed). Existing script suite: 72 passed, 2 skipped under the offline test runner. An initial invocation loaded pytest-asyncio twice; reran with the runner's required PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, without code changes.
- Root independently parsed 3 TOML and 3 XML files and validated trial AppSettings and relay port/TLS/tokenSource contracts. Caddy/frp/WinSW/systemd native validators are unavailable; no downloads, installation, startup or cloud actions performed.
- Static inspection of existing local configuration deliberately fails trial isolation and concurrency checks while confirming its configured solver, template, input roots and built assets exist. This is not a release failure or authorization to reuse production state; report: output/deployment/existing-local-static-check-20261115.json. No live probe performed.
- Review ruling: use protected tokenSource files instead of Go environment templates, avoiding unset variables rendering a known shared token. Default frp behavior does not reject blank files, so startup/operator format validation is required.
- Review fix: ignore /local/trial-release/ as well as direct deployment runtime copies; git check-ignore confirms nested configuration, XML, token and CA examples are protected. Without this rule a nested release containing credentials could be staged accidentally.
- Operational ruling: recommend an independent complete release root before invitations because existing backup protection and frontend builds are shared at project_root level. Do not loosen backup checks or stop the existing service to bypass this constraint.
- Independent read-only final review: approved with no unresolved critical, important or minor findings after the nested-release ignore fix. Manual startup templates are promoted to delayed automatic only after acceptance; the guide explicitly requires a separately arranged machine reboot window to avoid disrupting the existing instance. No live deployment was performed.
