from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeAlias
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from great_agent.db.models import ExperimentModel, PlanRevisionModel, utc_now
from great_agent.db.repositories import PlanRepository
from great_agent.domain.plans import ExperimentSpec
from great_agent.errors import PlanFrozenError, RevisionConflictError


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True)
class FieldDiff:
    path: str
    before: JsonValue
    after: JsonValue

    def as_dict(self) -> dict[str, JsonValue]:
        return {"path": self.path, "before": self.before, "after": self.after}


@dataclass(frozen=True)
class ExperimentRecord:
    id: UUID
    status: str
    current_revision_id: UUID


@dataclass(frozen=True)
class RevisionRecord:
    id: UUID
    experiment_id: UUID
    number: int
    source: str
    spec: ExperimentSpec
    is_frozen: bool


@dataclass(frozen=True)
class RevisionPreview:
    id: UUID
    experiment_id: UUID
    source: str
    candidate: ExperimentSpec
    diff: list[dict[str, JsonValue]]


def diff_specs(
    before: ExperimentSpec, after: ExperimentSpec
) -> tuple[FieldDiff, ...]:
    before_json = before.model_dump(mode="json")
    after_json = after.model_dump(mode="json")
    before_json["outputs"] = sorted(before_json["outputs"])
    after_json["outputs"] = sorted(after_json["outputs"])
    return tuple(_diff_json(before_json, after_json, ""))


def _diff_json(
    before: JsonValue, after: JsonValue, path: str
) -> list[FieldDiff]:
    if isinstance(before, dict) and isinstance(after, dict):
        differences: list[FieldDiff] = []
        for key in sorted(before.keys() | after.keys()):
            child_path = f"{path}/{_escape_pointer_token(key)}"
            differences.extend(
                _diff_json(before.get(key), after.get(key), child_path)
            )
        return differences
    if before != after:
        return [FieldDiff(path=path or "/", before=before, after=after)]
    return []


def _escape_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


class PlanService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_experiment(self, spec: ExperimentSpec) -> ExperimentRecord:
        validated = self._validated_copy(spec)
        experiment_id = uuid4()
        revision_id = uuid4()
        with self._session_factory.begin() as session:
            experiment = ExperimentModel(
                id=str(experiment_id), status="draft", current_revision_id=None
            )
            session.add(experiment)
            session.flush()
            revision = PlanRevisionModel(
                id=str(revision_id),
                experiment_id=str(experiment_id),
                number=1,
                source="initial",
                spec_json=validated.model_dump_json(),
                is_frozen=False,
            )
            session.add(revision)
            session.flush()
            experiment.current_revision_id = revision.id
        return ExperimentRecord(
            id=experiment_id,
            status="draft",
            current_revision_id=revision_id,
        )

    def preview_revision(
        self, experiment_id: UUID, candidate: ExperimentSpec, *, source: str
    ) -> RevisionPreview:
        validated = self._validated_copy(candidate)
        current = self.get_current_spec(experiment_id)
        return RevisionPreview(
            id=uuid4(),
            experiment_id=experiment_id,
            source=source,
            candidate=validated,
            diff=[item.as_dict() for item in diff_specs(current, validated)],
        )

    def accept_revision(
        self,
        experiment_id: UUID,
        candidate: ExperimentSpec,
        *,
        expected_revision: int,
        source: str = "card",
    ) -> RevisionRecord:
        validated = self._validated_copy(candidate)
        new_revision_id = uuid4()
        try:
            with self._session_factory.begin() as session:
                repository = PlanRepository(session)
                experiment = repository.require_experiment(str(experiment_id))
                if experiment.status == "ready":
                    raise PlanFrozenError(f"experiment {experiment_id} is frozen")
                current = repository.require_current_revision(experiment)
                if current.number != expected_revision:
                    raise RevisionConflictError(
                        f"expected revision {expected_revision}, current is {current.number}"
                    )

                revision = PlanRevisionModel(
                    id=str(new_revision_id),
                    experiment_id=str(experiment_id),
                    number=current.number + 1,
                    source=source,
                    spec_json=validated.model_dump_json(),
                    is_frozen=False,
                )
                session.add(revision)
                session.flush()
                updated = session.execute(
                    update(ExperimentModel)
                    .where(
                        ExperimentModel.id == str(experiment_id),
                        ExperimentModel.current_revision_id == current.id,
                        ExperimentModel.status == "draft",
                    )
                    .values(
                        current_revision_id=revision.id,
                        updated_at=utc_now(),
                    )
                )
                if updated.rowcount != 1:
                    raise RevisionConflictError(
                        f"experiment {experiment_id} changed concurrently"
                    )
                number = revision.number
        except IntegrityError as error:
            raise RevisionConflictError(
                f"experiment {experiment_id} changed concurrently"
            ) from error

        return RevisionRecord(
            id=new_revision_id,
            experiment_id=experiment_id,
            number=number,
            source=source,
            spec=validated,
            is_frozen=False,
        )

    def freeze_revision(
        self, experiment_id: UUID, *, expected_revision: int
    ) -> RevisionRecord:
        with self._session_factory.begin() as session:
            repository = PlanRepository(session)
            experiment = repository.require_experiment(str(experiment_id))
            if experiment.status == "ready":
                raise PlanFrozenError(f"experiment {experiment_id} is frozen")
            current = repository.require_current_revision(experiment)
            if current.number != expected_revision:
                raise RevisionConflictError(
                    f"expected revision {expected_revision}, current is {current.number}"
                )
            current.is_frozen = True
            experiment.status = "ready"
            experiment.updated_at = utc_now()
            record = self._revision_record(current)
        return RevisionRecord(
            id=record.id,
            experiment_id=record.experiment_id,
            number=record.number,
            source=record.source,
            spec=record.spec,
            is_frozen=True,
        )

    def get_experiment(self, experiment_id: UUID) -> ExperimentRecord:
        with self._session_factory() as session:
            experiment = PlanRepository(session).require_experiment(
                str(experiment_id)
            )
            if experiment.current_revision_id is None:
                raise RuntimeError(
                    f"experiment {experiment_id} has no current revision"
                )
            return ExperimentRecord(
                id=UUID(experiment.id),
                status=experiment.status,
                current_revision_id=UUID(experiment.current_revision_id),
            )

    def get_current_revision(self, experiment_id: UUID) -> RevisionRecord:
        with self._session_factory() as session:
            repository = PlanRepository(session)
            experiment = repository.require_experiment(str(experiment_id))
            return self._revision_record(
                repository.require_current_revision(experiment)
            )

    def get_current_spec(self, experiment_id: UUID) -> ExperimentSpec:
        return self.get_current_revision(experiment_id).spec

    @staticmethod
    def _validated_copy(spec: ExperimentSpec) -> ExperimentSpec:
        return ExperimentSpec.model_validate(spec.model_dump(mode="python"))

    @staticmethod
    def _revision_record(revision: PlanRevisionModel) -> RevisionRecord:
        return RevisionRecord(
            id=UUID(revision.id),
            experiment_id=UUID(revision.experiment_id),
            number=revision.number,
            source=revision.source,
            spec=ExperimentSpec.model_validate(json.loads(revision.spec_json)),
            is_frozen=revision.is_frozen,
        )
