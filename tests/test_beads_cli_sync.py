from __future__ import annotations

import json
from dataclasses import dataclass

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
            "dependencies": [],
            "dependents": [],
            "comments": [],
        }
    ]

    def run(argv: list[str], **kwargs: object) -> _Completed:
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

    monkeypatch.setattr(beads_cli_mod.subprocess, "run", run)

    cli = BeadsCli(beads_dir=".beads")
    issue = cli.get_issue("beadsflow-1")
    assert issue.id == "beadsflow-1"

    assert show_calls == 2
    assert any(argv[:3] == ["bd", "--no-daemon", "sync"] for argv in calls)
