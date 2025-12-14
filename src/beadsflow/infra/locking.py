from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from beadsflow.application.errors import LockError

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


@dataclass(slots=True)
class EpicLock:
    lock_path: Path
    _file: IO[str] = field(init=False)

    def __enter__(self) -> EpicLock:
        if fcntl is None:  # pragma: no cover
            raise LockError("File locking is not supported on this platform")

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.lock_path, "a+", encoding="utf-8")  # noqa: SIM115
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LockError(f"Failed to acquire lock: {self.lock_path}") from exc

        self._file.seek(0)
        self._file.truncate(0)
        self._file.write(f"pid={os.getpid()}\n")
        self._file.flush()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        if fcntl is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
