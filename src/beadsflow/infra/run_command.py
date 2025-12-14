from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from beadsflow.application.errors import CommandError


@dataclass(frozen=True, slots=True)
class CommandSpec:
    argv: list[str]

    @staticmethod
    def from_string(command: str) -> CommandSpec:
        argv = shlex.split(command)
        if not argv:
            raise CommandError("Command is empty")
        return CommandSpec(argv=argv)

    def render(self, *, epic_id: str, issue_id: str) -> list[str]:
        rendered: list[str] = []
        for arg in self.argv:
            rendered.append(arg.replace("{epic_id}", epic_id).replace("{issue_id}", issue_id))
        return rendered


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: Sequence[str]
    returncode: int
    stdout: str
    stderr: str


def run_command(
    *,
    argv: Sequence[str],
    timeout_seconds: int,
    env: dict[str, str],
) -> CommandResult:
    try:
        completed = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={**os.environ, **env},
        )
        return CommandResult(
            argv=argv,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    except subprocess.TimeoutExpired as exc:
        stdout: str
        stderr: str
        if isinstance(exc.stdout, bytes):
            stdout = exc.stdout.decode(errors="replace")
        else:
            stdout = exc.stdout or ""
        if isinstance(exc.stderr, bytes):
            stderr = exc.stderr.decode(errors="replace")
        else:
            stderr = exc.stderr or ""
        if not stderr:
            stderr = f"Timed out after {timeout_seconds} seconds."
        return CommandResult(argv=argv, returncode=124, stdout=stdout, stderr=stderr)
