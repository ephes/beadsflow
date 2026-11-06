from __future__ import annotations

import json
import math
import os
import shlex
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from beadsflow.application.errors import BeadsError
from beadsflow.domain.models import Comment, Dependency, Issue, IssueStatus, IssueSummary, IssueType
from beadsflow.infra.run_command import CommandResult, run_command


def _parse_datetime(value: str) -> datetime:
    # bd emits RFC3339-ish with timezone; datetime.fromisoformat handles "+01:00" and "Z" (py3.11+).
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_status(value: str) -> IssueStatus:
    try:
        return IssueStatus(value)
    except ValueError as exc:  # pragma: no cover
        raise BeadsError(f"Unknown issue status: {value}") from exc


def _parse_issue_type(value: str) -> IssueType:
    try:
        return IssueType(value)
    except ValueError as exc:  # pragma: no cover
        raise BeadsError(f"Unknown issue type: {value}") from exc


@dataclass(frozen=True, slots=True)
class BeadsCli:
    beads_dir: str
    no_db: bool = False
    command_timeout_seconds: float = 30

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

    def _execute(self, argv: list[str], *, deadline: float, operation: str) -> CommandResult:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BeadsError(f"{operation} timed out")
        try:
            result = run_command(argv=argv, timeout_seconds=remaining, env=self._env())
        except OSError as exc:
            raise BeadsError(f"Cannot execute {operation} ({type(exc).__name__})") from exc
        if result.returncode == 124:
            # Completion is ambiguous: never recover/retry a timed-out command.
            raise BeadsError(f"{operation} timed out")
        return result

    def _run(self, *args: str) -> CommandResult:
        deadline = time.monotonic() + self.command_timeout_seconds
        argv = self._argv(*args)
        diagnostic_args = args[1:] if args[0] == "--json" else args
        # Include the operation and issue, but never a comments-add body.
        count = 3 if diagnostic_args[0] == "comments" else 2
        operation = "bd " + shlex.join(arg[:200] for arg in diagnostic_args[:count])
        completed = self._execute(argv, deadline=deadline, operation=operation)
        if completed.returncode != 0 and self._is_db_out_of_sync(completed.stderr):
            recovery_operation = f"bd sync --import-only (recovering {operation})"
            sync = self._execute(
                self._argv("sync", "--import-only"),
                deadline=deadline,
                operation=recovery_operation,
            )
            if sync.returncode != 0:
                raise BeadsError(f"{recovery_operation} failed: {sync.stderr.strip()[:1000]}")
            completed = self._execute(argv, deadline=deadline, operation=operation)
        if completed.returncode != 0:
            raise BeadsError(f"{operation} failed ({completed.returncode}): {completed.stderr.strip()[:1000]}")
        return completed

    def _run_json(self, *args: str) -> Any:
        completed = self._run("--json", *args)
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:  # pragma: no cover
            raise BeadsError(f"Invalid JSON from bd: bd {' '.join(args)}") from exc

    def get_issue(self, issue_id: str) -> Issue:
        data = self._run_json("show", issue_id)
        if not isinstance(data, list) or not data:
            raise BeadsError(f"Unexpected bd show payload for {issue_id}")
        raw = data[0]
        return self._parse_issue(raw)

    def comment(self, issue_id: str, text: str) -> None:
        self._run("comments", "add", issue_id, text)

    def close(self, issue_id: str) -> None:
        self._run("close", issue_id)

    def _parse_issue(self, raw: dict[str, Any]) -> Issue:
        dependencies = [
            Dependency(
                id=str(dep["id"]),
                status=_parse_status(str(dep["status"])),
                dependency_type=str(dep.get("dependency_type") or "blocks"),
            )
            for dep in raw.get("dependencies", [])
        ]
        dependents = [
            IssueSummary(
                id=str(child["id"]),
                title=str(child["title"]),
                status=_parse_status(str(child["status"])),
                # Beads omits priority for default P0; treat missing as 0.
                priority=int(child.get("priority", 0)),
                created_at=_parse_datetime(str(child["created_at"])),
            )
            for child in raw.get("dependents", [])
        ]
        comments = [
            Comment(
                id=int(comment["id"]),
                author=str(comment.get("author", "")),
                text=str(comment.get("text", "")),
                created_at=_parse_datetime(str(comment["created_at"])),
            )
            for comment in raw.get("comments", [])
        ]
        return Issue(
            id=str(raw["id"]),
            title=str(raw["title"]),
            status=_parse_status(str(raw["status"])),
            # Beads omits priority for default P0; treat missing as 0.
            priority=int(raw.get("priority", 0)),
            issue_type=_parse_issue_type(str(raw["issue_type"])),
            created_at=_parse_datetime(str(raw["created_at"])),
            updated_at=_parse_datetime(str(raw["updated_at"])),
            dependencies=dependencies,
            dependents=dependents,
            comments=comments,
            description=str(raw.get("description", "")),
            acceptance_criteria=str(raw.get("acceptance_criteria", "")),
        )
