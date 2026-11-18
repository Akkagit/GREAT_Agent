"""OS-owned, non-inheritable single solver worker boundary."""
from __future__ import annotations

import os
from pathlib import Path


class WorkerAlreadyRunning(RuntimeError):
    pass


class WorkerLock:
    def __init__(self, database_path: Path):
        self.path = Path(str(database_path.resolve()) + '.worker.lock')
        self._file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open('a+b')
        os.set_inheritable(stream.fileno(), False)
        try:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0)
                if not stream.read(1):
                    stream.write(b'\0')
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise WorkerAlreadyRunning(f'已有 Solver Worker 使用数据库 {self.path}') from error
        self._file = stream
        return self

    def __exit__(self, *args):
        stream, self._file = self._file, None
        if stream is not None:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            stream.close()
