"""A real 0006 database upgrades safely without redefining 0004 adoption."""
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, text

from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base
from great_agent.db import knowledge_chat_models  # noqa: F401
from great_agent.db.knowledge_chat_schema import TABLE_NAMES
from great_agent.db.schema import require_runtime_schema, upgrade_runtime_database


def migration_config(database):
    config = Config(str(Path(__file__).parents[2] / 'alembic.ini'))
    config.set_main_option('sqlalchemy.url', f'sqlite:///{database.as_posix()}')
    config.set_main_option('path_separator', 'os')
    return config


def test_runtime_rejects_0006_even_if_create_all_added_new_tables(tmp_path):
    database = tmp_path / 'old.db'
    config = migration_config(database)
    command.upgrade(config, '0006')
    engine, _ = create_engine_and_session_factory(database)
    Base.metadata.create_all(engine)
    try:
        with pytest.raises(RuntimeError, match='migration'):
            require_runtime_schema(engine)
    finally:
        engine.dispose()


def test_upgrade_adds_five_tables_and_preserves_conversation_and_fts(tmp_path):
    database = tmp_path / 'upgrade.db'
    config = migration_config(database)
    command.upgrade(config, '0006')
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO conversations(id,scope,title,state_json) VALUES('chat','global','Old','{}')")
    backup = upgrade_runtime_database(database)
    assert backup and backup.exists()
    engine, _ = create_engine_and_session_factory(database)
    try:
        require_runtime_schema(engine)
        assert TABLE_NAMES <= set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0010'
            assert connection.execute(text('SELECT title FROM conversations')).scalar_one() == 'Old'
            assert 'fts5' in connection.execute(text("SELECT sql FROM sqlite_master WHERE name='knowledge_chunks_fts'")).scalar_one().lower()
        command.downgrade(config, '0006')
        assert not TABLE_NAMES.intersection(inspect(engine).get_table_names())
        command.upgrade(config, 'head')
        require_runtime_schema(engine)
    finally:
        engine.dispose()


def test_0004_adoption_is_independent_of_new_metadata_tables(tmp_path):
    database = tmp_path / 'unversioned.db'
    command.upgrade(migration_config(database), '0004')
    with sqlite3.connect(database) as connection:
        connection.execute('DROP TABLE alembic_version')
        connection.execute('CREATE TABLE research_results(id TEXT PRIMARY KEY)')
        connection.execute("INSERT INTO research_results VALUES('retain')")
    backup = upgrade_runtime_database(database)
    assert backup and backup.exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT version_num FROM alembic_version').fetchone() == ('0010',)
        assert connection.execute('SELECT id FROM research_results').fetchone() == ('retain',)


@pytest.mark.parametrize('unversioned', [False, True])
def test_reserved_knowledge_chat_collision_is_rejected_before_stamp(tmp_path, unversioned):
    database = tmp_path / 'collision.db'
    command.upgrade(migration_config(database), '0006')
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE knowledge_retrieval_traces(run_id INTEGER PRIMARY KEY)')
        if unversioned:
            connection.execute('DROP TABLE alembic_version')
    with pytest.raises(RuntimeError, match='reserved'):
        upgrade_runtime_database(database)
    with sqlite3.connect(database) as connection:
        if unversioned:
            assert connection.execute("SELECT name FROM sqlite_master WHERE name='alembic_version'").fetchone() is None
        else:
            assert connection.execute('SELECT version_num FROM alembic_version').fetchone() == ('0006',)
