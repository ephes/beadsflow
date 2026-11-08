from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from beadsflow.application.errors import CommandError

MAX_FINGERPRINT_BYTES = 64 * 1024 * 1024
FINGERPRINT_TIMEOUT_SECONDS = 30


@dataclass
class FingerprintBudget:
    deadline: float
    remaining_bytes: int

    @classmethod
    def start(cls) -> FingerprintBudget:
        return cls(monotonic() + FINGERPRINT_TIMEOUT_SECONDS, MAX_FINGERPRINT_BYTES)

    def check(self) -> None:
        if monotonic() >= self.deadline:
            raise CommandError("Untracked fingerprint deadline exceeded")

    def reserve(self, size: int) -> None:
        self.check()
        if size > self.remaining_bytes:
            raise CommandError("Untracked fingerprint byte budget exceeded")
        self.remaining_bytes -= size


def untracked_signature(root: Path, relative: str, *, budget: FingerprintBudget | None = None) -> bytes:
    """Hash Git-reported paths without traversing symlinks or reading special files."""
    budget = budget if budget is not None else FingerprintBudget.start()
    budget.check()
    parts = relative.removesuffix("/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise CommandError("Unsafe untracked Git path")
    try:
        directory = os.open(root.resolve(), os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise CommandError("Cannot open repository for untracked fingerprinting") from exc
    try:
        for part in parts[:-1]:
            budget.check()
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        return _entry_signature(directory, parts[-1], budget)
    except OSError as exc:
        raise CommandError(f"Cannot fingerprint untracked path: {relative}") from exc
    finally:
        os.close(directory)


def _entry_signature(directory: int, name: str, budget: FingerprintBudget) -> bytes:
    metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
    digest = hashlib.sha256()
    if stat.S_ISLNK(metadata.st_mode):
        target = os.fsencode(os.readlink(name, dir_fd=directory))
        budget.reserve(len(target))
        digest.update(target)
    elif stat.S_ISREG(metadata.st_mode):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb", buffering=0) as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise CommandError(f"Untracked path is not a regular file or symlink: {name}")
            budget.reserve(metadata.st_size)
            remaining = metadata.st_size
            while remaining:
                budget.check()
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    raise CommandError("Untracked file changed during fingerprinting")
                digest.update(chunk)
                remaining -= len(chunk)
            after = os.fstat(handle.fileno())
            if (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_mode) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
                after.st_mode,
            ):
                raise CommandError("Untracked file changed during fingerprinting")
    budget.check()
    return str(metadata.st_mode).encode("ascii") + b":" + digest.digest()
