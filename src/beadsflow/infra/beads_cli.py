from __future__ import annotations

import json
import math
import os
import re
import shlex
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from beadsflow.application.errors import BeadsError
from beadsflow.domain.models import Comment, Dependency, Issue, IssueStatus, IssueSummary, IssueType
from beadsflow.infra.run_command import CommandResult, run_command

# Beads issue payloads are shallow; refuse deeper nesting explicitly because
# CPython 3.12+ decodes far deeper JSON without raising RecursionError.
MAX_JSON_DEPTH = 64


def _validate_json_depth(raw: object) -> None:
    pending: list[tuple[object, int]] = [(raw, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise ValueError("JSON exceeds nesting limit")
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected text")
    return value


def _relationship(value: object) -> str:
    value = _text(value)
    if not value.strip():
        raise ValueError("expected explicit relationship")
    return value


def _identity(value: object) -> str:
    value = _text(value)
    # Match the epic-lock policy before backend IDs reach argv or profiles.
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is None:
        raise ValueError("expected canonical identity")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("expected nonnegative integer")
    return value


def _parse_datetime(value: object) -> datetime:
    # Require timezone-aware values so every timestamp can be compared safely.
    parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("expected timezone-aware timestamp")
    return parsed


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return value


def _records(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    values = raw.get(key, [])
    if not isinstance(values, list):
        raise ValueError("expected list")
    records = [_object(value) for value in values]
    # Duplicate identities make both selection and comment ordering ambiguous.
    ids = [record["id"] for record in records]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate identity")
    return records


@dataclass(frozen=True, slots=True)
class BeadsCli:
    beads_dir: str
    no_db: bool = False
    command_timeout_seconds: float = 30
    auto_sync: bool = True

    def __post_init__(self) -> None:
        if not math.isfinite(self.command_timeout_seconds) or self.command_timeout_seconds <= 0:
            raise BeadsError("bd command_timeout_seconds must be finite and positive")

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["BEADS_NO_DAEMON"] = "1"
        env["BEADS_DIR"] = self.beads_dir
        return env

    def _argv(self, *args: str) -> list[str]:
        argv = ["bd", "--no-daemon"]
        if self.no_db:
            argv.append("--no-db")
        return [*argv, *args]

    @staticmethod
    def _is_db_out_of_sync(stderr: str) -> bool:
        return "Database out of sync with JSONL" in stderr

    def _execute(self, argv: list[str], *, deadline: float, operation: str, read: bool) -> CommandResult:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BeadsError(f"{operation} timed out")
        result = None
        failure = ""
        try:
            result = run_command(argv=argv, timeout_seconds=remaining, env=self._env(), strict_utf8=True)
        except UnicodeDecodeError:
            failure = f"{operation}: invalid UTF-8 from bd"
            if not read:
                failure += "; outcome uncertain; read back Beads state before any manual retry."
        except OSError as exc:
            failure = f"Cannot execute {operation} ({type(exc).__name__})"
        # Raise outside the handler: raw external errors must not survive in
        # either the explicit cause or implicit exception context.
        if failure:
            raise BeadsError(failure)
        assert result is not None
        if result.limit == "timeout" or result.returncode == 124:
            # Completion is ambiguous: never recover/retry a timed-out command.
            raise BeadsError(f"{operation} timed out")
        if result.limit == "output":
            failure = f"{operation}: output exceeded capture limit"
            if not read:
                failure += "; outcome uncertain; read back Beads state before any manual retry."
            raise BeadsError(failure)
        return result

    def _run(self, *args: str) -> CommandResult:
        deadline = time.monotonic() + self.command_timeout_seconds
        argv = self._argv(*args)
        diagnostic_args = args[1:] if args[0] == "--json" else args
        # Include the operation and issue, but never a comments-add body.
        count = 3 if diagnostic_args[0] == "comments" else 2
        operation = "bd " + shlex.join(arg[:200] for arg in diagnostic_args[:count])
        read = diagnostic_args[0] == "show"
        completed = self._execute(argv, deadline=deadline, operation=operation, read=read)
        if completed.returncode != 0 and self._is_db_out_of_sync(completed.stderr):
            # Only this public read is safe to replay. Writes may have committed
            # before reporting failure; importing cannot prove otherwise.
            if not read:
                raise BeadsError(f"{operation}: outcome uncertain; read back Beads state before any manual retry.")
            if not self.auto_sync:
                raise BeadsError(
                    f"{operation}: database out of sync; dry-run will not import. "
                    "Run bd sync --import-only explicitly before retrying."
                )
            recovery_operation = f"bd sync --import-only (recovering {operation})"
            sync = self._execute(
                self._argv("sync", "--import-only"),
                deadline=deadline,
                operation=recovery_operation,
                read=False,
            )
            if sync.returncode != 0:
                raise BeadsError(f"{recovery_operation} failed (exit {sync.returncode})")
            completed = self._execute(argv, deadline=deadline, operation=operation, read=read)
        if completed.returncode != 0:
            failure = f"{operation} failed (exit {completed.returncode})"
            if not read:
                failure += "; outcome uncertain; read back Beads state before any manual retry."
            raise BeadsError(failure)
        return completed

    def _run_json(self, *args: str) -> Any:
        completed = self._run("--json", *args)
        try:
            data = json.loads(completed.stdout, object_pairs_hook=_unique_object)
            _validate_json_depth(data)
            return data
        except (ValueError, RecursionError, MemoryError):
            pass
        raise BeadsError("Invalid JSON from bd")

    def get_issue(self, issue_id: str) -> Issue:
        data = self._run_json("show", issue_id)
        try:
            if not isinstance(data, list) or len(data) != 1:
                raise ValueError("expected one issue")
            issue = self._parse_issue(_object(data[0]))
            if issue.id != issue_id:
                raise BeadsError(f"bd show identity mismatch for {issue_id[:200]}; use the full canonical issue ID")
            return issue
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            # Never echo malformed field values or whole external payloads.
            category = type(exc).__name__
        raise BeadsError(f"Invalid bd show payload for {issue_id[:200]} ({category})")

    def comment(self, issue_id: str, text: str) -> None:
        self._run("comments", "add", issue_id, text)

    def close(self, issue_id: str) -> None:
        self._run("close", issue_id)

    def _parse_issue(self, raw: dict[str, Any]) -> Issue:
        dependencies = [
            Dependency(
                id=_identity(dep["id"]),
                status=IssueStatus(_text(dep["status"])),
                dependency_type=(_text(dep["dependency_type"]) or "blocks")
                if dep.get("dependency_type") is not None
                else "blocks",
            )
            for dep in _records(raw, "dependencies")
        ]
        dependents = [
            IssueSummary(
                id=_identity(child["id"]),
                title=_text(child["title"]),
                status=IssueStatus(_text(child["status"])),
                # Beads omits priority for default P0; treat missing as 0.
                priority=_integer(child.get("priority", 0)),
                created_at=_parse_datetime(child["created_at"]),
                # Missing/ambiguous reverse edges cannot establish ownership.
                dependency_type=_relationship(child["dependency_type"]),
            )
            for child in _records(raw, "dependents")
        ]
        comments = [
            Comment(
                id=_integer(comment["id"]),
                author=_text(comment.get("author", "")),
                text=_text(comment.get("text", "")),
                created_at=_parse_datetime(comment["created_at"]),
            )
            for comment in _records(raw, "comments")
        ]
        return Issue(
            id=_identity(raw["id"]),
            title=_text(raw["title"]),
            status=IssueStatus(_text(raw["status"])),
            # Beads omits priority for default P0; treat missing as 0.
            priority=_integer(raw.get("priority", 0)),
            issue_type=IssueType(_text(raw["issue_type"])),
            created_at=_parse_datetime(raw["created_at"]),
            updated_at=_parse_datetime(raw["updated_at"]),
            dependencies=dependencies,
            dependents=dependents,
            comments=comments,
            description=_text(raw.get("description", "")),
            acceptance_criteria=_text(raw.get("acceptance_criteria", "")),
        )
