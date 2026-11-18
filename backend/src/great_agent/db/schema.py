"""Reject partially bootstrapped historical databases before runtime queries."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4

from sqlalchemy import Engine, UniqueConstraint, inspect, text
from great_agent.db.knowledge_schema import TABLE_NAMES as KNOWLEDGE_TABLES, verify_knowledge_fts
from great_agent.db.knowledge_chat_schema import TABLE_NAMES as KNOWLEDGE_CHAT_TABLES
from great_agent.db.knowledge_translation_schema import TABLE_NAMES as KNOWLEDGE_TRANSLATION_TABLES
from great_agent.research.models import TABLE_NAMES as RESEARCH_TABLES


# This is the complete 0004 bootstrap baseline. New metadata registrations must
# never silently become prerequisites for adopting a verified historical DB.
HISTORICAL_0004_TABLES = frozenset({
    'experiments', 'plan_revisions', 'daily_tasks', 'task_attempts', 'task_events',
    'result_files', 'execution_previews', 'execution_approvals', 'plan_proposals',
    'conversations', 'conversation_runs', 'conversation_messages', 'conversation_events',
})


RECOVERY_COLUMNS = {
    'owner_session_id', 'launch_phase', 'process_created_at',
    'process_executable', 'command_sha256', 'recovery_state',
    'recovery_reason', 'last_checked_at', 'version',
    'analysis_status', 'artifact_version',
}


def _verify_reserved_extensions(engine: Engine) -> None:
    from great_agent.db.models import Base
    schema = inspect(engine)
    tables = set(schema.get_table_names())
    def reject(detail):
        raise RuntimeError(f'Incompatible reserved runtime schema: {detail}')
    def check_column(actual, expected, name):
        if actual['type']._type_affinity is not expected.type._type_affinity:
            reject(f'{name} type')
        if not expected.primary_key and actual['nullable'] != expected.nullable:
            reject(f'{name} nullability')
        if expected.server_default is not None:
            normalize = lambda value: str(value).strip().strip('()').strip("'\"")
            if normalize(actual.get('default')) != normalize(expected.server_default.arg):
                reject(f'{name} default')
    if 'task_attempts' in tables:
        expected = Base.metadata.tables['task_attempts'].columns
        for actual in schema.get_columns('task_attempts'):
            if actual['name'] in RECOVERY_COLUMNS:
                check_column(actual, expected[actual['name']], f"task_attempts.{actual['name']}")
    for name in ('experiment_events', 'monitor_state', 'analysis_jobs', 'worker_heartbeats', *sorted(KNOWLEDGE_TABLES | KNOWLEDGE_CHAT_TABLES | KNOWLEDGE_TRANSLATION_TABLES | RESEARCH_TABLES)):
        if name not in tables:
            continue
        expected = Base.metadata.tables[name]
        actual = {column['name']: column for column in schema.get_columns(name)}
        if set(actual) != set(expected.columns.keys()):
            reject(f'{name} columns')
        for column in expected.columns:
            check_column(actual[column.name], column, f'{name}.{column.name}')
        if schema.get_pk_constraint(name)['constrained_columns'] != [c.name for c in expected.primary_key.columns]:
            reject(f'{name} primary key')
        unique = {tuple(item['column_names']) for item in schema.get_unique_constraints(name)}
        unique |= {tuple(item['column_names']) for item in schema.get_indexes(name) if item['unique']}
        for constraint in expected.constraints:
            if isinstance(constraint, UniqueConstraint) and tuple(c.name for c in constraint.columns) not in unique:
                reject(f'{name} uniqueness')
        indexes = {(tuple(item['column_names']), bool(item['unique'])) for item in schema.get_indexes(name)}
        for index in expected.indexes:
            if (tuple(c.name for c in index.columns), bool(index.unique)) not in indexes:
                reject(f'{name} indexes')
        foreign = {(tuple(item['constrained_columns']), item['referred_table'], tuple(item['referred_columns']))
                   for item in schema.get_foreign_keys(name)}
        for constraint in expected.foreign_key_constraints:
            if (tuple(constraint.column_keys), constraint.referred_table.name,
                    tuple(e.column.name for e in constraint.elements)) not in foreign:
                reject(f'{name} foreign keys')
        if name in ('experiment_events', 'research_events'):
            with engine.connect() as connection:
                sql=connection.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"), {'name': name}).scalar_one()
            if 'AUTOINCREMENT' not in sql.upper():
                reject(f'{name} cursor allocation')
    with engine.connect() as connection:
        verify_knowledge_fts(connection, allow_missing=True)


def require_runtime_schema(engine: Engine) -> None:
    schema = inspect(engine)
    tables = set(schema.get_table_names())
    expected = RECOVERY_COLUMNS
    present = {column['name'] for column in schema.get_columns('task_attempts')} if 'task_attempts' in tables else set()
    with engine.connect() as connection:
        revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar() if 'alembic_version' in tables else None
    required = {'experiment_events', 'monitor_state', 'analysis_jobs', 'worker_heartbeats'} | KNOWLEDGE_TABLES | KNOWLEDGE_CHAT_TABLES | KNOWLEDGE_TRANSLATION_TABLES | RESEARCH_TABLES
    if expected - present or required - tables or (revision is not None and revision != '0010'):
        raise RuntimeError(
            'Database schema requires migration. Stop API/Worker, back up the database '
            'and runs together, then run uv run great-agent migrate --config <app.toml> '
            '(verified bootstrap adoption followed by alembic upgrade head) before restarting.'
        )
    _verify_reserved_extensions(engine)
    if revision is None:
        _verify_unversioned_schema(engine)
    with engine.connect() as connection:
        verify_knowledge_fts(connection)


def _verify_unversioned_schema(engine: Engine) -> None:
    """Only adopt a complete known bootstrap, never stamp an unknown database."""
    from great_agent.db.models import Base
    schema = inspect(engine)
    present = set(schema.get_table_names())
    historical = {name: Base.metadata.tables[name] for name in HISTORICAL_0004_TABLES}
    def reject(detail):
        raise RuntimeError(f'Unrecognized unversioned database ({detail}); no Alembic stamp applied.')
    try:
        _verify_reserved_extensions(engine)
    except RuntimeError as error:
        reject(str(error))
    if set(historical) - present:
        reject('incomplete historical tables')
    for name, table in historical.items():
        actual = {column['name']: column for column in schema.get_columns(name)}
        expected = {column.name: column for column in table.columns
                    if not (name == 'task_attempts' and column.name in RECOVERY_COLUMNS)}
        allowed = set(expected) | (RECOVERY_COLUMNS if name == 'task_attempts' else set())
        if set(expected) - actual.keys() or actual.keys() - allowed:
            reject(f'{name} columns')
        for column_name, column in expected.items():
            old = actual[column_name]
            if old['type']._type_affinity is not column.type._type_affinity:
                reject(f'{name}.{column_name} type')
            if not column.primary_key and old['nullable'] != column.nullable:
                reject(f'{name}.{column_name} nullability')
        if schema.get_pk_constraint(name)['constrained_columns'] != [c.name for c in table.primary_key.columns]:
            reject(f'{name} primary key')
        unique = {tuple(item['column_names']) for item in schema.get_unique_constraints(name)}
        unique |= {tuple(item['column_names']) for item in schema.get_indexes(name) if item['unique']}
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint) and tuple(c.name for c in constraint.columns) not in unique:
                reject(f'{name} uniqueness')
        foreign = {(tuple(item['constrained_columns']), item['referred_table'], tuple(item['referred_columns']))
                   for item in schema.get_foreign_keys(name)}
        for constraint in table.foreign_key_constraints:
            expected_fk = (tuple(constraint.column_keys), constraint.referred_table.name,
                           tuple(element.column.name for element in constraint.elements))
            if expected_fk not in foreign:
                reject(f'{name} foreign keys')


def upgrade_runtime_database(database_path: Path) -> Path | None:
    """Migrate the configured database, backing up and verifying unstamped history.

    Callers must hold WorkerLock and stop API/Worker and back up runs first.
    Unrelated user tables are preserved and never interpreted as app tables.
    """
    from alembic import command
    from alembic.config import Config
    from great_agent.db.engine import create_engine_and_session_factory
    database_path = database_path.resolve()
    engine, _ = create_engine_and_session_factory(database_path)
    backup = None
    try:
        tables = set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            revision = connection.execute(text('SELECT version_num FROM alembic_version')).first() if 'alembic_version' in tables else None
        adopt = bool(tables - {'alembic_version'}) and revision is None
        if adopt:
            _verify_unversioned_schema(engine)
        else:
            _verify_reserved_extensions(engine)
        if tables:
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            backup = database_path.with_name(f'{database_path.name}.before-worker-monitor-{stamp}-{uuid4().hex[:8]}.bak')
            with sqlite3.connect(database_path) as source, sqlite3.connect(backup) as target:
                source.backup(target)
        config = Config()
        config.set_main_option('script_location', str(Path(__file__).resolve().parents[3]/'alembic'))
        config.set_main_option('sqlalchemy.url', f'sqlite:///{database_path.as_posix()}')
        config.set_main_option('path_separator', 'os')
        if adopt:
            command.stamp(config, '0004')
        command.upgrade(config, 'head')
        require_runtime_schema(engine)
        return backup
    finally:
        engine.dispose()
