"""Real SQLite checks for immutable, owner-controlled knowledge publication."""
from __future__ import annotations

import importlib.util
import math

import pytest
from sqlalchemy import text

from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base


def test_knowledge_repository_is_available():
    assert importlib.util.find_spec("great_agent.db.knowledge_repository") is not None


@pytest.fixture
def repo(tmp_path):
    from great_agent.db import knowledge_models  # noqa: F401
    from great_agent.db.knowledge_repository import KnowledgeRepository
    engine, factory = create_engine_and_session_factory(tmp_path / "knowledge.db")
    Base.metadata.create_all(engine)
    yield KnowledgeRepository(factory), engine
    engine.dispose()


def upload(repository, base_id, digest="a" * 64, filename="paper.pdf"):
    return repository.register_upload(base_id, digest, 25, "application/pdf",
                                      f"blobs/{digest[:2]}/{digest}.pdf", filename, "baseline")


def parsed(body="PPP ambiguity resolution uses satellite clock corrections.", warnings=None):
    return {
        "parser": {"name": "baseline", "version": "1", "config": {}},
        "metadata": {"title": "A paper"},
        "pages": [{"page_index": 0, "page_label": "i", "width": 600.0,
                   "height": 800.0, "role": "body", "raw_text": body}],
        "elements": [{"id": "e1", "kind": "paragraph", "text": body, "section": "Methods",
                      "locations": [{"page_index": 0, "page_label": "i", "bbox": [1, 2, 200, 40]}],
                      "quality_flags": ["reading_order_heuristic"]}],
        "chunks": [{"ordinal": 0, "kind": "text", "section": "Methods", "text": body,
                    "locations": [{"page_index": 0, "page_label": "i", "bbox": [1, 2, 200, 40]}],
                    "element_ids": ["e1"], "quality_flags": ["reading_order_heuristic"]}],
        "warnings": warnings or [],
    }


def test_document_counts_follow_published_active_version(repo):
    repo,_=repo
    base=repo.create_base('Counts')
    accepted=upload(repo,base['id'])
    pending=repo.get_document(accepted['document']['id'])
    assert (pending['page_count'],pending['chunk_count'])==(0,0)
    document=publish(repo,accepted)
    ready=repo.get_document(document['id'])
    assert (ready['page_count'],ready['chunk_count'])==(1,1)
    repo.queue_reparse(document['id'],'baseline','count-failed')
    failed=repo.claim_job('counts-owner')
    repo.fail_job(failed['id'],'counts-owner','parse_error','bad file')
    retained=repo.get_document(document['id'])
    assert (retained['page_count'],retained['chunk_count'])==(1,1)
    payload=parsed()
    payload['chunks'].append({**payload['chunks'][0],'ordinal':1})
    job=repo.queue_reparse(document['id'],'baseline','count-reparse')
    publish(repo,{'document':document,'job':job},payload=payload)
    latest=repo.list_documents(base['id'])[0]
    assert (latest['page_count'],latest['chunk_count'])==(1,2)


def publish(repository, uploaded, owner="owner-1", payload=None):
    job = repository.claim_job(owner)
    assert job["id"] == uploaded["job"]["id"]
    assert job["status"] == "parsing"
    assert job["blob"]["sha256"]
    assert repository.mark_indexing(job["id"], owner)
    assert repository.publish_job(job["id"], owner, payload or parsed())
    assert repository.get_job(job["id"])["status"] in ("completed", "completed_with_warnings")
    return repository.get_document(uploaded["document"]["id"])


def test_hash_identity_is_per_library_and_filename_is_only_display(repo):
    repository, _ = repo
    base = repository.create_base("GNSS")
    other = repository.create_base("Other")
    first = upload(repository, base["id"])
    duplicate = upload(repository, base["id"], filename="renamed.pdf")
    changed = upload(repository, base["id"], digest="b" * 64)
    elsewhere = upload(repository, other["id"])
    assert not first["duplicate"] and duplicate["duplicate"]
    assert duplicate["document"]["id"] == first["document"]["id"]
    assert duplicate["job"]["id"] == first["job"]["id"]
    assert changed["document"]["id"] != first["document"]["id"]
    assert elsewhere["document"]["id"] != first["document"]["id"]
    assert len(repository.list_documents(base["id"])) == 2
    assert len(repository.list_bases()) == 2


def test_publish_search_and_source_provenance(repo):
    repository, _ = repo
    base = repository.create_base("GNSS")
    item = upload(repository, base["id"])
    assert repository.search(base["id"], "ambiguity")["items"] == []
    document = publish(repository, item, payload=parsed(warnings=["layout uncertain"]))
    assert document["status"] == "completed_with_warnings"
    assert document["versions"][0]["warnings"] == ["layout uncertain"]
    result = repository.search(base["id"], "ambiguity")
    assert result["mode"] == "lexical"
    chunk = result["items"][0]
    assert chunk["document_id"] == item["document"]["id"]
    assert chunk["version_id"] == document["active_version_id"]
    assert chunk["locations"][0]["page_label"] == "i"
    assert chunk["locations"][0]["bbox"] == [1, 2, 200, 40]
    assert chunk["quality_flags"] == ["reading_order_heuristic"]
    assert repository.get_chunk(chunk["id"])["text"] == chunk["text"]


def test_failed_reparse_preserves_active_and_success_keeps_old_citations(repo):
    repository, _ = repo
    base = repository.create_base("GNSS")
    item = upload(repository, base["id"])
    first = publish(repository, item)
    old_chunk = repository.list_chunks(first["id"])["items"][0]
    retry = repository.queue_reparse(first["id"], "baseline", "r1")
    assert repository.queue_reparse(first["id"], "baseline", "r1")["id"] == retry["id"]
    claimed = repository.claim_job("owner-1")
    assert repository.fail_job(claimed["id"], "owner-1", "parse_error", "bad file")
    assert repository.get_document(first["id"])["active_version_id"] == first["active_version_id"]
    assert repository.search(base["id"], "ambiguity")["items"]
    job = repository.queue_reparse(first["id"], "baseline", "r2")
    updated = publish(repository, {"document": first, "job": job}, payload=parsed("New troposphere model."))
    assert updated["active_version_id"] != first["active_version_id"]
    assert repository.search(base["id"], "ambiguity")["items"] == []
    assert repository.list_chunks(first["id"], first["active_version_id"])["items"][0]["id"] == old_chunk["id"]
    assert repository.get_chunk(old_chunk["id"])["text"].startswith("PPP")


def test_restart_rejects_late_owner_and_release_is_owner_scoped(repo):
    repository, _ = repo
    base = repository.create_base("GNSS")
    item = upload(repository, base["id"])
    old = repository.claim_job("old")
    assert repository.claim_job("new") is None
    repository.recover_jobs("new")
    assert not repository.publish_job(old["id"], "old", parsed())
    current = repository.claim_job("new")
    assert current["id"] == old["id"]
    assert not repository.fail_job(current["id"], "old", "x", "late")
    assert not repository.release_job(current["id"], "old")
    assert repository.release_job(current["id"], "new")
    current = repository.claim_job("new")
    assert repository.publish_job(current["id"], "new", parsed())
    assert not repository.publish_job(current["id"], "new", parsed())
    assert len(repository.get_document(item["document"]["id"])["versions"]) == 1


def test_disabled_documents_and_cross_library_ids_never_leak(repo):
    from great_agent.db.knowledge_repository import KnowledgeNotFound
    repository, _ = repo
    base = repository.create_base("GNSS")
    other = repository.create_base("Other")
    item = upload(repository, base["id"])
    doc = publish(repository, item)
    chunk = repository.list_chunks(doc["id"])["items"][0]
    assert repository.search(other["id"], "PPP", [doc["id"]])["items"] == []
    repository.set_enabled(doc["id"], False)
    assert repository.search(base["id"], "PPP")["items"] == []
    with pytest.raises(KnowledgeNotFound):
        repository.list_chunks(doc["id"])
    with pytest.raises(KnowledgeNotFound):
        repository.get_chunk(chunk["id"])
    repository.set_enabled(doc["id"], True)
    assert repository.search(base["id"], "PPP")["items"]


@pytest.mark.parametrize("query", ['" OR * NEAR(', "'; DROP TABLE knowledge_chunks; --", "*", 'text:PPP OR "'])
def test_fts_metacharacters_are_data_and_do_not_break_database(repo, query):
    repository, _ = repo
    base = repository.create_base("GNSS")
    publish(repository, upload(repository, base["id"]))
    assert repository.search(base["id"], query)["mode"] == "lexical"
    assert repository.search(base["id"], "ambiguity")["items"]


@pytest.mark.parametrize("invalid", ["empty", "bbox", "missing_page", "element_ref", "nan"])
def test_invalid_publication_rolls_back_and_keeps_previous_version(repo, invalid):
    from great_agent.db.knowledge_repository import KnowledgeValidationError
    repository, engine = repo
    base = repository.create_base("GNSS")
    doc = publish(repository, upload(repository, base["id"]))
    job = repository.queue_reparse(doc["id"], "baseline", "bad")
    repository.claim_job("owner")
    payload = parsed()
    if invalid == "empty": payload["chunks"] = []
    if invalid == "bbox": payload["chunks"][0]["locations"][0]["bbox"] = [0, 0, 900, 5]
    if invalid == "missing_page": payload["chunks"][0]["locations"][0]["page_index"] = 10
    if invalid == "element_ref": payload["chunks"][0]["element_ids"] = ["missing"]
    if invalid == "nan": payload["pages"][0]["width"] = math.nan
    with pytest.raises(KnowledgeValidationError):
        repository.publish_job(job["id"], "owner", payload)
    assert repository.get_document(doc["id"])["active_version_id"] == doc["active_version_id"]
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM knowledge_versions")).scalar_one() == 1


def test_fts_triggers_follow_chunk_insert_update_delete(repo):
    repository, engine = repo
    base = repository.create_base("GNSS")
    doc = publish(repository, upload(repository, base["id"]))
    chunk = repository.list_chunks(doc["id"])["items"][0]
    with engine.begin() as connection:
        connection.execute(text("UPDATE knowledge_chunks SET text='ionosphere' WHERE id=:id"), {"id": chunk["id"]})
    assert repository.search(base["id"], "ambiguity")["items"] == []
    assert repository.search(base["id"], "ionosphere")["items"]
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM knowledge_chunks WHERE id=:id"), {"id": chunk["id"]})
    assert repository.search(base["id"], "ionosphere")["items"] == []


@pytest.mark.parametrize("kind", ["table", "trigger", "shadow"])
def test_fts_reserved_names_cannot_adopt_foreign_schema(tmp_path, kind):
    from great_agent.db import knowledge_models  # noqa: F401
    engine, _ = create_engine_and_session_factory(tmp_path / f"conflict-{kind}.db")
    with engine.begin() as connection:
        if kind == "table": connection.execute(text("CREATE TABLE knowledge_chunks_fts (text TEXT)"))
        elif kind == "shadow": connection.execute(text("CREATE TABLE knowledge_chunks_fts_data (text TEXT)"))
        else:
            connection.execute(text("CREATE TABLE unrelated (id INTEGER)"))
            connection.execute(text("CREATE TRIGGER knowledge_chunks_ai AFTER INSERT ON unrelated BEGIN SELECT 1; END"))
    with pytest.raises(RuntimeError, match="reserved|incompatible|Incompatible"):
        Base.metadata.create_all(engine)
    engine.dispose()


def test_preserves_structured_table_and_native_parser_document(repo):
    import json
    repository, engine = repo
    base = repository.create_base("GNSS")
    payload = parsed()
    payload["elements"][0]["kind"] = "table"
    payload["elements"][0]["structure"] = {"rows": [["signal", "bias"], ["L1", "0.25"]]}
    payload["raw_document"] = {"schema_name": "DoclingDocument", "tables": [{"data": {"num_rows": 2}}]}
    doc = publish(repository, upload(repository, base["id"]), payload=payload)
    assert doc["versions"][0]["raw_document"] == payload["raw_document"]
    with engine.connect() as connection:
        value = connection.execute(text("SELECT structure_json FROM knowledge_elements")).scalar_one()
    assert json.loads(value) == {"rows": [["signal", "bias"], ["L1", "0.25"]]}


def test_fts_prefix_validation_preserves_unrelated_research_tables(tmp_path):
    from great_agent.db import knowledge_models  # noqa: F401
    from great_agent.db.knowledge_schema import verify_knowledge_fts
    engine, _ = create_engine_and_session_factory(tmp_path / "research.db")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE knowledgeXchunksYfts (note TEXT)"))
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        verify_knowledge_fts(connection)
        assert connection.execute(text("SELECT COUNT(*) FROM knowledgeXchunksYfts")).scalar_one() == 0
    engine.dispose()


def test_sql_failure_rolls_back_partial_fts_and_relational_publication(repo):
    from sqlalchemy.exc import IntegrityError
    repository, engine = repo
    base = repository.create_base("GNSS")
    doc = publish(repository, upload(repository, base["id"]))
    job = repository.queue_reparse(doc["id"], "baseline", "bad-sql")
    repository.claim_job("owner")
    payload = parsed("new ionosphere")
    payload["chunks"].append({**payload["chunks"][0], "ordinal": 1, "text": "reject me"})
    with engine.begin() as connection:
        connection.exec_driver_sql("""CREATE TRIGGER reject_test_chunk BEFORE INSERT ON knowledge_chunks
            WHEN NEW.text='reject me' BEGIN SELECT RAISE(ABORT, 'reject test'); END""")
    with pytest.raises(IntegrityError):
        repository.publish_job(job["id"], "owner", payload)
    assert repository.get_document(doc["id"])["active_version_id"] == doc["active_version_id"]
    assert repository.search(base["id"], "ionosphere")["items"] == []
    assert len(repository.search(base["id"], "ambiguity")["items"]) == 1
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM knowledge_versions")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM knowledge_chunks")).scalar_one() == 1


def test_same_bytes_renamed_from_markdown_to_text_reuse_content_identity(repo):
    repository, _ = repo
    base = repository.create_base("Notes")
    first = repository.register_upload(base["id"], "a" * 64, 25, "text/markdown",
        "blobs/aa/" + "a" * 64 + ".md", "notes.md", "baseline")
    renamed = repository.register_upload(base["id"], "a" * 64, 25, "text/plain",
        "blobs/aa/" + "a" * 64 + ".txt", "renamed.txt", "baseline")
    assert renamed["duplicate"]
    assert renamed["document"]["id"] == first["document"]["id"]


def test_concurrent_claims_reserve_one_job_for_only_one_owner(repo):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    repository, _ = repo
    base = repository.create_base("GNSS")
    item = upload(repository, base["id"])
    barrier = Barrier(2)
    def claim(owner):
        barrier.wait(timeout=5)
        return repository.claim_job(owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim, owner) for owner in ("a", "b")]
        results = [future.result(timeout=10) for future in futures]
    claimed = [row for row in results if row is not None]
    assert len(claimed) == 1
    assert claimed[0]["id"] == item["job"]["id"]
    assert repository.get_job(item["job"]["id"])["owner_id"] == claimed[0]["owner_id"]


def test_invalid_input_and_reparse_request_conflict_are_domain_errors(repo):
    from great_agent.db.knowledge_repository import KnowledgeConflict, KnowledgeNotFound, KnowledgeValidationError
    repository, _ = repo
    with pytest.raises(KnowledgeValidationError): repository.create_base("  ")
    with pytest.raises(KnowledgeNotFound): repository.get_base("missing")
    base = repository.create_base("GNSS")
    with pytest.raises(KnowledgeValidationError):
        repository.register_upload(base["id"], "a" * 64, 25, "application/pdf", "blobs/../../secret.pdf", "name", "baseline")
    item = upload(repository, base["id"])
    with pytest.raises(KnowledgeConflict): repository.queue_reparse(item["document"]["id"], "baseline", "r1")
    doc = publish(repository, item)
    job = repository.queue_reparse(doc["id"], "baseline", "r2")
    with pytest.raises(KnowledgeConflict): repository.queue_reparse(doc["id"], "docling", "r2")
    assert job["id"] == repository.queue_reparse(doc["id"], "baseline", "r2")["id"]
    with pytest.raises(KnowledgeValidationError): repository.set_enabled(doc["id"], "false")
    with pytest.raises(KnowledgeValidationError): repository.search(base["id"], "PPP", limit=0)
    with pytest.raises(KnowledgeValidationError): repository.search(base["id"], "PPP", document_ids="oops")
    assert repository.list_chunks(doc["id"], offset=1)["total"] == 1
    assert repository.list_chunks(doc["id"], offset=1)["items"] == []


def test_document_summary_carries_current_source_and_latest_job_failure(repo):
    repository, _ = repo
    base = repository.create_base("GNSS")
    uploaded = upload(repository, base["id"])
    initial = uploaded["document"]
    assert initial["title"] == "paper.pdf"
    assert initial["warnings"] == []
    assert initial["latest_job"]["id"] == uploaded["job"]["id"]
    assert initial["latest_job"]["version_id"] is None
    assert initial["latest_job"]["warnings"] == []
    doc = publish(repository, uploaded, payload=parsed(warnings=["reading order is heuristic"]))
    job = repository.get_job(uploaded["job"]["id"])
    assert job["version_id"] == doc["active_version_id"]
    assert job["warnings"] == ["reading order is heuristic"]
    listed = repository.list_documents(base["id"])[0]
    assert listed["title"] == "A paper"
    assert listed["warnings"] == ["reading order is heuristic"]
    assert listed["latest_job"]["warnings"] == ["reading order is heuristic"]
    retry = repository.queue_reparse(doc["id"], "baseline", "failed-summary")
    repository.claim_job("owner")
    repository.fail_job(retry["id"], "owner", "parser_failed", "failed reparse")
    failed = repository.get_document(doc["id"])
    assert failed["title"] == "A paper"
    assert failed["warnings"] == ["reading order is heuristic"]
    assert failed["latest_job"]["id"] == retry["id"]
    assert failed["latest_job"]["error_code"] == "parser_failed"
    assert failed["latest_job"]["error_message"] == "failed reparse"
    assert failed["latest_job"]["version_id"] is None
    assert failed["latest_job"]["warnings"] == []


@pytest.mark.parametrize("query,body", [
    ("模糊度固定", "Integer ambiguity resolution for satellite navigation."),
    ("精密单点定位", "Precise point positioning converges in a few minutes."),
    ("卫星钟差", "Satellite clock correction is updated every second."),
    ("电离层", "An ionospheric delay correction is applied."),
    ("troposphere", "对流层。"),
    ("BeiDou", "北斗。"),
    ("ambiguity resolution", "模糊度固定。"),
])
def test_controlled_gnss_dictionary_is_explicitly_lexical(repo, query, body):
    repository, _ = repo
    base = repository.create_base("GNSS")
    doc = publish(repository, upload(repository, base["id"]), payload=parsed(body))
    result = repository.search(base["id"], query)
    assert result["mode"] == "lexical"
    assert len(result["items"]) == 1
    assert result["items"][0]["document_id"] == doc["id"]


def test_real_text_parser_without_heading_can_publish(repo, tmp_path):
    from great_agent.knowledge.parsers import parse_document
    repository, _ = repo
    source = tmp_path / "notes.txt"
    source.write_text("Satellite clock corrections improve PPP.", encoding="utf-8")
    payload = parse_document(source, "text/plain", "pdfplumber", 300).model_dump(mode="json")
    assert payload["chunks"][0]["section"] is None
    base = repository.create_base("Notes")
    doc = publish(repository, upload(repository, base["id"]), payload=payload)
    chunks = repository.list_chunks(doc["id"])["items"]
    assert chunks[0]["section"] == ""
    assert chunks[0]["locations"] == [{"page_index": 0, "page_label": "1", "bbox": None}]
    assert repository.search(base["id"], "clock")["items"]


def test_duplicate_upload_returns_published_job_version_and_warnings(repo):
    repository, _ = repo
    base = repository.create_base("GNSS")
    first = upload(repository, base["id"])
    doc = publish(repository, first, payload=parsed(warnings=["table values need review"]))
    duplicate = upload(repository, base["id"], filename="same-content-renamed.pdf")
    assert duplicate["duplicate"]
    assert duplicate["job"]["id"] == first["job"]["id"]
    assert duplicate["job"]["version_id"] == doc["active_version_id"]
    assert duplicate["job"]["warnings"] == ["table values need review"]


def test_idempotent_reparse_returns_published_job_version_and_warnings(repo):
    repository, _ = repo
    base = repository.create_base("GNSS")
    doc = publish(repository, upload(repository, base["id"]))
    job = repository.queue_reparse(doc["id"], "baseline", "reviewed-reparse")
    assert job["version_id"] is None
    assert job["warnings"] == []
    updated = publish(repository, {"document": doc, "job": job}, payload=parsed(warnings=["layout needs review"]))
    repeated = repository.queue_reparse(doc["id"], "baseline", "reviewed-reparse")
    assert repeated["id"] == job["id"]
    assert repeated["version_id"] == updated["active_version_id"]
    assert repeated["warnings"] == ["layout needs review"]


@pytest.mark.parametrize("suffix", ["data", "idx", "docsize", "config"])
@pytest.mark.parametrize("collision", ["columns", "primary_key"])
def test_fts_shadow_collision_is_rejected_before_retrieval(repo, suffix, collision):
    from great_agent.db.knowledge_schema import verify_knowledge_fts
    _, engine = repo
    with engine.begin() as connection:
        # Identifiers come only from this fixed parametrization.
        connection.exec_driver_sql(f"DROP TABLE knowledge_chunks_fts_{suffix}")
        columns = {"data": "id INTEGER, block BLOB", "idx": "segid, term, pgno",
                   "docsize": "id INTEGER, sz BLOB", "config": "k, v"}
        definition = "wrong TEXT" if collision == "columns" else columns[suffix]
        connection.exec_driver_sql(f"CREATE TABLE knowledge_chunks_fts_{suffix} ({definition})")
        with pytest.raises(RuntimeError, match="shadow|reserved|Incompatible"):
            verify_knowledge_fts(connection)
