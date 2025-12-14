from __future__ import annotations

import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "beadsflow", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_help() -> None:
    completed = _run("--help")
    assert completed.returncode == 0
    assert "usage:" in completed.stdout.lower()


def test_run_help() -> None:
    completed = _run("run", "--help")
    assert completed.returncode == 0


def test_session_help() -> None:
    completed = _run("session", "--help")
    assert completed.returncode == 0
