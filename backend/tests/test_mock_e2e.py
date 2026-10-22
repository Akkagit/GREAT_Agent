from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from great_agent.adapters.great_pppflt import GreatPppfltAdapter
from great_agent.cli import _preflight, build_app
from great_agent.config import AppSettings, ModelSettings, PppfltSettings
from great_agent.domain.dates import DateSegment, YearDoy
from great_agent.domain.plans import ExperimentSpec, Scenario


STATIONS = tuple(f"S{i:03d}" for i in range(15))


@pytest.fixture
def mock_context(tmp_path: Path):
    project = tmp_path / "project"; project.mkdir()
    template = project / "template.xml"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "minimal_pppflt.xml", template)
    executable = project / "mock.exe"; executable.write_text("mock", encoding="utf-8")
    pppflt = PppfltSettings(
        executable=executable, template_path=template,
        observation_root=project / "observations", product_root=project / "products",
    )
    settings = AppSettings(
        project_root=project, run_root=project / "runs",
        database_path=project / "data" / "state.db", max_concurrency=2,
        model=ModelSettings(base_url="https://example.invalid/v1", name="mock"),
        pppflt=pppflt,
    )
    settings.assert_safe_paths()
    spec = ExperimentSpec(
        scenario=Scenario(mode="static", frequency="DF", ambiguity="float"),
        date_segments=(
            DateSegment(year=2024, start_doy=366, end_doy=366),
            DateSegment(year=2025, start_doy=1, end_doy=1),
        ),
        station_group_id="default_15", stations=STATIONS, concurrency=2,
    )
    adapter = GreatPppfltAdapter(pppflt)
    for date in (YearDoy(year=2024, doy=366), YearDoy(year=2025, doy=1)):
        report = adapter.preflight_task(spec, date, settings.run_root / "preflight")
        for item in report.missing:
            item.path.parent.mkdir(parents=True, exist_ok=True)
            item.path.write_text("input", encoding="utf-8")
    solver = Path(__file__).parent / "fixtures" / "mock_solver.py"
    command_factory = lambda rendered, attempt_dir: (
        sys.executable, str(solver), "--mode", "success", "--output-dir",
        str(rendered.output_dir), "--stations", ",".join(STATIONS),
    )
    return build_app(settings, command_factory=command_factory), spec


def test_mock_user_journey(mock_context) -> None:
    app, spec = mock_context
    client = TestClient(app)
    experiment = client.post(
        "/api/v1/experiments", json=spec.model_dump(mode="json")
    ).json()
    experiment_id = experiment["id"]

    assert client.post(f"/api/v1/experiments/{experiment_id}/validate").json()["ok"] is True
    client.post(f"/api/v1/experiments/{experiment_id}/freeze").raise_for_status()
    run = client.post(f"/api/v1/experiments/{experiment_id}/run")
    run.raise_for_status()
    assert run.json()["count"] == 2

    async def run_until_idle() -> None:
        while await app.state.worker.run_once():
            pass

    asyncio.run(run_until_idle())
    tasks = client.get(f"/api/v1/experiments/{experiment_id}/tasks").json()

    assert [(task["year"], task["doy"]) for task in tasks] == [(2024, 366), (2025, 1)]
    assert all(task["status"] == "succeeded" for task in tasks)
    assert all(len(task["results"]) == 15 for task in tasks)
    detail = client.get(f"/api/v1/tasks/{tasks[0]['id']}").json()
    artifacts = client.get(
        f"/api/v1/tasks/{tasks[0]['id']}/attempts/{detail['active_attempt_id']}/results"
    ).json()
    plots = [item for item in artifacts if item["kind"] == "plot"]
    assert len(plots) == 16
    assert len([item for item in artifacts if item["kind"] == "analysis"]) == 1
    png = client.get(plots[0]["url"])
    assert png.status_code == 200
    assert png.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_preflight_reports_missing_template_without_starting_solver(
    mock_context, tmp_path: Path, capsys
) -> None:
    app, spec = mock_context
    settings = app.state.settings.model_copy(
        update={
            "pppflt": app.state.settings.pppflt.model_copy(
                update={"template_path": tmp_path / "missing.xml"}
            )
        }
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(spec.model_dump_json(), encoding="utf-8")

    result = _preflight(settings, str(plan_path))
    report = json.loads(capsys.readouterr().out)

    assert result == 2
    assert report["ok"] is False
    assert any(item["kind"] == "template" for item in report["items"])
    assert report["tasks"][0]["xml_path"] is None
