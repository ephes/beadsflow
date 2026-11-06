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
command = "beadsflow review --cli claude"
comment_mode = "stdout"

[run]
max_iterations = 500
resume_in_progress = true
selection_strategy = "priority_then_oldest"
on_command_failure = "stop"
command_timeout_seconds = 3600
```

If no repo-root `beadsflow.toml` exists, defaults apply. An explicit `--config` or `BEADSFLOW_CONFIG` path must exist and be readable.
Missing explicit files, read/UTF-8/TOML failures, and empty or incorrectly quoted profile commands fail with exit code 1 and a configuration diagnostic before locking or launching processes. File diagnostics include bounded path context and the error type, without dumping file contents or a traceback.

Numeric settings must be TOML integers (quoted numbers, floats, and booleans are rejected):

| Setting | Allowed range (inclusive) | Zero behavior |
| --- | --- | --- |
| `interval_seconds` / `--interval` / `BEADSFLOW_INTERVAL` | 0–86400 seconds | Poll without sleeping |
| `run.max_iterations` / `--max-iterations` | 1–1000000 | Rejected |
| `run.command_timeout_seconds` | 1–86400 seconds | Rejected |

TOML flags (`beads_no_db`, `run.resume_in_progress`, profile `require_git_changes`) require actual `true`/`false` values.
`BEADSFLOW_BEADS_NO_DB` accepts `1/0`, `true/false`, `yes/no`, or `on/off` (case insensitive).
Merged CLI/environment numeric settings are validated before acquiring an epic lock or starting commands.

### Profile options (optional)

Each implementer/reviewer profile can customize how comments are posted:

- `comment_mode` (default: `"command"`): `"command"` means your command posts Beads comments itself; `"stdout"` means beadsflow posts the command stdout as the Beads comment.
- `comment_prefix` / `comment_suffix`: strings appended before/after stdout when `comment_mode = "stdout"`.
- `require_git_changes` (default: `false`): for implementers, fail the run if the Git signature did not change. The signature includes HEAD, tracked/staged patches, and Git-reported untracked file bytes and modes. Committed changes and edits to already-untracked files count; ignored files do not. Symlinks contribute their link target text without reading the target. Git-reported directories (including nested repositories) and special files contribute type/mode metadata without reading their contents.
- Stdout comments are truncated by default (800 bytes / 40 lines). Override with `BEADSFLOW_MAX_COMMENT_BYTES` and `BEADSFLOW_MAX_COMMENT_LINES` (set to `0` to disable).

Example (stdout mode):

```toml
[implementers.codex]
command = "codex --ask-for-approval never exec ..."
comment_mode = "stdout"
comment_prefix = "Ready for review:\\n\\n"
comment_suffix = "\\n\\nValidation:\\n- uv run pytest"
require_git_changes = true
```

### Built-in reviewer helper (optional)

`beadsflow review` is a small helper that gathers issue context, the latest Ready for review comment, and git status/diff,
then asks a local CLI (default: `claude -p`) to produce a review comment. It prints the comment to stdout so you can use
`comment_mode = "stdout"`.

The helper includes the issue identity/title/status/description/acceptance criteria,
latest readiness and review comments, Git status, staged/unstaged diff statistics,
and a byte-limited patch. Comment bodies have a separate byte limit. Truncation
preserves UTF-8 and adds a notice when space permits. Synthetic orchestration tests
exercise this assembly, request forwarding, Git failure handling and conservative
verdict normalization without running Beads or an agent.

```toml
[reviewers.claude]
command = "beadsflow review --cli claude"
comment_mode = "stdout"
```

`--cli` is parsed with `shlex.split(...)`, so you can pass a full command (e.g., `--cli "claude --model opus"`).
Use `--prompt-arg` to match the flag your CLI uses to receive the prompt (default: `-p`).

The standalone helper limits its reviewer command to 3600 seconds by default, matching the runner default. Set `beadsflow review --timeout-seconds 120` to override (positive integer). Configure this explicitly alongside `run.command_timeout_seconds` when using a different runner deadline; allow the outer runner enough time for context collection and cleanup. A timeout exits nonzero, writes a diagnostic to stderr, and prints no review comment or approval, even if the command emitted partial output. Python terminates and waits for the direct reviewer process; independently spawned descendants are not guaranteed to be terminated.

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
- Concurrency: run one epic per workspace. Each epic run takes a lock at `.beads/locks/beadsflow-<epic-id>.lock`. Epic IDs may contain only letters, digits, `.`, `_` and `-` and must start with a letter or digit; anything else (path separators, a leading dot) is refused before the lock path is built.

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

Noninteractive `session status` and `session stop` commands each give Zellij 10
seconds to complete. A timeout or process startup failure returns exit code 1
with an operation diagnostic, without echoing captured subprocess output.
Python kills and waits for the direct timed-out process; this does not guarantee
cleanup of independently spawned descendants. A stop timeout can be ambiguous;
check session status before retrying. Interactive `session start` and `session attach`
retain their normal interactive lifetimes, with no short control timeout.

Epic locks refuse symlinked components in the two directory levels above the lock file (`.beads/locks` for epic runs) and nonregular lock files before writing PID metadata. Contention and acquisition/PID-write failures close the handle and release any acquired lock, allowing a later retry.

Trusted ancestors above those two levels are resolved first, so system aliases such as macOS `/tmp` remain usable. A symlinked `.beads` directory is refused; use its real directory path in the configuration.

Runner profile commands on macOS/Linux execute in an owned process group. On timeout or interruption, beadsflow sends TERM, allows a short cleanup grace period, then sends KILL to remaining group members and waits for the direct command. Timeout results retain partial output and return code 124. Output draining is bounded even if an escaped descendant retains a pipe. Descendants that deliberately leave the process group are outside the cleanup guarantee; this is not a process sandbox. This applies to runner profiles, not the standalone `beadsflow review` helper.

Runner profiles and internal Beads commands have a hard 8 MiB combined stdout/stderr
capture limit per command. Git context/signature commands have a separate 64 MiB cap
to accommodate large tracked diffs. Dirty trees exceeding the Git cap stop runs/reviews
with an explicit exit-125 error rather than using incomplete context. Both pipes are drained together;
retained bytes stay within that limit, including during TERM/KILL cleanup. Exceeding it
stops the owned process group and returns code 125 with an overflow diagnostic. The
phase fails before interpreting stdout, so even a captured `LGTM` cannot approve work.
Failure logs retain only the bounded capture plus the diagnostic, not the full output.
The standalone `beadsflow review` helper's reviewer subprocess is separate from this cap.

Failed profile diagnostics are stored as unique files under
`<beads_dir>/logs/beadsflow/`, with a timestamp and random suffix in each filename. New files use mode 0600; the log directory is owned by the
current user and set to 0700, including when upgrading an existing directory. Beads and
log directory symlinks are refused, and existing files are never overwritten. Logs include
argv and captured output, which may contain secrets: keep them private and delete them
manually when no longer needed. There is no automatic rotation or expiry. Previously
created logs are not rewritten; inspect their permissions and contents when upgrading.
If a log cannot be written safely, beadsflow still reports the original profile failure
and posts its failure comment without a log path. Normal and dry-run progress messages omit profile arguments. Commands can still print
sensitive output, and failure comments can contain excerpts: avoid echoing credentials.

Internal `bd` operations (`show`, comment, close, and import-only recovery) have a separate 30-second total deadline per operation, including the one permitted out-of-sync recovery and retry. Cleanup uses the same bounded TERM/KILL process-group handling as runner profiles and may add up to one second for output draining. Timeouts and launch failures become `BeadsError` diagnostics naming the operation and issue (excluding comment bodies); epic runs exit nonzero and release their lock. Timed-out commands are never retried, because a comment or close may already have taken effect. Check Beads state before retrying manually. Only an explicit out-of-sync failure triggers automatic import/retry; other failures stop immediately. `run.command_timeout_seconds` continues to apply only to profiles.

`bd show` must return exactly one issue object. Beads resolves short/partial
request IDs; its returned canonical identity is preserved. Consumed identities must be nonempty strings, and issue/child statuses and issue
types must be supported enum values. Priority is a nonnegative JSON integer
(omitted priority still means P0); comment IDs are nonnegative JSON integers.
Timestamps must be parseable ISO 8601 values with a timezone, including `Z` or
an explicit offset, so all issue/child/comment times can be compared safely.
Optional dependency/child/comment lists may be omitted but must be arrays of
objects when present; duplicate IDs within a list are refused. Consumed text
fields must be strings (omitted optional text remains empty). Missing, empty or
null dependency types retain their legacy blocking meaning. Malformed data
produces a bounded schema diagnostic without echoing payload values. No comment,
close or profile is launched using that malformed context.


Git status, diff, HEAD and untracked-path commands used for review context and
`require_git_changes` each have a separate 30-second execution budget (at most
five commands per collection). Diff collection disables external diff and textconv
helpers. A timeout, launch failure or unsuccessful Git command stops the operation
with a command error; the review helper prints no comment and never launches its
reviewer with failed context. Runner signature failures stop before launching an
implementer or accepting its output. Git output is decoded without newline
translation or loss of filename bytes for signatures. Review prompts replace
invalid UTF-8 with replacement characters so non-UTF-8 text patches remain reviewable. Owned Git process groups
use the same TERM/KILL cleanup as runner profiles, with up to one second of
additional output draining; descendants that deliberately leave the group are
outside this guarantee. These budgets are separate from profile/reviewer timeouts.
