from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

import uvicorn

from great_agent.adapters.great_pppflt import GreatPppfltAdapter
from great_agent.adapters.great_pppflt import RenderedTask
from great_agent.adapters.agentscope_model import AgentScopeModelProvider
from great_agent.api.dependencies import AppServices
from great_agent.app import create_app
from great_agent.config import AppSettings, ConversationSettings
from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base
from great_agent.db.schema import require_runtime_schema, upgrade_runtime_database
from sqlalchemy import inspect
from great_agent.db.repositories import TaskRepository
from great_agent.domain.plans import ExperimentSpec
from great_agent.runtime.process_runner import ProcessRunner
from great_agent.runtime.result_inspector import PppfltResultInspector
from great_agent.runtime.run_storage import RunStorage
from great_agent.runtime.worker import CommandFactory, Worker
from great_agent.runtime.worker_lock import WorkerAlreadyRunning, WorkerLock
from great_agent.logging_config import build_api_logging_config
from great_agent.services.chat_service import ChatService
from great_agent.services.plan_service import PlanService
from great_agent.services.plan_validation_service import PlanValidationService
from great_agent.services.task_service import TaskService
from great_agent.services.result_analysis_service import analyze_task


def build_app(
    settings: AppSettings, *, command_factory: CommandFactory | None = None
):
    settings.assert_safe_paths()
    engine, session_factory = create_engine_and_session_factory(
        settings.database_path
    )
    if not inspect(engine).get_table_names():
        Base.metadata.create_all(engine)
    require_runtime_schema(engine)
    adapter = GreatPppfltAdapter(settings.pppflt)
    plan_service = PlanService(session_factory, validation_service=PlanValidationService(
        max_concurrency=settings.max_concurrency, profiles=adapter.profiles,
    ))
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
        execution_service=task_service.execution_service,
    )
    chat_service = ChatService(
        plan_service=plan_service,
        provider=AgentScopeModelProvider(settings.model),
    )
    conversation_service = None
    conversation_settings = getattr(settings, "conversation", ConversationSettings())
    if conversation_settings.enabled:
        from great_agent.runtime.conversation_agent import AgentScopeConversationEngine
        from great_agent.services.conversation_service import ConversationService
        conversation_service = ConversationService(
            session_factory, plan_service=plan_service,
            execution_service=task_service.execution_service,
            engine=AgentScopeConversationEngine(
                settings.model, resource_root=settings.project_root / "config" / "agent",
                max_model_requests=conversation_settings.max_model_requests,
                max_tool_calls=conversation_settings.max_tool_calls,
                timeout_seconds=conversation_settings.timeout_seconds,
            ), timeout_seconds=conversation_settings.timeout_seconds,
        )
    knowledge_service = None
    knowledge_chat = None
    knowledge_translation = None
    if settings.knowledge.enabled:
        from great_agent.db.knowledge_repository import KnowledgeRepository
        from great_agent.knowledge.storage import KnowledgeStorage
        from great_agent.services.knowledge_service import KnowledgeService
        knowledge_service = KnowledgeService(
            KnowledgeRepository(session_factory), KnowledgeStorage(settings.knowledge.root, settings.knowledge.max_upload_bytes),
            parser_timeout_seconds=settings.knowledge.parser_timeout_seconds,
            max_pages=settings.knowledge.max_pages, docling_artifacts=settings.knowledge.docling_artifacts,
            poll_interval_seconds=settings.knowledge.poll_interval_seconds,
        )
        if settings.knowledge.chat_enabled:
            from great_agent.db.knowledge_chat_repository import KnowledgeChatRepository
            from great_agent.knowledge.retrieval import KnowledgeRetriever
            from great_agent.runtime.knowledge_chat_engine import KnowledgeChatEngine
            from great_agent.services.knowledge_chat_service import KnowledgeChatService
            from great_agent.knowledge.formula_recognition import FormulaRecognitionService
            knowledge_repository = KnowledgeChatRepository(session_factory)
            formula_model=settings.model.model_copy(update={'name':settings.knowledge.formula_model_name}) \
                if settings.knowledge.formula_model_name else settings.model
            knowledge_chat = KnowledgeChatService(
                knowledge_repository,
                KnowledgeRetriever(knowledge_repository, settings.knowledge, settings.knowledge.root),
                KnowledgeChatEngine(settings.model),
                settings.model,
                formula_service=FormulaRecognitionService(
                    knowledge_repository, knowledge_service.storage, formula_model, allow_remote=False),
            )
            from great_agent.db.knowledge_translation_repository import KnowledgeTranslationRepository
            from great_agent.services.knowledge_source_validation import KnowledgeSourceValidator
            from great_agent.services.knowledge_translation_service import KnowledgeTranslationService
            knowledge_translation = KnowledgeTranslationService(
                KnowledgeTranslationRepository(session_factory), settings.model,
                source_validator=KnowledgeSourceValidator(knowledge_repository, knowledge_service),
            )
    research_service = None
    if settings.research.enabled:
        from great_agent.research.materials import ResearchMaterials
        from great_agent.research.repository import ResearchRepository
        from great_agent.research.service import ResearchService
        research_service = ResearchService(ResearchRepository(session_factory),
            ResearchMaterials(session_factory, settings, knowledge_service=knowledge_service,
                              retriever=knowledge_chat.retriever if knowledge_chat is not None else None), settings)
    services = AppServices(
        plan_service=plan_service,
        task_service=task_service,
        task_repository=repository,
        worker=worker,
        run_storage=storage,
        chat_service=chat_service,
        conversation_service=conversation_service,
        knowledge_service=knowledge_service,
        knowledge_chat_service=knowledge_chat,
        knowledge_translation_service=knowledge_translation,
        research_service=research_service,
    )
    app = create_app(settings, services=services)
    app.state.worker = worker
    app.state.task_repository = repository
    if research_service is not None:
        from great_agent.research.worker import ResearchWorker
        app.state.research_worker = ResearchWorker(research_service)
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
    del settings, plan_path
    print(json.dumps({"error": {"code": "execution_confirmation_required",
        "message": "Automatic smoke execution is disabled. Review the actual XML, executable, inputs and outputs in the Web workspace, then explicitly confirm execution."}}, ensure_ascii=False))
    return 2


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
    for name in ("api", "worker", "research-worker", "monitor-reset", "migrate"):
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
    if args.command == "migrate":
        try:
            with WorkerLock(settings.database_path), WorkerLock(Path(str(settings.database_path) + '.research')):
                backup = upgrade_runtime_database(settings.database_path)
                print(f'Database upgraded: {settings.database_path}')
                if backup is not None:
                    print(f'Database backup: {backup}')
        except WorkerAlreadyRunning as error:
            print(str(error))
            return 2
        return 0
    if args.command == "monitor-reset":
        try:
            with WorkerLock(settings.database_path):
                from great_agent.db.monitor_repository import MonitorRepository
                engine, sessions = create_engine_and_session_factory(settings.database_path)
                try:
                    require_runtime_schema(engine)
                    print(MonitorRepository(sessions).rotate_epoch())
                finally:
                    engine.dispose()
        except WorkerAlreadyRunning as error:
            print(str(error))
            return 2
        return 0
    if args.command == "api":
        uvicorn.run(
            build_app(settings), host=settings.host, port=settings.port,
            log_config=build_api_logging_config(settings.run_root),
        )
        return 0
    if args.command == "worker":
        try:
            with WorkerLock(settings.database_path):
                app = build_app(settings)
                asyncio.run(app.state.worker.run_forever())
        except WorkerAlreadyRunning as error:
            print(str(error))
            return 2
        return 0
    if args.command == "research-worker":
        if not settings.research.enabled:
            print('Research is disabled in this configuration.')
            return 0
        try:
            with WorkerLock(Path(str(settings.database_path) + '.research')):
                app = build_app(settings)
                asyncio.run(app.state.research_worker.run_forever(settings.research.poll_interval_seconds))
        except WorkerAlreadyRunning as error:
            print(str(error))
            return 2
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
