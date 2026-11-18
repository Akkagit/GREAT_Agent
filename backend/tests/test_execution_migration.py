from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect

from test_api import api_context, valid_spec
from test_execution_preview import preview_context
from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base, DailyTaskModel, ExecutionApprovalModel, ExecutionPreviewModel, ExperimentModel, PlanRevisionModel
from great_agent.services.plan_service import PlanService


@pytest.mark.parametrize('bootstrapped', [False, True])
def test_upgrade_preserves_history_and_handles_app_created_tables(tmp_path, valid_spec, bootstrapped):
    database = tmp_path / 'historical.db'
    config = Config(str(Path(__file__).parents[2] / 'alembic.ini'))
    config.set_main_option('sqlalchemy.url', f'sqlite:///{database.as_posix()}')
    config.set_main_option('path_separator', 'os')
    command.upgrade(config, '0001')
    engine, sessions = create_engine_and_session_factory(database)
    plans = PlanService(sessions)
    experiment = plans.create_experiment(valid_spec)
    # Seed a historical freeze using only 0001 columns. The current service
    # requires the new proposal table before it can enforce its freeze gate.
    with sessions.begin() as session:
        session.get(PlanRevisionModel, str(experiment.current_revision_id)).is_frozen = True
        session.get(ExperimentModel, str(experiment.id)).status = "ready"
    if bootstrapped:
        # build_app creates missing tables before an operator runs Alembic.
        Base.metadata.create_all(engine)
    command.upgrade(config, 'head')
    assert plans.get_current_spec(experiment.id) == valid_spec
    assert plans.get_current_revision(experiment.id).is_frozen
    assert len(plans.list_experiments()) == 1
    assert {'execution_previews', 'execution_approvals', 'plan_proposals'} <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_upgrade_preserves_a_bootstrapped_pending_proposal(tmp_path, valid_spec):
    database = tmp_path / "pending.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.set_main_option("path_separator", "os")
    command.upgrade(config, "0002")
    engine, sessions = create_engine_and_session_factory(database)
    Base.metadata.create_all(engine)
    plans = PlanService(sessions)
    experiment = plans.create_experiment(valid_spec)
    proposal = plans.preview_revision(experiment.id, valid_spec, source="card")

    command.upgrade(config, "head")

    restored = PlanService(sessions).get_proposal(experiment.id, proposal.id)
    assert restored.status == "pending"
    assert restored.base_revision_id == experiment.current_revision_id
    assert restored.candidate == valid_spec
    engine.dispose()


def test_proposal_migration_preserves_reviewed_xml_and_execution_authorizations(preview_context):
    _, services, sessions, experiment = preview_context
    preview = services.task_service.execution_service.prepare(experiment.id, expected_revision=1)
    services.task_service.execution_service.confirm(
        experiment.id, preview_id=preview.id, expected_revision=1, confirmed=True,
    )
    tables = [DailyTaskModel.__table__, ExecutionPreviewModel.__table__, ExecutionApprovalModel.__table__]
    with sessions() as session:
        before = {table.name: session.execute(table.select()).all() for table in tables}
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    database = Path(sessions.kw["bind"].url.database)
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.set_main_option("path_separator", "os")
    command.stamp(config, "0002")

    command.upgrade(config, "head")

    with sessions() as session:
        after = {table.name: session.execute(table.select()).all() for table in tables}
    assert before == after
    assert len(after["execution_approvals"]) == 1
    assert services.task_service.execution_service.get(preview.id) == preview
