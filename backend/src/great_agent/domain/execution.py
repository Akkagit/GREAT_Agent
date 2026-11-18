from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from great_agent.domain.plans import ExperimentSpec


class ExecutionModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InputSnapshot(ExecutionModel):
    kind: str
    station: str | None
    path: str
    state: Literal["present"] = "present"
    size_bytes: int
    mtime_ns: int


class ExecutionJob(ExecutionModel):
    year: int
    doy: int
    attempt_number: int
    task_id: UUID | None = None
    snapshot_path: str
    xml_path: str
    xml_content: str
    xml_sha256: str
    output_dir: str
    cwd: str
    command: tuple[str, ...]
    inputs: tuple[InputSnapshot, ...]


class ExecutionPreview(ExecutionModel):
    id: UUID
    experiment_id: UUID
    revision: int
    revision_id: UUID
    intent: Literal["initial", "retry"]
    expires_at: datetime
    executable_path: str
    executable_sha256: str
    template_path: str
    template_sha256: str
    spec: ExperimentSpec
    jobs: tuple[ExecutionJob, ...]
