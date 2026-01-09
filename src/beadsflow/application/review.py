from __future__ import annotations

import logging
import os
import shlex
import subprocess
import textwrap
from dataclasses import dataclass

from beadsflow.application.errors import CommandError
from beadsflow.application.select import marker_from_comment
from beadsflow.domain.models import Comment, Issue, Marker
from beadsflow.infra.beads_cli import BeadsCli

DEFAULT_DIFF_MAX_BYTES = 8000
DEFAULT_COMMENT_MAX_BYTES = 4000
TRUNCATED_NOTICE = "\n\n[truncated]"
logger = logging.getLogger("beadsflow")


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    issue_id: str
    epic_id: str
    beads_dir: str
    beads_no_db: bool
    cli_command: str
    prompt_arg: str
    diff_max_bytes: int
    comment_max_bytes: int


def _truncate_utf8(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    # Trim by bytes; drop any partial multibyte sequence.
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _truncate_with_notice(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    notice_bytes = len(TRUNCATED_NOTICE.encode("utf-8"))
    if max_bytes <= notice_bytes:
        return _truncate_utf8(text, max_bytes)
    trimmed = _truncate_utf8(text, max_bytes - notice_bytes)
    return trimmed.rstrip("\n") + TRUNCATED_NOTICE


def _latest_comment(comments: list[Comment], markers: set[Marker]) -> Comment | None:
    for comment in sorted(comments, key=lambda c: c.created_at, reverse=True):
        marker = marker_from_comment(comment)
        if marker in markers:
            return comment
    return None


def _format_comment(comment: Comment | None, max_bytes: int) -> str:
    if comment is None:
        return "(none)"
    header = f"{comment.author} {comment.created_at.isoformat()}"
    body = _truncate_with_notice(comment.text, max_bytes)
    return f"{header}\n{body}"


def _run_git(args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or "unknown error"
        logger.debug("git %s failed (exit %s): %s", " ".join(args), completed.returncode, detail)
        return f"(git {' '.join(args)} failed: {detail})"
    return completed.stdout.strip()


def _format_git_sections(unstaged: str, staged: str) -> str:
    sections: list[str] = []
    if unstaged.strip():
        sections.append(f"Unstaged:\n{unstaged}")
    if staged.strip():
        sections.append(f"Staged:\n{staged}")
    if not sections:
        return "(no diff)"
    return "\n\n".join(sections)


def _collect_git_context(diff_max_bytes: int) -> tuple[str, str, str]:
    status = _run_git(["status", "-sb"])
    diff_stat = _format_git_sections(
        _run_git(["diff", "--stat"]),
        _run_git(["diff", "--staged", "--stat"]),
    )
    diff_patch = _format_git_sections(
        _run_git(["diff", "--patch"]),
        _run_git(["diff", "--staged", "--patch"]),
    )
    diff_patch = _truncate_with_notice(diff_patch, diff_max_bytes)
    return status, diff_stat, diff_patch


def _build_prompt(
    *,
    issue: Issue,
    epic_id: str,
    ready_comment: Comment | None,
    review_comment: Comment | None,
    git_status: str,
    diff_stat: str,
    diff_patch: str,
    comment_max_bytes: int,
) -> str:
    ready_context = _format_comment(ready_comment, comment_max_bytes)
    review_context = _format_comment(review_comment, comment_max_bytes)
    return textwrap.dedent(
        f"""\
        Review bead {issue.id} under epic {epic_id}.

        Checklist:
        - Use the issue context and latest Ready for review comment below.
        - Review the git diff for correctness, edge cases, and maintainability.
        - If a Validation section says "(run by reviewer)", do not request changes for
          missing results; call out that validation is pending.

        Issue:
        Title: {issue.title}
        Status: {issue.status.value}
        Description:
        {issue.description or "(none)"}
        Acceptance criteria:
        {issue.acceptance_criteria or "(none)"}

        Comments:
        READY_COMMENT
        {ready_context}

        LAST_REVIEW_COMMENT
        {review_context}

        Git status:
        {git_status}

        Git diff (stat):
        {diff_stat}

        Git diff (patch, truncated):
        {diff_patch}

        Respond with a single Beads comment. The first non-empty line must be exactly
        LGTM or start with Changes requested:. Do not wrap that marker in
        markdown/backticks.
        Keep the comment concise; if you have more to say, summarize and ask for a
        follow-up issue instead of writing a long comment.
        """
    ).strip()


def _run_reviewer_command(*, cli_command: str, prompt_arg: str, prompt: str) -> str:
    argv = shlex.split(cli_command)
    if not argv:
        raise CommandError("Reviewer command is empty")
    argv = [*argv, prompt_arg, prompt]
    completed = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or "unknown error"
        raise CommandError(f"Reviewer command failed (exit {completed.returncode}): {detail}")
    return completed.stdout or ""


def _is_lgtm_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if not stripped.upper().startswith("LGTM"):
        return False
    return len(stripped) == 4 or not stripped[4].isalnum()


def _is_changes_requested_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    lower = stripped.lower()
    return lower == "changes requested" or lower.startswith("changes requested:")


def _ensure_marker(output: str) -> str:
    body = output.strip()
    if not body:
        return "Changes requested:\n\n(no review output)"
    lines = body.splitlines()
    for line in lines:
        if not line.strip():
            continue
        if _is_lgtm_line(line) or _is_changes_requested_line(line):
            first_line = line.strip()
            if lines[0].strip() == first_line:
                return body
            return f"{first_line}\n\n{body}"
        break

    marker = None
    for line in lines:
        if _is_lgtm_line(line):
            marker = "LGTM"
            break
        if _is_changes_requested_line(line):
            marker = "Changes requested:"
            break
    if marker is None:
        marker = "Changes requested:"
    return f"{marker}\n\n{body}"


def run_review(request: ReviewRequest) -> str:
    beads = BeadsCli(beads_dir=request.beads_dir, no_db=request.beads_no_db)
    issue = beads.get_issue(request.issue_id)
    ready_comment = _latest_comment(issue.comments, {Marker.READY_FOR_REVIEW})
    review_comment = _latest_comment(issue.comments, {Marker.LGTM, Marker.CHANGES_REQUESTED})
    git_status, diff_stat, diff_patch = _collect_git_context(request.diff_max_bytes)
    prompt = _build_prompt(
        issue=issue,
        epic_id=request.epic_id,
        ready_comment=ready_comment,
        review_comment=review_comment,
        git_status=git_status,
        diff_stat=diff_stat,
        diff_patch=diff_patch,
        comment_max_bytes=request.comment_max_bytes,
    )
    output = _run_reviewer_command(
        cli_command=request.cli_command,
        prompt_arg=request.prompt_arg,
        prompt=prompt,
    )
    return _ensure_marker(output)


def resolve_review_request(
    *,
    issue_id: str | None,
    epic_id: str | None,
    beads_dir: str | None,
    beads_no_db: bool,
    cli_command: str,
    prompt_arg: str,
    diff_max_bytes: int | None,
    comment_max_bytes: int | None,
) -> ReviewRequest:
    resolved_issue = issue_id or os.environ.get("BEADSFLOW_ISSUE_ID")
    if not resolved_issue:
        raise CommandError("Missing issue id (set --issue-id or BEADSFLOW_ISSUE_ID)")
    resolved_epic = epic_id or os.environ.get("BEADSFLOW_EPIC_ID")
    if not resolved_epic:
        raise CommandError("Missing epic id (set --epic-id or BEADSFLOW_EPIC_ID)")
    resolved_beads_dir = beads_dir or os.environ.get("BEADSFLOW_BEADS_DIR") or os.environ.get("BEADS_DIR") or ".beads"
    if not beads_no_db:
        raw_no_db = os.environ.get("BEADSFLOW_BEADS_NO_DB") or os.environ.get("BEADS_NO_DB")
        if raw_no_db and raw_no_db.strip().lower() in {"1", "true", "yes", "on"}:
            beads_no_db = True
    return ReviewRequest(
        issue_id=resolved_issue,
        epic_id=resolved_epic,
        beads_dir=resolved_beads_dir,
        beads_no_db=beads_no_db,
        cli_command=cli_command,
        prompt_arg=prompt_arg,
        diff_max_bytes=diff_max_bytes if diff_max_bytes is not None else DEFAULT_DIFF_MAX_BYTES,
        comment_max_bytes=comment_max_bytes if comment_max_bytes is not None else DEFAULT_COMMENT_MAX_BYTES,
    )
