from __future__ import annotations

import json
import sys

import pytest

from beadsflow.application.errors import BeadsError
from beadsflow.infra.beads_cli import BeadsCli
from beadsflow.infra.run_command import run_command


def payload() -> bytes:
    return json.dumps(
        [
            {
                "id": "child",
                "title": "TEXT",
                "status": "open",
                "issue_type": "task",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "comments": [{"id": 1, "author": "synthetic", "text": "TEXT", "created_at": "2026-01-01T00:00:00Z"}],
            }
        ]
    ).encode("utf-8")


@pytest.mark.parametrize("invalid", [b"\xff", b"\xe9", b"\xf0\x9f\x92"])
def test_bd_json_rejects_malformed_utf8_before_issue_admission(
    monkeypatch: pytest.MonkeyPatch, invalid: bytes
) -> None:
    raw = payload().replace(b"TEXT", invalid)
    monkeypatch.setattr(
        BeadsCli, "_argv", lambda self, *args: [sys.executable, "-c", f"import os; os.write(1, {raw!r})"]
    )
    with pytest.raises(BeadsError, match="Invalid UTF-8 from bd"):
        BeadsCli(".beads").get_issue("child")


def test_bd_json_preserves_valid_utf8_split_across_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = payload().replace(b"TEXT", "雪".encode())
    split = raw.index("雪".encode()) + 1
    script = f"import os,time; os.write(1,{raw[:split]!r}); time.sleep(.02); os.write(1,{raw[split:]!r})"
    monkeypatch.setattr(BeadsCli, "_argv", lambda self, *args: [sys.executable, "-c", script])
    issue = BeadsCli(".beads").get_issue("child")
    assert issue.title == "雪" and issue.comments[0].text == "雪"


def test_profile_output_keeps_lenient_utf8_decoding() -> None:
    result = run_command(
        argv=[sys.executable, "-c", "import os; os.write(1,b'profile\\xff')"], timeout_seconds=3, env={}
    )
    assert result.stdout == "profile\ufffd"
