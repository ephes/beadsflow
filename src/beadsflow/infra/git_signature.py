from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from beadsflow.application.errors import CommandError


def untracked_signature(root: Path, relative: str) -> bytes:
    """Hash Git-reported paths without traversing symlinks or reading special files."""
    parts = relative.removesuffix("/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise CommandError("Unsafe untracked Git path")
    try:
        directory = os.open(root.resolve(), os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise CommandError("Cannot open repository for untracked fingerprinting") from exc
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        return _entry_signature(directory, parts[-1])
    except OSError as exc:
        raise CommandError(f"Cannot fingerprint untracked path: {relative}") from exc
    finally:
        os.close(directory)


def _entry_signature(directory: int, name: str) -> bytes:
    metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
    digest = hashlib.sha256()
    if stat.S_ISLNK(metadata.st_mode):
        digest.update(os.fsencode(os.readlink(name, dir_fd=directory)))
    elif stat.S_ISREG(metadata.st_mode):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise CommandError(f"Untracked path is not a regular file or symlink: {name}")
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    return str(metadata.st_mode).encode("ascii") + b":" + digest.digest()
