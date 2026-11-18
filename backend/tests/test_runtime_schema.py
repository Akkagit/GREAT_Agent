from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest

from great_agent.db.engine import create_engine_and_session_factory
from sqlalchemy import inspect, text
import sqlite3


def test_runtime_schema_rejects_old_columns_after_create_all(tmp_path):
    from great_agent.db.models import Base
    from great_agent.db.schema import require_runtime_schema
    database = tmp_path / 'old.db'
    config = Config(str(Path(__file__).parents[2] / 'alembic.ini'))
    config.set_main_option('sqlalchemy.url', f'sqlite:///{database.as_posix()}')
    config.set_main_option('path_separator', 'os')
    command.upgrade(config, '0004')
    engine, _ = create_engine_and_session_factory(database)
    Base.metadata.create_all(engine)
    with pytest.raises(RuntimeError, match='alembic upgrade head'):
        require_runtime_schema(engine)
    command.upgrade(config, 'head')
    require_runtime_schema(engine)
    engine.dispose()


def test_adopts_only_verified_unversioned_historical_database(tmp_path):
    from great_agent.db.schema import upgrade_runtime_database, require_runtime_schema
    database = tmp_path/'unversioned.db'
    config = Config(str(Path(__file__).parents[2]/'alembic.ini'))
    config.set_main_option('sqlalchemy.url',f'sqlite:///{database.as_posix()}')
    config.set_main_option('path_separator','os')
    command.upgrade(config,'0004')
    engine,_ = create_engine_and_session_factory(database)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO experiments(id,status,created_at,updated_at) VALUES ('preserve-me','draft',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        connection.execute(text('DROP TABLE alembic_version'))
    engine.dispose()
    backup = upgrade_runtime_database(database)
    assert backup is not None and backup.is_file()
    with sqlite3.connect(backup) as original:
        assert original.execute('SELECT id,status FROM experiments').fetchone() == ('preserve-me','draft')
        assert original.execute("SELECT name FROM sqlite_master WHERE name='alembic_version'").fetchone() is None
    engine,_ = create_engine_and_session_factory(database)
    require_runtime_schema(engine)
    with engine.connect() as connection:
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0010'
        assert connection.execute(text('SELECT id,status FROM experiments')).one() == ('preserve-me','draft')
    engine.dispose()


def test_refuses_to_stamp_unknown_unversioned_layout(tmp_path):
    from great_agent.db.schema import upgrade_runtime_database
    database = tmp_path/'unknown.db'
    engine,_ = create_engine_and_session_factory(database)
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE experiments (id TEXT PRIMARY KEY, status REAL)'))
    engine.dispose()
    with pytest.raises(RuntimeError,match='unversioned'):
        upgrade_runtime_database(database)
    engine,_ = create_engine_and_session_factory(database)
    assert 'alembic_version' not in inspect(engine).get_table_names()
    engine.dispose()


@pytest.mark.parametrize('collision', ['monitor_table', 'recovery_column'])
def test_rejects_incompatible_reserved_names_before_adopting(tmp_path, collision):
    from great_agent.db.schema import upgrade_runtime_database
    database=tmp_path/'collision.db'
    config=Config(str(Path(__file__).parents[2]/'alembic.ini'))
    config.set_main_option('sqlalchemy.url',f'sqlite:///{database.as_posix()}')
    config.set_main_option('path_separator','os')
    command.upgrade(config,'0004')
    engine,_=create_engine_and_session_factory(database)
    with engine.begin() as connection:
        connection.execute(text('DROP TABLE alembic_version'))
        connection.execute(text('CREATE TABLE experiment_events (id INTEGER PRIMARY KEY)' if collision=='monitor_table'
                                else 'ALTER TABLE task_attempts ADD COLUMN version TEXT'))
    engine.dispose()
    with pytest.raises(RuntimeError,match='unversioned'):
        upgrade_runtime_database(database)
    engine,_=create_engine_and_session_factory(database)
    assert 'alembic_version' not in inspect(engine).get_table_names()
    engine.dispose()
