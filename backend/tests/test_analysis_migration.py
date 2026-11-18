import sqlite3

from alembic import command
import pytest
from sqlalchemy import inspect

from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.schema import require_runtime_schema, upgrade_runtime_database
from test_knowledge_chat_migration import migration_config


def test_0009_upgrades_and_downgrades_without_changing_research_data(tmp_path):
    path = tmp_path / 'state.db'
    config = migration_config(path)
    command.upgrade(config, '0008')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE research_data(id TEXT PRIMARY KEY)')
        db.execute("INSERT INTO research_data VALUES('preserved')")
    engine, _ = create_engine_and_session_factory(path)
    with pytest.raises(RuntimeError, match='migration'):
        require_runtime_schema(engine)
    engine.dispose()
    assert upgrade_runtime_database(path).is_file()
    engine, _ = create_engine_and_session_factory(path)
    require_runtime_schema(engine)
    assert {'analysis_jobs', 'worker_heartbeats'} <= set(inspect(engine).get_table_names())
    command.downgrade(config, '0008')
    assert not {'analysis_jobs', 'worker_heartbeats'} & set(inspect(engine).get_table_names())
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT id FROM research_data').fetchone() == ('preserved',)
    command.upgrade(config, 'head')
    require_runtime_schema(engine)
    engine.dispose()


def test_0009_refuses_incompatible_analysis_table_before_upgrading(tmp_path):
    path = tmp_path / 'collision.db'
    command.upgrade(migration_config(path), '0008')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE analysis_jobs(id INTEGER PRIMARY KEY)')
    with pytest.raises(RuntimeError, match='reserved'):
        upgrade_runtime_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version_num FROM alembic_version').fetchone() == ('0008',)
