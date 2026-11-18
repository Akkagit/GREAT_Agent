"""0008 preserves frozen adoption and keeps its downgrade independent of 0007."""
import sqlite3

import pytest
from alembic import command
from sqlalchemy import inspect

from test_knowledge_chat_migration import migration_config
from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base
from great_agent.db.knowledge_chat_schema import TABLE_NAMES as CHAT_TABLES
from great_agent.db.knowledge_translation_schema import TABLE_NAMES
from great_agent.db.schema import require_runtime_schema, upgrade_runtime_database


def test_0007_requires_migration_even_after_create_all(tmp_path):
    database = tmp_path / 'old.sqlite'
    command.upgrade(migration_config(database), '0007')
    engine, _ = create_engine_and_session_factory(database)
    Base.metadata.create_all(engine)
    try:
        with pytest.raises(RuntimeError, match='migration'):
            require_runtime_schema(engine)
    finally:
        engine.dispose()


def test_translation_upgrade_downgrade_and_0007_tables_are_independent(tmp_path):
    database = tmp_path / 'upgrade.sqlite'
    config = migration_config(database)
    command.upgrade(config, '0007')
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO conversations(id,scope,title,state_json) VALUES('retained','global','old','{}')")
    assert upgrade_runtime_database(database).exists()
    engine, _ = create_engine_and_session_factory(database)
    try:
        require_runtime_schema(engine)
        assert TABLE_NAMES <= set(inspect(engine).get_table_names())
        assert not TABLE_NAMES & CHAT_TABLES
        command.downgrade(config, '0007')
        assert not TABLE_NAMES & set(inspect(engine).get_table_names())
        assert CHAT_TABLES <= set(inspect(engine).get_table_names())
        command.upgrade(config, 'head')
        require_runtime_schema(engine)
        with sqlite3.connect(database) as db:
            assert db.execute('SELECT version_num FROM alembic_version').fetchone() == ('0010',)
            assert db.execute('SELECT title FROM conversations').fetchone() == ('old',)
    finally:
        engine.dispose()


@pytest.mark.parametrize('unversioned', [False, True])
def test_translation_collision_rejected_before_stamp(tmp_path, unversioned):
    database = tmp_path / 'collision.sqlite'
    command.upgrade(migration_config(database), '0007')
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE knowledge_citation_translations(cache_key INTEGER PRIMARY KEY)')
        if unversioned:
            db.execute('DROP TABLE alembic_version')
    with pytest.raises(RuntimeError, match='reserved'):
        upgrade_runtime_database(database)
    with sqlite3.connect(database) as db:
        if unversioned:
            assert db.execute("SELECT name FROM sqlite_master WHERE name='alembic_version'").fetchone() is None
        else:
            assert db.execute('SELECT version_num FROM alembic_version').fetchone() == ('0007',)
