from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from beadsflow.application.errors import BeadsError
from beadsflow.entrypoints.cli import main
from beadsflow.infra.beads_cli import BeadsCli
from beadsflow.infra.run_command import CommandResult


def _issue() -> dict[str, Any]:
    return {
        "id": "epic-1",
        "title": "Synthetic epic",
        "status": "open",
        "issue_type": "epic",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T01:00:00+01:00",
        "dependencies": [{"id": "dep-1", "status": "closed"}],
        "dependents": [
            {"id": "child-1", "title": "Synthetic child", "status": "open", "created_at": "2026-01-01T00:00:00Z"}
        ],
        "comments": [{"id": 1, "text": "LGTM", "author": "synthetic", "created_at": "2026-01-01T00:00:00Z"}],
    }


# Malformed consumed fields, without any real Beads state or profile execution.
_BAD_FIELDS: list[tuple[str, object]] = [
    ("id", None),
    ("id", {}),
    ("id", ""),
    ("title", []),
    ("status", "unexpected"),
    ("issue_type", "unexpected"),
    ("priority", True),
    ("priority", "1"),
    ("priority", 1.5),
    ("created_at", None),
    ("created_at", "not-a-date"),
    ("created_at", "2026-01-01T00:00:00"),
    ("updated_at", "2026-01-01T00:00:00"),
    ("description", {}),
    ("acceptance_criteria", None),
    ("dependencies", None),
    ("dependents", {}),
    ("comments", "bad"),
    ("dependencies", [None]),
    ("dependents", [1]),
    ("comments", [[]]),
    ("dependencies", [{}]),
    ("dependents", [{}]),
    ("comments", [{}]),
    ("dependencies", [{"id": {}, "status": "closed"}]),
    ("dependencies", [{"id": "dep-1", "status": "closed", "dependency_type": []}]),
    ("dependents", [{"id": "child-1", "title": "child", "status": "open", "created_at": "2026-01-01"}]),
    ("comments", [{"id": "1", "created_at": "2026-01-01T00:00:00Z"}]),
    ("comments", [{"id": True, "created_at": "2026-01-01T00:00:00Z"}]),
    ("comments", [{"id": 1, "text": {}, "created_at": "2026-01-01T00:00:00Z"}]),
]


def _malformed_payloads() -> list[object]:
    payloads: list[object] = [None, {}, [], [None], [{}], [_issue(), _issue()]]
    for field, value in _BAD_FIELDS:
        issue = _issue()
        issue[field] = value
        payloads.append([issue])
    for field in ("id", "title", "status", "issue_type", "created_at", "updated_at"):
        issue = _issue()
        del issue[field]
        payloads.append([issue])
    for field in ("dependencies", "dependents", "comments"):
        issue = _issue()
        issue[field] *= 2
        payloads.append([issue])
    return payloads


@pytest.mark.parametrize("payload", _malformed_payloads())
def test_malformed_show_is_bounded_domain_error(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    import beadsflow.infra.beads_cli as module

    def run(*, argv: list[str], **kwargs: object) -> CommandResult:
        assert argv == ["bd", "--no-daemon", "--json", "show", "epic-1"]
        return CommandResult(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr(module, "run_command", run)
    with pytest.raises(BeadsError) as caught:
        BeadsCli("synthetic").get_issue("epic-1")
    assert len(str(caught.value)) < 300


@pytest.mark.parametrize("where", ["epic", "child"])
@pytest.mark.parametrize("bad_field", ["comments", "dependencies", "created_at", "id", "mismatched_id"])
def test_run_cli_rejects_schema_before_mutation_or_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, where: str, bad_field: str
) -> None:
    import beadsflow.application.runner as runner
    import beadsflow.infra.beads_cli as module

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".beads").mkdir()
    monkeypatch.delenv("BEADSFLOW_CONFIG", raising=False)
    monkeypatch.delenv("BEADSFLOW_INTERVAL", raising=False)
    calls: list[list[str]] = []

    def run(*, argv: list[str], **kwargs: object) -> CommandResult:
        calls.append(argv)
        assert argv[2:4] == ["--json", "show"], "malformed data must never trigger a mutation"
        issue = deepcopy(_issue())
        issue["id"] = argv[-1]
        if argv[-1] == "child-1":
            issue["issue_type"] = "task"
            issue["dependents"] = []
        if argv[-1] == ("epic-1" if where == "epic" else "child-1"):
            if bad_field == "mismatched_id":
                issue["id"] = "private-sentinel-other-id"
            else:
                issue[bad_field] = None
        return CommandResult(argv, 0, json.dumps([issue]), "")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("malformed data must not reach a profile")

    monkeypatch.setattr(module, "run_command", run)
    monkeypatch.setattr(runner.EpicRunLoop, "_run_profile_command", forbidden)
    assert main(["run", "epic-1", "--once", "--beads-dir", str(tmp_path / ".beads")]) == 1
    assert len(calls) == (1 if where == "epic" else 2)
    assert "bd show" in caplog.text
    assert len(caplog.text) < 500
    assert "private-sentinel" not in caplog.text


def test_valid_defaults_and_offset_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    import beadsflow.infra.beads_cli as module

    monkeypatch.setattr(BeadsCli, "_run_json", lambda self, *args: [_issue()])
    parsed = module.BeadsCli("synthetic").get_issue("epic-1")
    assert parsed.priority == parsed.dependents[0].priority == 0
    assert parsed.created_at == parsed.updated_at
    assert parsed.dependencies[0].dependency_type == "blocks"


@pytest.mark.parametrize("requested", ["1", "other-epic", "epic-1"])
def test_show_binds_requested_identity(monkeypatch: pytest.MonkeyPatch, requested: str) -> None:
    import beadsflow.infra.beads_cli as module

    calls: list[list[str]] = []

    def run(*, argv: list[str], **kwargs: object) -> CommandResult:
        calls.append(argv)
        return CommandResult(argv, 0, json.dumps([_issue()]), "")

    monkeypatch.setattr(module, "run_command", run)
    if requested == "epic-1":
        assert BeadsCli("synthetic").get_issue(requested).id == requested
    else:
        with pytest.raises(BeadsError, match="identity mismatch") as caught:
            BeadsCli("synthetic").get_issue(requested)
        assert len(str(caught.value)) < 300
    assert calls == [["bd", "--no-daemon", "--json", "show", requested]]


@pytest.mark.parametrize("where", ["epic", "child"])
@pytest.mark.parametrize("location", ["id", "dependencies", "dependents"])
@pytest.mark.parametrize("identity", ["--help", "-x", ".hidden", "bad id", "bad\nID", "bad\x00ID", "bad\x1bID"])
def test_run_refuses_unsafe_consumed_identities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    where: str,
    location: str,
    identity: str,
) -> None:
    import beadsflow.application.runner as runner
    import beadsflow.infra.beads_cli as module

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".beads").mkdir()
    for name in ("BEADSFLOW_CONFIG", "BEADSFLOW_INTERVAL"):
        monkeypatch.delenv(name, raising=False)
    calls: list[list[str]] = []

    def run(*, argv: list[str], **kwargs: object) -> CommandResult:
        calls.append(argv)
        assert argv[2:4] == ["--json", "show"], "unsafe identity reached a mutation"
        assert argv[-1] == ("epic-1" if len(calls) == 1 else "child-1"), "unsafe identity reached bd"
        issue = _issue()
        issue["id"] = argv[-1]
        if argv[-1] == "child-1":
            issue["issue_type"] = "task"
            issue["dependents"] = []
        if argv[-1] == ("epic-1" if where == "epic" else "child-1"):
            if location == "id":
                issue["id"] = identity
            else:
                issue[location] = deepcopy(_issue()[location])
                issue[location][0]["id"] = identity
        return CommandResult(argv, 0, json.dumps([issue]), "")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("unsafe identity reached a profile")

    monkeypatch.setattr(module, "run_command", run)
    monkeypatch.setattr(runner.EpicRunLoop, "_run_profile_command", forbidden)
    assert main(["run", "epic-1", "--once", "--beads-dir", str(tmp_path / ".beads")]) == 1
    assert len(calls) == (1 if where == "epic" else 2)
    assert "Invalid bd show payload" in caplog.text
    assert identity not in caplog.text
    assert len(caplog.text) < 500


@pytest.mark.parametrize("identity", ["beadsflow-3cx.1", "Repo_2-A.7", "1"])
def test_canonical_identity_policy_preserves_valid_ids(monkeypatch: pytest.MonkeyPatch, identity: str) -> None:
    raw = _issue()
    raw["id"] = identity
    raw["dependencies"][0]["id"] = identity
    raw["dependents"][0]["id"] = identity
    monkeypatch.setattr(BeadsCli, "_run_json", lambda self, *args: [raw])
    parsed = BeadsCli("synthetic").get_issue(identity)
    assert parsed.id == parsed.dependencies[0].id == parsed.dependents[0].id == identity


def _decoder_payload(kind: str, issue_id: str) -> str:
    raw = _issue()
    raw["id"] = issue_id
    if issue_id == "child-1":
        raw["issue_type"] = "task"
        raw["dependents"] = []
    payload = json.dumps([raw])
    if kind == "depth":
        return payload[:-2] + ', "unused": ' + "[" * 2000 + "0" + "]" * 2000 + "}]"
    if kind == "integer":
        return payload[:-2] + ', "private-sentinel": ' + "9" * 5000 + "}]"
    if kind == "duplicate-id":
        return payload.replace(f'"id": "{issue_id}"', f'"id": "private-sentinel", "id": "{issue_id}"', 1)
    if kind == "duplicate-status":
        return payload.replace('"status": "open"', '"status": "closed", "status": "open"', 1)
    return payload.replace('"text": "LGTM"', '"text": "private-sentinel", "text": "LGTM"', 1)


@pytest.mark.parametrize("kind", ["depth", "integer", "duplicate-id", "duplicate-status", "duplicate-comment"])
@pytest.mark.parametrize("where", ["epic-1", "child-1"])
def test_decoder_failures_stop_cli_before_profile_or_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    kind: str,
    where: str,
) -> None:
    import beadsflow.application.runner as runner
    import beadsflow.infra.beads_cli as module

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".beads").mkdir()
    for name in ("BEADSFLOW_CONFIG", "BEADSFLOW_INTERVAL"):
        monkeypatch.delenv(name, raising=False)
    calls: list[list[str]] = []

    def run(*, argv: list[str], **kwargs: object) -> CommandResult:
        calls.append(argv)
        assert argv[2:4] == ["--json", "show"], "decoder failure reached a mutation"
        payload = _decoder_payload(kind, where) if argv[-1] == where else json.dumps([_issue()])
        return CommandResult(argv, 0, payload, "")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("decoder failure reached a profile")

    monkeypatch.setattr(module, "run_command", run)
    monkeypatch.setattr(runner.EpicRunLoop, "_run_profile_command", forbidden)
    assert main(["run", "epic-1", "--once"]) == 1
    assert len(calls) == (1 if where == "epic-1" else 2)
    assert "Invalid JSON from bd" in caplog.text
    assert "private-sentinel" not in caplog.text
    assert len(caplog.text) < 500


def test_valid_json_runs_actual_decoder(monkeypatch: pytest.MonkeyPatch) -> None:
    import beadsflow.infra.beads_cli as module

    monkeypatch.setattr(
        module,
        "run_command",
        lambda **kwargs: CommandResult([], 0, json.dumps([_issue()]), ""),
    )
    assert BeadsCli("synthetic").get_issue("epic-1").id == "epic-1"
