import logging
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
        original_read = Path.read_text

        def unreadable(self: Path, *args: object, **kwargs: object) -> str:
            if self == path:
                raise PermissionError("synthetic denial")
            return original_read(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", unreadable)
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
