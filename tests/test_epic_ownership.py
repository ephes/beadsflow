import logging
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from beadsflow.application.errors import BeadsError
from beadsflow.application.runner import EpicRunLoop
from beadsflow.infra.beads_cli import BeadsCli
from beadsflow.infra.paths import RepoPaths
from beadsflow.settings import Settings


def _raw(issue_id: str, *, status: str = "open", edge: str = "parent-child") -> dict[str, Any]:
    return {
        "id": issue_id,
        "title": "Synthetic",
        "status": status,
        "priority": 1,
        "issue_type": "epic" if issue_id == "epic" else "task",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "dependency_type": edge,
    }


@pytest.mark.parametrize("child_status", ["open", "closed"])
@pytest.mark.parametrize("other_status", ["open", "closed"])
@pytest.mark.parametrize("blocker_status", ["open", "closed"])
def test_only_parent_child_edges_own_epic_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    child_status: str,
    other_status: str,
    blocker_status: str,
) -> None:
    epic = _raw("epic")
    epic["dependents"] = [
        _raw("blocking-dependent", status=other_status, edge="blocks"),
        _raw("discovered-dependent", status=other_status, edge="discovered-from"),
        _raw("child", status=child_status),
    ]
    child = _raw("child", status=child_status)
    child["dependencies"] = [
        {"id": "epic", "status": "open", "dependency_type": "parent-child"},
        {"id": "outside", "status": blocker_status, "dependency_type": "blocks"},
    ]
    payloads = {"epic": [epic], "child": [child]}
    # An unexpected request (including any non-child) fails the fake bd command.
    executable = tmp_path / "bd"
    reads = tmp_path / "reads"
    executable.write_text(
        f"#!{sys.executable}\nimport json, pathlib, sys\n"
        f"payloads = {payloads!r}\n"
        f"with pathlib.Path({str(reads)!r}).open('a') as out: out.write(sys.argv[-1] + '\\n')\n"
        "print(json.dumps(payloads[sys.argv[-1]]))\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    dispatched: list[str] = []
    monkeypatch.setattr(EpicRunLoop, "_run_implementer", lambda self, *, issue_id: dispatched.append(issue_id))
    loop = EpicRunLoop(
        BeadsCli(str(tmp_path / ".beads")),
        "epic",
        Settings.defaults(),
        RepoPaths(tmp_path, tmp_path / ".beads"),
        "synthetic",
        None,
        logging.getLogger("test.ownership"),
    )
    complete = child_status == "closed"
    ready = not complete and blocker_status == "closed"
    with caplog.at_level(logging.INFO):
        assert loop.run(once=True, dry_run=False, max_iterations=1) == (0 if complete or ready else 2)
    assert dispatched == (["child"] if ready else [])
    assert ("Epic complete." in caplog.text) == complete
    assert reads.read_text().splitlines() == (["epic", "child", "epic"] if complete else ["epic", "child"])


@pytest.mark.parametrize("edge", [None, "", [], 1])
def test_ambiguous_dependent_edge_refuses_context(monkeypatch: pytest.MonkeyPatch, edge: object) -> None:
    epic = _raw("epic")
    dependent = _raw("child")
    dependent["dependency_type"] = edge
    epic["dependents"] = [dependent]
    monkeypatch.setattr(BeadsCli, "_run_json", lambda self, *args: [epic])
    with pytest.raises(BeadsError, match="Invalid bd show payload"):
        BeadsCli("synthetic").get_issue("epic")


def test_missing_dependent_edge_refuses_context(monkeypatch: pytest.MonkeyPatch) -> None:
    epic, dependent = _raw("epic"), _raw("child")
    del dependent["dependency_type"]
    epic["dependents"] = [dependent]
    monkeypatch.setattr(BeadsCli, "_run_json", lambda self, *args: [epic])
    with pytest.raises(BeadsError, match="Invalid bd show payload"):
        BeadsCli("synthetic").get_issue("epic")


def test_adapter_retains_reverse_relationship_types(monkeypatch: pytest.MonkeyPatch) -> None:
    epic = _raw("epic")
    epic["dependents"] = [
        _raw(f"dependent-{i}", edge=edge)
        for i, edge in enumerate(["parent-child", "blocks", "discovered-from", "related"])
    ]
    monkeypatch.setattr(BeadsCli, "_run_json", lambda self, *args: [epic])
    issue = BeadsCli("synthetic").get_issue("epic")
    assert [item.dependency_type for item in issue.dependents] == [
        "parent-child",
        "blocks",
        "discovered-from",
        "related",
    ]
