from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect,text

from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base


def config(database):
    result=Config(str(Path(__file__).parents[2]/'alembic.ini'))
    result.set_main_option('sqlalchemy.url',f'sqlite:///{database.as_posix()}')
    result.set_main_option('path_separator','os')
    return result


def test_knowledge_head_preserves_0005_and_bootstrap_fts(tmp_path):
    from great_agent.db.schema import upgrade_runtime_database,require_runtime_schema
    path=tmp_path/'v5.db'
    command.upgrade(config(path),'0005')
    engine,_=create_engine_and_session_factory(path)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO experiments(id,status,created_at,updated_at) VALUES ('old','draft',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
    engine.dispose()
    backup=upgrade_runtime_database(path)
    assert backup and backup.exists()
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version_num FROM alembic_version').fetchone()==('0010',)
        assert db.execute('SELECT id FROM experiments').fetchone()==('old',)
        assert 'fts5' in db.execute("SELECT sql FROM sqlite_master WHERE name='knowledge_chunks_fts'").fetchone()[0].lower()
    engine,_=create_engine_and_session_factory(path)
    require_runtime_schema(engine)
    engine.dispose()


def test_runtime_rejects_old_version_even_after_create_all(tmp_path):
    from great_agent.db.schema import require_runtime_schema
    path=tmp_path/'old.db'
    command.upgrade(config(path),'0005')
    engine,_=create_engine_and_session_factory(path)
    Base.metadata.create_all(engine)
    with pytest.raises(RuntimeError,match='migration'): require_runtime_schema(engine)
    command.upgrade(config(path),'head')
    require_runtime_schema(engine)
    engine.dispose()


def test_create_all_knowledge_reserved_collision_is_rejected(tmp_path):
    path=tmp_path/'collision.db'
    command.upgrade(config(path),'0005')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE knowledge_documents (id INTEGER PRIMARY KEY)')
    from great_agent.db.schema import upgrade_runtime_database
    with pytest.raises(RuntimeError,match='reserved'): upgrade_runtime_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version_num FROM alembic_version').fetchone()==('0005',)
