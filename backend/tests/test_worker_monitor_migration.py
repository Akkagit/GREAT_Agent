from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base


def migration_config(database):
    config = Config(str(Path(__file__).parents[2] / 'alembic.ini'))
    config.set_main_option('sqlalchemy.url', f'sqlite:///{database.as_posix()}')
    config.set_main_option('path_separator', 'os')
    return config


@pytest.mark.parametrize('bootstrap', [False, True])
def test_recovery_monitor_upgrade_preserves_old_attempt_and_authorization(tmp_path, bootstrap):
    database = tmp_path / 'history.db'
    config = migration_config(database)
    command.upgrade(config, '0004')
    engine, _ = create_engine_and_session_factory(database)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO experiments(id,status,created_at,updated_at) VALUES ('exp','ready',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO plan_revisions(id,experiment_id,number,source,spec_json,is_frozen,created_at) VALUES ('rev','exp',1,'initial','{}',1,CURRENT_TIMESTAMP)"))
        connection.execute(text("UPDATE experiments SET current_revision_id='rev' WHERE id='exp'"))
        connection.execute(text("INSERT INTO daily_tasks(id,experiment_id,revision_id,year,doy,status,created_at,updated_at) VALUES ('task','exp','rev',2024,350,'running',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO task_attempts(id,task_id,number,status,pid) VALUES ('attempt','task',1,'running',4242)"))
        connection.execute(text("UPDATE daily_tasks SET active_attempt_id='attempt' WHERE id='task'"))
        connection.execute(text("INSERT INTO execution_previews(id,experiment_id,revision_id,payload_json,confirmed_at) VALUES ('preview','exp','rev','{}',CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO execution_approvals(task_id,attempt_number,preview_id,job_index) VALUES ('task',1,'preview',0)"))
    if bootstrap:
        Base.metadata.create_all(engine)
    command.upgrade(config, '0005')
    with engine.connect() as connection:
        row = connection.execute(text('SELECT * FROM task_attempts')).mappings().one()
        assert row['pid'] == 4242 and row['status'] == 'running'
        assert row['owner_session_id'] is None and row['process_created_at'] is None
        assert row['launch_phase'] == 'legacy_unknown'
        assert row['analysis_status'] == 'unknown' and row['artifact_version'] == 0
        assert row['version'] == 0
        assert connection.execute(text('SELECT COUNT(*) FROM execution_approvals')).scalar_one() == 1
    assert {'experiment_events', 'monitor_state'} <= set(inspect(engine).get_table_names())
    command.downgrade(config, '0004')
    with engine.connect() as connection:
        assert connection.execute(text('SELECT pid,status FROM task_attempts')).one() == (4242, 'running')
        assert connection.execute(text('SELECT COUNT(*) FROM execution_approvals')).scalar_one() == 1
    engine.dispose()
