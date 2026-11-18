"""Research migration is additive and preserves the frozen adoption baseline."""
import sqlite3

import pytest
from alembic import command
from sqlalchemy import inspect

from test_knowledge_chat_migration import migration_config
from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base
from great_agent.db.schema import require_runtime_schema, upgrade_runtime_database
from great_agent.research.models import TABLE_NAMES


def test_0009_requires_research_migration_even_after_create_all(tmp_path):
    database = tmp_path / 'old.db'
    command.upgrade(migration_config(database), '0009')
    engine, _ = create_engine_and_session_factory(database)
    Base.metadata.create_all(engine)
    try:
        with pytest.raises(RuntimeError, match='migration'):
            require_runtime_schema(engine)
    finally:
        engine.dispose()


def test_research_upgrade_preserves_existing_rows_and_downgrade_is_independent(tmp_path):
    database = tmp_path / 'upgrade.db'
    config = migration_config(database)
    command.upgrade(config, '0009')
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO conversations(id,scope,title,state_json) VALUES('old','global','retained','{}')")
    backup = upgrade_runtime_database(database)
    assert backup and backup.exists()
    engine, _ = create_engine_and_session_factory(database)
    try:
        require_runtime_schema(engine)
        assert TABLE_NAMES <= set(inspect(engine).get_table_names())
        command.downgrade(config, '0009')
        assert not TABLE_NAMES.intersection(inspect(engine).get_table_names())
        with sqlite3.connect(database) as db:
            assert db.execute('SELECT title FROM conversations').fetchone() == ('retained',)
        command.upgrade(config, 'head')
        require_runtime_schema(engine)
    finally:
        engine.dispose()


@pytest.mark.parametrize('unversioned', [False, True])
def test_reserved_research_collision_is_rejected_before_migration(tmp_path, unversioned):
    database = tmp_path / 'collision.db'
    command.upgrade(migration_config(database), '0009')
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE research_sessions(id INTEGER PRIMARY KEY)')
        if unversioned:
            db.execute('DROP TABLE alembic_version')
    with pytest.raises(RuntimeError, match='reserved'):
        upgrade_runtime_database(database)
    with sqlite3.connect(database) as db:
        if unversioned:
            assert db.execute("SELECT name FROM sqlite_master WHERE name='alembic_version'").fetchone() is None
        else:
            assert db.execute('SELECT version_num FROM alembic_version').fetchone() == ('0009',)
