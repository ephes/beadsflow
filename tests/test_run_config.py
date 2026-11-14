import logging
import os
from pathlib import Path

import pytest

from beadsflow.entrypoints.cli import main
from beadsflow.infra.locking import EpicLock


@pytest.fixture
def isolated_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    for name in (
        "BEADSFLOW_CONFIG",
        "BEADSFLOW_INTERVAL",
        "BEADSFLOW_BEADS_DIR",
        "BEADSFLOW_BEADS_NO_DB",
        "BEADSFLOW_IMPLEMENTER",
        "BEADSFLOW_REVIEWER",
        "BEADSFLOW_LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".beads").mkdir()

    def unexpected_lock(self: EpicLock) -> None:
        pytest.fail("invalid config must fail before locking or launching bd/profiles")

    monkeypatch.setattr(EpicLock, "__enter__", unexpected_lock)
    return tmp_path


@pytest.mark.parametrize(
    "args,env",
    [
        (["--interval", "-1"], {}),
        (["--interval", "86401"], {}),
        (["--max-iterations", "0"], {}),
        (["--max-iterations", "1000001"], {}),
        ([], {"BEADSFLOW_INTERVAL": "oops"}),
        ([], {"BEADSFLOW_INTERVAL": "-1"}),
        ([], {"BEADSFLOW_INTERVAL": "86401"}),
        ([], {"BEADSFLOW_BEADS_NO_DB": "typo"}),
    ],
)
def test_invalid_overrides_fail_before_work(
    isolated_run: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    args: list[str],
    env: dict[str, str],
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with caplog.at_level(logging.ERROR, logger="beadsflow"):
        assert main(["run", "epic-1", "--once", *args]) == 1
    assert caplog.messages
    assert not (isolated_run / ".beads" / "locks").exists()


@pytest.mark.parametrize("source", ["cli", "env"])
@pytest.mark.parametrize("failure", ["missing", "unreadable", "utf8", "toml", "quote", "empty"])
def test_config_errors_return_cli_failure(
    isolated_run: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    source: str,
    failure: str,
) -> None:
    path = isolated_run / "config.toml"
    if failure == "utf8":
        path.write_bytes(b"\xff")
    elif failure == "toml":
        path.write_text("[run", encoding="utf-8")
    elif failure in {"quote", "empty"}:
        command = "'unterminated" if failure == "quote" else ""
        path.write_text(f'[implementers.fake]\ncommand = "{command}"', encoding="utf-8")
    elif failure == "unreadable":
        path.write_text("", encoding="utf-8")
        original_open = os.open

        def unreadable(
            name: str | os.PathLike[str],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if name == path:
                raise PermissionError("synthetic denial")
            return original_open(name, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", unreadable)
    args = ["--config", str(path)] if source == "cli" else []
    if source == "env":
        monkeypatch.setenv("BEADSFLOW_CONFIG", str(path))
    with caplog.at_level(logging.ERROR, logger="beadsflow"):
        assert main(["run", "epic-1", "--once", *args]) == 1
    assert caplog.messages
    assert "Traceback" not in caplog.text
    assert not (isolated_run / ".beads" / "locks").exists()


def test_absent_discovered_config_uses_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from beadsflow.application.runner import EpicRunLoop

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".beads").mkdir()
    for name in ("BEADSFLOW_CONFIG", "BEADSFLOW_INTERVAL", "BEADSFLOW_BEADS_NO_DB"):
        monkeypatch.delenv(name, raising=False)

    def run(self: EpicRunLoop, **kwargs: object) -> int:
        assert self.settings.interval_seconds == 30
        return 0

    monkeypatch.setattr(EpicRunLoop, "run", run)
    assert main(["run", "epic-1", "--beads-dir", str(tmp_path / ".beads"), "--once"]) == 0


@pytest.mark.parametrize("source", ["cli", "env", "default"])
@pytest.mark.parametrize("failure", ["oversize", "depth", "depth-table", "integer", "directory"])
def test_config_resource_failures_stop_before_work(
    isolated_run: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    source: str,
    failure: str,
) -> None:
    path = isolated_run / ("beadsflow.toml" if source == "default" else "config.toml")
    if failure == "directory":
        path.mkdir()
    else:
        content = {
            "oversize": "# private-sentinel" + "x" * 65536,
            "depth": "unused = " + "[" * 2000 + "0" + "]" * 2000,
            "depth-table": "[beads_dir." + "nested." * 1500 + "leaf]",
            "integer": "unused = " + "9" * 5000,
        }[failure]
        path.write_text(content, encoding="utf-8")
    args = ["--config", str(path)] if source == "cli" else []
    if source == "env":
        monkeypatch.setenv("BEADSFLOW_CONFIG", str(path))
    with caplog.at_level(logging.ERROR, logger="beadsflow"):
        assert main(["run", "epic-1", "--once", *args]) == 1
    assert "Cannot read config" in caplog.text
    assert "private-sentinel" not in caplog.text
    assert "Traceback" not in caplog.text
    assert not (isolated_run / ".beads" / "locks").exists()


def test_fifo_config_fails_promptly_without_backend_or_profile(tmp_path: Path) -> None:
    import subprocess
    import sys

    path = tmp_path / "config.toml"
    os.mkfifo(path)
    (tmp_path / ".beads").mkdir()
    script = """
from unittest.mock import patch
from beadsflow.entrypoints.cli import main
import sys
with (
    patch("beadsflow.infra.beads_cli.run_command", side_effect=AssertionError("backend launched")),
    patch("beadsflow.application.runner.EpicRunLoop._run_profile_command",
          side_effect=AssertionError("profile launched")),
):
    assert main(["run", "epic-1", "--once", "--config", sys.argv[1]]) == 1
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert result.returncode == 0, result.stderr
    assert "Cannot read config" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / ".beads" / "locks").exists()
