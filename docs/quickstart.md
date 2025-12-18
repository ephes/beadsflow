# Quickstart

This guide covers installing beadsflow, configuring it, running it against a repo-local `.beads` database, and how to stop/attach to long-running runs.

## Prerequisites

- A repository with a local Beads DB at `.beads/` (created via `bd init`).
- `uv` installed.
- `bd` available on your PATH.

## Install / setup

From the `beadsflow` checkout:

```bash
uv sync
```

In another repo where you want to use beadsflow, you can run it via:

```bash
uvx --from <git-url-or-package> beadsflow --help
```

## Configure

Create `beadsflow.toml` at the repo root:

```toml
beads_dir = ".beads"
beads_no_db = false
interval_seconds = 30
log_level = "info"
implementer = "codex"
reviewer = "claude"

[implementers.codex]
command = "codex"

[reviewers.claude]
command = "claude"

[run]
max_iterations = 500
resume_in_progress = true
selection_strategy = "priority_then_oldest"
on_command_failure = "stop"
command_timeout_seconds = 3600
```

### Profile options (optional)

Each implementer/reviewer profile can customize how comments are posted:

- `comment_mode` (default: `"command"`): `"command"` means your command posts Beads comments itself; `"stdout"` means beadsflow posts the command stdout as the Beads comment.
- `comment_prefix` / `comment_suffix`: strings appended before/after stdout when `comment_mode = "stdout"`.
- `require_git_changes` (default: `false`): for implementers, fail the run if the git working tree did not change.

Example (stdout mode):

```toml
[implementers.codex]
command = "codex --ask-for-approval never exec ..."
comment_mode = "stdout"
comment_prefix = "Ready for review:\\n\\n"
comment_suffix = "\\n\\nValidation:\\n- uv run pytest"
require_git_changes = true
```

### Command execution semantics

- `command` is **not** run via an implicit shell; it is parsed with `shlex.split(...)` into argv and executed directly.
- If you want shell features (pipes, heredocs, `set -euo pipefail`, etc.), wrap your command explicitly: `bash -lc '...'`.
- `{epic_id}` and `{issue_id}` in the configured argv are substituted per run.
- The following env vars are always provided to implementer/reviewer commands:
  - `BEADSFLOW_EPIC_ID`, `BEADSFLOW_ISSUE_ID`
  - `BEADS_DIR`, `BEADS_NO_DAEMON=1`

### Codex CLI implementer + reviewer profiles (example)

This is a practical config that runs Codex CLI for both phases and has the reviewer emit an autopilot marker (`LGTM` / `Changes requested:`).

```toml
implementer = "codex"
reviewer = "codex"

[implementers.codex]
command = """bash -lc 'set -euo pipefail
issue="{issue_id}"
epic="{epic_id}"
msgfile="$(mktemp -t beadsflow-codex-msg.XXXXXX)"
trap "rm -f \\"$msgfile\\"" EXIT

codex --ask-for-approval never exec --sandbox workspace-write --output-last-message "$msgfile" \
  "Implement bead ${issue} under epic ${epic}. First, read the bead and deps using JSONL mode: bd --no-daemon --no-db show ${issue}; bd --no-daemon --no-db dep tree ${issue}. Implement the acceptance criteria by editing files in this repo. Ensure there is a non-empty git diff when done. Do not post Beads comments yourself."

just test
just typecheck
just lint

body="$(cat "$msgfile")"
comment_text="$(cat <<EOF
Ready for review:

$body

Validation:
- just test
- just typecheck
- just lint
EOF
)"
bd --no-daemon --no-db comment "$issue" "$comment_text"
'"""

[reviewers.codex]
command = """bash -lc 'set -euo pipefail
issue="{issue_id}"
epic="{epic_id}"
msgfile="$(mktemp -t beadsflow-codex-review.XXXXXX)"
trap "rm -f \\"$msgfile\\"" EXIT

codex --ask-for-approval never exec --sandbox workspace-write --output-last-message "$msgfile" \
  "Review bead ${issue} under epic ${epic}. Review the repo state and changes. Respond with a Beads comment. The first non-empty line must be exactly LGTM or start with Changes requested:. Do not wrap that marker in markdown/backticks."

bd --no-daemon comment "$issue" "$(cat "$msgfile")"
'"""
```

Environment overrides (optional):

**bash/zsh**
```bash
export BEADSFLOW_CONFIG="$PWD/beadsflow.toml"
export BEADSFLOW_IMPLEMENTER=codex
export BEADSFLOW_REVIEWER=claude
export BEADSFLOW_BEADS_NO_DB=1  # optional: force --no-db
```

**fish**
```fish
set -gx BEADSFLOW_CONFIG "$PWD/beadsflow.toml"
set -gx BEADSFLOW_IMPLEMENTER codex
set -gx BEADSFLOW_REVIEWER claude
set -gx BEADSFLOW_BEADS_NO_DB 1  # optional: force --no-db
```

## Run

Dry run (recommended first):

```bash
uv run beadsflow run <epic-id> --dry-run --verbose
```

Run one iteration and exit:

```bash
uv run beadsflow run <epic-id> --once --verbose
```

Run continuously (polling):

```bash
uv run beadsflow run <epic-id> --interval 30
```

Notes:

- `beadsflow` uses `bd --no-daemon` semantics and sets `BEADS_NO_DAEMON=1` and `BEADS_DIR=...` for internal `bd` calls.
- Concurrency: run one epic per workspace. Each epic run takes a lock at `.beads/locks/beadsflow-<epic-id>.lock`.

## Stop / attach

### Stop

- Foreground run: press `Ctrl-C`.

### Attach (recommended workflow)

If you use zellij, `beadsflow session` can start and manage a session for you.

```bash
uv run beadsflow session start beadsflow-<epic-id> --epic <epic-id> -- --interval 30
```

Attach later:

```bash
uv run beadsflow session attach beadsflow-<epic-id>
```

Stop the session:

```bash
uv run beadsflow session stop beadsflow-<epic-id>
```

Check session status:

```bash
uv run beadsflow session status beadsflow-<epic-id>
```

If zellij is not installed, `beadsflow session ...` prints an equivalent manual `uv run beadsflow run ...` command.
