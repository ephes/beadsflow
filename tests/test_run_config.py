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
