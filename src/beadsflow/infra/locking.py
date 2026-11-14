from __future__ import annotations

import os
import re
import stat
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from beadsflow.application.errors import LockError

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

# Epic IDs become one lock filename component; anything that could add or
# change path components (separators, "..", leading dots, NUL) is refused.
_EPIC_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def epic_lock_path(beads_dir: Path, epic_id: str) -> Path:
    """Build the lock path for an epic, refusing IDs that are not a plain filename component."""
    if _EPIC_ID_PATTERN.fullmatch(epic_id) is None:
        raise LockError(
            f"Refusing epic ID {epic_id!r} for locking: only letters, digits, '.', '_' and '-' "
            "are allowed, starting with a letter or digit"
        )
    return beads_dir / "locks" / f"beadsflow-{epic_id}.lock"


@dataclass(slots=True)
class EpicLock:
    lock_path: Path
    _file: IO[str] = field(init=False)

    def __enter__(self) -> EpicLock:
        if fcntl is None:  # pragma: no cover
            raise LockError("File locking is not supported on this platform")

        try:
            with ExitStack() as failed_acquisition:
                with ExitStack() as directories:
                    fd, chain = self._open_safe(directories)
                    try:
                        self._file = os.fdopen(fd, "a+", encoding="utf-8")
                    except BaseException:
                        os.close(fd)
                        raise
                    # Closing also releases any acquired flock, including when
                    # closing a directory descriptor fails after the PID write.
                    failed_acquisition.callback(self._file.close)
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._check_inode(fd)
                    for parent, name, child in chain:
                        if not os.path.samestat(os.stat(name, dir_fd=parent, follow_symlinks=False), os.fstat(child)):
                            raise OSError("Lock path changed during acquisition")
                    self._file.seek(0)
                    self._file.truncate(0)
                    self._file.write(f"pid={os.getpid()}\n")
                    self._file.flush()
                failed_acquisition.pop_all()
        except BlockingIOError as exc:
            raise LockError(f"Lock is already held: {self.lock_path}") from exc
        except OSError as exc:
            raise LockError(f"Failed to acquire lock (unsafe path or I/O failure): {self.lock_path}: {exc}") from exc

        return self

    @staticmethod
    def _check_inode(fd: int) -> None:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_uid != os.geteuid():
            raise OSError("Lock must be a single-link regular file owned by the current user")

    def _open_safe(self, directories: ExitStack) -> tuple[int, list[tuple[int, str, int]]]:
        # Traverse using directory descriptors so checks cannot race with symlink swaps.
        path = self.lock_path.absolute()
        # Epic locks live under <trusted repo>/.beads/locks/<name>.
        # Resolve the trusted prefix, but never the two lock-directory levels.
        prefix = path.parent.parent.parent.resolve(strict=True)
        directory = os.open(prefix, os.O_RDONLY | os.O_DIRECTORY)
        directories.callback(os.close, directory)
        chain: list[tuple[int, str, int]] = []
        for component in path.parts[-3:-1]:
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            except FileNotFoundError:
                try:
                    os.mkdir(component, dir_fd=directory)
                except FileExistsError:
                    pass
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            directories.callback(os.close, child)
            chain.append((directory, component, child))
            directory = child
        fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        try:
            self._check_inode(fd)
        except BaseException:
            os.close(fd)
            raise
        chain.append((directory, path.name, fd))
        return fd, chain

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        try:
            if fcntl is not None:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
