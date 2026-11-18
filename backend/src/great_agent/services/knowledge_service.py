"""Own the durable ingestion queue; parsers never own database publication."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

from great_agent.knowledge.types import PARSER_OUTPUT_MAX_BYTES, ParsedDocument
from great_agent.runtime.worker_lock import WorkerLock

logger = logging.getLogger(__name__)


class ParserFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class KnowledgeService:
    def __init__(self, repository, storage, *, parser_timeout_seconds=300,
                 max_pages=300, docling_artifacts=None, poll_interval_seconds=0.25):
        self.repository, self.storage = repository, storage
        self.parser_timeout_seconds, self.max_pages = parser_timeout_seconds, max_pages
        self.docling_artifacts = docling_artifacts
        self.poll_interval_seconds = poll_interval_seconds
        self.owner_id = str(uuid4())
        database = repository.sessions.kw['bind'].url.database
        self._owner_lock_path = Path(str(Path(database).resolve()) + '.knowledge')
        self._task = None
        self._lock = None
        self._process = None

    async def start(self):
        if self._task is not None:
            return
        lock = WorkerLock(self._owner_lock_path)
        lock.__enter__()
        self._lock = lock
        self.owner_id = str(uuid4())
        try:
            recovery = asyncio.create_task(asyncio.to_thread(self.repository.recover_jobs, self.owner_id))
            try:
                await asyncio.shield(recovery)
            except asyncio.CancelledError as cancellation:
                # A thread cannot be cancelled. Keep the OS lock until recovery
                # actually finishes so it cannot invalidate a newer owner.
                with suppress(Exception):
                    await recovery
                raise cancellation
            self._task = asyncio.create_task(self._run(), name='knowledge-ingestion')
        except BaseException:
            lock.__exit__(None, None, None)
            self._lock = None
            raise

    async def stop(self):
        task, self._task = self._task, None
        try:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        finally:
            if self._lock is not None:
                self._lock.__exit__(None, None, None)
                self._lock = None

    async def _run(self):
        while True:
            job = None
            try:
                # Cancellation can arrive during claim. Finish the short claim so
                # the owner can release it, instead of losing the claimed job ID.
                claim = asyncio.create_task(asyncio.to_thread(self.repository.claim_job, self.owner_id))
                try:
                    job = await asyncio.shield(claim)
                except asyncio.CancelledError as cancellation:
                    try:
                        job = await claim
                        if job:
                            await asyncio.to_thread(self.repository.release_job, job['id'], self.owner_id)
                    except Exception:
                        logger.exception('Claim/release failed during knowledge shutdown')
                    raise cancellation
                if job is None:
                    await asyncio.sleep(self.poll_interval_seconds)
                    continue
                await self._process_job(job)
            except asyncio.CancelledError:
                if job:
                    await asyncio.to_thread(self.repository.release_job, job['id'], self.owner_id)
                raise
            except Exception:
                logger.exception('Knowledge queue operation failed')
                # Operational DB failures must not permanently kill the queue.
                if job:
                    with suppress(Exception):
                        await asyncio.to_thread(self.repository.release_job, job['id'], self.owner_id)
                await asyncio.sleep(self.poll_interval_seconds)

    async def _process_job(self, job):
        try:
            parsed = await self._parse(job)
            if await asyncio.to_thread(self.repository.mark_indexing, job['id'], self.owner_id):
                await asyncio.to_thread(self.repository.publish_job, job['id'], self.owner_id,
                                        parsed.model_dump(mode='json'))
        except asyncio.CancelledError:
            raise
        except ParserFailure as error:
            await asyncio.to_thread(self.repository.fail_job, job['id'], self.owner_id, error.code, str(error)[:1500])
        except (ValueError, OSError) as error:
            await asyncio.to_thread(self.repository.fail_job, job['id'], self.owner_id, 'parse_failed', str(error)[:1500])

    async def _parse(self, job):
        blob = job['blob']
        source = self.storage.original_path(blob['original_relpath'])
        stage = self.storage.original_path(f"staging/{job['id']}/{self.owner_id}/{uuid4()}")
        stage.mkdir(parents=True, exist_ok=False)
        output = stage / 'parsed.json'
        command = [sys.executable, '-m', 'great_agent.knowledge.parse_worker', '--source', str(source),
                   '--media-type', blob['media_type'], '--parser', job['parser_name'],
                   '--output', str(output), '--max-pages', str(self.max_pages)]
        if self.docling_artifacts is not None:
            command += ['--artifacts', str(self.docling_artifacts)]
        env = {**os.environ, 'PYTHONIOENCODING':'utf-8', 'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1'}
        # pytest fixtures and source checkouts are supported without depending on
        # an editable install in the child interpreter.
        source_root = str(Path(__file__).resolve().parents[2])
        env['PYTHONPATH'] = source_root + os.pathsep + env.get('PYTHONPATH','')
        process = None
        try:
            process = await asyncio.create_subprocess_exec(*command, env=env,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            self._process = process
            try:
                code = await asyncio.wait_for(process.wait(), timeout=self.parser_timeout_seconds)
            except asyncio.TimeoutError as error:
                raise ParserFailure('parse_timeout', '解析超过时间限制，请检查文件或调整本地解析配置。') from error
            if not output.is_file():
                raise ParserFailure('parse_failed', '解析进程没有产生结果。')
            if output.stat().st_size > PARSER_OUTPUT_MAX_BYTES:
                raise ParserFailure('parse_output_too_large', '解析结果超过大小限制。')
            raw = await asyncio.to_thread(output.read_bytes)
            if code != 0:
                try:
                    message = json.loads(raw).get('error', '解析进程失败。')
                except (ValueError, AttributeError):
                    message = '解析进程失败。'
                raise ParserFailure('parse_failed', str(message)[:1500])
            return ParsedDocument.model_validate_json(raw)
        finally:
            if process is not None and process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
            self._process = None
            await asyncio.to_thread(self._cleanup_stage, stage)

    def _cleanup_stage(self, stage):
        root = self.storage.original_path('staging')
        target = stage.resolve()
        # This exact job/owner/random UUID directory was created above. Never
        # recursively remove a computed target outside that staging subtree.
        if not target.is_relative_to(root) or target.parent.parent.parent != root:
            raise ValueError('unsafe parser staging cleanup path')
        shutil.rmtree(target, ignore_errors=False)
        for parent in (target.parent, target.parent.parent):
            with suppress(OSError):
                parent.rmdir()

    def register_staged_upload(self, base_id, filename, staged_path):
        self.repository.get_base(base_id)
        blob = self.storage.store_file(staged_path, filename)
        return self.repository.register_upload(base_id=base_id,filename=filename,
            parser_name='pdfplumber', **blob)

    def original(self, document_id):
        from great_agent.db.knowledge_repository import KnowledgeNotFound
        document = self.repository.get_document(document_id)
        if not document['enabled']:
            raise KnowledgeNotFound('document is disabled')
        path = self.storage.original_path(document['blob']['original_relpath'])
        if not path.is_file():
            raise KnowledgeNotFound('original is unavailable')
        return path, document
