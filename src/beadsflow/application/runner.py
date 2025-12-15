from __future__ import annotations

import logging
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

from beadsflow.application.errors import CommandError, ConfigError
from beadsflow.application.phase import Phase
from beadsflow.application.select import determine_next_work, select_next_child
from beadsflow.domain.models import Issue, IssueStatus, IssueType
from beadsflow.infra.beads_cli import BeadsCli
from beadsflow.infra.paths import RepoPaths
from beadsflow.infra.run_command import CommandResult, run_command
from beadsflow.settings import Settings


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
        implementer_name = self.implementer_name
        if implementer_name is None:
            raise ConfigError("No implementer selected (set --implementer or BEADSFLOW_IMPLEMENTER)")
        implementer = self.settings.implementers.get(implementer_name)
        if implementer is None:
            raise ConfigError(f"Unknown implementer profile: {implementer_name}")

        argv = implementer.command.render(epic_id=self.epic_id, issue_id=issue_id)
        self.logger.info(f"Running implementer: {' '.join(argv)}")
        result = self._exec(argv=argv, issue_id=issue_id)
        if result.returncode != 0:
            log_path = self._write_command_log(issue_id=issue_id, phase="implementer", result=result)
            failure = self._format_failure("implementer", result, log_path=log_path)
            self.logger.error(failure)
            try:
                self.beads.comment(issue_id, failure)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to post failure comment to Beads: {exc}")
            raise CommandError(f"Implementer failed with code {result.returncode} (log: {log_path})")

        refreshed, phase = self._wait_for_phase(issue_id=issue_id, expected={Phase.REVIEW})
        if phase is not Phase.REVIEW:
            self.beads.comment(issue_id, "Implementer completed but did not mark `Ready for review:`; stopping.")
            raise CommandError("Implementer did not mark Ready for review")

    def _run_reviewer(self, *, issue_id: str) -> None:
        reviewer_name = self.reviewer_name
        if reviewer_name is None:
            raise ConfigError("No reviewer selected (set --reviewer or BEADSFLOW_REVIEWER)")
        reviewer = self.settings.reviewers.get(reviewer_name)
        if reviewer is None:
            raise ConfigError(f"Unknown reviewer profile: {reviewer_name}")

        argv = reviewer.command.render(epic_id=self.epic_id, issue_id=issue_id)
        self.logger.info(f"Running reviewer: {' '.join(argv)}")
        result = self._exec(argv=argv, issue_id=issue_id)
        if result.returncode != 0:
            log_path = self._write_command_log(issue_id=issue_id, phase="reviewer", result=result)
            failure = self._format_failure("reviewer", result, log_path=log_path)
            self.logger.error(failure)
            try:
                self.beads.comment(issue_id, failure)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to post failure comment to Beads: {exc}")
            raise CommandError(f"Reviewer failed with code {result.returncode} (log: {log_path})")

        refreshed, phase = self._wait_for_phase(issue_id=issue_id, expected={Phase.CLOSE, Phase.IMPLEMENT})
        if phase not in {Phase.CLOSE, Phase.IMPLEMENT}:
            self.beads.comment(
                issue_id,
                "Reviewer completed but did not comment `LGTM` or `Changes requested:`; stopping.",
            )
            raise CommandError("Reviewer did not produce expected marker")

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
