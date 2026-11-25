from __future__ import annotations

import os
import re
import secrets
import time
from contextlib import ExitStack, suppress
from pathlib import Path

from beadsflow.application.errors import CommandError

_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def write_command_log(*, beads_dir: Path, issue_id: str, phase: str, content: str) -> Path:
    if any(_COMPONENT.fullmatch(value) is None for value in (issue_id, phase)):
        raise CommandError("Cannot write diagnostic log: unsafe issue or phase filename")
    try:
        return _write_private(beads_dir=beads_dir, prefix=f"{issue_id}.{phase}.", content=content)
    except UnicodeEncodeError:
        raise CommandError("Cannot write diagnostic log: content cannot be encoded as UTF-8") from None
    except OSError as exc:
        raise CommandError("Cannot write diagnostic log: unsafe directory or I/O failure") from exc


def _write_private(*, beads_dir: Path, prefix: str, content: str) -> Path:
    # Resolve only the trusted prefix. Never follow Beads/log directory symlinks,
    # including a swap between checking the path and opening the file.
    beads_dir = beads_dir.absolute()
    with ExitStack() as descriptors:
        directory = os.open(beads_dir.parent.resolve(strict=True), os.O_RDONLY | os.O_DIRECTORY)
        descriptors.callback(os.close, directory)
        chain: list[tuple[int, str, int]] = []
        for component in (beads_dir.name, "logs", "beadsflow"):
            try:
                os.mkdir(component, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            descriptors.callback(os.close, child)
            chain.append((directory, component, child))
            directory = child
        if os.fstat(directory).st_uid != os.getuid():
            raise OSError("Diagnostic directory is not owned by the current user")
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        token = secrets.token_hex(16)
        name = f"{prefix}{timestamp}.{token}.log"
        _check_basename(directory, name)

        os.fchmod(directory, 0o700)
        staged = f".{token}.tmp"
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        # Keep the inode pinned through publication and staged-name cleanup.
        descriptors.callback(os.close, fd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as handle:
                handle.write(content)
            os.fsync(fd)

            if not _matches_inode(directory, staged, fd):
                raise OSError("Staged diagnostic changed during log creation")
            # Linking is exclusive at the destination.
            os.link(staged, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        finally:
            _remove_staged(directory, staged, fd)

        # Persist the log name and its directory chain, including entries that
        # may have been created by an earlier interrupted or concurrent writer.
        try:
            os.fsync(directory)
            for parent, _, _ in reversed(chain):
                os.fsync(parent)
        except OSError:
            raise CommandError("Cannot confirm durability of published diagnostic log") from None

        # Check name identity at completion. This does not lock the path.
        for parent, component, child in chain:
            if not _matches_inode(parent, component, child):
                raise OSError("Diagnostic directory changed during log creation")
        if not _matches_inode(directory, name, fd):
            raise OSError("Diagnostic file changed during log creation")
        return beads_dir / "logs" / "beadsflow" / name


def _check_basename(directory: int, name: str) -> None:
    name_max = os.fpathconf(directory, "PC_NAME_MAX")
    if name_max < 0:
        raise CommandError("Cannot write diagnostic log: filename length limit unavailable")
    if len(os.fsencode(name)) > name_max:
        raise CommandError("Cannot write diagnostic log: filename exceeds directory name limit")


def _matches_inode(directory: int, name: str, fd: int) -> bool:
    return os.path.samestat(os.stat(name, dir_fd=directory, follow_symlinks=False), os.fstat(fd))


def _remove_staged(directory: int, staged: str, fd: int) -> None:
    # A cleanup error may leave a private .tmp file and must preserve the primary error.
    with suppress(OSError):
        if _matches_inode(directory, staged, fd):
            os.unlink(staged, dir_fd=directory)
