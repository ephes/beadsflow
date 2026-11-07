from __future__ import annotations

from dataclasses import dataclass

import pytest

from beadsflow.application.errors import ConfigError
from beadsflow.application.session import SessionStartRequest, SessionStatusRequest, handle_session
from beadsflow.entrypoints import cli as cli_mod


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
            return _Completed(returncode=0, stdout="foo\nbeadsflow-3cx\n")
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


def test_cli_session_start_forwards_unknown_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[SessionStartRequest] = []

    def fake_handle_session(request: object) -> int:
        assert isinstance(request, SessionStartRequest)
        captured.append(request)
        return 0

    monkeypatch.setattr(cli_mod, "handle_session", fake_handle_session)
    exit_code = cli_mod.main(
        [
            "session",
            "start",
            "sess",
            "--epic",
            "beadsflow-3cx",
            "--",
            "--interval",
            "30",
            "--verbose",
        ]
    )
    assert exit_code == 0
    assert captured
    assert captured[0].epic_id == "beadsflow-3cx"
    assert captured[0].run_args == ["--interval", "30", "--verbose"]


@pytest.mark.parametrize("operation", ["status", "stop"])
@pytest.mark.parametrize("failure", ["timeout", "launch"])
def test_control_failure_returns_nonzero_without_raw_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], operation: str, failure: str
) -> None:
    import subprocess

    import beadsflow.infra.zellij as zellij_mod

    monkeypatch.setattr(zellij_mod.shutil, "which", lambda _: "/synthetic/zellij")
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> _Completed:
        calls.append(argv)
        assert kwargs.get("timeout") == 10
        if failure == "timeout":
            raise subprocess.TimeoutExpired(
                argv, 10, output="synthetic-private-output", stderr="synthetic-private-error"
            )
        raise OSError("synthetic-private-error")

    monkeypatch.setattr(zellij_mod.subprocess, "run", run)
    assert cli_mod.main(["session", operation, "synthetic-session"]) == 1
    assert calls == [
        ["zellij", "list-sessions", "--no-formatting", "--short"]
        if operation == "status"
        else ["zellij", "kill-session", "synthetic-session"]
    ]
    result = capsys.readouterr()
    assert result.out == ""
    assert ("list" if operation == "status" else "stop") in result.err
    assert ("timed out after 10 seconds" if failure == "timeout" else "OSError") in result.err
    assert "synthetic-private" not in result.err


@pytest.mark.parametrize("operation", ["start", "attach"])
def test_interactive_sessions_have_no_control_timeout(monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    import beadsflow.infra.zellij as zellij_mod

    monkeypatch.setattr(zellij_mod.shutil, "which", lambda _: "/synthetic/zellij")
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> _Completed:
        calls.append(argv)
        assert "timeout" not in kwargs
        return _Completed(0)

    monkeypatch.setattr(zellij_mod.subprocess, "run", run)
    args = ["session", operation, "synthetic-session"]
    if operation == "start":
        args.extend(["--epic", "epic-1"])
    assert cli_mod.main(args) == 0
    assert len(calls) == 1


@pytest.mark.parametrize("operation", ["status", "stop"])
@pytest.mark.parametrize("returncode", [0, 1])
def test_control_success_and_command_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], operation: str, returncode: int
) -> None:
    import beadsflow.infra.zellij as zellij_mod

    monkeypatch.setattr(zellij_mod.shutil, "which", lambda _: "/synthetic/zellij")
    monkeypatch.setattr(
        zellij_mod.subprocess, "run", lambda *args, **kwargs: _Completed(returncode, "synthetic-session\n")
    )
    assert cli_mod.main(["session", operation, "synthetic-session"]) == returncode
    output = capsys.readouterr()
    if returncode:
        assert "Failed to" in output.err
    elif operation == "status":
        assert output.out == "running\n"


@pytest.mark.parametrize(
    "name,display",
    [
        ("sess", "\x1b[32;1msess\x1b[0m [Created 2m ago]"),
        ("current", "current [Created 1h ago] (current)"),
        ("exited", "exited [Created 1d ago] (EXITED - attach to resurrect)"),
        ("plain", "plain"),
        ("team:λ+計画", "team:λ+計画 [Created 1m ago]"),
        ("two words", "two words [Created 1m ago]"),
    ],
)
def test_status_uses_exact_unformatted_names(monkeypatch: pytest.MonkeyPatch, name: str, display: str) -> None:
    import beadsflow.infra.zellij as zellij_mod

    monkeypatch.setattr(zellij_mod.shutil, "which", lambda _: "/synthetic/zellij")

    def run(argv: list[str], **kwargs: object) -> _Completed:
        assert kwargs == {"check": False, "capture_output": True, "text": True, "timeout": 10}
        # A synthetic Zellij responding according to its public format flags.
        output = name if argv == ["zellij", "list-sessions", "--no-formatting", "--short"] else display
        return _Completed(0, output + "\n")

    monkeypatch.setattr(zellij_mod.subprocess, "run", run)
    assert handle_session(SessionStatusRequest(name=name)) == 0


@pytest.mark.parametrize("output", ["", "\n\n", "sess:other\n", "sess-long\n", "sess other\n", "sess…\n"])
def test_status_never_accepts_prefixes(monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    import beadsflow.infra.zellij as zellij_mod

    monkeypatch.setattr(zellij_mod.shutil, "which", lambda _: "/synthetic/zellij")
    monkeypatch.setattr(zellij_mod.subprocess, "run", lambda *args, **kwargs: _Completed(0, output))
    assert handle_session(SessionStatusRequest(name="sess")) == 1


@pytest.mark.parametrize(
    "returncode,stdout,stderr,empty",
    [
        (1, "", "No active zellij sessions found.\n", True),
        (1, "", "synthetic-private-error", False),
        (2, "", "No active zellij sessions found.\n", False),
        (1, "sess\n", "No active zellij sessions found.\n", False),
    ],
)
def test_cli_status_distinguishes_empty_list_from_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    returncode: int,
    stdout: str,
    stderr: str,
    empty: bool,
) -> None:
    import beadsflow.infra.zellij as zellij_mod

    monkeypatch.setattr(zellij_mod.shutil, "which", lambda _: "/synthetic/zellij")
    monkeypatch.setattr(zellij_mod.subprocess, "run", lambda *args, **kwargs: _Completed(returncode, stdout, stderr))
    assert cli_mod.main(["session", "status", "sess"]) == 1
    result = capsys.readouterr()
    assert result.out == ("not running\n" if empty else "")
    assert result.err == ("" if empty else "Failed to list zellij sessions\n")


@pytest.mark.parametrize("operation", ["start", "attach"])
@pytest.mark.parametrize("failure", [FileNotFoundError, PermissionError, None])
def test_interactive_failure_has_safe_cli_envelope(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operation: str,
    failure: type[OSError] | None,
) -> None:
    import beadsflow.infra.zellij as zellij_mod

    monkeypatch.setattr(zellij_mod.shutil, "which", lambda _: "/synthetic/zellij")
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> _Completed:
        calls.append(argv)
        assert kwargs == {"check": False}  # Inherit the terminal and interactive lifetime.
        if failure is not None:
            raise failure("synthetic-private-exception-payload")
        return _Completed(7)

    monkeypatch.setattr(zellij_mod.subprocess, "run", run)
    args = ["session", operation, "sess"]
    expected = ["zellij", "attach", "sess"]
    if operation == "start":
        args.extend(["--epic", "epic-1", "--", "--interval", "30"])
        expected = ["zellij", "-s", "sess", "-c", "--", "uv", "run", "beadsflow", "run", "epic-1", "--interval", "30"]
    assert cli_mod.main(args) == 1
    assert calls == [expected]
    result = capsys.readouterr()
    assert result.out == ""
    assert operation in result.err
    assert "synthetic-private" not in result.err
    if failure is not None:
        assert failure.__name__ in result.err
