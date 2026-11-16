from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
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
    with _untracked_parent(root, relative, budget) as (directory, name):
        return _entry_signature(directory, name, budget)


def untracked_source(root: Path, relative: str, *, budget: FingerprintBudget) -> str:
    """Admit complete UTF-8 regular-file evidence under the fingerprint path policy."""
    with _untracked_parent(root, relative, budget) as (directory, name):
        metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode):
            raise CommandError("Untracked review source must be a regular file")
        data = b"".join(_regular_chunks(directory, name, metadata, budget))
        _check_named_generation(directory, name, metadata)
        budget.check()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise CommandError("Untracked review source is not valid UTF-8") from None
    if "\0" in text:
        raise CommandError("Untracked review source contains NUL")
    return text


@contextmanager
def _untracked_parent(root: Path, relative: str, budget: FingerprintBudget) -> Iterator[tuple[int, str]]:
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
        yield directory, parts[-1]
    except OSError as exc:
        raise CommandError(f"Cannot fingerprint untracked path: {relative}") from exc
    finally:
        os.close(directory)


def _generation(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _entry_signature(directory: int, name: str, budget: FingerprintBudget) -> bytes:
    metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
    digest = hashlib.sha256()
    if stat.S_ISLNK(metadata.st_mode):
        target = os.fsencode(os.readlink(name, dir_fd=directory))
        budget.reserve(len(target))
        digest.update(target)
    elif stat.S_ISREG(metadata.st_mode):
        for chunk in _regular_chunks(directory, name, metadata, budget):
            digest.update(chunk)
    else:
        raise CommandError(f"Untracked path is not a regular file or symlink: {name[:120]!r}")
    _check_named_generation(directory, name, metadata)
    budget.check()
    return str(metadata.st_mode).encode("ascii") + b":" + digest.digest()


def _regular_chunks(directory: int, name: str, metadata: os.stat_result, budget: FingerprintBudget) -> Iterator[bytes]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb", buffering=0) as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise CommandError(f"Untracked path is not a regular file or symlink: {name[:120]!r}")
        if _generation(metadata) != _generation(opened):
            raise CommandError("Untracked file changed during fingerprinting")
        budget.reserve(metadata.st_size)
        remaining = metadata.st_size
        while remaining:
            budget.check()
            chunk = handle.read(min(65536, remaining))
            if not chunk:
                raise CommandError("Untracked file changed during fingerprinting")
            yield chunk
            remaining -= len(chunk)
        after = os.fstat(handle.fileno())
        if _generation(metadata) != _generation(after):
            raise CommandError("Untracked file changed during fingerprinting")


def _check_named_generation(directory: int, name: str, metadata: os.stat_result) -> None:
    named_after = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if _generation(metadata) != _generation(named_after):
        raise CommandError("Untracked file changed during fingerprinting")


def review_pathspec(root: Path, beads_dir: Path) -> list[str]:
    scope = ["--", ".", ":(top,literal,exclude).beadsflow/locks/execution.lock"]
    try:
        relative = beads_dir.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        pass  # External databases are already outside the worktree.
    else:
        if relative != ".":
            scope.append(f":(top,literal,exclude){relative}")
    return scope
