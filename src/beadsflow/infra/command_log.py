from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path

from beadsflow.application.errors import CommandError

_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def write_command_log(*, beads_dir: Path, issue_id: str, phase: str, content: str) -> Path:
    if any(_COMPONENT.fullmatch(value) is None for value in (issue_id, phase)):
        raise CommandError("Cannot write diagnostic log: unsafe issue or phase filename")
    try:
        return _write_private(beads_dir=beads_dir, prefix=f"{issue_id}.{phase}.", content=content)
    except OSError as exc:
        raise CommandError("Cannot write diagnostic log: unsafe directory or I/O failure") from exc


def _write_private(*, beads_dir: Path, prefix: str, content: str) -> Path:
    # Resolve only the trusted prefix. Never follow Beads/log directory symlinks,
    # including a swap between checking the path and opening the file.
    beads_dir = beads_dir.absolute()
    directory = os.open(beads_dir.parent.resolve(strict=True), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in (beads_dir.name, "logs", "beadsflow"):
            try:
                os.mkdir(component, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        if os.fstat(directory).st_uid != os.getuid():
            raise OSError("Diagnostic directory is not owned by the current user")
        os.fchmod(directory, 0o700)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        name = f"{prefix}{timestamp}.{secrets.token_hex(16)}.log"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            handle = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise
        with handle:
            handle.write(content)
        return beads_dir / "logs" / "beadsflow" / name
    finally:
        os.close(directory)
