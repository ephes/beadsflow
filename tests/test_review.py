from __future__ import annotations

import pytest

from beadsflow.application.errors import CommandError
from beadsflow.application.review import (
    DEFAULT_COMMENT_MAX_BYTES,
    DEFAULT_DIFF_MAX_BYTES,
    TRUNCATED_NOTICE,
    _ensure_marker,
    _is_changes_requested_line,
    _is_lgtm_line,
    _truncate_utf8,
    _truncate_with_notice,
    resolve_review_request,
)


def test_truncate_utf8_returns_empty_for_non_positive() -> None:
    assert _truncate_utf8("abc", 0) == ""
    assert _truncate_utf8("abc", -5) == ""


def test_truncate_utf8_drops_partial_multibyte() -> None:
    snowman = "\N{SNOWMAN}"
    text = f"hi{snowman}"
    assert _truncate_utf8(text, 4) == "hi"
    assert _truncate_utf8(text, 5) == text


def test_truncate_with_notice_no_truncation() -> None:
    text = "short"
    assert _truncate_with_notice(text, 64) == text


def test_truncate_with_notice_adds_notice() -> None:
    text = "abcdefghijklmnopqrstuvwxyz"
    max_bytes = len(TRUNCATED_NOTICE.encode("utf-8")) + 2
    truncated = _truncate_with_notice(text, max_bytes)
    assert truncated.startswith("ab")
    assert truncated.endswith(TRUNCATED_NOTICE)
    assert len(truncated.encode("utf-8")) <= max_bytes


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("LGTM", True),
        ("LGTM.", True),
        ("LGTM!", True),
        ("LGTM123", False),
        ("lgtm", True),
        ("Looks good", False),
    ],
)
def test_is_lgtm_line(line: str, expected: bool) -> None:
    assert _is_lgtm_line(line) is expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Changes requested", True),
        ("Changes requested:", True),
        ("Changes requested: fix this", True),
        ("Changes requested - fix this", False),
        ("changes requested", True),
        ("Needs work", False),
    ],
)
def test_is_changes_requested_line(line: str, expected: bool) -> None:
    assert _is_changes_requested_line(line) is expected


def test_ensure_marker_empty_output_defaults_to_changes_requested() -> None:
    assert _ensure_marker("") == "Changes requested:\n\n(no review output)"


def test_ensure_marker_keeps_first_marker_line() -> None:
    output = "LGTM\n\nLooks good"
    assert _ensure_marker(output) == output


def test_ensure_marker_trims_leading_blank_lines() -> None:
    output = "\n\nLGTM\nAll good"
    assert _ensure_marker(output) == "LGTM\nAll good"


def test_ensure_marker_prepends_marker_from_later_line() -> None:
    output = "Looks good\n\nLGTM\nMore details"
    expected = "LGTM\n\nLooks good\n\nLGTM\nMore details"
    assert _ensure_marker(output) == expected


def test_ensure_marker_prepends_changes_requested_when_missing() -> None:
    output = "Needs work"
    assert _ensure_marker(output) == "Changes requested:\n\nNeeds work"


def test_resolve_review_request_requires_issue_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEADSFLOW_ISSUE_ID", raising=False)
    with pytest.raises(CommandError):
        resolve_review_request(
            issue_id=None,
            epic_id="E-1",
            beads_dir=None,
            beads_no_db=False,
            cli_command="claude",
            prompt_arg="-p",
            diff_max_bytes=1,
            comment_max_bytes=1,
        )


def test_resolve_review_request_requires_epic_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEADSFLOW_EPIC_ID", raising=False)
    with pytest.raises(CommandError):
        resolve_review_request(
            issue_id="I-1",
            epic_id=None,
            beads_dir=None,
            beads_no_db=False,
            cli_command="claude",
            prompt_arg="-p",
            diff_max_bytes=1,
            comment_max_bytes=1,
        )


def test_resolve_review_request_uses_env_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEADSFLOW_ISSUE_ID", "I-2")
    monkeypatch.setenv("BEADSFLOW_EPIC_ID", "E-2")
    monkeypatch.setenv("BEADS_DIR", ".beads-alt")
    monkeypatch.setenv("BEADSFLOW_BEADS_NO_DB", "yes")
    request = resolve_review_request(
        issue_id=None,
        epic_id=None,
        beads_dir=None,
        beads_no_db=False,
        cli_command="claude",
        prompt_arg="-p",
        diff_max_bytes=None,
        comment_max_bytes=None,
    )
    assert request.issue_id == "I-2"
    assert request.epic_id == "E-2"
    assert request.beads_dir == ".beads-alt"
    assert request.beads_no_db is True
    assert request.diff_max_bytes == DEFAULT_DIFF_MAX_BYTES
    assert request.comment_max_bytes == DEFAULT_COMMENT_MAX_BYTES
