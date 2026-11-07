from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from beadsflow.infra.beads_cli import BeadsCli


@dataclass(frozen=True, slots=True)
class _Completed:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def test_beads_cli_auto_imports_on_out_of_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    import beadsflow.infra.beads_cli as beads_cli_mod

    calls: list[list[str]] = []
    show_calls = 0

    payload = [
        {
            "id": "beadsflow-1",
            "title": "t",
            "status": "open",
            "priority": 1,
            "issue_type": "task",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
            "dependencies": [
                {"id": "external-1", "status": "blocked", "dependency_type": "blocks"},
                {"id": "epic-1", "status": "open", "dependency_type": "parent-child"},
                {"id": "legacy-1", "status": "closed"},
                {"id": "empty-1", "status": "open", "dependency_type": ""},
                {"id": "null-1", "status": "open", "dependency_type": None},
            ],
            "dependents": [],
            "comments": [],
        }
    ]

    def run(*, argv: list[str], **kwargs: object) -> _Completed:
        nonlocal show_calls
        calls.append(argv)
        if argv[:4] == ["bd", "--no-daemon", "--json", "show"]:
            show_calls += 1
            if show_calls == 1:
                return _Completed(returncode=1, stderr="Error: Database out of sync with JSONL.")
            return _Completed(returncode=0, stdout=json.dumps(payload))
        if argv[:3] == ["bd", "--no-daemon", "sync"] and "--import-only" in argv:
            return _Completed(returncode=0)
        raise AssertionError(f"Unexpected argv: {argv}")

    monkeypatch.setattr(beads_cli_mod, "run_command", run)

    cli = BeadsCli(beads_dir=".beads")
    issue = cli.get_issue("beadsflow-1")
    assert issue.id == "beadsflow-1"
    assert [dep.dependency_type for dep in issue.dependencies] == [
        "blocks",
        "parent-child",
        "blocks",
        "blocks",
        "blocks",
    ]

    assert show_calls == 2
    assert any(argv[:3] == ["bd", "--no-daemon", "sync"] for argv in calls)


def test_beads_cli_comment_retries_on_out_of_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    import beadsflow.infra.beads_cli as beads_cli_mod

    calls: list[list[str]] = []
    comment_calls = 0

    def run(*, argv: list[str], **kwargs: object) -> _Completed:
        nonlocal comment_calls
        calls.append(argv)
        if argv[:4] == ["bd", "--no-daemon", "comments", "add"]:
            comment_calls += 1
            if comment_calls == 1:
                return _Completed(returncode=1, stderr="Error: Database out of sync with JSONL.")
            return _Completed(returncode=0)
        if argv[:3] == ["bd", "--no-daemon", "sync"] and "--import-only" in argv:
            return _Completed(returncode=0)
        raise AssertionError(f"Unexpected argv: {argv}")

    monkeypatch.setattr(beads_cli_mod, "run_command", run)

    cli = BeadsCli(beads_dir=".beads")
    cli.comment("beadsflow-2", "hello")

    assert comment_calls == 2
    assert any(argv[:3] == ["bd", "--no-daemon", "sync"] for argv in calls)


@pytest.mark.parametrize("operation", ["show", "comment", "close"])
@pytest.mark.parametrize("failure", ["command", "sync", "retry"])
def test_beads_cli_recovery_failures_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    failure: str,
) -> None:
    import beadsflow.infra.beads_cli as module
    from beadsflow.application.errors import BeadsError

    calls: list[list[str]] = []

    def run(*, argv: list[str], **kwargs: object) -> _Completed:
        calls.append(argv)
        if len(calls) == 1 and failure != "command":
            return _Completed(1, stderr="Database out of sync with JSONL")
        if len(calls) == 2 and failure == "retry":
            return _Completed(0)
        return _Completed(1, stderr="synthetic failure " + "x" * 2000)

    monkeypatch.setattr(module, "run_command", run)
    cli = BeadsCli(".beads")
    with pytest.raises(BeadsError) as caught:
        if operation == "show":
            cli.get_issue("child-1")
        elif operation == "comment":
            cli.comment("child-1", "synthetic")
        else:
            cli.close("child-1")
    assert len(calls) == {"command": 1, "sync": 2, "retry": 3}[failure]
    expected_operation = {
        "show": "bd show child-1",
        "comment": "bd comments add child-1",
        "close": "bd close child-1",
    }[operation]
    assert expected_operation in str(caught.value)
    assert "synthetic failure" in str(caught.value)
    assert len(str(caught.value)) < 1100


@pytest.mark.parametrize("failure_at", ["epic", "child"])
def test_dry_run_never_imports_out_of_sync_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure_at: str
) -> None:
    import beadsflow.infra.beads_cli as module
    from beadsflow.entrypoints.cli import main
    from beadsflow.infra.run_command import CommandResult

    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith("BEADSFLOW_"):
            monkeypatch.delenv(key)
    beads = tmp_path / ".beads"
    beads.mkdir()
    content = beads / "issues.jsonl"
    content.write_text("synthetic database bytes")
    calls: list[list[str]] = []

    def run(*, argv: list[str], **kwargs: object) -> CommandResult:
        calls.append(argv)
        if "sync" in argv:
            content.write_text("recovery mutated content")
            return CommandResult(argv, 1, "", "synthetic recovery failure")
        assert argv[2:4] == ["--json", "show"]
        if argv[-1] == failure_at:
            return CommandResult(argv, 1, "", "Database out of sync with JSONL")
        payload = [
            {
                "id": "epic",
                "title": "Synthetic",
                "status": "open",
                "issue_type": "epic",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "dependents": [
                    {"id": "child", "title": "Child", "status": "open", "created_at": "2026-01-01T00:00:00Z"}
                ],
            }
        ]
        return CommandResult(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr(module, "run_command", run)
    assert main(["run", "epic", "--once", "--dry-run"]) == 1
    assert all(argv[2:4] == ["--json", "show"] for argv in calls)
    assert content.read_text() == "synthetic database bytes"
    assert "out of sync" in caplog.text and "import-only" in caplog.text
