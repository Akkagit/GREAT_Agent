from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import func, select

from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base, DailyTaskModel, PlanProposalModel, PlanRevisionModel
from great_agent.domain.dates import DateSegment
from great_agent.domain.plans import ExperimentSpec, Scenario
from great_agent.errors import PendingProposalError, PlanFrozenError, PlanValidationError, RevisionConflictError
from great_agent.services.chat_service import ChatService
from great_agent.services.plan_service import PlanService


@pytest.fixture
def proposal_store(tmp_path: Path):
    engine, sessions = create_engine_and_session_factory(tmp_path / "proposals.db")
    Base.metadata.create_all(engine)
    spec = ExperimentSpec(
        scenario=Scenario(mode="static", frequency="DF", ambiguity="float"),
        date_segments=(DateSegment(year=2024, start_doy=350, end_doy=350),),
        station_group_id="custom",
        stations=("ABCD",),
    )
    plans = PlanService(sessions)
    experiment = plans.create_experiment(spec)
    yield plans, sessions, experiment, spec
    engine.dispose()


def test_proposal_survives_new_service_without_accepting_or_queueing(proposal_store):
    plans, sessions, experiment, spec = proposal_store
    preview = plans.preview_revision(
        experiment.id, spec.model_copy(update={"concurrency": 2}),
        source="card", base_revision_id=experiment.current_revision_id,
    )

    restored = PlanService(sessions).get_proposal(experiment.id, preview.id)

    assert restored.base_revision_id == experiment.current_revision_id
    assert restored.base_revision_number == 1
    assert restored.status == "pending"
    assert restored.candidate.concurrency == 2
    assert restored.diff == [{"path": "/concurrency", "before": 1, "after": 2}]
    assert plans.get_current_revision(experiment.id).number == 1
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DailyTaskModel)) == 0


def test_old_candidate_cannot_be_accepted_by_changing_expected_revision(proposal_store):
    plans, _, experiment, spec = proposal_store
    old = plans.preview_revision(experiment.id, spec, source="card")
    plans.accept_revision(
        experiment.id, spec.model_copy(update={"concurrency": 2}), expected_revision=1,
    )

    with pytest.raises(RevisionConflictError):
        plans.accept_proposal(experiment.id, old.id, expected_revision=2)

    assert plans.get_current_spec(experiment.id).concurrency == 2
    assert plans.get_proposal(experiment.id, old.id).status == "stale"


def test_old_candidate_cannot_be_recreated_against_a_different_base(proposal_store):
    plans, _, experiment, spec = proposal_store
    old = plans.preview_revision(experiment.id, spec, source="card")
    current = plans.accept_revision(
        experiment.id, spec.model_copy(update={"concurrency": 2}), expected_revision=1,
    )

    with pytest.raises(RevisionConflictError):
        plans.preview_revision(
            experiment.id, spec, source="card", base_revision_id=current.id,
            source_proposal_id=old.id,
        )


def test_derivative_inherits_base_and_supersedes_its_source(proposal_store):
    plans, _, experiment, spec = proposal_store
    original = plans.preview_revision(experiment.id, spec, source="chat")
    derived = plans.preview_revision(
        experiment.id, spec.model_copy(update={"concurrency": 2}), source="card",
        base_revision_id=original.base_revision_id, source_proposal_id=original.id,
    )

    assert derived.base_revision_id == original.base_revision_id
    assert derived.source_proposal_id == original.id
    assert plans.get_proposal(experiment.id, original.id).status == "superseded"
    with pytest.raises(RevisionConflictError):
        plans.accept_proposal(experiment.id, original.id, expected_revision=1)


def test_duplicate_accept_returns_same_revision_without_extra_rows(proposal_store):
    plans, sessions, experiment, spec = proposal_store
    preview = plans.preview_revision(
        experiment.id, spec.model_copy(update={"concurrency": 2}), source="card",
    )

    first = plans.accept_proposal(experiment.id, preview.id, expected_revision=1)
    again = PlanService(sessions).accept_proposal(experiment.id, preview.id, expected_revision=1)

    assert again.id == first.id
    assert first.number == 2
    assert plans.get_proposal(experiment.id, preview.id).status == "accepted"
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(PlanRevisionModel)) == 2
        assert session.scalar(select(func.count()).select_from(DailyTaskModel)) == 0


def test_competing_accepts_make_one_revision(proposal_store):
    plans, sessions, experiment, spec = proposal_store
    proposal = plans.preview_revision(experiment.id, spec, source="card")
    ready = Barrier(2)

    def accept():
        ready.wait(timeout=5)
        return PlanService(sessions).accept_proposal(
            experiment.id, proposal.id, expected_revision=1,
        ).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(accept) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]

    assert results[0] == results[1]
    assert plans.get_current_revision(experiment.id).number == 2


def test_cross_experiment_proposal_cannot_be_read_or_accepted(proposal_store):
    plans, _, first, spec = proposal_store
    second = plans.create_experiment(spec)
    proposal = plans.preview_revision(first.id, spec, source="card")

    with pytest.raises(LookupError):
        plans.get_proposal(second.id, proposal.id)
    with pytest.raises(LookupError):
        plans.accept_proposal(second.id, proposal.id, expected_revision=1)


def test_rejected_proposal_cannot_be_restored_as_pending(proposal_store):
    plans, sessions, experiment, spec = proposal_store
    preview = plans.preview_revision(experiment.id, spec, source="card")
    plans.reject_proposal(experiment.id, preview.id)

    restored = PlanService(sessions)
    assert restored.get_proposal(experiment.id, preview.id).status == "rejected"
    assert not restored.list_proposals(experiment.id, status="pending")
    with pytest.raises(RevisionConflictError):
        restored.accept_proposal(experiment.id, preview.id, expected_revision=1)


def test_altered_persisted_candidate_cannot_be_accepted(proposal_store):
    plans, sessions, experiment, spec = proposal_store
    preview = plans.preview_revision(experiment.id, spec, source="card")
    with sessions.begin() as session:
        stored = session.get(PlanProposalModel, str(preview.id))
        stored.candidate_json = spec.model_copy(update={"concurrency": 2}).model_dump_json()

    with pytest.raises(PlanValidationError, match="digest"):
        plans.accept_proposal(experiment.id, preview.id, expected_revision=1)
    assert plans.get_current_revision(experiment.id).number == 1


@pytest.mark.parametrize("entrypoint", ["create", "preview", "accept", "freeze"])
def test_plan_entrypoints_share_domain_validation(proposal_store, entrypoint):
    plans, sessions, experiment, spec = proposal_store
    invalid = spec.model_copy(update={
        "date_segments": (DateSegment(year=2025, start_doy=366, end_doy=366),),
    })

    with pytest.raises(PlanValidationError, match="2025 DOY 366"):
        if entrypoint == "create":
            plans.create_experiment(invalid)
        elif entrypoint == "preview":
            plans.preview_revision(experiment.id, invalid, source="card")
        elif entrypoint == "accept":
            plans.accept_revision(experiment.id, invalid, expected_revision=1)
        else:
            with sessions.begin() as session:
                session.get(PlanRevisionModel, str(experiment.current_revision_id)).spec_json = invalid.model_dump_json()
            plans.freeze_revision(experiment.id, expected_revision=1)

    assert plans.get_experiment(experiment.id).status == "draft"
    assert plans.get_current_revision(experiment.id).number == 1


async def test_revision_change_during_model_request_does_not_rebase_candidate(proposal_store):
    plans, _, experiment, spec = proposal_store

    class ConcurrentEditProvider:
        async def propose_plan(self, message, current):
            plans.accept_revision(
                experiment.id, spec.model_copy(update={"concurrency": 2}),
                expected_revision=1,
            )
            return {"candidate": spec.model_dump(mode="json"), "explanation": "synthetic"}

    chat = ChatService(plan_service=plans, provider=ConcurrentEditProvider())
    with pytest.raises(RevisionConflictError):
        await chat.propose(experiment.id, "synthetic parameter change")

    assert plans.get_current_spec(experiment.id).concurrency == 2
    assert not plans.list_proposals(experiment.id, status="pending")


def test_pending_proposal_blocks_freezing_until_explicitly_rejected(proposal_store):
    plans, _, experiment, spec = proposal_store
    proposal = plans.preview_revision(experiment.id, spec, source="card")

    with pytest.raises(PendingProposalError):
        plans.freeze_revision(experiment.id, expected_revision=1)

    assert plans.get_experiment(experiment.id).status == "draft"
    assert not plans.get_current_revision(experiment.id).is_frozen
    assert plans.get_proposal(experiment.id, proposal.id).status == "pending"
    plans.reject_proposal(experiment.id, proposal.id)
    assert plans.freeze_revision(experiment.id, expected_revision=1).is_frozen


def test_competing_proposal_and_freeze_cannot_both_succeed(proposal_store):
    plans, sessions, experiment, spec = proposal_store
    ready = Barrier(2)

    def create_proposal():
        ready.wait(timeout=5)
        try:
            PlanService(sessions).preview_revision(experiment.id, spec, source="card")
            return "proposal"
        except PlanFrozenError:
            return "blocked"

    def freeze():
        ready.wait(timeout=5)
        try:
            PlanService(sessions).freeze_revision(experiment.id, expected_revision=1)
            return "freeze"
        except PendingProposalError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create_proposal), pool.submit(freeze)]
        outcomes = [future.result(timeout=10) for future in futures]

    assert outcomes.count("blocked") == 1
    assert plans.get_current_revision(experiment.id).is_frozen == ("freeze" in outcomes)
    assert len(plans.list_proposals(experiment.id, status="pending")) == ("proposal" in outcomes)
