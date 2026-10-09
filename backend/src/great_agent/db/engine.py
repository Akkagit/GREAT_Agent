from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def create_engine_and_session_factory(
    database_path: Path,
) -> tuple[Engine, sessionmaker[Session]]:
    resolved_path = database_path.resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{resolved_path.as_posix()}")

    @event.listens_for(engine, "connect")
    def configure_sqlite(
        dbapi_connection: sqlite3.Connection, connection_record: object
    ) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    return engine, sessionmaker(bind=engine, expire_on_commit=False)
