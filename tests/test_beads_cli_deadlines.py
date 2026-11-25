import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from beadsflow.application.errors import BeadsError
from beadsflow.application.run_epic import RunEpicRequest, run_epic
from beadsflow.infra.beads_cli import BeadsCli
from beadsflow.infra.locking import EpicLock, epic_lock_path
from beadsflow.infra.run_command import CommandResult


@pytest.mark.parametrize("operation", ["show", "sync", "comment", "close"])
def test_timeout_reaps_command_without_retry_and_releases_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    import beadsflow.infra.run_command as command_module

    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    calls = tmp_path / "calls"
    script = """
import sys, time
from pathlib import Path
with Path(sys.argv[1]).open('a') as stream:
    stream.write(sys.argv[2] + '\\n')
if sys.argv[3] == 'sync' and sys.argv[2] == '--json':
    print('Database out of sync with JSONL', file=sys.stderr)
    sys.exit(1)
print("Database out of sync with JSONL", file=sys.stderr, flush=True)
time.sleep(30)
"""
    processes: list[subprocess.Popen[str]] = []
    original_popen = subprocess.Popen

    def popen(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(command_module.subprocess, "Popen", popen)
    monkeypatch.setattr(
        BeadsCli,
        "_argv",
        lambda self, *args: [sys.executable, "-c", script, str(calls), args[0], operation],
    )
    cli = BeadsCli(beads_dir=str(beads_dir), command_timeout_seconds=0.5)
    lock_path = epic_lock_path(beads_dir, "epic-1")
    started = time.monotonic()
    with pytest.raises(BeadsError, match="timed out") as caught:
        with EpicLock(lock_path):
            if operation == "comment":
                cli.comment("child-1", "Synthetic comment")
            elif operation == "close":
                cli.close("child-1")
            else:
                cli.get_issue("epic-1")
    expected_operation = {
        "show": "bd show epic-1",
        "sync": "bd sync --import-only (recovering bd show epic-1)",
        "comment": "bd comments add child-1",
        "close": "bd close child-1",
    }[operation]
    assert expected_operation in str(caught.value)
    assert "Synthetic comment" not in str(caught.value)
    assert time.monotonic() - started < 5
    assert processes and all(process.poll() is not None for process in processes)
    expected = (
        ["--json", "sync"]
        if operation == "sync"
        else [{"show": "--json", "comment": "comments", "close": "close"}[operation]]
    )
    assert calls.read_text().splitlines() == expected
    with EpicLock(lock_path):
        pass


def test_run_epic_releases_lock_on_beads_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import beadsflow.application.run_epic as run_module

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".beads").mkdir()
    cli = BeadsCli(beads_dir=str(tmp_path / ".beads"), command_timeout_seconds=0.2)
    monkeypatch.setattr(run_module, "BeadsCli", lambda **kwargs: cli)
    monkeypatch.setattr(BeadsCli, "_argv", lambda self, *args: [sys.executable, "-c", "import time; time.sleep(30)"])
    monkeypatch.delenv("BEADSFLOW_CONFIG", raising=False)
    monkeypatch.delenv("BEADSFLOW_INTERVAL", raising=False)
    request = RunEpicRequest("epic-1", str(tmp_path / ".beads"), None, True, None, False)
    assert run_epic(request) == 1
    assert "bd show epic-1 timed out" in caplog.text
    with EpicLock(epic_lock_path(tmp_path / ".beads", "epic-1")):
        pass


def test_recovery_shares_original_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    import beadsflow.infra.beads_cli as module

    clock = [0.0]
    timeouts: list[float] = []
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    def run(*, argv: list[str], timeout_seconds: float, env: dict[str, str], **kwargs: Any) -> CommandResult:
        timeouts.append(timeout_seconds)
        clock[0] += 0.25
        if len(timeouts) == 1:
            return CommandResult(argv, 1, "", "Database out of sync with JSONL")
        return CommandResult(argv, 0, "[]", "")

    monkeypatch.setattr(module, "run_command", run)
    assert BeadsCli(".beads", command_timeout_seconds=1)._run_json("show", "child-1") == []
    assert timeouts == [1, 0.75, 0.5]


def test_expired_recovery_budget_does_not_launch_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    import beadsflow.infra.beads_cli as module

    clock = [0.0]
    calls: list[list[str]] = []
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    def run(*, argv: list[str], **kwargs: Any) -> CommandResult:
        calls.append(argv)
        clock[0] += 0.5
        return CommandResult(argv, 1 if len(calls) == 1 else 0, "", "Database out of sync with JSONL")

    monkeypatch.setattr(module, "run_command", run)
    with pytest.raises(BeadsError, match="timed out"):
        BeadsCli(".beads", command_timeout_seconds=1).get_issue("child-1")
    assert len(calls) == 2


def test_missing_executable_is_beads_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import beadsflow.infra.beads_cli as module

    def missing(**kwargs: Any) -> CommandResult:
        raise FileNotFoundError("synthetic missing executable")

    monkeypatch.setattr(module, "run_command", missing)
    with pytest.raises(BeadsError, match="FileNotFoundError"):
        BeadsCli(".beads").get_issue("child-1")


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_beads_deadline_must_be_finite_positive(timeout: float) -> None:
    with pytest.raises(BeadsError, match="finite and positive"):
        BeadsCli(".beads", command_timeout_seconds=timeout)


def test_transport_passes_finite_deadline_to_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import math
    import selectors

    original = selectors.DefaultSelector.select
    observed: list[float] = []

    def select(self: Any, timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
        assert timeout is not None and math.isfinite(timeout) and timeout > 0
        observed.append(timeout)
        return original(self, timeout)

    monkeypatch.setattr(selectors.DefaultSelector, "select", select)
    monkeypatch.setattr(BeadsCli, "_argv", lambda self, *args: [sys.executable, "-c", "print('[]')"])
    assert BeadsCli(str(tmp_path))._run_json("show", "child-1") == []
    assert observed and observed[0] <= 30


@pytest.mark.parametrize(("operation", "read"), [("show", True), ("comment", False), ("close", False)])
def test_cleanup_failure_after_completion_is_beads_error(
    monkeypatch: pytest.MonkeyPatch, operation: str, read: bool
) -> None:
    import beadsflow.infra.beads_cli as module
    from beadsflow.application.errors import CommandError

    def cleanup_failed(**kwargs: Any) -> CommandResult:
        raise CommandError("Command cleanup incomplete: direct-child reap failed.")

    monkeypatch.setattr(module, "run_command", cleanup_failed)
    cli = BeadsCli(".beads")
    with pytest.raises(BeadsError, match="cleanup incomplete") as caught:
        if operation == "show":
            cli.get_issue("child-1")
        elif operation == "comment":
            cli.comment("child-1", "synthetic body")
        else:
            cli.close("child-1")
    message = str(caught.value)
    assert "child-1" in message
    assert ("outcome uncertain" in message) is not read
    assert "synthetic body" not in message
    assert caught.value.__context__ is None


def test_beads_keeps_failed_child_status_when_group_cleanup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import beadsflow.infra.beads_cli as module
    import beadsflow.infra.run_command as command_module

    script = (
        "import sys; print('synthetic stdout'); print('Database out of sync with JSONL', file=sys.stderr); sys.exit(7)"
    )
    launches: list[tuple[str, ...]] = []
    results: list[CommandResult] = []

    def argv(self: BeadsCli, *args: str) -> list[str]:
        launches.append(args)
        return [sys.executable, "-c", script]

    def signal_failed(*args: object) -> None:
        raise OSError("synthetic cleanup exception")

    def run(**kwargs: Any) -> CommandResult:
        result = command_module.run_command(**kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(BeadsCli, "_argv", argv)
    monkeypatch.setattr(command_module, "_signal_group", signal_failed)
    monkeypatch.setattr(module, "run_command", run)

    with pytest.raises(BeadsError) as caught:
        BeadsCli(".beads").get_issue("child-1")

    assert str(caught.value) == "bd show child-1 failed (exit 7): command cleanup incomplete"
    assert caught.value.__context__ is None
    assert launches == [("--json", "show", "child-1")]
    (result,) = results
    assert result.returncode == 7
    assert result.limit is None
    assert result.stdout == "synthetic stdout\n"
    assert "Database out of sync with JSONL" in result.stderr
    assert "Command cleanup incomplete: SIGTERM signal failed; SIGKILL signal failed." in result.stderr
    assert "synthetic cleanup exception" not in result.stderr
