from types import SimpleNamespace
from uuid import UUID

from great_agent import cli
from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base
from great_agent.db.monitor_repository import MonitorRepository
from great_agent.runtime.worker_lock import WorkerLock
from great_agent.db.schema import require_runtime_schema


def test_monitor_reset_uses_configured_database_and_refuses_active_worker(tmp_path, monkeypatch, capsys):
    database = tmp_path/'configured.db'
    engine, sessions = create_engine_and_session_factory(database)
    Base.metadata.create_all(engine)
    repository = MonitorRepository(sessions)
    monkeypatch.setattr(cli,'_load_settings',lambda path: SimpleNamespace(database_path=database))
    before = repository.rotate_epoch()
    assert cli.main(['monitor-reset','--config','synthetic.toml']) == 0
    changed = capsys.readouterr().out.strip()
    assert str(UUID(changed)) == changed and changed != before
    with WorkerLock(database):
        assert cli.main(['monitor-reset','--config','synthetic.toml']) == 2
    engine.dispose()


def test_migrate_cli_targets_configuration_and_preserves_other_database(tmp_path, monkeypatch):
    database = tmp_path/'configured.db'
    untouched = tmp_path/'other.db'
    untouched.write_bytes(b'not-the-configured-database')
    monkeypatch.setattr(cli,'_load_settings',lambda path: SimpleNamespace(database_path=database))
    assert cli.main(['migrate','--config','synthetic.toml']) == 0
    engine,_ = create_engine_and_session_factory(database)
    require_runtime_schema(engine)
    engine.dispose()
    assert untouched.read_bytes() == b'not-the-configured-database'
    with WorkerLock(database):
        assert cli.main(['migrate','--config','synthetic.toml']) == 2
