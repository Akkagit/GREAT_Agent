"""Inspect and stop only a verified process instance, using one Windows HANDLE."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from dataclasses import dataclass

import psutil


def command_sha256(command: tuple[str, ...] | list[str]) -> str:
    return hashlib.sha256(json.dumps(list(command), ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    created_at: int
    executable: str
    command_sha256: str


@dataclass(frozen=True)
class ProcessObservation:
    state: str  # running / exited / blocked
    reason: str | None = None
    identity: ProcessIdentity | None = None
    stopped: bool = False


class _WindowsProcess:
    def __init__(self, pid: int, stop: bool):
        from ctypes import wintypes
        self.pid = pid
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.api.GetProcessTimes.restype = wintypes.BOOL
        self.api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.api.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.WaitForSingleObject.restype = wintypes.DWORD
        self.api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateProcess.restype = wintypes.BOOL
        self.handle = self.api.OpenProcess(0x1000 | 0x100000 | (1 if stop else 0), False, pid)
        if not self.handle:
            code = ctypes.get_last_error()
            if code in (87, 1168):
                raise psutil.NoSuchProcess(pid)
            raise psutil.AccessDenied(pid)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.api.CloseHandle(self.handle)

    def identity(self):
        from ctypes import wintypes
        times = [wintypes.FILETIME() for _ in range(4)]
        if not self.api.GetProcessTimes(self.handle, *(ctypes.byref(t) for t in times)):
            raise psutil.AccessDenied(self.pid)
        creation = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        path = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(path))
        if not self.api.QueryFullProcessImageNameW(self.handle, 0, path, ctypes.byref(size)):
            raise psutil.AccessDenied(self.pid)
        command = psutil.Process(self.pid).cmdline()
        return ProcessIdentity(self.pid, creation, path.value, command_sha256(command))

    def exited(self):
        result = self.api.WaitForSingleObject(self.handle, 0)
        if result == 0xFFFFFFFF:
            raise psutil.AccessDenied(self.pid)
        return result == 0

    def terminate_and_wait(self):
        if not self.api.TerminateProcess(self.handle, 1) and not self.exited():
            raise psutil.AccessDenied(self.pid)
        return self.api.WaitForSingleObject(self.handle, 5000) == 0


class _PortableProcess:
    """Development fallback; production on Windows always uses HANDLE identity."""
    def __init__(self, pid, stop):
        self.process = psutil.Process(pid)
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def identity(self):
        p = self.process
        return ProcessIdentity(p.pid, int(p.create_time() * 10_000_000), p.exe(), command_sha256(p.cmdline()))
    def exited(self):
        return not self.process.is_running() or self.process.status() == psutil.STATUS_ZOMBIE
    def terminate_and_wait(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
            return True
        except psutil.TimeoutExpired:
            return False


def open_process(pid, stop=False):
    return (_WindowsProcess if os.name == 'nt' else _PortableProcess)(pid, stop)


def capture_identity(pid: int) -> ProcessIdentity:
    with open_process(pid) as process:
        return process.identity()


class ProcessInspector:
    def __init__(self, *, open_process=open_process):
        self.open_process = open_process

    def inspect(self, attempt, *, stop=False):
        if attempt.pid is None:
            return self._discover(attempt, stop=stop)
        try:
            with self.open_process(attempt.pid, stop) as process:
                if process.exited():
                    return ProcessObservation('exited')
                identity = process.identity()
                if attempt.process_created_at is not None and identity.created_at != attempt.process_created_at:
                    return ProcessObservation('exited', 'pid_reused')
                if not all((attempt.process_created_at, attempt.process_executable, attempt.command_sha256)):
                    return ProcessObservation('blocked', 'identity_missing')
                approved = getattr(attempt, 'command', None)
                if not approved or command_sha256(approved) != attempt.command_sha256:
                    return ProcessObservation('blocked', 'approval_identity_missing')
                if (not os.path.isabs(approved[0]) or os.path.normcase(os.path.abspath(identity.executable))
                        != os.path.normcase(os.path.abspath(approved[0]))):
                    return ProcessObservation('blocked', 'approval_identity_mismatch')
                if (os.path.normcase(identity.executable) != os.path.normcase(attempt.process_executable)
                        or identity.command_sha256 != attempt.command_sha256):
                    return ProcessObservation('blocked', 'identity_mismatch')
                if process.exited():
                    return ProcessObservation('exited')
                if stop:
                    if process.terminate_and_wait():
                        return ProcessObservation('exited', identity=identity, stopped=True)
                    return ProcessObservation('blocked', 'stop_not_confirmed')
                return ProcessObservation('running', identity=identity)
        except (psutil.NoSuchProcess, ProcessLookupError):
            return ProcessObservation('exited')
        except (psutil.AccessDenied, PermissionError):
            return ProcessObservation('blocked', 'access_denied')
        except (OSError, psutil.Error):
            return ProcessObservation('blocked', 'inspection_failed')

    def _discover(self, attempt, *, stop):
        # Only the immutable approved command can close the spawn-before-PID gap.
        if (attempt.launch_phase != 'spawning' or not attempt.command
                or command_sha256(attempt.command) != attempt.command_sha256):
            return ProcessObservation('blocked', 'launch_unknown')
        candidates = []
        incomplete = False
        for candidate in psutil.process_iter():
            try:
                if command_sha256(candidate.cmdline()) == attempt.command_sha256:
                    identity = capture_identity(candidate.pid)
                    if os.path.normcase(identity.executable) == os.path.normcase(attempt.process_executable or ''):
                        candidates.append(identity)
            except psutil.NoSuchProcess:
                continue
            except (psutil.AccessDenied, OSError):
                # Windows has protected unrelated processes: cannot claim this
                # was an exhaustive scan when their command lines are hidden.
                incomplete = True
        if len(candidates) > 1:
            return ProcessObservation('blocked', 'candidate_conflict')
        if incomplete:
            return ProcessObservation('blocked', 'launch_unknown')
        if not candidates:
            return ProcessObservation('exited')
        from dataclasses import replace
        identity = candidates[0]
        found = replace(attempt, pid=identity.pid, process_created_at=identity.created_at,
                        process_executable=identity.executable, command_sha256=identity.command_sha256)
        return self.inspect(found, stop=stop)
