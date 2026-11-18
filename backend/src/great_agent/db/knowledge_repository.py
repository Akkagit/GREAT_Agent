"""Transactional local knowledge catalogue and explicitly lexical retrieval.

Document text is inert data. SQL identifiers and FTS syntax are always generated
here; source content never controls queries, paths, parsers or job ownership.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from uuid import uuid4

from sqlalchemy import func, select, text

from great_agent.db.knowledge_models import (
    KnowledgeBaseModel as Library, KnowledgeBlobModel as Blob,
    KnowledgeChunkModel as Chunk, KnowledgeDocumentModel as Document,
    KnowledgeElementModel as Element, KnowledgeJobModel as Job,
    KnowledgePageModel as Page, KnowledgeVersionModel as Version,
)
from great_agent.db.models import utc_now


class KnowledgeNotFound(Exception):
    pass


class KnowledgeConflict(Exception):
    pass


class KnowledgeValidationError(ValueError):
    pass


_BUSY = ("queued", "parsing", "indexing")
_OWNED = ("parsing", "indexing")
_PUBLISHED = ("ready", "completed_with_warnings")

# A deliberately small, fixed vocabulary. These are lexical synonyms, not
# embeddings, generated translations, stemming or a semantic relevance model.
_GNSS_LEXICAL_GROUPS = (
    ("模糊度固定", "模糊度解算", "ambiguity", "ambiguities", "ambiguity resolution", "ambiguity fixing"),
    ("精密单点定位", "ppp", "precise point positioning"),
    ("卫星轨道", "轨道", "orbit", "orbital"),
    ("卫星钟差", "钟差", "clock"),
    ("电离层", "ionosphere", "ionospheric"),
    ("对流层", "troposphere", "tropospheric"),
    ("北斗", "beidou", "bds"),
)


def _lexical_terms(query):
    normalized = query.casefold()
    terms = list(dict.fromkeys(re.findall(r"[^\W_]+", normalized, re.UNICODE)))[:64]
    for aliases in _GNSS_LEXICAL_GROUPS:
        def mentioned(alias):
            if re.search(r"[\u3400-\u9fff]", alias):
                return alias in normalized
            return re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", normalized) is not None
        if any(mentioned(alias) for alias in aliases):
            terms.extend(aliases)
    return list(dict.fromkeys(terms))[:96]


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as error:
        raise KnowledgeValidationError("invalid JSON data") from error


def _string(value, label, maximum=None, *, empty=False):
    if not isinstance(value, str) or "\0" in value or (not empty and not value.strip()):
        raise KnowledgeValidationError(f"invalid {label}")
    if maximum is not None and len(value) > maximum:
        raise KnowledgeValidationError(f"{label} is too long")
    return value


def _integer(value, label, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise KnowledgeValidationError(f"invalid {label}")
    if maximum is not None and value > maximum:
        raise KnowledgeValidationError(f"invalid {label}")
    return value


def _serialize(row):
    result = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if column.name.endswith("_json"):
            result[column.name[:-5]] = json.loads(value)
        else:
            result[column.name] = value.isoformat() if hasattr(value, "isoformat") else value
    if isinstance(row, Version):
        result["parser"] = {"name": result["parser_name"], "version": result["parser_version"],
                            "config": result.pop("parser_config")}
    return result


class KnowledgeRepository:
    def __init__(self, session_factory):
        self.sessions = session_factory

    @staticmethod
    def _require(session, model, identifier):
        row = session.get(model, str(identifier))
        if row is None:
            raise KnowledgeNotFound(f"unknown {model.__tablename__}")
        return row

    @staticmethod
    def _begin(session):
        # SQLite's write reservation serializes read-then-write decisions and
        # prevents two API processes from claiming the same queued job.
        session.execute(text("BEGIN IMMEDIATE"))

    @staticmethod
    def _job(session, row):
        result = _serialize(row)
        version = session.scalar(select(Version).where(Version.job_id == row.id))
        result["version_id"] = version.id if version else None
        result["warnings"] = json.loads(version.warnings_json) if version else []
        return result

    @staticmethod
    def _document(session, row, *, details=False):
        result = _serialize(row)
        result["blob"] = _serialize(session.get(Blob, row.blob_id))
        active = session.get(Version, row.active_version_id) if row.active_version_id else None
        result['page_count'] = int(session.scalar(select(func.count()).select_from(Page).where(
            Page.version_id == active.id)) or 0) if active else 0
        result['chunk_count'] = int(session.scalar(select(func.count()).select_from(Chunk).where(
            Chunk.version_id == active.id)) or 0) if active else 0
        title = json.loads(active.metadata_json).get("title") if active else None
        result["title"] = title if isinstance(title, str) and title.strip() else row.filename
        result["warnings"] = json.loads(active.warnings_json) if active else []
        latest = session.scalar(select(Job).where(Job.document_id == row.id)
                                .order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
        result["latest_job"] = KnowledgeRepository._job(session, latest) if latest else None
        if details:
            result["versions"] = [_serialize(version) for version in session.scalars(
                select(Version).where(Version.document_id == row.id).order_by(Version.created_at, Version.id))]
            result["jobs"] = [KnowledgeRepository._job(session, job) for job in session.scalars(
                select(Job).where(Job.document_id == row.id).order_by(Job.created_at, Job.id))]
        return result

    def create_base(self, name):
        name = _string(name, "base name", 200).strip()
        with self.sessions.begin() as session:
            row = Library(id=str(uuid4()), name=name)
            session.add(row)
            session.flush()
            return _serialize(row)

    def list_bases(self):
        with self.sessions() as session:
            return [_serialize(row) for row in session.scalars(select(Library).order_by(Library.created_at, Library.id))]

    def get_base(self, base_id):
        with self.sessions() as session:
            return _serialize(self._require(session, Library, base_id))

    def register_upload(self, base_id, sha256, size_bytes, media_type, original_relpath, filename, parser_name):
        if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise KnowledgeValidationError("invalid SHA256")
        _integer(size_bytes, "size_bytes", 1)
        if media_type not in ("application/pdf", "text/plain", "text/markdown"):
            raise KnowledgeValidationError("unsupported media type")
        _string(filename, "filename", 255)
        _string(original_relpath, "original path", 300)
        source_path = PurePosixPath(original_relpath)
        if (source_path.is_absolute() or "\\" in original_relpath or ":" in original_relpath
                or ".." in source_path.parts or not source_path.parts or source_path.parts[0] != "blobs"):
            raise KnowledgeValidationError("invalid original path")
        _string(parser_name, "parser", 64)
        with self.sessions.begin() as session:
            self._begin(session)
            self._require(session, Library, base_id)
            blob = session.scalar(select(Blob).where(Blob.sha256 == sha256))
            if blob is None:
                blob = Blob(id=str(uuid4()), sha256=sha256, size_bytes=size_bytes, media_type=media_type,
                            original_relpath=original_relpath)
                session.add(blob)
                session.flush()
            elif blob.size_bytes != size_bytes:
                raise KnowledgeConflict("blob hash already registered with a different size")
            row = session.scalar(select(Document).where(Document.base_id == str(base_id), Document.blob_id == blob.id))
            duplicate = row is not None
            if row is None:
                row = Document(id=str(uuid4()), base_id=str(base_id), blob_id=blob.id,
                               filename=filename, enabled=True, status="queued")
                session.add(row)
                session.flush()
                job = Job(id=str(uuid4()), document_id=row.id, parser_name=parser_name,
                          request_id="upload", status="queued")
                session.add(job)
                session.flush()
            else:
                job = session.scalar(select(Job).where(Job.document_id == row.id)
                                     .order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
            return {"document": self._document(session, row), "job": self._job(session, job), "duplicate": duplicate}

    def list_documents(self, base_id):
        with self.sessions() as session:
            self._require(session, Library, base_id)
            return [self._document(session, row) for row in session.scalars(
                select(Document).where(Document.base_id == str(base_id)).order_by(Document.created_at, Document.id))]

    def get_document(self, document_id):
        with self.sessions() as session:
            return self._document(session, self._require(session, Document, document_id), details=True)

    def set_enabled(self, document_id, enabled):
        if not isinstance(enabled, bool):
            raise KnowledgeValidationError("enabled must be boolean")
        with self.sessions.begin() as session:
            self._begin(session)
            row = self._require(session, Document, document_id)
            row.enabled = enabled
            session.flush()
            return self._document(session, row)

    def queue_reparse(self, document_id, parser_name, request_id):
        _string(parser_name, "parser", 64)
        _string(request_id, "request ID", 128)
        with self.sessions.begin() as session:
            self._begin(session)
            document = self._require(session, Document, document_id)
            job = session.scalar(select(Job).where(Job.document_id == document.id, Job.request_id == request_id))
            if job:
                if job.parser_name != parser_name:
                    raise KnowledgeConflict("request ID already used for another parser")
                return self._job(session, job)
            if session.scalar(select(Job.id).where(Job.document_id == document.id, Job.status.in_(_BUSY)).limit(1)):
                raise KnowledgeConflict("document already has an active ingestion job")
            job = Job(id=str(uuid4()), document_id=document.id, parser_name=parser_name,
                      request_id=request_id, status="queued")
            session.add(job)
            document.status = "queued"
            session.flush()
            return self._job(session, job)

    def get_job(self, job_id):
        with self.sessions() as session:
            job = self._require(session, Job, job_id)
            return self._job(session, job)

    def recover_jobs(self, owner_id):
        _string(owner_id, "owner", 128)
        with self.sessions.begin() as session:
            self._begin(session)
            jobs = session.scalars(select(Job).where(Job.status.in_(_OWNED))).all()
            count = 0
            for job in jobs:
                if job.owner_id != owner_id:
                    self._requeue(session, job)
                    count += 1
            return count

    def claim_job(self, owner_id):
        _string(owner_id, "owner", 128)
        with self.sessions.begin() as session:
            self._begin(session)
            job = session.scalar(select(Job).where(Job.status == "queued")
                                 .order_by(Job.created_at, Job.id).limit(1))
            if job is None:
                return None
            job.status, job.owner_id, job.started_at = "parsing", owner_id, utc_now()
            job.finished_at, job.error_code, job.error_message = None, None, None
            document = session.get(Document, job.document_id)
            document.status = "parsing"
            session.flush()
            result = _serialize(job)
            result["document"] = self._document(session, document)
            result["blob"] = result["document"]["blob"]
            return result

    @staticmethod
    def _owned(session, job_id, owner_id):
        job = session.get(Job, str(job_id))
        return job if job and job.owner_id == owner_id and job.status in _OWNED else None

    def mark_indexing(self, job_id, owner_id):
        with self.sessions.begin() as session:
            self._begin(session)
            job = self._owned(session, job_id, owner_id)
            if job is None:
                return False
            job.status = "indexing"
            session.get(Document, job.document_id).status = "indexing"
            return True

    @staticmethod
    def _validate_parsed(parsed):
        if not isinstance(parsed, dict):
            raise KnowledgeValidationError("parsed result must be an object")
        _json(parsed)
        parser = parsed.get("parser")
        if not isinstance(parser, dict) or not isinstance(parser.get("config"), dict):
            raise KnowledgeValidationError("invalid parser metadata")
        _string(parser.get("name"), "parser name", 64)
        _string(parser.get("version"), "parser version", 100)
        if not isinstance(parsed.get("metadata"), dict):
            raise KnowledgeValidationError("metadata must be an object")
        warnings = parsed.get("warnings")
        if not isinstance(warnings, list) or any(not isinstance(value, str) for value in warnings):
            raise KnowledgeValidationError("warnings must be strings")
        pages, elements, chunks = (parsed.get(key) for key in ("pages", "elements", "chunks"))
        if not all(isinstance(rows, list) for rows in (pages, elements, chunks)) or not pages or not chunks:
            raise KnowledgeValidationError("cannot publish an empty index")
        page_map = {}
        for page in pages:
            if not isinstance(page, dict):
                raise KnowledgeValidationError("invalid page")
            index = _integer(page.get("page_index"), "page index")
            if index in page_map:
                raise KnowledgeValidationError("duplicate page index")
            for key in ("width", "height"):
                value = page.get(key)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                          or not math.isfinite(value) or value <= 0):
                    raise KnowledgeValidationError("invalid page dimensions")
            _string(page.get("page_label"), "page label", empty=True)
            _string(page.get("role"), "page role", 64)
            _string(page.get("raw_text"), "raw page text", empty=True)
            page_map[index] = page

        def validate_locations(locations):
            if not isinstance(locations, list) or not locations:
                raise KnowledgeValidationError("source locations required")
            for location in locations:
                if not isinstance(location, dict):
                    raise KnowledgeValidationError("invalid location")
                index = _integer(location.get("page_index"), "location page index")
                if index not in page_map:
                    raise KnowledgeValidationError("unknown location page")
                page = page_map[index]
                if location.get("page_label") != page["page_label"]:
                    raise KnowledgeValidationError("inconsistent location page label")
                bbox = location.get("bbox")
                # Text files have no physical coordinates; explicit null is valid.
                if bbox is None:
                    if page.get("width") is not None or page.get("height") is not None:
                        raise KnowledgeValidationError("physical page requires bbox")
                    continue
                if (not isinstance(bbox, list) or len(bbox) != 4
                        or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in bbox)):
                    raise KnowledgeValidationError("invalid bounding box")
                x0, y0, x1, y1 = bbox
                if x0 < 0 or y0 < 0 or x1 < x0 or y1 < y0:
                    raise KnowledgeValidationError("invalid bounding box bounds")
                if ((page.get("width") is not None and x1 > page["width"])
                        or (page.get("height") is not None and y1 > page["height"])):
                    raise KnowledgeValidationError("bounding box exceeds page")

        def validate_block(block):
            if not isinstance(block, dict):
                raise KnowledgeValidationError("invalid source block")
            _string(block.get("kind"), "block kind", 64)
            _string(block.get("text"), "block text", empty=True)
            if block.get("section") is not None:
                _string(block["section"], "block section", empty=True)
            flags = block.get("quality_flags")
            if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
                raise KnowledgeValidationError("invalid quality flags")
            validate_locations(block.get("locations"))

        element_ids = set()
        for element in elements:
            validate_block(element)
            identifier = _string(element.get("id"), "element ID", 200)
            if identifier in element_ids:
                raise KnowledgeValidationError("duplicate element ID")
            element_ids.add(identifier)
        ordinals = set()
        for chunk in chunks:
            validate_block(chunk)
            _string(chunk["text"], "chunk text")
            ordinal = _integer(chunk.get("ordinal"), "chunk ordinal")
            if ordinal in ordinals:
                raise KnowledgeValidationError("duplicate chunk ordinal")
            ordinals.add(ordinal)
            refs = chunk.get("element_ids")
            if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in element_ids for ref in refs):
                raise KnowledgeValidationError("unknown chunk element reference")

    def publish_job(self, job_id, owner_id, parsed_dict):
        with self.sessions.begin() as session:
            self._begin(session)
            job = self._owned(session, job_id, owner_id)
            if job is None:
                return False
            self._validate_parsed(parsed_dict)
            document = session.get(Document, job.document_id)
            blob = session.get(Blob, document.blob_id)
            parser = parsed_dict["parser"]
            status = "completed_with_warnings" if parsed_dict["warnings"] else "ready"
            fingerprint = hashlib.sha256(_json({"blob": blob.sha256, "parser": parser}).encode("utf-8")).hexdigest()
            version = Version(id=str(uuid4()), document_id=document.id, job_id=job.id, status=status,
                              fingerprint=fingerprint, parser_name=parser["name"], parser_version=parser["version"],
                              parser_config_json=_json(parser["config"]), metadata_json=_json(parsed_dict["metadata"]),
                              warnings_json=_json(parsed_dict["warnings"]),
                              raw_document_json=_json(parsed_dict.get("raw_document")))
            session.add(version)
            session.flush()
            for page in parsed_dict["pages"]:
                session.add(Page(id=str(uuid4()), version_id=version.id, page_index=page["page_index"],
                                 page_label=page["page_label"], width=page.get("width"), height=page.get("height"),
                                 role=page["role"], raw_text=page["raw_text"]))
            element_map = {element["id"]: str(uuid4()) for element in parsed_dict["elements"]}
            for element in parsed_dict["elements"]:
                session.add(Element(id=element_map[element["id"]], version_id=version.id, source_id=element["id"],
                                    kind=element["kind"], text=element["text"], section=element.get("section") or "",
                                    locations_json=_json(element["locations"]), quality_flags_json=_json(element["quality_flags"]),
                                    structure_json=_json(element.get("structure"))))
            for chunk in parsed_dict["chunks"]:
                session.add(Chunk(id=str(uuid4()), document_id=document.id, version_id=version.id,
                                  ordinal=chunk["ordinal"], kind=chunk["kind"], text=chunk["text"], section=chunk.get("section") or "",
                                  locations_json=_json(chunk["locations"]),
                                  element_ids_json=_json([element_map[identifier] for identifier in chunk["element_ids"]]),
                                  quality_flags_json=_json(chunk["quality_flags"])))
            # This flush executes FTS triggers before switching the document pointer.
            session.flush()
            document.active_version_id, document.status = version.id, status
            job.status, job.finished_at = ("completed" if status == "ready" else status), utc_now()
            return True

    def fail_job(self, job_id, owner_id, error_code, error_message):
        _string(error_code, "error code", 100)
        _string(error_message, "error message", 10000)
        with self.sessions.begin() as session:
            self._begin(session)
            job = self._owned(session, job_id, owner_id)
            if job is None:
                return False
            job.status, job.error_code, job.error_message, job.finished_at = "failed", error_code, error_message, utc_now()
            document = session.get(Document, job.document_id)
            old = session.get(Version, document.active_version_id) if document.active_version_id else None
            document.status = old.status if old else "failed"
            return True

    @staticmethod
    def _requeue(session, job):
        job.status, job.owner_id, job.started_at, job.finished_at = "queued", None, None, None
        session.get(Document, job.document_id).status = "queued"

    def release_job(self, job_id, owner_id):
        with self.sessions.begin() as session:
            self._begin(session)
            job = self._owned(session, job_id, owner_id)
            if job is None:
                return False
            self._requeue(session, job)
            return True

    def list_chunks(self, document_id, version_id=None, offset=0, limit=50):
        _integer(offset, "offset")
        _integer(limit, "limit", 1, 200)
        with self.sessions() as session:
            document = self._require(session, Document, document_id)
            if not document.enabled:
                raise KnowledgeNotFound("document is disabled")
            selected_version = version_id if version_id is not None else document.active_version_id
            if selected_version is None:
                return {"items": [], "total": 0}
            version = self._require(session, Version, selected_version)
            if version.document_id != document.id or version.status not in _PUBLISHED:
                raise KnowledgeNotFound("unknown published version")
            filters = (Chunk.document_id == document.id, Chunk.version_id == version.id)
            total = session.scalar(select(func.count()).select_from(Chunk).where(*filters))
            items = [_serialize(row) for row in session.scalars(select(Chunk).where(*filters)
                     .order_by(Chunk.ordinal).offset(offset).limit(limit))]
            return {"items": items, "total": total}

    def get_chunk(self, chunk_id):
        with self.sessions() as session:
            chunk = self._require(session, Chunk, chunk_id)
            document = self._require(session, Document, chunk.document_id)
            version = self._require(session, Version, chunk.version_id)
            if not document.enabled or version.status not in _PUBLISHED:
                raise KnowledgeNotFound("chunk source is unavailable")
            return _serialize(chunk)

    def search(self, base_id, query, document_ids=None, limit=8):
        _string(query, "query", 4000, empty=True)
        _integer(limit, "limit", 1, 50)
        if document_ids is not None and (not isinstance(document_ids, list)
                or len(document_ids) > 200 or any(not isinstance(identifier, str) for identifier in document_ids)):
            raise KnowledgeValidationError("invalid document IDs")
        # Quoting every term removes FTS operators/column selectors and all SQL
        # values stay bound. Scientific words remain a lexical OR query.
        terms = _lexical_terms(query)
        with self.sessions() as session:
            self._require(session, Library, base_id)
            if not terms or document_ids == []:
                return {"mode": "lexical", "items": []}
            match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
            parameters = {"base_id": str(base_id), "match": match, "limit": limit}
            extra = ""
            if document_ids is not None:
                parameters.update({f"doc{index}": value for index, value in enumerate(document_ids)})
                extra = " AND d.id IN (" + ",".join(f":doc{index}" for index in range(len(document_ids))) + ")"
            matches = session.execute(text("""SELECT c.id, bm25(knowledge_chunks_fts) AS score
                FROM knowledge_chunks_fts JOIN knowledge_chunks c ON c.rowid=knowledge_chunks_fts.rowid
                JOIN knowledge_documents d ON d.id=c.document_id
                JOIN knowledge_versions v ON v.id=c.version_id
                WHERE knowledge_chunks_fts MATCH :match AND d.base_id=:base_id
                AND d.enabled=1 AND d.active_version_id=c.version_id
                AND v.status IN ('ready','completed_with_warnings')""" + extra +
                " ORDER BY score, c.ordinal, c.id LIMIT :limit"), parameters).all()
            items = []
            for identifier, score in matches:
                chunk = session.get(Chunk, identifier)
                document = session.get(Document, chunk.document_id)
                version = session.get(Version, chunk.version_id)
                result = _serialize(chunk)
                title = json.loads(version.metadata_json).get("title") or document.filename
                result.update(filename=document.filename, title=title, base_id=document.base_id,
                              chunk_id=chunk.id, score=float(score))
                items.append(result)
            return {"mode": "lexical", "items": items}
