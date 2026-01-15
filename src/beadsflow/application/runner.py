from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

from beadsflow.application.errors import CommandError, ConfigError
from beadsflow.application.phase import Phase
from beadsflow.application.select import determine_next_work, marker_from_text, select_next_child
from beadsflow.domain.models import Issue, IssueStatus, IssueType, Marker
from beadsflow.infra.beads_cli import BeadsCli
from beadsflow.infra.paths import RepoPaths
from beadsflow.infra.run_command import CommandResult, run_command
from beadsflow.settings import Profile, Settings

DEFAULT_MAX_COMMENT_BYTES = 800
DEFAULT_MAX_COMMENT_LINES = 40
TRUNCATED_NOTICE = "\n\n[truncated]\n"


def _env_limit(name: str, default: int) -> int | None:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    if value <= 0:
        return None
    return value


def _truncate_utf8(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _cap_comment_body(*, body: str, prefix: str, suffix: str) -> str:
    max_lines = _env_limit("BEADSFLOW_MAX_COMMENT_LINES", DEFAULT_MAX_COMMENT_LINES)
    max_bytes = _env_limit("BEADSFLOW_MAX_COMMENT_BYTES", DEFAULT_MAX_COMMENT_BYTES)
    truncated = False

    if max_lines is not None:
        lines = body.splitlines(keepends=True)
        if len(lines) > max_lines:
            body = "".join(lines[:max_lines])
            truncated = True

    if max_bytes is not None:
        max_body_bytes = max_bytes - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
        if max_body_bytes < 0:
            max_body_bytes = 0
        if len(body.encode("utf-8")) > max_body_bytes:
            truncated = True
            body = _truncate_utf8(body, max_body_bytes)
        if truncated:
            max_body_bytes = max(0, max_body_bytes - len(TRUNCATED_NOTICE.encode("utf-8")))
            body = _truncate_utf8(body, max_body_bytes)

    if truncated:
        body = body.rstrip("\n") + TRUNCATED_NOTICE

    return body


@dataclass(frozen=True, slots=True)
class EpicRunLoop:
    beads: BeadsCli
    epic_id: str
    settings: Settings
    repo_paths: RepoPaths
    implementer_name: str | None
    reviewer_name: str | None
    logger: logging.Logger

    def run(self, *, once: bool, dry_run: bool, max_iterations: int) -> int:
        for _iteration in range(1, max_iterations + 1):
            exit_code = self._run_one_iteration(dry_run=dry_run)
            if exit_code is not None:
                return exit_code

            if once:
                return 0

            time.sleep(self.settings.interval_seconds)

        raise ConfigError(f"Reached max_iterations={max_iterations}")

    def _run_one_iteration(self, *, dry_run: bool) -> int | None:
        epic = self.beads.get_issue(self.epic_id)
        if epic.issue_type is not IssueType.EPIC:
            raise ConfigError(f"{self.epic_id} is not an epic")

        if self._is_epic_complete(epic):
            self.logger.info("Epic complete.")
            return 0

        selected = self._select_next_child(epic)
        if selected is None:
            self.logger.info("No ready work found.")
            return 2

        next_work = determine_next_work(issue_id=selected.id, comments=selected.comments)
        self.logger.info(f"Selected {selected.id} ({next_work.phase.value}).")

        if dry_run:
            self._log_dry_run(phase=next_work.phase, issue_id=selected.id)
            return 0

        match next_work.phase:
            case Phase.CLOSE:
                self.beads.close(next_work.issue_id)
                self.logger.info(f"Closed {next_work.issue_id}.")
                return None
            case Phase.IMPLEMENT:
                self._run_implementer(issue_id=next_work.issue_id)
                return None
            case Phase.REVIEW:
                self._run_reviewer(issue_id=next_work.issue_id)
                return None

    def _is_epic_complete(self, epic: Issue) -> bool:
        return all(child.status is IssueStatus.CLOSED for child in epic.dependents)

    def _select_next_child(self, epic: Issue) -> Issue | None:
        child_ids = {child.id for child in epic.dependents}
        cache: dict[str, Issue] = {}

        def is_ready(child_id: str) -> bool:
            child = cache.get(child_id)
            if child is None:
                child = self.beads.get_issue(child_id)
                cache[child_id] = child
            return self._is_child_ready(child, child_ids)

        selected = select_next_child(
            children=epic.dependents,
            resume_in_progress=self.settings.run.resume_in_progress,
            is_ready=is_ready,
        )
        if selected is None:
            return None
        return cache.get(selected.id) or self.beads.get_issue(selected.id)

    def _is_child_ready(self, child: Issue, epic_child_ids: set[str]) -> bool:
        for dep in child.dependencies:
            if dep.id in epic_child_ids and dep.status is not IssueStatus.CLOSED:
                return False
        return True

    def _run_implementer(self, *, issue_id: str) -> None:
        implementer = self._require_profile(
            name=self.implementer_name,
            kind="implementer",
            profiles=self.settings.implementers,
        )
        before_sig = self._maybe_capture_git_signature(implementer)
        result = self._run_profile_command(profile=implementer, issue_id=issue_id, phase="implementer")
        self._ensure_git_changes(profile=implementer, issue_id=issue_id, before_sig=before_sig)
        self._maybe_comment_from_stdout(
            profile=implementer,
            issue_id=issue_id,
            result=result,
            expected_markers={Marker.READY_FOR_REVIEW},
            phase="implementer",
        )
        self._ensure_phase(
            issue_id=issue_id,
            expected={Phase.REVIEW},
            failure_comment="Implementer completed but did not mark `Ready for review:`; stopping.",
            error_message="Implementer did not mark Ready for review",
        )

    def _run_reviewer(self, *, issue_id: str) -> None:
        reviewer = self._require_profile(
            name=self.reviewer_name,
            kind="reviewer",
            profiles=self.settings.reviewers,
        )
        result = self._run_profile_command(profile=reviewer, issue_id=issue_id, phase="reviewer")
        self._maybe_comment_from_stdout(
            profile=reviewer,
            issue_id=issue_id,
            result=result,
            expected_markers={Marker.LGTM, Marker.CHANGES_REQUESTED},
            phase="reviewer",
        )
        self._ensure_phase(
            issue_id=issue_id,
            expected={Phase.CLOSE, Phase.IMPLEMENT},
            failure_comment="Reviewer completed but did not comment `LGTM` or `Changes requested:`; stopping.",
            error_message="Reviewer did not produce expected marker",
        )

    def _exec(self, *, argv: list[str], issue_id: str) -> CommandResult:
        return run_command(
            argv=argv,
            timeout_seconds=self.settings.run.command_timeout_seconds,
            env={
                "BEADSFLOW_EPIC_ID": self.epic_id,
                "BEADSFLOW_ISSUE_ID": issue_id,
                "BEADS_DIR": str(self.repo_paths.beads_dir),
                "BEADS_NO_DAEMON": "1",
            },
        )

    def _wait_for_phase(self, *, issue_id: str, expected: set[Phase]) -> tuple[Issue, Phase]:
        deadline = time.monotonic() + 10.0
        refreshed = self.beads.get_issue(issue_id)
        phase = determine_next_work(issue_id=refreshed.id, comments=refreshed.comments).phase
        while phase not in expected and time.monotonic() < deadline:
            time.sleep(0.5)
            refreshed = self.beads.get_issue(issue_id)
            phase = determine_next_work(issue_id=refreshed.id, comments=refreshed.comments).phase
        return refreshed, phase

    def _require_profile(self, *, name: str | None, kind: str, profiles: dict[str, Profile]) -> Profile:
        if name is None:
            raise ConfigError(f"No {kind} selected (set --{kind} or BEADSFLOW_{kind.upper()})")
        profile = profiles.get(name)
        if profile is None:
            raise ConfigError(f"Unknown {kind} profile: {name}")
        return profile

    def _run_profile_command(self, *, profile: Profile, issue_id: str, phase: str) -> CommandResult:
        argv = profile.command.render(epic_id=self.epic_id, issue_id=issue_id)
        self.logger.info(f"Running {phase}: {' '.join(argv)}")
        result = self._exec(argv=argv, issue_id=issue_id)
        if result.returncode == 0:
            return result
        log_path = self._write_command_log(issue_id=issue_id, phase=phase, result=result)
        failure = self._format_failure(phase, result, log_path=log_path)
        self.logger.error(failure)
        try:
            self.beads.comment(issue_id, failure)
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"Failed to post failure comment to Beads: {exc}")
        phase_label = phase.capitalize()
        raise CommandError(f"{phase_label} failed with code {result.returncode} (log: {log_path})")

    def _maybe_capture_git_signature(self, profile: Profile) -> str | None:
        if not profile.require_git_changes:
            return None
        return self._git_signature()

    def _ensure_git_changes(self, *, profile: Profile, issue_id: str, before_sig: str | None) -> None:
        if not profile.require_git_changes or before_sig is None:
            return
        after_sig = self._git_signature()
        if after_sig != before_sig:
            return
        self.beads.comment(
            issue_id,
            "Implementer completed but did not produce working-tree changes; stopping.",
        )
        raise CommandError("Implementer produced no working-tree changes")

    def _maybe_comment_from_stdout(
        self,
        *,
        profile: Profile,
        issue_id: str,
        result: CommandResult,
        expected_markers: set[Marker],
        phase: str,
    ) -> None:
        if profile.comment_mode != "stdout":
            return
        try:
            self._comment_from_stdout(
                issue_id=issue_id,
                result=result,
                prefix=profile.comment_prefix,
                suffix=profile.comment_suffix,
                expected_markers=expected_markers,
                phase=phase,
            )
        except CommandError as exc:
            self.logger.error(str(exc))
            try:
                self.beads.comment(issue_id, str(exc))
            except Exception as comment_exc:  # noqa: BLE001
                self.logger.error(f"Failed to post comment to Beads: {comment_exc}")
            raise

    def _ensure_phase(
        self,
        *,
        issue_id: str,
        expected: set[Phase],
        failure_comment: str,
        error_message: str,
    ) -> None:
        _refreshed, phase = self._wait_for_phase(issue_id=issue_id, expected=expected)
        if phase in expected:
            return
        self.beads.comment(issue_id, failure_comment)
        raise CommandError(error_message)

    def _git_signature(self) -> str:
        if shutil.which("git") is None:
            raise CommandError("require_git_changes is enabled but git is not available")
        status = self._git_capture(["git", "status", "--porcelain=v1", "-uall"], allow_exit={0})
        diff = self._git_capture(["git", "diff", "--binary"], allow_exit={0, 1})
        staged = self._git_capture(["git", "diff", "--binary", "--cached"], allow_exit={0, 1})
        digest = hashlib.sha256()
        digest.update(diff.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(staged.encode("utf-8", errors="replace"))
        return f"{status}\n# diff-sha256={digest.hexdigest()}\n"

    def _git_capture(self, argv: list[str], *, allow_exit: set[int]) -> str:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            cwd=self.repo_paths.repo_root,
        )
        if completed.returncode not in allow_exit:
            stderr = (completed.stderr or "").strip()
            raise CommandError(f"{' '.join(argv)} failed: {stderr or 'unknown error'}")
        return completed.stdout

    def _comment_from_stdout(
        self,
        *,
        issue_id: str,
        result: CommandResult,
        prefix: str,
        suffix: str,
        expected_markers: set[Marker],
        phase: str,
    ) -> None:
        body = result.stdout or ""
        excerpt = body.strip()
        if not excerpt:
            raise CommandError(f"{phase} produced no stdout to post as a comment")
        if len(excerpt) > 1000:
            excerpt = excerpt[:1000] + "…"
        body = _cap_comment_body(body=body, prefix=prefix, suffix=suffix)
        comment_text = f"{prefix}{body}{suffix}"
        marker = marker_from_text(comment_text)
        if marker not in expected_markers:
            raise CommandError(f"{phase} output missing expected marker.\n\nOutput:\n{excerpt}")
        self.beads.comment(issue_id, comment_text)

    def _format_failure(self, phase: str, result: CommandResult, *, log_path: Path | None = None) -> str:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        excerpt = stderr or stdout
        if len(excerpt) > 1000:
            excerpt = excerpt[:1000] + "…"
        message = f"{phase} command failed (exit {result.returncode})."
        if log_path is not None:
            message += f"\n\nLog:\n{log_path}"
        if excerpt:
            message += f"\n\nOutput:\n{excerpt}"
        return message

    def _write_command_log(self, *, issue_id: str, phase: str, result: CommandResult) -> Path:
        logs_dir = self.repo_paths.beads_dir / "logs" / "beadsflow"
        logs_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        path = logs_dir / f"{issue_id}.{phase}.{timestamp}.log"
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        content = "\n".join(
            [
                f"phase={phase}",
                f"issue_id={issue_id}",
                f"epic_id={self.epic_id}",
                f"returncode={result.returncode}",
                "",
                "argv:",
                textwrap.indent(" ".join(result.argv), "  "),
                "",
                "stdout:",
                textwrap.indent(stdout.rstrip("\n"), "  "),
                "",
                "stderr:",
                textwrap.indent(stderr.rstrip("\n"), "  "),
                "",
            ]
        )
        path.write_text(content, encoding="utf-8")
        return path

    def _log_dry_run(self, *, phase: Phase, issue_id: str) -> None:
        if phase is Phase.CLOSE:
            self.logger.info(f"[dry-run] Would close {issue_id}.")
            return
        if phase is Phase.IMPLEMENT:
            if self.implementer_name is None:
                self.logger.info("[dry-run] No implementer selected.")
                return
            implementer = self.settings.implementers.get(self.implementer_name)
            if implementer is None:
                self.logger.info(f"[dry-run] Unknown implementer profile: {self.implementer_name}")
                return
            argv = implementer.command.render(epic_id=self.epic_id, issue_id=issue_id)
            self.logger.info(f"[dry-run] Would run implementer: {' '.join(argv)}")
            return
        if phase is Phase.REVIEW:
            if self.reviewer_name is None:
                self.logger.info("[dry-run] No reviewer selected.")
                return
            reviewer = self.settings.reviewers.get(self.reviewer_name)
            if reviewer is None:
                self.logger.info(f"[dry-run] Unknown reviewer profile: {self.reviewer_name}")
                return
            argv = reviewer.command.render(epic_id=self.epic_id, issue_id=issue_id)
            self.logger.info(f"[dry-run] Would run reviewer: {' '.join(argv)}")
            return
