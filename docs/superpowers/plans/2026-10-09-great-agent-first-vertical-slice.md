# GREAT Agent First Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a native-Windows local Web Agent that turns chat or card edits into a frozen PPPFLT plan, validates read-only GNSS inputs, expands the requested date range into N recoverable daily jobs, runs GREAT_PVT, and exposes status, logs, and result files.

**Architecture:** A React/TypeScript browser talks to a FastAPI API over HTTP and SSE. FastAPI owns plan revisions and validation; a separate Python worker claims SQLite jobs and invokes GREAT through a deterministic `GreatPppfltAdapter`. Runtime files stay under `F:\GREAT_Agent\runs`; existing D/F-drive research inputs and legacy scripts remain read-only.

**Tech Stack:** Python 3.11, uv, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, SQLite, httpx, pytest, React 19, TypeScript, Vite, Vitest, React Testing Library, npm, native Windows PowerShell.

**Spec:** `docs/superpowers/specs/2026-10-09-great-agent-design.md`

## Global Constraints

- Run natively on Windows; Docker is optional future packaging, not a first-version dependency.
- Bind the API to `127.0.0.1` by default; first version has no login or remote access.
- Keep all new source, SQLite state, logs, and task output under `F:\GREAT_Agent`.
- Treat existing observation data, products, templates, executables, and legacy scripts as read-only.
- Never let the model provide executable shell text; it may only propose a typed plan candidate.
- Freeze a validated plan before creating jobs; a frozen plan is immutable.
- Expand the user's actual date set into N daily jobs; never hard-code 34.
- Keep all 15 configured stations in each daily XML; do not split stations into jobs.
- Use strict preflight: one missing required input blocks queue creation until the user revises or fixes the plan.
- Use argument-array process launches, isolated run directories, categorized errors, retained attempt logs, and explicit retries.
- Do not implement B2bOut PVTFLT, UPD computation, UPD archive, authentication, or time-series analysis in this plan.
- Use the local toolchain already present: Python 3.11.9, Node 24.19.0, npm 11.17.0, uv 0.11.4.

## Review Focus

- Cross-year ranges, leap-year DOY 366, overlapping segments, exclusions, and an empty final date set must be deterministic; Task 2 pins these cases.
- A model/card proposal containing an absolute output path, path traversal, unknown field, or command text must be rejected; Tasks 1, 3, and 9 pin these cases.
- Two workers racing for one pending job must not both claim it, and a stale running job must become interrupted without silent duplicate execution; Task 7 pins these cases.
- Exit code 0 with one or more missing/empty station results must fail as `missing_output_error`; Task 7 pins this case.
- Model timeout, invalid JSON, wrong schema version, or unsupported enum values must preserve manual card editing and must not mutate the current revision; Task 9 pins these cases.

---

## Locked File Map

```text
F:\GREAT_Agent\
├─ pyproject.toml
├─ uv.lock
├─ alembic.ini
├─ README.md
├─ config\
│  └─ app.example.toml
├─ backend\
│  ├─ alembic\
│  │  ├─ env.py
│  │  └─ versions\0001_initial_schema.py
│  ├─ src\great_agent\
│  │  ├─ __init__.py
│  │  ├─ app.py
│  │  ├─ cli.py
│  │  ├─ config.py
│  │  ├─ errors.py
│  │  ├─ domain\
│  │  │  ├─ dates.py
│  │  │  ├─ plans.py
│  │  │  └─ rules.py
│  │  ├─ db\
│  │  │  ├─ engine.py
│  │  │  ├─ models.py
│  │  │  └─ repositories.py
│  │  ├─ services\
│  │  │  ├─ plan_service.py
│  │  │  ├─ task_service.py
│  │  │  └─ chat_service.py
│  │  ├─ adapters\
│  │  │  ├─ model_provider.py
│  │  │  ├─ openai_compatible.py
│  │  │  ├─ great_pppflt.py
│  │  │  ├─ pppflt_xml.py
│  │  │  └─ pppflt_preflight.py
│  │  ├─ runtime\
│  │  │  ├─ run_storage.py
│  │  │  ├─ process_runner.py
│  │  │  ├─ result_inspector.py
│  │  │  └─ worker.py
│  │  └─ api\
│  │     ├─ dependencies.py
│  │     ├─ experiments.py
│  │     ├─ tasks.py
│  │     ├─ chat.py
│  │     └─ events.py
│  └─ tests\
│     ├─ fixtures\
│     │  ├─ minimal_pppflt.xml
│     │  └─ mock_solver.py
│     ├─ test_config.py
│     ├─ test_dates_and_rules.py
│     ├─ test_plan_service.py
│     ├─ test_pppflt_adapter.py
│     ├─ test_task_materialization.py
│     ├─ test_worker.py
│     ├─ test_api.py
│     ├─ test_chat.py
│     └─ test_mock_e2e.py
├─ frontend\
│  ├─ package.json
│  ├─ package-lock.json
│  ├─ vite.config.ts
│  └─ src\
│     ├─ api\client.ts
│     ├─ api\types.ts
│     ├─ components\ChatPanel.tsx
│     ├─ components\PlanCards.tsx
│     ├─ components\PlanDiff.tsx
│     ├─ components\TaskBoard.tsx
│     ├─ components\TaskLog.tsx
│     ├─ pages\ExperimentWorkspace.tsx
│     ├─ pages\RunDashboard.tsx
│     └─ tests\
│        ├─ ExperimentWorkspace.test.tsx
│        └─ RunDashboard.test.tsx
└─ scripts\
   ├─ dev.ps1
   ├─ start.ps1
   ├─ stop.ps1
   └─ real-smoke.ps1
```

Each file has one primary responsibility. Domain files contain no FastAPI, SQLAlchemy, UI, or process code. Adapters translate external systems. Services own use cases. API modules only translate HTTP. Runtime modules own processes and files.

### Task 1: Repository Bootstrap, Local Configuration, and Health Check

**Files:**
- Create: `pyproject.toml`
- Create: `config/app.example.toml`
- Create: `backend/src/great_agent/__init__.py`
- Create: `backend/src/great_agent/config.py`
- Create: `backend/src/great_agent/app.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_config.py`
- Create: `backend/tests/test_health.py`

**Interfaces:**
- Consumes: Windows environment and a TOML configuration path.
- Produces: `AppSettings.load(path: Path, environ: Mapping[str, str]) -> AppSettings` and `create_app(settings: AppSettings) -> FastAPI`.

- [ ] **Step 1: Write failing settings and health tests**

```python
# backend/tests/test_config.py
def test_settings_reject_run_root_outside_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    config = write_config(project, run_root=tmp_path / "outside")
    with pytest.raises(ValueError, match="run_root must be inside project_root"):
        AppSettings.load(config, {})

def test_api_key_only_comes_from_environment(valid_config):
    settings = AppSettings.load(valid_config, {"GREAT_AGENT_MODEL_API_KEY": "secret"})
    assert settings.model.api_key.get_secret_value() == "secret"
    assert "secret" not in settings.model_dump_json()
```

```python
# backend/tests/test_health.py
def test_health_is_local_ready(app):
    response = TestClient(app).get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "local"}
```

- [ ] **Step 2: Run the tests and confirm the intended failures**

Run: `uv run pytest backend/tests/test_config.py backend/tests/test_health.py -v`

Expected: collection fails because `great_agent.config` and `great_agent.app` do not exist.

- [ ] **Step 3: Add the backend package and strict settings loader**

Use Python 3.11 `tomllib`; keep the API key out of TOML and load it from `GREAT_AGENT_MODEL_API_KEY`.

Create `pyproject.toml` with these bounded dependency families and package settings:

```toml
[project]
name = "great-agent"
version = "0.1.0"
requires-python = ">=3.11,<3.13"
dependencies = [
  "alembic>=1.14,<2",
  "fastapi>=0.115,<1",
  "httpx>=0.28,<1",
  "psutil>=6,<8",
  "pydantic>=2.10,<3",
  "sqlalchemy>=2.0,<3",
  "uvicorn[standard]>=0.34,<1",
]

[dependency-groups]
dev = ["pytest>=8,<10", "pytest-asyncio>=0.25,<2"]

[tool.uv]
package = true

[tool.pytest.ini_options]
pythonpath = ["backend/src"]
asyncio_mode = "auto"
```

```python
# backend/src/great_agent/config.py
class AppSettings(BaseModel):
    project_root: Path
    run_root: Path
    database_path: Path
    host: str = "127.0.0.1"
    port: int = 8765
    max_concurrency: int = Field(default=2, ge=1, le=8)
    model: ModelSettings
    pppflt: PppfltSettings

    @classmethod
    def load(cls, path: Path, environ: Mapping[str, str]) -> "AppSettings":
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        raw.setdefault("model", {})["api_key"] = environ.get(
            "GREAT_AGENT_MODEL_API_KEY", ""
        )
        settings = cls.model_validate(raw)
        settings.assert_safe_paths()
        return settings
```

`assert_safe_paths()` must resolve paths and require `run_root` and `database_path` to be descendants of `project_root`. It must require `host == "127.0.0.1"` for `mode = "local"`.

Add `config/app.example.toml` with `F:\GREAT_Agent` defaults, configurable GREAT/template/input paths, Qwen-compatible `base_url`, a non-secret model name, default concurrency 1, and maximum concurrency 2.

- [ ] **Step 4: Add the health endpoint and verify the package installs**

```python
# backend/src/great_agent/app.py
def create_app(settings: AppSettings) -> FastAPI:
    app = FastAPI(title="GREAT Agent", version="0.1.0")
    app.state.settings = settings

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "local"}

    return app
```

Run: `uv sync --all-groups`

Run: `uv run pytest backend/tests/test_config.py backend/tests/test_health.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the bootstrap**

```powershell
git add pyproject.toml uv.lock config backend/src/great_agent backend/tests
git commit -m "build: bootstrap local GREAT Agent backend"
```

### Task 2: Typed Experiment Domain, Date Expansion, and PPPFLT Rules

**Files:**
- Create: `backend/src/great_agent/errors.py`
- Create: `backend/src/great_agent/domain/dates.py`
- Create: `backend/src/great_agent/domain/plans.py`
- Create: `backend/src/great_agent/domain/rules.py`
- Create: `backend/tests/test_dates_and_rules.py`

**Interfaces:**
- Consumes: typed plan input from cards or a model proposal.
- Produces: `ExperimentSpec`, `YearDoy`, `expand_dates(spec) -> tuple[YearDoy, ...]`, and `required_products(scenario) -> frozenset[ProductKind]`.

- [ ] **Step 1: Write date and product-rule tests**

```python
def test_expand_dates_cross_year_overlap_and_exclusion():
    spec = make_spec(
        segments=[DateSegment(year=2024, start_doy=365, end_doy=366),
                  DateSegment(year=2025, start_doy=1, end_doy=2),
                  DateSegment(year=2025, start_doy=2, end_doy=3)],
        excluded=[YearDoy(year=2025, doy=2)],
    )
    assert expand_dates(spec) == (
        YearDoy(year=2024, doy=365), YearDoy(year=2024, doy=366),
        YearDoy(year=2025, doy=1), YearDoy(year=2025, doy=3),
    )

def test_non_leap_doy_366_is_rejected():
    with pytest.raises(PlanValidationError, match="2025 DOY 366"):
        expand_dates(make_spec(segments=[DateSegment(year=2025, start_doy=366, end_doy=366)]))

@pytest.mark.parametrize(("frequency", "ambiguity", "expected"), [
    ("DF", "float", set()),
    ("DF", "fixed", {ProductKind.UPD}),
    ("TF", "float", {ProductKind.IFCB}),
    ("TF", "fixed", {ProductKind.IFCB, ProductKind.UPD}),
])
def test_product_rules(frequency, ambiguity, expected):
    assert required_products(Scenario(
        mode="static", frequency=frequency, ambiguity=ambiguity
    )) == expected
```

Also pin an empty final date set, invalid reset hours, concurrency above the configured maximum, an unknown workflow, and duplicate station codes.

- [ ] **Step 2: Run tests and verify they fail**

Run: `uv run pytest backend/tests/test_dates_and_rules.py -v`

Expected: FAIL because the domain models and functions do not exist.

- [ ] **Step 3: Implement immutable Pydantic domain models**

```python
class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True)
    mode: Literal["static", "kinematic"]
    frequency: Literal["DF", "TF"]
    ambiguity: Literal["float", "fixed"]
    reset_hours: Literal[0, 4, 8] = 0

class ExperimentSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    workflow: Literal["pppflt"] = "pppflt"
    scenario: Scenario
    date_segments: tuple[DateSegment, ...]
    excluded_dates: tuple[YearDoy, ...] = ()
    station_group_id: str
    stations: tuple[str, ...]
    input_profile_id: str = "local_default"
    solver_profile_id: str = "great_local"
    template_profile_id: str = "pppflt_base"
    concurrency: int = Field(default=1, ge=1, le=8)
    outputs: frozenset[Literal["flt", "log"]] = frozenset({"flt", "log"})
```

Normalize station codes to uppercase, require exactly 15 unique stations for `default_15`, and keep the frozen station snapshot inside every revision. Profile identifiers must match `^[a-z0-9][a-z0-9_-]{0,63}$`; raw filesystem paths are configuration data and cannot enter `ExperimentSpec`. Add `validate_against_policy(spec, max_concurrency)` so a plan cannot exceed the configured local concurrency cap.

- [ ] **Step 4: Implement deterministic date expansion and product rules**

```python
def expand_dates(spec: ExperimentSpec) -> tuple[YearDoy, ...]:
    expanded: set[YearDoy] = set()
    for segment in spec.date_segments:
        maximum = 366 if calendar.isleap(segment.year) else 365
        if not 1 <= segment.start_doy <= segment.end_doy <= maximum:
            raise PlanValidationError(f"invalid range for {segment.year}")
        expanded.update(YearDoy(year=segment.year, doy=doy)
                        for doy in range(segment.start_doy, segment.end_doy + 1))
    remaining = tuple(sorted(expanded.difference(spec.excluded_dates)))
    if not remaining:
        raise PlanValidationError("date selection is empty")
    return remaining
```

Run: `uv run pytest backend/tests/test_dates_and_rules.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the domain**

```powershell
git add backend/src/great_agent/domain backend/src/great_agent/errors.py backend/tests/test_dates_and_rules.py
git commit -m "feat: add typed PPPFLT experiment domain"
```

### Task 3: SQLite Schema, Plan Revisions, Diff, and Freeze

**Files:**
- Create: `alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/versions/0001_initial_schema.py`
- Create: `backend/src/great_agent/db/engine.py`
- Create: `backend/src/great_agent/db/models.py`
- Create: `backend/src/great_agent/db/repositories.py`
- Create: `backend/src/great_agent/services/plan_service.py`
- Create: `backend/tests/test_plan_service.py`

**Interfaces:**
- Consumes: `ExperimentSpec` candidates.
- Produces: `PlanService.create_experiment`, `preview_revision`, `accept_revision`, `freeze_revision`, and immutable stored revisions.

- [ ] **Step 1: Write failing plan-lifecycle tests**

```python
def test_preview_does_not_mutate_current_revision(plan_service, base_spec):
    experiment = plan_service.create_experiment(base_spec)
    candidate = base_spec.model_copy(update={"concurrency": 2})
    preview = plan_service.preview_revision(experiment.id, candidate, source="card")
    assert preview.diff == [{"path": "/concurrency", "before": 1, "after": 2}]
    assert plan_service.get_current_spec(experiment.id).concurrency == 1

def test_frozen_revision_is_immutable(plan_service, base_spec):
    experiment = plan_service.create_experiment(base_spec)
    frozen = plan_service.freeze_revision(experiment.id, expected_revision=1)
    with pytest.raises(PlanFrozenError):
        plan_service.accept_revision(experiment.id, base_spec, expected_revision=frozen.number)

def test_candidate_rejects_output_path_and_command_fields(plan_service, base_spec):
    payload = base_spec.model_dump()
    payload["command"] = "del /s D:\\Data"
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(payload)
```

- [ ] **Step 2: Run tests and confirm schema/service failures**

Run: `uv run pytest backend/tests/test_plan_service.py -v`

Expected: FAIL because database and plan service modules are absent.

- [ ] **Step 3: Create the migration and repository models**

The first migration must create:

- `experiments(id, status, current_revision_id, created_at, updated_at)`;
- `plan_revisions(id, experiment_id, number, source, spec_json, is_frozen, created_at)`;
- `daily_tasks(id, experiment_id, revision_id, year, doy, status, active_attempt_id, created_at, updated_at)`;
- `task_attempts(id, task_id, number, status, pid, exit_code, error_code, started_at, finished_at, log_path)`;
- `task_events(id, task_id, sequence, kind, payload_json, created_at)`;
- `result_files(id, task_id, attempt_id, station, kind, path, size_bytes, created_at)`.

Add unique constraints on `(experiment_id, number)`, `(experiment_id, revision_id, year, doy)`, `(task_id, number)`, and `(task_id, sequence)`. Enable SQLite foreign keys and WAL mode in `create_engine_and_session_factory()`.

- [ ] **Step 4: Implement recursive diff and optimistic revision control**

```python
@dataclass(frozen=True)
class FieldDiff:
    path: str
    before: JsonValue
    after: JsonValue

def diff_specs(before: ExperimentSpec, after: ExperimentSpec) -> tuple[FieldDiff, ...]:
    return tuple(diff_json(before.model_dump(mode="json"), after.model_dump(mode="json")))
```

`accept_revision()` must require `expected_revision`, validate the candidate, insert the next revision in one transaction, and reject concurrent stale edits with `RevisionConflictError`. `freeze_revision()` must mark exactly one revision frozen and update experiment status to `ready`.

Run: `uv run alembic upgrade head`

Run: `uv run pytest backend/tests/test_plan_service.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit persistence and revisioning**

```powershell
git add alembic.ini backend/alembic backend/src/great_agent/db backend/src/great_agent/services/plan_service.py backend/tests/test_plan_service.py
git commit -m "feat: persist versioned experiment plans"
```

### Task 4: Structure-Safe PPPFLT XML Renderer and Strict Preflight

**Files:**
- Create: `backend/src/great_agent/adapters/pppflt_xml.py`
- Create: `backend/src/great_agent/adapters/pppflt_preflight.py`
- Create: `backend/src/great_agent/adapters/great_pppflt.py`
- Create: `backend/tests/fixtures/minimal_pppflt.xml`
- Create: `backend/tests/test_pppflt_adapter.py`

**Interfaces:**
- Consumes: frozen `ExperimentSpec`, one `YearDoy`, `PppfltSettings`, a template path, and a task output directory.
- Produces: `GreatPppfltAdapter.render_task(...) -> RenderedTask`, `preflight_task(...) -> PreflightReport`, and `build_command(...) -> tuple[str, ...]`.

- [ ] **Step 1: Write XML and preflight tests before implementation**

```python
def test_df_float_xml_is_valid_and_removes_upd_ifcb(adapter, df_float_spec, tmp_path):
    rendered = adapter.render_task(df_float_spec, YearDoy(year=2024, doy=350), tmp_path)
    root = ET.parse(rendered.xml_path).getroot()
    assert root.find("./inputs/upd") is None
    assert root.find("./inputs/ifcb") is None
    assert root.findtext("./process/frequency").strip() == "2"
    assert root.findtext("./ambiguity/fix_mode").strip() == "NO"
    assert len(root.findtext("./gen/rec").split()) == 15
    assert root.find("./filter").attrib["reset_par"] == "0"

def test_rendered_paths_match_target_doy(adapter, df_float_spec, tmp_path):
    root = ET.parse(adapter.render_task(
        df_float_spec, YearDoy(year=2025, doy=6), tmp_path).xml_path
    ).getroot()
    assert "2025\\006" in root.findtext("./inputs/rinexo")
    assert "2025006" in root.findtext("./inputs/rinexn")
    assert "2024\\350" not in ET.tostring(root, encoding="unicode")

def test_strict_preflight_reports_exact_missing_station(adapter, df_float_spec, tmp_path):
    report = adapter.preflight_task(
        df_float_spec, YearDoy(year=2024, doy=350), tmp_path
    )
    assert report.ok is False
    assert any(item.station == "AHB0" and item.kind == "observation" for item in report.missing)
```

Add parameterized assertions for DF Fixed, TF Float, TF Fixed, reset 0/4/8, static/kinematic, output path containment, and a template containing unrelated `2024` or `350` text that must remain unchanged.

- [ ] **Step 2: Run adapter tests and verify failure**

Run: `uv run pytest backend/tests/test_pppflt_adapter.py -v`

Expected: FAIL because adapter modules do not exist.

- [ ] **Step 3: Implement XML edits through ElementTree nodes**

```python
def set_text(root: Element, xpath: str, value: str) -> None:
    node = root.find(xpath)
    if node is None:
        raise XmlValidationError(f"missing XML node: {xpath}")
    node.text = f" {value} "

def remove_input(root: Element, name: str) -> None:
    inputs = require_node(root, "./inputs")
    node = inputs.find(name)
    if node is not None:
        inputs.remove(node)
```

Parse with `TreeBuilder(insert_comments=True)`. Edit exact nodes and attributes; never use global string replacement. Render all input names from configured templates using `year`, zero-padded `doy`, two-digit year, station uppercase/lowercase, and configured product roots. Write outputs into `task_dir / "results"`.

- [ ] **Step 4: Implement strict preflight and semantic round-trip validation**

`PreflightReport` must list every required file with kind, date, optional station, path, and state. `render_task()` must parse the newly written XML again and compare mode, frequency, ambiguity, reset, date, station list, required/forbidden product nodes, and output root against the frozen plan.

The command must be exactly:

```python
(str(settings.executable), "-x", str(rendered.xml_path))
```

Run: `uv run pytest backend/tests/test_pppflt_adapter.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the deterministic adapter**

```powershell
git add backend/src/great_agent/adapters backend/tests/fixtures/minimal_pppflt.xml backend/tests/test_pppflt_adapter.py
git commit -m "feat: add safe PPPFLT adapter and preflight"
```

### Task 5: Isolated Run Storage and Dynamic Daily Task Materialization

**Files:**
- Create: `backend/src/great_agent/runtime/run_storage.py`
- Create: `backend/src/great_agent/services/task_service.py`
- Create: `backend/tests/test_task_materialization.py`

**Interfaces:**
- Consumes: a frozen revision, expanded dates, repository transaction, and `RunStorage`.
- Produces: `TaskService.materialize(experiment_id) -> tuple[DailyTaskRecord, ...]` and stable task directories.

- [ ] **Step 1: Write failing dynamic-materialization tests**

```python
def test_materialize_creates_one_task_per_unique_date(task_service, frozen_experiment):
    tasks = task_service.materialize(frozen_experiment.id)
    assert [(task.year, task.doy) for task in tasks] == [
        (2024, 365), (2024, 366), (2025, 1), (2025, 3)
    ]
    assert all(task.station_count == 15 for task in tasks)

def test_materialize_is_idempotent(task_service, frozen_experiment):
    first = task_service.materialize(frozen_experiment.id)
    second = task_service.materialize(frozen_experiment.id)
    assert [task.id for task in second] == [task.id for task in first]

def test_storage_rejects_escape(run_storage):
    with pytest.raises(UnsafePathError):
        run_storage.resolve("..", "outside")
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `uv run pytest backend/tests/test_task_materialization.py -v`

Expected: FAIL because `RunStorage` and `TaskService` do not exist.

- [ ] **Step 3: Implement safe run paths and atomic JSON snapshots**

```python
class RunStorage:
    def task_dir(self, experiment_id: UUID, date: YearDoy) -> Path:
        return self._inside_root(str(experiment_id), "tasks", f"{date.year:04d}_{date.doy:03d}")

    def atomic_json(self, path: Path, value: JsonValue) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
```

Create `plan/revisions`, `tasks/<year>_<doy>/attempts`, and `summary.json` only beneath the configured run root. Store the frozen plan before inserting tasks.

- [ ] **Step 4: Implement preflight-gated task creation**

`materialize()` must:

1. require a frozen revision;
2. expand the actual dates;
3. preflight every date before inserting any task;
4. return one aggregated missing-input report if any date fails;
5. insert all daily tasks in one transaction only when every date passes;
6. write a manifest containing the plan hash, template path, template size/mtime, executable path, station snapshot, and input paths.

Run: `uv run pytest backend/tests/test_task_materialization.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit storage and materialization**

```powershell
git add backend/src/great_agent/runtime/run_storage.py backend/src/great_agent/services/task_service.py backend/tests/test_task_materialization.py
git commit -m "feat: materialize isolated daily PPPFLT tasks"
```

### Task 6: Atomic Task Claiming and Attempt State Machine

**Files:**
- Modify: `backend/src/great_agent/db/repositories.py`
- Create: `backend/src/great_agent/runtime/result_inspector.py`
- Create: `backend/tests/test_worker.py`

**Interfaces:**
- Consumes: pending daily tasks and expected 15-station output names.
- Produces: `TaskRepository.claim_next(worker_id) -> ClaimedTask | None`, attempt transitions, and `PppfltResultInspector.inspect(...) -> ResultReport`.

- [ ] **Step 1: Write race, recovery, and result-completeness tests**

```python
def test_two_workers_cannot_claim_same_task(repo_factory, pending_task):
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda worker: repo_factory().claim_next(worker), ["w1", "w2"]))
    assert sum(claim is not None for claim in claims) == 1

def test_stale_running_task_becomes_interrupted(repository, running_task):
    repository.recover_stale_tasks(active_pids=set())
    assert repository.get_task(running_task.id).status == "interrupted"

def test_zero_exit_with_one_missing_station_is_failure(result_inspector, attempt_dir):
    write_outputs(attempt_dir, stations=DEFAULT_STATIONS[:-1])
    report = result_inspector.inspect(attempt_dir, expected_stations=DEFAULT_STATIONS)
    assert report.ok is False
    assert report.error_code == "missing_output_error"
    assert report.missing_stations == (DEFAULT_STATIONS[-1],)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest backend/tests/test_worker.py -v -k "claim or stale or missing_station"`

Expected: FAIL because claim/recovery/result methods are absent.

- [ ] **Step 3: Implement one-statement SQLite claiming**

Use an `UPDATE ... RETURNING` statement inside a short transaction:

```sql
UPDATE daily_tasks
SET status = 'running', worker_id = :worker_id, updated_at = :now
WHERE id = (
  SELECT id FROM daily_tasks
  WHERE status = 'pending'
  ORDER BY year, doy
  LIMIT 1
)
AND status = 'pending'
RETURNING *;
```

Create an attempt row in the same transaction. State-transition methods must require the expected previous state and reject invalid transitions.

- [ ] **Step 4: Implement stale recovery and exact result inspection**

`recover_stale_tasks(active_pids)` marks a task interrupted only if its stored PID is absent. If the PID is alive, keep the task blocked and publish an event requiring user action. `PppfltResultInspector` must perform case-insensitive station matching, require non-empty FLT files for all 15 stations, index log and result sizes, and return a categorized report.

Run: `uv run pytest backend/tests/test_worker.py -v -k "claim or stale or missing_station"`

Expected: selected tests pass.

- [ ] **Step 5: Commit task claiming and result checks**

```powershell
git add backend/src/great_agent/db/repositories.py backend/src/great_agent/runtime/result_inspector.py backend/tests/test_worker.py
git commit -m "feat: add atomic task claims and result validation"
```

### Task 7: Windows Process Runner, Worker Loop, Cancellation, and Retry

**Files:**
- Create: `backend/src/great_agent/runtime/process_runner.py`
- Create: `backend/src/great_agent/runtime/worker.py`
- Create: `backend/tests/fixtures/mock_solver.py`
- Modify: `backend/tests/test_worker.py`

**Interfaces:**
- Consumes: claimed task, rendered XML, command tuple, repository, and result inspector.
- Produces: `ProcessRunner.run(...) -> ProcessOutcome`, `Worker.run_once()`, `Worker.cancel_experiment()`, and retry attempts.

- [ ] **Step 1: Add failing process lifecycle tests**

```python
async def test_worker_records_success_and_files(worker, successful_task):
    await worker.run_once()
    task = worker.repository.get_task(successful_task.id)
    assert task.status == "succeeded"
    assert task.active_attempt.exit_code == 0
    assert len(task.result_files(kind="flt")) == 15

async def test_worker_records_nonzero_exit(worker, failing_task):
    await worker.run_once()
    attempt = worker.repository.get_task(failing_task.id).active_attempt
    assert attempt.status == "failed"
    assert attempt.error_code == "solver_exit_error"

async def test_cancel_stops_process_and_preserves_log(worker, slow_task):
    run = asyncio.create_task(worker.run_once())
    await wait_until_running(slow_task.id)
    await worker.cancel_experiment(slow_task.experiment_id)
    await run
    task = worker.repository.get_task(slow_task.id)
    assert task.status == "cancelled"
    assert Path(task.active_attempt.log_path).is_file()
```

- [ ] **Step 2: Run lifecycle tests and verify failure**

Run: `uv run pytest backend/tests/test_worker.py -v -k "records_success or nonzero_exit or cancel"`

Expected: FAIL because process runner and worker are absent.

- [ ] **Step 3: Implement safe Windows subprocess execution**

```python
process = await asyncio.create_subprocess_exec(
    *command,
    cwd=str(task_dir),
    stdout=log_file,
    stderr=asyncio.subprocess.STDOUT,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
)
```

Persist the PID immediately. On cancellation, call `terminate()`, wait for the configured grace period, then call `kill()` if needed. Never invoke `shell=True`.

- [ ] **Step 4: Implement worker concurrency and retries**

The worker must use an `asyncio.Semaphore(settings.max_concurrency)`, claim tasks until slots are full, emit ordered task events, and sleep only when no work exists. Retry must create attempt number `previous + 1`, reset only the selected failed/interrupted task to pending, and retain every old attempt directory and log.

Run: `uv run pytest backend/tests/test_worker.py -v`

Expected: all worker tests pass.

- [ ] **Step 5: Commit runtime execution**

```powershell
git add backend/src/great_agent/runtime backend/tests/fixtures/mock_solver.py backend/tests/test_worker.py
git commit -m "feat: run recoverable GREAT tasks on Windows"
```

### Task 8: FastAPI Experiment, Task, Result, and SSE Endpoints

**Files:**
- Create: `backend/src/great_agent/api/dependencies.py`
- Create: `backend/src/great_agent/api/experiments.py`
- Create: `backend/src/great_agent/api/tasks.py`
- Create: `backend/src/great_agent/api/events.py`
- Modify: `backend/src/great_agent/app.py`
- Create: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: PlanService, TaskService, repositories, and task events.
- Produces: versioned `/api/v1` endpoints and SSE event streams.

- [ ] **Step 1: Write failing API contract tests**

```python
def test_full_manual_plan_flow(client, valid_spec_payload):
    created = client.post("/api/v1/experiments", json=valid_spec_payload).json()
    preview = client.post(
        f"/api/v1/experiments/{created['id']}/proposals",
        json={"source": "card", "candidate": {**valid_spec_payload, "concurrency": 2}},
    ).json()
    assert preview["diff"] == [{"path": "/concurrency", "before": 1, "after": 2}]
    accepted = client.post(
        f"/api/v1/experiments/{created['id']}/revisions",
        json={"proposal_id": preview["id"], "expected_revision": 1},
    )
    assert accepted.status_code == 201
    assert client.post(f"/api/v1/experiments/{created['id']}/freeze").status_code == 200

def test_run_is_blocked_when_preflight_has_missing_inputs(client, frozen_experiment):
    response = client.post(f"/api/v1/experiments/{frozen_experiment.id}/run")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "preflight_error"
```

Add tests for stale expected revision (409), editing a frozen plan (409), retrying a succeeded task (409), stopping an unknown experiment (404), and result paths outside run root (500 plus security event).

- [ ] **Step 2: Run API tests and verify failure**

Run: `uv run pytest backend/tests/test_api.py -v`

Expected: FAIL/404 because routers are not registered.

- [ ] **Step 3: Implement thin routers and normalized errors**

Routers must delegate to services and return errors in this shape:

```json
{
  "error": {
    "code": "preflight_error",
    "message": "Required inputs are missing",
    "details": [{"year": 2024, "doy": 350, "station": "AHB0", "path": "..."}]
  }
}
```

Register endpoints from the design spec. Keep SQLAlchemy models out of response bodies; define Pydantic response types.

- [ ] **Step 4: Implement resumable SSE**

`GET /api/v1/tasks/{id}/events` must emit `id`, `event`, and JSON `data`, accept `Last-Event-ID`, and replay only events with a larger sequence before tailing new rows.

Run: `uv run pytest backend/tests/test_api.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit HTTP and SSE APIs**

```powershell
git add backend/src/great_agent/api backend/src/great_agent/app.py backend/tests/test_api.py
git commit -m "feat: expose experiment and task APIs"
```

### Task 9: Qwen-Compatible Model Provider and Safe Chat Proposals

**Files:**
- Create: `backend/src/great_agent/adapters/model_provider.py`
- Create: `backend/src/great_agent/adapters/openai_compatible.py`
- Create: `backend/src/great_agent/services/chat_service.py`
- Create: `backend/src/great_agent/api/chat.py`
- Modify: `backend/src/great_agent/app.py`
- Create: `backend/tests/test_chat.py`

**Interfaces:**
- Consumes: user message, current `ExperimentSpec`, model settings, and a model provider.
- Produces: `ChatProposal(candidate: ExperimentSpec, explanation: str, diff: tuple[FieldDiff, ...])`; it never accepts or freezes automatically.

- [ ] **Step 1: Write failing chat safety tests with a fake provider**

```python
def test_chat_returns_preview_without_mutating_revision(chat_service, experiment):
    proposal = chat_service.propose(experiment.id, "把日期改成350到360")
    assert proposal.candidate.date_segments[0] == DateSegment(
        year=2024, start_doy=350, end_doy=360
    )
    assert chat_service.plan_service.get_current_revision(experiment.id).number == 1

@pytest.mark.parametrize("provider_error", [
    ModelTimeout("timeout"), InvalidModelJson("not json"),
    InvalidModelSchema({"schema_version": "9.0"}),
])
def test_model_failure_keeps_manual_plan_available(chat_service, experiment, provider_error):
    chat_service.provider.raise_on_call = provider_error
    with pytest.raises(ModelProposalError):
        chat_service.propose(experiment.id, "改成三频")
    assert chat_service.plan_service.get_current_revision(experiment.id).number == 1

def test_model_cannot_inject_command(chat_service, experiment):
    chat_service.provider.response = {"candidate": {"command": "format F:"}}
    with pytest.raises(ModelProposalError, match="schema"):
        chat_service.propose(experiment.id, "执行命令")
```

- [ ] **Step 2: Run chat tests and verify failure**

Run: `uv run pytest backend/tests/test_chat.py -v`

Expected: FAIL because provider/chat modules are absent.

- [ ] **Step 3: Implement the provider protocol and OpenAI-compatible adapter**

```python
class ModelProvider(Protocol):
    async def propose_plan(
        self, message: str, current: ExperimentSpec | None
    ) -> ModelPlanProposal: ...
```

Use `httpx.AsyncClient` with configured `base_url`, bearer key, model, connect/read timeouts, and a JSON-schema response request. The system prompt must state the allowed workflow and enums, include the current plan, require a complete candidate spec, and forbid paths, commands, XML, or invented files.

- [ ] **Step 4: Validate provider output before creating a proposal**

Parse response text as JSON, validate `ModelPlanProposal`, validate its candidate as `ExperimentSpec`, compute the server-side diff, and return a preview only. Register `POST /api/v1/chat/proposals`.

Run: `uv run pytest backend/tests/test_chat.py backend/tests/test_api.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit model-assisted plan proposals**

```powershell
git add backend/src/great_agent/adapters/model_provider.py backend/src/great_agent/adapters/openai_compatible.py backend/src/great_agent/services/chat_service.py backend/src/great_agent/api/chat.py backend/src/great_agent/app.py backend/tests/test_chat.py
git commit -m "feat: add safe Qwen plan proposals"
```

### Task 10: React Experiment Workspace with Chat, Cards, and Diff Approval

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/components/ChatPanel.tsx`
- Create: `frontend/src/components/PlanCards.tsx`
- Create: `frontend/src/components/PlanDiff.tsx`
- Create: `frontend/src/pages/ExperimentWorkspace.tsx`
- Create: `frontend/src/tests/ExperimentWorkspace.test.tsx`

**Interfaces:**
- Consumes: `/experiments`, `/chat/proposals`, `/proposals`, `/revisions`, `/validate`, and `/freeze` APIs.
- Produces: an editable experiment workspace that never executes without an explicit frozen-plan confirmation.

- [ ] **Step 1: Scaffold Vite and write failing workspace tests**

Run the exact scaffold and dependency commands:

```powershell
npm create vite@latest frontend -- --template react-ts
npm --prefix frontend install
npm --prefix frontend install -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom
```

Set `test`, `typecheck`, and `build` scripts in `frontend/package.json`; configure Vitest with the `jsdom` environment and a test setup file that imports `@testing-library/jest-dom/vitest`.

```tsx
it("previews chat changes before accepting a revision", async () => {
  render(<ExperimentWorkspace experimentId="exp-1" />, { wrapper: TestProviders });
  await userEvent.type(screen.getByRole("textbox", { name: /message/i }), "改成三频");
  await userEvent.click(screen.getByRole("button", { name: /发送/i }));
  expect(await screen.findByText("frequency")).toBeInTheDocument();
  expect(screen.getByText("DF → TF")).toBeInTheDocument();
  expect(api.acceptRevision).not.toHaveBeenCalled();
});

it("submits card edits through the same proposal flow", async () => {
  render(<ExperimentWorkspace experimentId="exp-1" />, { wrapper: TestProviders });
  await userEvent.selectOptions(screen.getByLabelText("频率"), "TF");
  await userEvent.click(screen.getByRole("button", { name: /预览修改/i }));
  expect(api.previewProposal).toHaveBeenCalledWith(expect.objectContaining({ source: "card" }));
});
```

Add tests that freeze is disabled while validation fails, API keys never appear in browser config, and a stale revision conflict reloads the latest plan instead of overwriting it.

- [ ] **Step 2: Run frontend tests and verify failure**

Run: `npm --prefix frontend test -- --run`

Expected: FAIL because workspace components do not exist.

- [ ] **Step 3: Implement the three-column workspace**

Build:

- chat history and message composer;
- scenario, date, station, input, runtime, output, and preflight cards;
- proposal diff drawer with accept/reject controls;
- revision indicator and frozen badge;
- accessible labels, keyboard focus, loading states, and categorized error banners.

Use one `previewProposal(candidate, source)` client method for both direct card editing and model proposals.

- [ ] **Step 4: Verify UI tests, type checking, and production build**

Run: `npm --prefix frontend test -- --run`

Run: `npm --prefix frontend run typecheck`

Run: `npm --prefix frontend run build`

Expected: tests pass, TypeScript reports no errors, and Vite produces `frontend/dist`.

- [ ] **Step 5: Commit the planning workspace**

```powershell
git add frontend
git commit -m "feat: add chat and editable experiment cards"
```

### Task 11: React Run Dashboard, Logs, Results, and Controls

**Files:**
- Create: `frontend/src/components/TaskBoard.tsx`
- Create: `frontend/src/components/TaskLog.tsx`
- Create: `frontend/src/pages/RunDashboard.tsx`
- Create: `frontend/src/tests/RunDashboard.test.tsx`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/types.ts`

**Interfaces:**
- Consumes: experiment task list, task SSE stream, stop/retry endpoints, and result-file endpoints.
- Produces: live overall progress, filterable daily jobs, per-attempt logs, retry/stop actions, and result links.

- [ ] **Step 1: Write failing dashboard tests**

```tsx
it("updates daily task state from SSE without duplicating events", async () => {
  render(<RunDashboard experimentId="exp-1" />, { wrapper: TestProviders });
  mockEvents.emit({ id: "12", event: "task_status", data: { taskId: "t1", status: "running" } });
  mockEvents.emit({ id: "12", event: "task_status", data: { taskId: "t1", status: "running" } });
  expect(await screen.findAllByText("运行中")).toHaveLength(1);
});

it("allows retry only for failed or interrupted jobs", async () => {
  render(<RunDashboard experimentId="exp-1" />, { wrapper: TestProviders });
  expect(screen.getByRole("button", { name: /重试 2024-350/i })).toBeEnabled();
  expect(screen.queryByRole("button", { name: /重试 2024-351/i })).not.toBeInTheDocument();
});
```

Add tests for stop confirmation, event reconnect with `Last-Event-ID`, missing-output error details, attempt switching, and empty result state.

- [ ] **Step 2: Run dashboard tests and verify failure**

Run: `npm --prefix frontend test -- --run RunDashboard`

Expected: FAIL because dashboard components do not exist.

- [ ] **Step 3: Implement task board and event hook**

Display totals for pending/running/succeeded/failed/cancelled/interrupted, filter by year/DOY/status, show the selected attempt log, and display result filename, station, size, and open/download action. Reconnect SSE with the last accepted sequence and ignore duplicates.

- [ ] **Step 4: Verify complete frontend**

Run: `npm --prefix frontend test -- --run`

Run: `npm --prefix frontend run typecheck`

Run: `npm --prefix frontend run build`

Expected: all frontend tests and build pass.

- [ ] **Step 5: Commit the run dashboard**

```powershell
git add frontend/src
git commit -m "feat: add live PPPFLT run dashboard"
```

### Task 12: Native Windows Launch, Mock End-to-End, Documentation, and Real Smoke Gate

**Files:**
- Create: `backend/src/great_agent/cli.py`
- Modify: `backend/src/great_agent/app.py`
- Modify: `pyproject.toml`
- Create: `scripts/dev.ps1`
- Create: `scripts/start.ps1`
- Create: `scripts/stop.ps1`
- Create: `scripts/real-smoke.ps1`
- Create: `backend/tests/test_mock_e2e.py`
- Create: `README.md`
- Create after an approved real run: `docs/validation/2026-10-09-real-smoke.md`

**Interfaces:**
- Consumes: built frontend, configured API, worker entry point, mock solver, and later the real GREAT_PVT path.
- Produces: one-command local startup, a complete mock experiment, and a gated real DOY 350 validation record.

- [ ] **Step 1: Write the failing mock end-to-end test**

```python
def test_mock_user_journey(mock_app, valid_spec_payload):
    client = TestClient(mock_app)
    experiment = client.post("/api/v1/experiments", json=valid_spec_payload).json()
    assert client.post(f"/api/v1/experiments/{experiment['id']}/validate").json()["ok"] is True
    client.post(f"/api/v1/experiments/{experiment['id']}/freeze").raise_for_status()
    client.post(f"/api/v1/experiments/{experiment['id']}/run").raise_for_status()
    run_worker_until_idle(mock_app.state.worker)
    tasks = client.get(f"/api/v1/experiments/{experiment['id']}/tasks").json()
    assert all(task["status"] == "succeeded" for task in tasks)
    assert all(len(task["results"]) == 15 for task in tasks)
```

Use a two-day cross-year spec in this test so the mock flow proves dynamic N task creation without a long run.

- [ ] **Step 2: Run the end-to-end test and verify failure**

Run: `uv run pytest backend/tests/test_mock_e2e.py -v`

Expected: FAIL until startup wiring, worker entry point, and mock configuration are complete.

- [ ] **Step 3: Implement native launch scripts**

Add a stdlib-`argparse` CLI with `api`, `worker`, and `preflight` subcommands, and register `great-agent = "great_agent.cli:main"` in `pyproject.toml`. `app.py` must mount `frontend/dist` in production while keeping `/api/v1` routes authoritative.

`dev.ps1` must run `uv run great-agent api --config config/local.toml`, `uv run great-agent worker --config config/local.toml`, and `npm --prefix frontend run dev` with visible development logs. `start.ps1` must build the frontend if `dist` is absent, start FastAPI on `127.0.0.1:8765`, start one worker process, record PIDs under `data/pids`, and open the browser. `stop.ps1` must read only those PID files, verify the process command includes `great_agent`, and then stop those exact processes.

Do not recursively kill Python or Node processes by name.

- [ ] **Step 4: Finish mock E2E and operator documentation**

README must include:

- prerequisites and `uv sync` / `npm install`;
- copying `config/app.example.toml` to ignored `config/local.toml`;
- setting `GREAT_AGENT_MODEL_API_KEY`;
- native start/stop commands;
- mock mode;
- input read-only and output-root guarantees;
- backup guidance for `data/great_agent.db` and `runs`;
- troubleshooting for model, preflight, XML, launch, solver, output, and interrupted errors;
- open-source note that GREAT binaries and research data are not bundled.

Run: `uv run pytest backend/tests -v`

Run: `npm --prefix frontend test -- --run`

Run: `npm --prefix frontend run build`

Expected: all backend/frontend tests and production build pass.

- [ ] **Step 5: Commit the complete mock vertical slice**

```powershell
git add scripts README.md backend/tests/test_mock_e2e.py
git commit -m "feat: complete local mock PPPFLT workflow"
```

- [ ] **Step 6: Run read-only real preflight for the approved smoke case**

Configure:

```text
2024 DOY 350
stations = default_15
mode = static
frequency = DF
ambiguity = float
reset_hours = 0
concurrency = 1
```

Run: `powershell -ExecutionPolicy Bypass -File .\scripts\real-smoke.ps1 -PreflightOnly`

Expected: generated XML is standard-valid; IFCB and UPD are absent; all 15 stations and required DF Float inputs are reported; no source file is modified; no GREAT process starts.

- [ ] **Step 7: Stop for explicit user confirmation before the real solver run**

Show the user the frozen plan, generated XML path, exact argument array, input report, output directory, and estimated job count. Do not proceed from preflight to execution without this confirmation.

- [ ] **Step 8: Execute and record the approved real smoke run**

Run: `powershell -ExecutionPolicy Bypass -File .\scripts\real-smoke.ps1 -Execute`

Expected: one daily task completes with exit code 0, one retained log, and 15 non-empty station FLT files. Record actual duration, executable identity, template identity, result filenames, any adjusted result pattern, and user scientific review in `docs/validation/2026-10-09-real-smoke.md`.

- [ ] **Step 9: Commit only validation documentation, never runtime data**

```powershell
git add docs/validation/2026-10-09-real-smoke.md README.md
git commit -m "docs: record real PPPFLT smoke validation"
```

## Final Verification Gate

Run all checks from `F:\GREAT_Agent`:

```powershell
uv run alembic upgrade head
uv run pytest backend/tests -v
npm --prefix frontend test -- --run
npm --prefix frontend run typecheck
npm --prefix frontend run build
git status --short
```

Expected:

- database migration succeeds from an empty database;
- every backend test passes;
- every frontend test passes;
- TypeScript reports no errors;
- production frontend build succeeds;
- `git status --short` is empty;
- `data`, `runs`, secrets, generated XML, logs, and FLT files are not tracked.

Then run the application in mock mode and manually verify:

1. Create a plan in chat;
2. Modify the same plan through cards;
3. review and accept a diff;
4. freeze the plan;
5. execute a two-day mock experiment;
6. observe SSE progress;
7. inspect logs and 15 outputs per date;
8. retry an induced failure;
9. stop an induced slow task;
10. restart services and verify interrupted-state handling.
