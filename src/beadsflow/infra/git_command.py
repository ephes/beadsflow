from __future__ import annotations

from pathlib import Path

from beadsflow.application.errors import CommandError
from beadsflow.infra.run_command import run_command

GIT_TIMEOUT_SECONDS = 30


def capture_git(
    args: list[str], *, cwd: Path | None = None, allow_exit: set[int] | None = None, preserve_output: bool = True
) -> str:
    # Disable repository-configured diff helpers for every diff consumer.
    if args[0] == "diff":
        args = ["diff", "--no-ext-diff", "--no-textconv", *args[1:]]
    operation = "git " + " ".join(args)
    try:
        result = run_command(
            argv=["git", *args],
            timeout_seconds=GIT_TIMEOUT_SECONDS,
            env={},
            cwd=cwd,
            preserve_output=preserve_output,
        )
    except OSError as exc:
        raise CommandError(f"Cannot execute {operation} ({type(exc).__name__})") from exc
    if result.returncode == 124:
        raise CommandError(f"{operation} timed out after {GIT_TIMEOUT_SECONDS} seconds")
    if result.returncode not in (allow_exit if allow_exit is not None else {0}):
        detail = result.stderr.strip()[:1000] or "unknown error"
        raise CommandError(f"{operation} failed (exit {result.returncode}): {detail}")
    return result.stdout
