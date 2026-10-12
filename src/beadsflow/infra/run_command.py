from __future__ import annotations

import os
import shlex
import signal
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
    process = subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
        env={**os.environ, **env},
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        stdout, stderr = _stop_group(process)
        if not stderr:
            stderr = f"Timed out after {timeout_seconds} seconds."
        return CommandResult(argv=argv, returncode=124, stdout=stdout, stderr=stderr)
    except BaseException:
        _stop_group(process)
        raise
    return CommandResult(argv=argv, returncode=process.returncode, stdout=stdout, stderr=stderr)


def _signal_group(process: subprocess.Popen[str], sig: signal.Signals) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS can reject signalling a group containing only a zombie leader.
        if process.poll() is None:
            raise


def _stop_group(process: subprocess.Popen[str]) -> tuple[str, str]:
    # Give wrappers a short opportunity to reap their children, then kill any
    # remaining group members even when the wrapper has already exited.
    _signal_group(process, signal.SIGTERM)
    try:
        try:
            return process.communicate(timeout=0.5)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            try:
                return process.communicate(timeout=0.5)
            except subprocess.TimeoutExpired as exc:
                # Escaped descendants may retain pipe handles. Preserve captured
                # output without waiting indefinitely for their EOF.
                return _timeout_output(exc)
    finally:
        try:
            _signal_group(process, signal.SIGKILL)
        finally:
            process.wait()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def _timeout_output(exc: subprocess.TimeoutExpired) -> tuple[str, str]:
    def decode(value: str | bytes | None) -> str:
        return value.decode(errors="replace") if isinstance(value, bytes) else value or ""

    return decode(exc.stdout), decode(exc.stderr)
