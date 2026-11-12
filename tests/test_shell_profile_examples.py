from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from beadsflow.infra.run_command import CommandSpec

ROOT = Path(__file__).resolve().parents[1]


def _shell_examples() -> list[tuple[str, CommandSpec]]:
    examples: list[tuple[str, CommandSpec]] = []
    quickstart = (ROOT / "docs/quickstart.md").read_text(encoding="utf-8")
    sources = [("quickstart", block) for block in re.findall(r"```toml\n(.*?)\n```", quickstart, re.DOTALL)]
    sources.append(("dogfood", (ROOT / "examples/beadsflow.dogfood.toml").read_text(encoding="utf-8")))
    for source, text in sources:
        config = tomllib.loads(text)
        for kind in ("implementers", "reviewers"):
            for name, profile in config.get(kind, {}).items():
                spec = CommandSpec.from_string(profile["command"])
                if spec.argv[0] == "bash":
                    examples.append((f"{source}-{kind}-{name}", spec))
    assert len(examples) == 3
    return examples


@pytest.mark.parametrize("name,spec", _shell_examples(), ids=lambda value: value if isinstance(value, str) else None)
@pytest.mark.parametrize(
    "identity",
    [
        "ordinary-1.2",
        "spaces and 'single' and \"double\" quotes",
        "$(printf SHELL_EXECUTED >&2)",
        "`printf SHELL_EXECUTED >&2`",
    ],
)
def test_documented_shell_profiles_keep_identities_literal(
    tmp_path: Path, name: str, spec: CommandSpec, identity: str
) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("documented shell profiles require Bash")
    helper = tmp_path / "fake_commands.py"
    helper.write_text(
        """import hashlib
import json
import os
import sys
from pathlib import Path

if sys.argv[1] == "shasum":
    print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())
    sys.exit(0)
log = Path(os.environ["FIXTURE_LOG"])
records = json.loads(log.read_text()) if log.exists() else []
args = sys.argv[1:]
if args[0] == "git" and sum(row[0] == "git" for row in records) >= 3:
    print("synthetic change")
records.append(args)
log.write_text(json.dumps(records))
if args[0] == "mktemp":
    print(os.environ["FIXTURE_MSGFILE"])
elif args[0] == "codex":
    Path(args[args.index("--output-last-message") + 1]).write_text("LGTM\\n")
""",
        encoding="utf-8",
    )
    # Local shell functions ensure no installed agent, bd, just or Git command
    # runs. Use the parsed example's program, without login startup files.
    prelude = "\n".join(
        f'{tool}() {{ {shlex.quote(sys.executable)} {shlex.quote(str(helper))} {tool} "$@"; }}'
        for tool in ("codex", "bd", "just", "git", "mktemp", "shasum")
    )
    epic_id = "epic-" + identity
    argv = spec.render(epic_id=epic_id, issue_id=identity)
    assert argv[:2] == ["bash", "-lc"]
    log = tmp_path / "calls.json"
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", prelude + "\n" + argv[2]],
        cwd=tmp_path,
        env={
            **os.environ,
            "BEADSFLOW_ISSUE_ID": identity,
            "BEADSFLOW_EPIC_ID": epic_id,
            "FIXTURE_LOG": str(log),
            "FIXTURE_MSGFILE": str(tmp_path / "message"),
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, (name, result.stderr)
    assert "SHELL_EXECUTED" not in result.stdout + result.stderr
    calls = json.loads(log.read_text())
    agent_calls = [args for args in calls if args[0] == "codex"]
    assert len(agent_calls) == 1
    assert f"bead {identity} under epic {epic_id}." in agent_calls[0][-1]
    comments = [args for args in calls if args[0] == "bd" and "comment" in args]
    if name.endswith("implementers-codex"):
        assert len(comments) == 1
        assert comments[0][-2] == identity
        assert comments[0][-1].startswith("Ready for review:\n\nLGTM")
    else:
        assert not comments
        assert result.stdout == "LGTM\n"
    assert not (tmp_path / "message").exists(), "example must clean up its temporary message"


def test_direct_argv_placeholders_remain_literal() -> None:
    identity = "spaces 'quotes' $(printf SHELL_EXECUTED)"
    spec = CommandSpec.from_string("fake-agent --issue {issue_id} --epic {epic_id}")
    assert spec.render(issue_id=identity, epic_id=identity) == ["fake-agent", "--issue", identity, "--epic", identity]
