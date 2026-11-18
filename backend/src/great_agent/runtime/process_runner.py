from __future__ import annotations

import asyncio
import subprocess
from contextlib import suppress
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class ProcessOutcome:
    pid: int
    exit_code: int
    cancelled: bool


class ProcessCleanupError(RuntimeError):
    """The owned child may still run; retain its handle and keep its quota."""
    def __init__(self, key: UUID, process: asyncio.subprocess.Process):
        super().__init__(f'Cannot confirm cleanup of owned process {process.pid}')
        self.key = key
        self.pid = process.pid
        self.process = process


class ProcessRunner:
    def __init__(self, *, cancel_grace_seconds: float = 5.0) -> None:
        self.cancel_grace_seconds = cancel_grace_seconds
        self._active: dict[UUID, asyncio.subprocess.Process] = {}
        self._cancelled: set[UUID] = set()

    async def run(
        self,
        key: UUID,
        command: tuple[str, ...],
        *,
        cwd: Path,
        log_path: Path,
        on_started: Callable[[int], None],
    ) -> ProcessOutcome:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        with log_path.open("wb") as log_file:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd),
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=creationflags,
            )
            self._active[key] = process
            try:
                on_started(process.pid)
                exit_code = await process.wait()
            except BaseException:
                # The process is already ours even when persistence or the
                # callback fails. Never leave it alive without an observer.
                try:
                    await self._cleanup_process(process)
                except OSError as cleanup_error:
                    if process.returncode is None:
                        raise ProcessCleanupError(key, process) from cleanup_error
                self._cancelled.discard(key)
                raise
            finally:
                if process.returncode is not None:
                    self._active.pop(key, None)
        cancelled = key in self._cancelled
        self._cancelled.discard(key)
        return ProcessOutcome(
            pid=process.pid, exit_code=exit_code, cancelled=cancelled
        )

    async def _cleanup_process(self, process):
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=self.cancel_grace_seconds)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

    async def retry_cleanup(self, key: UUID) -> bool:
        process = self._active.get(key)
        if process is None:
            return True
        try:
            await self._cleanup_process(process)
        except OSError:
            if process.returncode is None:
                return False
        if process.returncode is None:
            return False
        self._active.pop(key, None)
        self._cancelled.discard(key)
        return True

    async def cancel(self, key: UUID) -> bool:
        process = self._active.get(key)
        if process is None or process.returncode is not None:
            return False
        self._cancelled.add(key)
        process.terminate()
        try:
            await asyncio.wait_for(
                process.wait(), timeout=self.cancel_grace_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
        return True
