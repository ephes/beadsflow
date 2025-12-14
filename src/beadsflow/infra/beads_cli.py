from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from beadsflow.application.errors import BeadsError
from beadsflow.domain.models import Comment, Dependency, Issue, IssueStatus, IssueSummary, IssueType


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

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["BEADS_NO_DAEMON"] = "1"
        env["BEADS_DIR"] = self.beads_dir
        return env

    @staticmethod
    def _is_db_out_of_sync(stderr: str) -> bool:
        return "Database out of sync with JSONL" in stderr

    def _sync_import_only(self) -> None:
        completed = subprocess.run(
            ["bd", "--no-daemon", "sync", "--import-only"],
            check=False,
            capture_output=True,
            text=True,
            env=self._env(),
        )
        if completed.returncode != 0:
            raise BeadsError(f"bd sync --import-only failed: {completed.stderr.strip()}")

    def _run_json(self, *args: str) -> Any:
        argv = ["bd", "--no-daemon", "--json", *args]
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            env=self._env(),
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            if self._is_db_out_of_sync(stderr):
                self._sync_import_only()
                completed = subprocess.run(
                    argv,
                    check=False,
                    capture_output=True,
                    text=True,
                    env=self._env(),
                )
            if completed.returncode != 0:
                raise BeadsError(
                    f"bd failed ({completed.returncode}): bd {' '.join(args)}\n{(completed.stderr or '').strip()}"
                )
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
        argv = ["bd", "--no-daemon", "comment", issue_id, text]
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            env=self._env(),
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            if self._is_db_out_of_sync(stderr):
                self._sync_import_only()
                completed = subprocess.run(
                    argv,
                    check=False,
                    capture_output=True,
                    text=True,
                    env=self._env(),
                )
            if completed.returncode != 0:
                raise BeadsError(f"bd comment failed: {(completed.stderr or '').strip()}")

    def close(self, issue_id: str) -> None:
        argv = ["bd", "--no-daemon", "close", issue_id]
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            env=self._env(),
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            if self._is_db_out_of_sync(stderr):
                self._sync_import_only()
                completed = subprocess.run(
                    argv,
                    check=False,
                    capture_output=True,
                    text=True,
                    env=self._env(),
                )
            if completed.returncode != 0:
                raise BeadsError(f"bd close failed: {(completed.stderr or '').strip()}")

    def _parse_issue(self, raw: dict[str, Any]) -> Issue:
        dependencies = [
            Dependency(id=str(dep["id"]), status=_parse_status(str(dep["status"])))
            for dep in raw.get("dependencies", [])
        ]
        dependents = [
            IssueSummary(
                id=str(child["id"]),
                title=str(child["title"]),
                status=_parse_status(str(child["status"])),
                priority=int(child["priority"]),
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
            priority=int(raw["priority"]),
            issue_type=_parse_issue_type(str(raw["issue_type"])),
            created_at=_parse_datetime(str(raw["created_at"])),
            updated_at=_parse_datetime(str(raw["updated_at"])),
            dependencies=dependencies,
            dependents=dependents,
            comments=comments,
        )
