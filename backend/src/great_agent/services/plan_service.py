from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TypeAlias
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from great_agent.db.models import DailyTaskModel, ExperimentModel, PlanProposalModel, PlanRevisionModel, utc_now
from great_agent.db.repositories import PlanRepository
from great_agent.domain.plans import ExperimentSpec
from great_agent.errors import PendingProposalError, PlanFrozenError, PlanValidationError, ProposalNotFoundError, RevisionConflictError
from great_agent.services.plan_validation_service import PlanValidationService


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
    base_revision_id: UUID
    base_revision_number: int
    candidate_hash: str
    status: str
    source_proposal_id: UUID | None = None
    explanation: str = ""


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
    def __init__(
        self, session_factory: sessionmaker[Session],
        *, validation_service: PlanValidationService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.validation_service = validation_service or PlanValidationService()

    def list_experiments(self, limit: int = 20) -> list[dict]:
        records = []
        with self._session_factory() as session:
            experiments = session.scalars(select(ExperimentModel).order_by(ExperimentModel.updated_at.desc()).limit(limit)).all()
            for experiment in experiments:
                revision = PlanRepository(session).require_current_revision(experiment)
                statuses = session.scalars(select(DailyTaskModel.status).where(DailyTaskModel.experiment_id == experiment.id)).all()
                state = next((s for s in ("running", "pending", "failed", "interrupted", "cancelled") if s in statuses), "succeeded" if statuses else "not_started")
                records.append({"id": experiment.id, "status": experiment.status, "current_revision": revision.number,
                    "spec": json.loads(revision.spec_json), "task_count": len(statuses), "succeeded_count": statuses.count("succeeded"), "run_status": state})
        return records

    def create_experiment(self, spec: ExperimentSpec, *, session: Session | None = None) -> ExperimentRecord:
        # A caller-owned transaction can atomically persist the domain write and
        # its conversation receipt. Never commit that transaction here.
        if session is None:
            with self._session_factory.begin() as owned_session:
                return self.create_experiment(spec, session=owned_session)
        validated = self._validated_copy(spec)
        experiment_id = uuid4()
        revision_id = uuid4()
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
        self, experiment_id: UUID, candidate: ExperimentSpec, *, source: str,
        base_revision_id: UUID | None = None,
        source_proposal_id: UUID | None = None, explanation: str = "",
        write_guard=None, skip_no_change: bool = False, conversation_run_id: str | None = None,
    ) -> RevisionPreview | None:
        validated = self._validated_copy(candidate)
        with self._session_factory.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            repository = PlanRepository(session)
            experiment = repository.require_experiment(str(experiment_id))
            if experiment.status == "ready":
                raise PlanFrozenError(f"experiment {experiment_id} is frozen")
            current = repository.require_current_revision(experiment)
            parent = None
            if source_proposal_id is not None:
                parent = self._require_proposal(session, experiment_id, source_proposal_id)
                if parent.status != "pending":
                    raise RevisionConflictError("source proposal is no longer pending")
                if base_revision_id is not None and str(base_revision_id) != parent.base_revision_id:
                    raise RevisionConflictError("derived proposal cannot change its base revision")
                base_revision_id = UUID(parent.base_revision_id)
            if base_revision_id is not None and str(base_revision_id) != current.id:
                raise RevisionConflictError("proposal is based on a stale revision")
            if write_guard is not None:
                write_guard(session)
            before = ExperimentSpec.model_validate_json(current.spec_json)
            if skip_no_change and not diff_specs(before, validated):
                return None
            proposal = PlanProposalModel(
                id=str(uuid4()), experiment_id=str(experiment_id),
                base_revision_id=current.id, source=source,
                candidate_json=validated.model_dump_json(),
                candidate_hash=self._candidate_hash(validated),
                diff_json=json.dumps([item.as_dict() for item in diff_specs(before, validated)]),
                explanation=explanation, status="pending",
                source_proposal_id=str(source_proposal_id) if source_proposal_id else None,
            )
            if parent is not None:
                parent.status = "superseded"
            session.execute(update(PlanProposalModel).where(
                PlanProposalModel.experiment_id == str(experiment_id),
                PlanProposalModel.status == "pending",
            ).values(status="superseded"))
            session.add(proposal)
            session.flush()
            if conversation_run_id is not None:
                from great_agent.db.conversation_models import ConversationRunModel
                session.get(ConversationRunModel, conversation_run_id).proposal_id = proposal.id
            return self._proposal_record(session, proposal)

    def validate_candidate(
        self, experiment_id: UUID, candidate: ExperimentSpec, *, base_revision_id: UUID,
    ) -> ExperimentSpec:
        validated = self._validated_copy(candidate)
        experiment = self.get_experiment(experiment_id)
        if experiment.status == "ready":
            raise PlanFrozenError(f"experiment {experiment_id} is frozen")
        if experiment.current_revision_id != base_revision_id:
            raise RevisionConflictError("proposal is based on a stale revision")
        return validated

    def get_proposal(self, experiment_id: UUID, proposal_id: UUID) -> RevisionPreview:
        with self._session_factory() as session:
            return self._proposal_record(
                session, self._require_proposal(session, experiment_id, proposal_id),
            )

    def list_proposals(
        self, experiment_id: UUID, *, status: str | None = None, limit: int = 20,
    ) -> list[RevisionPreview]:
        with self._session_factory() as session:
            PlanRepository(session).require_experiment(str(experiment_id))
            query = select(PlanProposalModel).where(PlanProposalModel.experiment_id == str(experiment_id))
            if status is not None:
                query = query.where(PlanProposalModel.status == status)
            rows = session.scalars(query.order_by(PlanProposalModel.created_at.desc(), PlanProposalModel.id).limit(limit))
            return [self._proposal_record(session, row) for row in rows]

    def reject_proposal(self, experiment_id: UUID, proposal_id: UUID) -> RevisionPreview:
        with self._session_factory.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            proposal = self._require_proposal(session, experiment_id, proposal_id)
            if proposal.status not in {"pending", "rejected"}:
                raise RevisionConflictError("proposal is no longer pending")
            proposal.status = "rejected"
            return self._proposal_record(session, proposal)

    def accept_proposal(
        self, experiment_id: UUID, proposal_id: UUID, *, expected_revision: int,
    ) -> RevisionRecord:
        with self._session_factory.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            repository = PlanRepository(session)
            experiment = repository.require_experiment(str(experiment_id))
            proposal = self._require_proposal(session, experiment_id, proposal_id)
            base = session.get(PlanRevisionModel, proposal.base_revision_id)
            if base is None or base.number != expected_revision:
                raise RevisionConflictError("expected revision does not match proposal base")
            if proposal.status == "accepted" and proposal.accepted_revision_id:
                accepted = session.get(PlanRevisionModel, proposal.accepted_revision_id)
                if accepted is None:
                    raise RuntimeError("accepted proposal has no revision")
                return self._revision_record(accepted)
            if proposal.status != "pending":
                raise RevisionConflictError("proposal is no longer pending")
            if experiment.status == "ready":
                raise PlanFrozenError(f"experiment {experiment_id} is frozen")
            current = repository.require_current_revision(experiment)
            if current.id != proposal.base_revision_id:
                raise RevisionConflictError("proposal is based on a stale revision")
            candidate = self._validated_copy(ExperimentSpec.model_validate_json(proposal.candidate_json))
            if self._candidate_hash(candidate) != proposal.candidate_hash:
                raise PlanValidationError("stored proposal content does not match its digest")
            revision = self._append_revision(session, experiment, current, candidate, proposal.source)
            proposal.status = "accepted"
            proposal.accepted_revision_id = revision.id
            return self._revision_record(revision)

    def accept_revision(
        self,
        experiment_id: UUID,
        candidate: ExperimentSpec,
        *,
        expected_revision: int,
        source: str = "card",
    ) -> RevisionRecord:
        validated = self._validated_copy(candidate)
        try:
            with self._session_factory.begin() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                repository = PlanRepository(session)
                experiment = repository.require_experiment(str(experiment_id))
                if experiment.status == "ready":
                    raise PlanFrozenError(f"experiment {experiment_id} is frozen")
                current = repository.require_current_revision(experiment)
                if current.number != expected_revision:
                    raise RevisionConflictError(
                        f"expected revision {expected_revision}, current is {current.number}"
                    )

                revision = self._append_revision(session, experiment, current, validated, source)
                record = self._revision_record(revision)
        except IntegrityError as error:
            raise RevisionConflictError(
                f"experiment {experiment_id} changed concurrently"
            ) from error

        return record

    def freeze_revision(
        self, experiment_id: UUID, *, expected_revision: int, session: Session | None = None,
    ) -> RevisionRecord:
        if session is None:
            with self._session_factory.begin() as owned_session:
                owned_session.execute(text("BEGIN IMMEDIATE"))
                return self.freeze_revision(experiment_id, expected_revision=expected_revision, session=owned_session)
        # The preview publisher supplies its writer transaction so a failed
        # preview insert also rolls back this freeze. Never commit it here.
        repository = PlanRepository(session)
        experiment = repository.require_experiment(str(experiment_id))
        if experiment.status == "ready":
            raise PlanFrozenError(f"experiment {experiment_id} is frozen")
        current = repository.require_current_revision(experiment)
        if current.number != expected_revision:
            raise RevisionConflictError(
                f"expected revision {expected_revision}, current is {current.number}"
            )
        if session.scalar(select(PlanProposalModel.id).where(
            PlanProposalModel.experiment_id == str(experiment_id),
            PlanProposalModel.status == "pending",
        ).limit(1)) is not None:
            raise PendingProposalError("存在尚未处理的提案，请先接受或放弃后再生成 XML。")
        self._validated_copy(ExperimentSpec.model_validate_json(current.spec_json))
        current.is_frozen = True
        experiment.status = "ready"
        experiment.updated_at = utc_now()
        return self._revision_record(current)

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

    def _validated_copy(self, spec: ExperimentSpec) -> ExperimentSpec:
        return self.validation_service.validate(spec)

    @staticmethod
    def _candidate_hash(spec: ExperimentSpec) -> str:
        canonical = json.dumps(
            spec.model_dump(mode="json"), ensure_ascii=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _require_proposal(session: Session, experiment_id: UUID, proposal_id: UUID) -> PlanProposalModel:
        proposal = session.get(PlanProposalModel, str(proposal_id))
        if proposal is None or proposal.experiment_id != str(experiment_id):
            raise ProposalNotFoundError("unknown proposal")
        return proposal

    @staticmethod
    def _proposal_record(session: Session, proposal: PlanProposalModel) -> RevisionPreview:
        base = session.get(PlanRevisionModel, proposal.base_revision_id)
        if base is None:
            raise RuntimeError("proposal base revision is missing")
        return RevisionPreview(
            id=UUID(proposal.id), experiment_id=UUID(proposal.experiment_id),
            source=proposal.source, candidate=ExperimentSpec.model_validate_json(proposal.candidate_json),
            diff=json.loads(proposal.diff_json), base_revision_id=UUID(proposal.base_revision_id),
            base_revision_number=base.number, candidate_hash=proposal.candidate_hash,
            status=proposal.status,
            source_proposal_id=UUID(proposal.source_proposal_id) if proposal.source_proposal_id else None,
            explanation=proposal.explanation,
        )

    @staticmethod
    def _append_revision(
        session: Session, experiment: ExperimentModel, current: PlanRevisionModel,
        candidate: ExperimentSpec, source: str,
    ) -> PlanRevisionModel:
        revision = PlanRevisionModel(
            id=str(uuid4()), experiment_id=experiment.id, number=current.number + 1,
            source=source, spec_json=candidate.model_dump_json(), is_frozen=False,
        )
        session.add(revision)
        session.flush()
        updated = session.execute(
            update(ExperimentModel).where(
                ExperimentModel.id == experiment.id,
                ExperimentModel.current_revision_id == current.id,
                ExperimentModel.status == "draft",
            ).values(current_revision_id=revision.id, updated_at=utc_now()),
        )
        if updated.rowcount != 1:
            raise RevisionConflictError("experiment changed concurrently")
        session.execute(update(PlanProposalModel).where(
            PlanProposalModel.experiment_id == experiment.id,
            PlanProposalModel.status == "pending",
        ).values(status="stale"))
        return revision

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
