from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

import psutil
import uvicorn

from great_agent.adapters.great_pppflt import GreatPppfltAdapter
from great_agent.adapters.great_pppflt import RenderedTask
from great_agent.adapters.openai_compatible import OpenAICompatibleProvider
from great_agent.api.dependencies import AppServices
from great_agent.app import create_app
from great_agent.config import AppSettings
from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base
from great_agent.db.repositories import TaskRepository
from great_agent.domain.plans import ExperimentSpec
from great_agent.runtime.process_runner import ProcessRunner
from great_agent.runtime.result_inspector import PppfltResultInspector
from great_agent.runtime.run_storage import RunStorage
from great_agent.runtime.worker import CommandFactory, Worker
from great_agent.services.chat_service import ChatService
from great_agent.services.plan_service import PlanService
from great_agent.services.task_service import TaskService
from great_agent.services.result_analysis_service import analyze_task


def build_app(
    settings: AppSettings, *, command_factory: CommandFactory | None = None
):
    engine, session_factory = create_engine_and_session_factory(
        settings.database_path
    )
    Base.metadata.create_all(engine)
    plan_service = PlanService(session_factory)
    adapter = GreatPppfltAdapter(settings.pppflt)
    storage = RunStorage(settings.run_root)
    repository = TaskRepository(session_factory)
    task_service = TaskService(
        session_factory=session_factory,
        plan_service=plan_service,
        adapter=adapter,
        run_storage=storage,
    )
    worker = Worker(
        repository=repository,
        plan_service=plan_service,
        adapter=adapter,
        run_storage=storage,
        process_runner=ProcessRunner(),
        result_inspector=PppfltResultInspector(),
        max_concurrency=settings.max_concurrency,
        command_factory=command_factory,
        convergence_hold_seconds=settings.convergence_hold_seconds,
    )
    chat_service = ChatService(
        plan_service=plan_service,
        provider=OpenAICompatibleProvider(settings.model),
    )
    services = AppServices(
        plan_service=plan_service,
        task_service=task_service,
        task_repository=repository,
        worker=worker,
        run_storage=storage,
        chat_service=chat_service,
    )
    app = create_app(settings, services=services)
    app.state.worker = worker
    app.state.task_repository = repository
    return app


def _load_settings(path: str) -> AppSettings:
    return AppSettings.load(Path(path), os.environ)


def _preflight(settings: AppSettings, plan_path: str) -> int:
    spec = ExperimentSpec.model_validate_json(
        Path(plan_path).read_text(encoding="utf-8")
    )
    app = build_app(settings)
    report = []
    tasks = []
    from great_agent.domain.dates import expand_dates

    for date in expand_dates(spec):
        checked = app.state.services.task_service.adapter.preflight_task(
            spec, date, settings.run_root / "preflight"
        )
        task_dir = settings.run_root / "preflight" / f"{date.year}_{date.doy:03d}"
        template_ready = any(
            item.kind == "template" and item.state == "present"
            for item in checked.items
        )
        rendered = (
            app.state.services.task_service.adapter.render_task(spec, date, task_dir)
            if template_ready
            else RenderedTask(
                xml_path=task_dir / "config.xml",
                output_dir=task_dir / "results",
            )
        )
        tasks.append({
            "year": date.year,
            "doy": date.doy,
            "xml_path": str(rendered.xml_path) if template_ready else None,
            "output_dir": str(rendered.output_dir),
            "command": list(
                app.state.services.task_service.adapter.build_command(rendered)
            ),
        })
        report.extend(
            {
                "kind": item.kind,
                "year": item.date.year,
                "doy": item.date.doy,
                "station": item.station,
                "path": str(item.path),
                "state": item.state,
            }
            for item in checked.items
        )
    print(json.dumps({
        "ok": all(i["state"] == "present" for i in report),
        "job_count": len(tasks),
        "plan": spec.model_dump(mode="json"),
        "tasks": tasks,
        "items": report,
    }, ensure_ascii=False, indent=2))
    return 0 if all(item["state"] == "present" for item in report) else 2


def _run_smoke(settings: AppSettings, plan_path: str) -> int:
    spec = ExperimentSpec.model_validate_json(
        Path(plan_path).read_text(encoding="utf-8")
    )
    app = build_app(settings)
    services = app.state.services
    experiment = services.plan_service.create_experiment(spec)
    services.plan_service.freeze_revision(experiment.id, expected_revision=1)
    services.task_service.materialize(experiment.id)

    async def run_until_idle() -> None:
        while await services.worker.run_once():
            pass

    asyncio.run(run_until_idle())
    tasks = services.task_repository.list_tasks(experiment.id)
    summary = {
        "experiment_id": str(experiment.id),
        "tasks": [
            {
                "id": str(task.id),
                "year": task.year,
                "doy": task.doy,
                "status": task.status,
                "exit_code": (
                    task.active_attempt.exit_code if task.active_attempt else None
                ),
                "results": [item.path for item in task.result_files(kind="flt")],
                "log": (
                    task.active_attempt.log_path if task.active_attempt else None
                ),
            }
            for task in tasks
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if tasks and all(task.status == "succeeded" for task in tasks) else 3


def _analyze_existing(
    settings: AppSettings, experiment_id: UUID,
    *, reference_experiment_id: UUID | None = None, hold_seconds: float | None = None,
) -> int:
    app = build_app(settings)
    services = app.state.services
    services.plan_service.get_experiment(experiment_id)
    tasks = services.task_repository.list_tasks(experiment_id)
    reports = []
    for task in tasks:
        try:
            if task.status != "succeeded":
                raise ValueError("solver task has not succeeded")
            artifacts = analyze_task(
                task.id, repository=services.task_repository,
                plan_service=services.plan_service, storage=services.run_storage,
                reference_experiment_id=reference_experiment_id,
                hold_seconds=settings.convergence_hold_seconds if hold_seconds is None else hold_seconds,
            )
            reports.append({"task_id": str(task.id), "ok": True, "artifacts": [str(item.path) for item in artifacts]})
        except (ValueError, OSError, RuntimeError, ImportError) as error:
            reports.append({"task_id": str(task.id), "ok": False, "error": str(error)})
    print(json.dumps({"experiment_id": str(experiment_id), "tasks": reports}, ensure_ascii=False, indent=2))
    return 0 if reports and all(item["ok"] for item in reports) else 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="great-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("api", "worker"):
        command = subparsers.add_parser(name)
        command.add_argument("--config", required=True)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--config", required=True)
    preflight.add_argument("--plan", required=True)
    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--config", required=True)
    smoke.add_argument("--plan", required=True)
    analyze = subparsers.add_parser("analyze")
    analyze.add_argument("--config", required=True)
    analyze.add_argument("--experiment-id", type=UUID, required=True)
    analyze.add_argument("--reference-experiment-id", type=UUID)
    analyze.add_argument("--hold-seconds", type=float)
    args = parser.parse_args(argv)
    settings = _load_settings(args.config)
    if args.command == "api":
        uvicorn.run(build_app(settings), host=settings.host, port=settings.port)
        return 0
    if args.command == "worker":
        app = build_app(settings)
        app.state.task_repository.recover_stale_tasks(set(psutil.pids()))
        asyncio.run(app.state.worker.run_forever())
        return 0
    if args.command == "preflight":
        return _preflight(settings, args.plan)
    if args.command == "analyze":
        return _analyze_existing(
            settings, args.experiment_id, reference_experiment_id=args.reference_experiment_id,
            hold_seconds=args.hold_seconds,
        )
    return _run_smoke(settings, args.plan)


if __name__ == "__main__":
    raise SystemExit(main())
