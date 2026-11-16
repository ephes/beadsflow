from __future__ import annotations

import os
import select
import selectors
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from beadsflow.application.errors import CommandError, ConfigError

DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
CLEANUP_SECONDS = 1.5
REAP_SECONDS = 0.5


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
    strict_utf8: bool = False,
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

    def decode(value: bytes, *, strict: bool = False) -> str:
        if strict:
            return value.decode("utf-8")
        if preserve_output:
            return value.decode("utf-8", errors="surrogateescape")
        # Profile display output intentionally tolerates malformed bytes.
        return value.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

    output = (bytearray(), bytearray())
    code: int
    notice = ""
    limit: Literal["timeout", "output"] | None = None
    try:
        _drain(process, output, timeout_seconds, max_output_bytes)
    except _OutputLimitExceeded:
        cleanup_notice = _stop_group(process, output, max_output_bytes)
        code = 125
        limit = "output"
        notice = f"\nCommand output exceeded {max_output_bytes} bytes; capture truncated." + cleanup_notice
    except subprocess.TimeoutExpired:
        cleanup_notice = _stop_group(process, output, max_output_bytes)
        code = 124
        limit = "timeout"
        if not output[1]:
            notice = f"Timed out after {timeout_seconds} seconds."
        notice += cleanup_notice
    except BaseException:
        _stop_group(process, output, max_output_bytes)
        raise
    else:
        code = _finish_group(process)
    return CommandResult(
        argv=argv,
        returncode=code,
        # Strict admission applies to the stdout of a completed command only; a
        # stopped capture may end mid-character and stderr is diagnostic text.
        stdout=decode(bytes(output[0]), strict=strict_utf8 and limit is None),
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
    _wait_for_exit(process, max(0, deadline - time.monotonic()))


def _wait_for_exit(process: subprocess.Popen[bytes] | subprocess.Popen[str], timeout_seconds: float) -> None:
    """Observe exit without reaping: cleanup must retain the process-group identity."""
    if sys.platform == "darwin":
        # macOS has kqueue but Python does not expose waitid there. Registering an
        # unreaped, already-exited child also delivers NOTE_EXIT immediately.
        with closing(select.kqueue()) as queue:
            event = select.kevent(
                process.pid,
                filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                fflags=select.KQ_NOTE_EXIT,
            )
            if queue.control([event], 1, timeout_seconds):
                return
    else:
        deadline = time.monotonic() + timeout_seconds
        while True:
            if os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.01, remaining))
    raise subprocess.TimeoutExpired(process.args, timeout_seconds)


def _signal_group(process: subprocess.Popen[bytes] | subprocess.Popen[str], sig: signal.Signals) -> None:
    # Never signal a group after reaping its leader: that ID may have been reused.
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS can reject signalling a group containing only a zombie leader.
        # Observe without poll(), which would reap and release the group identity.
        try:
            _wait_for_exit(process, 0)
        except subprocess.TimeoutExpired:
            raise PermissionError("Cannot signal owned process group") from None


def _finish_group(process: subprocess.Popen[bytes]) -> int:
    # Keep the exited leader unreaped until the last group signal. Its PID
    # pins the group identity, even if every descendant has already exited.
    _signal_group(process, signal.SIGTERM)
    _signal_group(process, signal.SIGKILL)
    try:
        cleanup_notice = _reap(process, time.monotonic() + REAP_SECONDS)
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    if cleanup_notice:
        raise CommandError(cleanup_notice.strip())
    assert process.returncode is not None
    return process.returncode


def _reap(process: subprocess.Popen[bytes], deadline: float) -> str:
    try:
        process.wait(timeout=max(0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        return "\nCommand cleanup incomplete: direct child could not be reaped within cleanup allowance."
    return ""


def _stop_group(process: subprocess.Popen[bytes], output: tuple[bytearray, bytearray], limit: int) -> str:
    deadline = time.monotonic() + CLEANUP_SECONDS
    # Cleanup keeps draining under the same cap, never communicating unbounded output.
    _signal_group(process, signal.SIGTERM)
    try:
        try:
            _drain(process, output, min(0.5, max(0, deadline - time.monotonic())), limit, fail_on_overflow=False)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            try:
                _drain(process, output, min(0.5, max(0, deadline - time.monotonic())), limit, fail_on_overflow=False)
            except subprocess.TimeoutExpired:
                # Escaped descendants may retain pipe handles; do not await their EOF.
                pass
    finally:
        try:
            _signal_group(process, signal.SIGKILL)
        finally:
            cleanup_notice = _reap(process, deadline)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
    return cleanup_notice
