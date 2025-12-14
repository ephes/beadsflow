from __future__ import annotations

from dataclasses import dataclass

import pytest

from beadsflow.application.errors import ConfigError
from beadsflow.application.session import SessionStartRequest, SessionStatusRequest, handle_session


@dataclass(frozen=True, slots=True)
class _Completed:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def test_session_start_missing_zellij_prints_manual_command(monkeypatch: pytest.MonkeyPatch) -> None:
    import beadsflow.infra.zellij as zellij_mod

    def which(_: str) -> str | None:
        return None

    monkeypatch.setattr(zellij_mod.shutil, "which", which)
    request = SessionStartRequest(name="s", epic_id="beadsflow-3cx", run_args=["--dry-run"])
    with pytest.raises(ConfigError) as excinfo:
        handle_session(request)
    assert "Run without zellij" in str(excinfo.value)
    assert "uv run beadsflow run beadsflow-3cx --dry-run" in str(excinfo.value)


def test_session_status_detects_session(monkeypatch: pytest.MonkeyPatch) -> None:
    import beadsflow.infra.zellij as zellij_mod

    def which(_: str) -> str | None:
        return "/usr/bin/zellij"

    def run(argv: list[str], **kwargs: object) -> _Completed:
        if argv[:2] == ["zellij", "list-sessions"]:
            return _Completed(returncode=0, stdout="foo\nbeadsflow-3cx [created]\n")
        return _Completed(returncode=0)

    monkeypatch.setattr(zellij_mod.shutil, "which", which)
    monkeypatch.setattr(zellij_mod.subprocess, "run", run)

    assert handle_session(SessionStatusRequest(name="beadsflow-3cx")) == 0
    assert handle_session(SessionStatusRequest(name="nope")) == 1


def test_session_start_invokes_zellij(monkeypatch: pytest.MonkeyPatch) -> None:
    import beadsflow.infra.zellij as zellij_mod

    calls: list[list[str]] = []

    def which(_: str) -> str | None:
        return "/usr/bin/zellij"

    def run(argv: list[str], **kwargs: object) -> _Completed:
        calls.append(argv)
        return _Completed(returncode=0)

    monkeypatch.setattr(zellij_mod.shutil, "which", which)
    monkeypatch.setattr(zellij_mod.subprocess, "run", run)

    request = SessionStartRequest(
        name="sess",
        epic_id="beadsflow-3cx",
        run_args=["--interval", "30", "--verbose"],
    )
    assert handle_session(request) == 0
    assert calls
    assert calls[0][:5] == ["zellij", "-s", "sess", "-c", "--"]
    assert calls[0][5:10] == ["uv", "run", "beadsflow", "run", "beadsflow-3cx"]
