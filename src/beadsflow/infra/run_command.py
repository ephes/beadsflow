from __future__ import annotations

import os
import selectors
import shlex
import signal
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from beadsflow.application.errors import ConfigError

DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024


class _OutputLimitExceeded(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CommandSpec:
    argv: list[str]

    @staticmethod
    def from_string(command: str) -> CommandSpec:
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise ConfigError("Invalid command quoting") from exc
        if not argv:
            raise ConfigError("Command is empty")
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
    # Set only when run_command itself stopped the command; a child's own 124/125 exit leaves it None.
    limit: Literal["timeout", "output"] | None = None


def run_command(
    *,
    argv: Sequence[str],
    timeout_seconds: float,
    env: dict[str, str],
    cwd: Path | None = None,
    preserve_output: bool = False,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> CommandResult:
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")
    process = subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        start_new_session=True,
        env={**os.environ, **env},
    )

    def decode(value: bytes) -> str:
        if preserve_output:
            return value.decode("utf-8", errors="surrogateescape")
        # Preserve the previous text-mode behavior for profile and Beads commands.
        return value.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

    output = (bytearray(), bytearray())
    code: int
    notice = ""
    limit: Literal["timeout", "output"] | None = None
    try:
        _drain(process, output, timeout_seconds, max_output_bytes)
        code = process.returncode
    except _OutputLimitExceeded:
        _stop_group(process, output, max_output_bytes)
        code = 125
        limit = "output"
        notice = f"\nCommand output exceeded {max_output_bytes} bytes; capture truncated."
    except subprocess.TimeoutExpired:
        _stop_group(process, output, max_output_bytes)
        code = 124
        limit = "timeout"
        if not output[1]:
            notice = f"Timed out after {timeout_seconds} seconds."
    except BaseException:
        _stop_group(process, output, max_output_bytes)
        raise
    return CommandResult(
        argv=argv,
        returncode=code,
        stdout=decode(bytes(output[0])),
        stderr=decode(bytes(output[1])) + notice,
        limit=limit,
    )


def _drain(
    process: subprocess.Popen[bytes],
    output: tuple[bytearray, bytearray],
    timeout_seconds: float,
    limit: int,
    *,
    fail_on_overflow: bool = True,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    with selectors.DefaultSelector() as selector:
        for pipe, buffer in zip((process.stdout, process.stderr), output, strict=True):
            if pipe is not None and not pipe.closed:
                selector.register(pipe, selectors.EVENT_READ, (buffer, pipe))
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout_seconds)
            for key, _events in selector.select(remaining):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.data[1].close()
                    continue
                available = max(0, limit - len(output[0]) - len(output[1]))
                key.data[0].extend(chunk[:available])
                if len(chunk) > available and fail_on_overflow:
                    raise _OutputLimitExceeded
    process.wait(timeout=max(0, deadline - time.monotonic()))


def _signal_group(process: subprocess.Popen[bytes] | subprocess.Popen[str], sig: signal.Signals) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS can reject signalling a group containing only a zombie leader.
        if process.poll() is None:
            raise


def _stop_group(process: subprocess.Popen[bytes], output: tuple[bytearray, bytearray], limit: int) -> None:
    # Cleanup keeps draining under the same cap, never communicating unbounded output.
    _signal_group(process, signal.SIGTERM)
    try:
        try:
            _drain(process, output, 0.5, limit, fail_on_overflow=False)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            try:
                _drain(process, output, 0.5, limit, fail_on_overflow=False)
            except subprocess.TimeoutExpired:
                # Escaped descendants may retain pipe handles; do not await their EOF.
                pass
    finally:
        try:
            _signal_group(process, signal.SIGKILL)
        finally:
            process.wait()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
