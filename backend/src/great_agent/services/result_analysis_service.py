from __future__ import annotations

import xml.etree.ElementTree as ET
from uuid import UUID

from great_agent.db.repositories import TaskRepository
from great_agent.domain.dates import YearDoy
from great_agent.runtime.result_analysis import generate_analysis
from great_agent.runtime.result_inspector import ResultFileInfo
from great_agent.runtime.run_storage import RunStorage
from great_agent.services.plan_service import PlanService


def analyze_task(
    task_id: UUID,
    *,
    repository: TaskRepository,
    plan_service: PlanService,
    storage: RunStorage,
    reference_experiment_id: UUID | None = None,
    hold_seconds: float = 600,
) -> tuple[ResultFileInfo, ...]:
    task = repository.get_task(task_id)
    attempt = task.active_attempt
    if attempt is None or task.status not in ("running", "succeeded"):
        raise ValueError("analysis requires an active or successful solver attempt")
    reference_id = reference_experiment_id or task.experiment_id
    reference_spec = plan_service.get_current_revision(reference_id).spec
    if reference_spec.scenario.frequency != "DF" or reference_spec.scenario.ambiguity != "float":
        raise ValueError("reference experiment must use DF float")
    if reference_spec.scenario.mode != "static":
        raise ValueError("a fixed station reference requires a static DF-float experiment")
    reference_task = next((
        item for item in repository.list_tasks(reference_id)
        if (item.year, item.doy) == (task.year, task.doy)
    ), None)
    if reference_task is None or reference_task.active_attempt is None:
        raise ValueError("reference experiment has no matching daily result")
    if reference_task.id != task.id and reference_task.status != "succeeded":
        raise ValueError("reference solver task must have succeeded")

    def flt_files(record) -> tuple[ResultFileInfo, ...]:
        selected = []
        for item in record.result_files(kind="flt"):
            if item.attempt_id != record.active_attempt.id:
                continue
            path = storage.resolve(str(item.path))
            selected.append(ResultFileInfo(item.station, item.kind, path, item.size_bytes))
        return tuple(selected)

    targets = flt_files(task)
    references = flt_files(reference_task)
    spec = plan_service.get_current_revision(task.experiment_id).spec
    if {item.station for item in targets} != set(spec.stations):
        raise ValueError("analysis requires all planned station results")
    date = YearDoy(year=task.year, doy=task.doy)
    attempt_dir = storage.task_dir(task.experiment_id, date) / "attempts" / f"{attempt.number:04d}"
    output = storage.resolve(str(attempt_dir / "analysis"))
    xml = ET.parse(attempt_dir / "config.xml").getroot()
    interval = float(xml.findtext("./gen/int", default="30"))
    artifacts = generate_analysis(
        targets, output, reference_files=references,
        hold_seconds=hold_seconds, interval_seconds=interval,
    )
    repository.add_result_files(task.id, attempt.id, artifacts)
    repository.publish_event(task.id, "analysis_completed", {
        "attempt_id": str(attempt.id), "artifact_count": len(artifacts),
        "reference_experiment_id": str(reference_id), "hold_seconds": hold_seconds,
    })
    return artifacts
