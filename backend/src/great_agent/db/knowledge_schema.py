"""Shared FTS DDL for migrations and disposable create_all databases."""
from __future__ import annotations

import re


TABLE_NAMES = frozenset({"knowledge_bases", "knowledge_blobs", "knowledge_documents", "knowledge_versions",
                         "knowledge_jobs", "knowledge_pages", "knowledge_elements", "knowledge_chunks"})
FTS_TABLE = "knowledge_chunks_fts"
_FTS_SQL = """CREATE VIRTUAL TABLE knowledge_chunks_fts USING fts5(
    text, section, content='knowledge_chunks', content_rowid='rowid', tokenize='unicode61')"""
_TRIGGERS = {
    "knowledge_chunks_ai": """CREATE TRIGGER knowledge_chunks_ai AFTER INSERT ON knowledge_chunks BEGIN
        INSERT INTO knowledge_chunks_fts(rowid, text, section) VALUES (new.rowid, new.text, new.section);
    END""",
    "knowledge_chunks_ad": """CREATE TRIGGER knowledge_chunks_ad AFTER DELETE ON knowledge_chunks BEGIN
        INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, text, section)
        VALUES ('delete', old.rowid, old.text, old.section);
    END""",
    "knowledge_chunks_au": """CREATE TRIGGER knowledge_chunks_au AFTER UPDATE ON knowledge_chunks BEGIN
        INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, text, section)
        VALUES ('delete', old.rowid, old.text, old.section);
        INSERT INTO knowledge_chunks_fts(rowid, text, section) VALUES (new.rowid, new.text, new.section);
    END""",
}
_SHADOWS = {f"{FTS_TABLE}_{suffix}" for suffix in ("data", "idx", "docsize", "config")}
_SHADOW_LAYOUTS = {
    "data": (("id", 1), ("block", 0)),
    "idx": (("segid", 1), ("term", 2), ("pgno", 0)),
    "docsize": (("id", 1), ("sz", 0)),
    "config": (("k", 1), ("v", 0)),
}


def _normalize(sql):
    return re.sub(r"\s+", "", sql or "").rstrip(";").casefold()


def verify_knowledge_fts(connection, *, allow_missing=False):
    """Reject collisions instead of silently adopting unrelated reserved objects."""
    objects = {row[0]: (row[1], row[2]) for row in connection.exec_driver_sql(
        "SELECT name, type, sql FROM sqlite_master WHERE name GLOB 'knowledge_chunks_fts*' "
        "OR name IN ('knowledge_chunks_ai','knowledge_chunks_ad','knowledge_chunks_au')")}
    if not objects and allow_missing:
        return
    expected_names = {FTS_TABLE, *_SHADOWS, *_TRIGGERS}
    if set(objects) != expected_names:
        raise RuntimeError("Incompatible reserved knowledge FTS schema: missing or unexpected objects")
    for name, ddl in {FTS_TABLE: _FTS_SQL, **_TRIGGERS}.items():
        expected_type = "table" if name == FTS_TABLE else "trigger"
        if objects[name][0] != expected_type or _normalize(objects[name][1]) != _normalize(ddl):
            raise RuntimeError(f"Incompatible reserved knowledge FTS schema: {name}")
    if any(objects[name][0] != "table" for name in _SHADOWS):
        raise RuntimeError("Incompatible reserved knowledge FTS shadow schema")
    for suffix, expected in _SHADOW_LAYOUTS.items():
        # SQLite versions vary in the declared types of these internal columns;
        # their names, order and primary-key positions define the required shape.
        name = f"{FTS_TABLE}_{suffix}"
        actual = tuple((row[1], row[5]) for row in connection.exec_driver_sql(f"PRAGMA table_info({name})"))
        if actual != expected:
            raise RuntimeError(f"Incompatible reserved knowledge FTS shadow schema: {name}")


def create_knowledge_fts(connection):
    verify_knowledge_fts(connection, allow_missing=True)
    exists = connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE name='knowledge_chunks_fts'").first()
    if exists:
        return
    connection.exec_driver_sql(_FTS_SQL)
    for ddl in _TRIGGERS.values():
        connection.exec_driver_sql(ddl)
    # Handles migration of pre-existing legitimate chunks, in the same transaction.
    connection.exec_driver_sql("INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts) VALUES ('rebuild')")
    verify_knowledge_fts(connection)
