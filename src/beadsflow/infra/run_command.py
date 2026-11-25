from __future__ import annotations

import math
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


class _CleanupLog:
    """Fixed-category cleanup issues plus the first interruption caught during cleanup."""

    __slots__ = ("interrupt", "issues")

    def __init__(self) -> None:
        self.issues: list[str] = []
        self.interrupt: BaseException | None = None

    def record(self, issue: str, exc: BaseException) -> None:
        self.issues.append(issue)
        # KeyboardInterrupt/SystemExit must survive cleanup: remaining stages
        # still run, then the interruption is re-raised.
        if self.interrupt is None and not isinstance(exc, Exception):
            self.interrupt = exc

    def notice(self) -> str:
        if not self.issues:
            return ""
        # Fixed stage categories only: never copy external exception text or argv.
        return "\nCommand cleanup incomplete: " + "; ".join(self.issues) + "."

    def raise_interrupt(self) -> None:
        interrupt = self.interrupt
        if interrupt is None:
            return
        self.interrupt = None
        interrupt.add_note(self.notice().strip())
        raise interrupt


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
        for arg in argv:
            if "\0" in arg:
                raise ConfigError("Command arguments must not contain NUL")
            try:
                os.fsencode(arg)
            except UnicodeEncodeError:
                raise ConfigError("Command arguments must be encodable for subprocess execution") from None
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
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    deadline = time.monotonic() + timeout_seconds
    process = subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        start_new_session=True,
        env={**os.environ, **env},
    )

    output = (bytearray(), bytearray())
    code: int
    notice = ""
    limit: Literal["timeout", "output"] | None = None
    try:
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(process.args, timeout_seconds)
        _drain(process, output, deadline, max_output_bytes)
    except _OutputLimitExceeded:
        cleanup = _stop_group(process, output, max_output_bytes)
        cleanup.raise_interrupt()
        code = 125
        limit = "output"
        notice = f"\nCommand output exceeded {max_output_bytes} bytes; capture truncated." + cleanup.notice()
    except subprocess.TimeoutExpired:
        cleanup = _stop_group(process, output, max_output_bytes)
        cleanup.raise_interrupt()
        code = 124
        limit = "timeout"
        separator = "\n" if output[1] and not output[1].endswith(b"\n") else ""
        notice = f"{separator}Timed out after {timeout_seconds} seconds." + cleanup.notice()
    except BaseException as exc:
        cleanup = _stop_group(process, output, max_output_bytes)
        if isinstance(exc, Exception):
            # An interruption during cleanup outranks an ordinary primary
            # failure, which stays attached as its implicit context.
            cleanup.raise_interrupt()
        cleanup_notice = cleanup.notice()
        if cleanup_notice:
            exc.add_note(cleanup_notice.strip())
        raise
    else:
        code = _finish_group(process, output, max_output_bytes)
    return CommandResult(
        argv=argv,
        returncode=code,
        # Only successful stdout is admitted as machine-readable output.
        # Failure output is diagnostic and may end mid-character.
        stdout=_decode_output(bytes(output[0]), preserve_output=preserve_output, strict=strict_utf8 and code == 0),
        stderr=_decode_output(bytes(output[1]), preserve_output=preserve_output) + notice,
        limit=limit,
    )


def _decode_output(value: bytes, *, preserve_output: bool, strict: bool = False) -> str:
    if strict:
        return value.decode("utf-8")
    if preserve_output:
        return value.decode("utf-8", errors="surrogateescape")
    # Profile display output intentionally tolerates malformed bytes.
    return value.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def _drain(
    process: subprocess.Popen[bytes],
    output: tuple[bytearray, bytearray],
    deadline: float,
    limit: int,
    *,
    fail_on_overflow: bool = True,
) -> None:
    with selectors.DefaultSelector() as selector:
        for pipe, buffer in zip((process.stdout, process.stderr), output, strict=True):
            if pipe is not None and not pipe.closed:
                selector.register(pipe, selectors.EVENT_READ, (buffer, pipe))
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, 0)
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
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(process.args, 0)
    _wait_for_exit(process, remaining)
    # A ready exit event can be returned after the original deadline.
    if time.monotonic() >= deadline:
        raise subprocess.TimeoutExpired(process.args, remaining)


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


def _finish_group(process: subprocess.Popen[bytes], output: tuple[bytearray, bytearray], limit: int) -> int:
    cleanup = _stop_group(process, output, limit, completed=True)
    cleanup.raise_interrupt()
    cleanup_notice = cleanup.notice()
    if cleanup_notice:
        raise CommandError(cleanup_notice.strip())
    assert process.returncode is not None
    return process.returncode


def _cleanup_drain(
    process: subprocess.Popen[bytes],
    output: tuple[bytearray, bytearray],
    limit: int,
    deadline: float,
    log: _CleanupLog,
) -> None:
    try:
        _drain(process, output, min(time.monotonic() + 0.5, deadline), limit, fail_on_overflow=False)
    except subprocess.TimeoutExpired:
        # Escaped descendants may retain pipe handles; do not await their EOF.
        pass
    except BaseException as exc:
        log.record("output drain failed", exc)


def _stop_group(
    process: subprocess.Popen[bytes], output: tuple[bytearray, bytearray], limit: int, *, completed: bool = False
) -> _CleanupLog:
    deadline = time.monotonic() + (REAP_SECONDS if completed else CLEANUP_SECONDS)
    log = _CleanupLog()
    # Keep the leader unreaped through the last signal to pin the group identity.
    # Each stage is independent: even an interruption in cleanup must not skip
    # escalation, reaping or either owned stream's close attempt.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            _signal_group(process, sig)
        except BaseException as exc:
            log.record(f"{sig.name} signal failed", exc)
        if not completed:
            _cleanup_drain(process, output, limit, deadline, log)
    try:
        process.wait(timeout=max(0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        log.issues.append("direct child could not be reaped within cleanup allowance")
    except BaseException as exc:
        log.record("direct-child reap failed", exc)
    _close_pipes(process, log)
    return log


def _close_pipes(process: subprocess.Popen[bytes], log: _CleanupLog) -> None:
    for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
        if pipe is not None:
            try:
                pipe.close()
            except BaseException as exc:
                log.record(f"{name} close failed", exc)
