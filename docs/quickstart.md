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
Missing explicit files, read/UTF-8/TOML failures, and empty or incorrectly quoted profile commands fail with exit code 1 and a configuration diagnostic before locking or launching processes. Configs must be regular files of at most 64 KiB (65,536 bytes) with at most 64 nesting levels below the root. A bounded iterative walk checks decoded tables and arrays before settings conversion, including otherwise unused fields. The loader opens one nonblocking descriptor, checks its type and size, and reads at most 65,537 bytes to detect overflow even if the file grows. FIFOs, devices, directories, oversized files, detected in-place changes and parser depth/resource failures are refused; the descriptor is closed on success and refusal. Symlinks to regular files remain supported; validation and reading use the opened file. File diagnostics include bounded path context and the error type, without dumping file contents or a traceback.

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
- Runner review now requires Git on PATH and an initialized Git worktree; non-Git Beads-only projects stop before reviewer launch (discovery, dry runs and ungated implementation remain supported).
- Runner reviewers must leave the Git signature unchanged during execution, regardless of `require_git_changes`. Before stdout publication or phase acceptance, beadsflow compares HEAD, tracked/staged diffs and untracked bytes/modes with their pre-review state. A changed tree stops the run without closing the issue or posting runner approval; edits are retained, the refused result is logged privately and a safe `Changes requested:` comment is attempted. A failed signature stops before launching the reviewer or accepting its result. Ignored files, the runner execution lock and the configured Beads metadata subdirectory are excluded from review signatures, including tracked JSONL comment exports. The rest of the entire Git worktree is fenced, even for a nested Beads project. Implementer change signatures still include Beads metadata. Transient changes restored before the final signature are not detected. Use `comment_mode = "stdout"` so the runner owns publication; command-mode profiles are responsible for comments they post themselves. Inspect any directly posted approval before retrying after a rejected review.
- After an unchanged successful review, the runner appends an `LGTM` comment with a `Beadsflow reviewed content v2:` digest. Both stdout and command-mode reviewers use this runner-recorded evidence. Automatic close (including a later `--once` invocation) requires the latest coordination comment to carry that digest and the current Git signature and issue requirements to match. Changed HEAD, tracked/staged content, untracked bytes/modes or issue requirements require review again. The v2 digest includes canonical ID, title, issue type, description, acceptance criteria and sorted dependency ID/type pairs; priority, dependency status, timestamps and comments are excluded. Requirements are re-read during review publication and after collecting Git evidence before close. Old Git-only evidence is stale. Legacy/manual `LGTM` comments without this evidence also require review: a normal run posts `Ready for review:` and runs the configured reviewer; a dry run only previews review. Keep a reviewer configured when resuming older work. Ignored files, the configured Beads metadata subdirectory and the runner execution lock (`.beadsflow/locks/execution.lock`) remain excluded. Other unignored `.beadsflow/` files are still covered. This digest records content freshness; any Beads writer can reproduce it, so it does not authenticate the approval author. The comparison occurs immediately before close; external editors are not locked out.
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

The standalone helper limits its reviewer command to 3600 seconds by default, matching the runner default. Set `beadsflow review --timeout-seconds 120` to override (positive integer). Configure this explicitly alongside `run.command_timeout_seconds` when using a different runner deadline; allow the outer runner enough time for context collection and cleanup. A timeout exits nonzero, writes a diagnostic to stderr, and prints no review comment or approval, even if the command emitted partial output. The shared command runner terminates the owned process group and waits for the direct reviewer process, using the TERM/KILL cleanup described below.

### Command execution semantics

- `command` is **not** run via an implicit shell; it is parsed with `shlex.split(...)` into argv and executed directly.
- If you want shell features (pipes, heredocs, `set -euo pipefail`, etc.), wrap your command explicitly: `bash -lc '...'`.
- `{epic_id}` and `{issue_id}` in the configured argv are substituted per run,
  after argv parsing. Direct-argv placeholders remain single arguments; substitution
  does not escape shell source. Never embed placeholders in a `bash -lc` program
  (including inside quotes). Use quoted `"$BEADSFLOW_ISSUE_ID"` and
  `"$BEADSFLOW_EPIC_ID"` expansions inside shell programs so identities remain data.
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
issue="$BEADSFLOW_ISSUE_ID"
epic="$BEADSFLOW_EPIC_ID"
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
issue="$BEADSFLOW_ISSUE_ID"
epic="$BEADSFLOW_EPIC_ID"
msgfile="$(mktemp -t beadsflow-codex-review.XXXXXX)"
trap "rm -f \\"$msgfile\\"" EXIT

codex --ask-for-approval never exec --sandbox workspace-write --output-last-message "$msgfile" \
  "Review bead ${issue} under epic ${epic}. Review the repo state and changes. Respond with a Beads comment. The first non-empty line must be exactly LGTM or start with Changes requested:. Do not wrap that marker in markdown/backticks." 1>&2

cat "$msgfile"
'"""
comment_mode = "stdout"
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
- Discovery walks ancestors for the configured relative Beads directory, the default `.beads` marker, or a Git worktree marker (`.git` directory or file). The nearest match sets the profile/config root, preserving Beads projects below a larger Git root. Absolute/shared database paths do not by themselves identify a root. A nearer nested clone or submodule Git marker is a separate project/worktree boundary; discovery does not cross it to find an enclosing Beads project. Invoke from the enclosing project to automate that project.
- Profiles run at the discovered repository root, including when beadsflow is invoked from a nested directory. Relative profile paths resolve there; child `BEADS_DIR` is absolute and `BEADS_NO_DAEMON=1`.
- Concurrency: each run first takes a worktree execution lock at `<worktree_root>/.beadsflow/locks/execution.lock`, independent of the configured Beads directory or nested Beads project, then the epic lock at `<beads_dir>/locks/beadsflow-<epic-id>.lock`. Contention fails immediately before any automation, including for distinct epics in one worktree. Locks are held for the full run (also dry runs) and released on failure/interruption. Separate linked worktrees have separate execution locks and may run distinct epics concurrently; a shared Beads directory still serializes the same epic. This does not lock out external editors or commands. The worktree root is the nearest Git ancestor of the discovered project root (or the project root outside Git). The execution lock directory is distinct from the Beads database; add `.beadsflow/` to the target repository’s ignore rules to keep lock metadata out of Git status. Epic IDs may contain only letters, digits, `.`, `_` and `-` and must start with a letter or digit; anything else (path separators, a leading dot) is refused before the lock path is built.

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
Launch failures for interactive commands also return exit code 1 with the operation
and exception type, without exposing the exception payload. The terminal remains inherited.
Status requires Zellij's `list-sessions --no-formatting --short` flags (verified with
Zellij 0.45.1) and compares whole session names, including punctuation and Unicode.
Display decorations and name prefixes are never used as session identities; an
unsupported format flag is reported as a list failure.
Zellij's exit-1 empty-list diagnostic maps to `not running`; other failures remain
errors. Status means the exact name is listed, including exited sessions that Zellij
can resurrect; the CLI's `running` label does not guarantee a live process.
These output contracts are defined in [Zellij 0.45.1's session listing](https://github.com/zellij-org/zellij/blob/v0.45.1/zellij-utils/src/sessions.rs).

Both worktree and epic locks refuse symlinked components in the two directory levels above the lock file (`.beads/locks` for epic runs). Before writing PID metadata, the opened inode must be a single-link regular file owned by the current user. Hardlinked lock entries are refused without changing the aliased file. The refusal message names the reason; if a stale lock file was left by another user (for example a root or container run) or was hardlinked by backup/sync tooling, remove that file (no beadsflow run may be active) and retry. Contention and acquisition/PID-write failures close the handle and release any acquired lock, allowing a later retry.

After acquiring `flock` and before writing PID metadata or entering automation,
beadsflow repeats inode admission and checks that the lock filename and both
directory names still identify the opened inodes through pinned directory
descriptors. A detected replacement, removal or redirection fails closed and
releases the opened lock without unlinking any entry or changing the replacement
holder's metadata. Keep lock files and their directories in place for the entire
run; these checks detect changes during acquisition, but do not lock the
directory namespace or prevent external changes after admission.

Trusted ancestors above those two levels are resolved first, so system aliases such as macOS `/tmp` remain usable. A symlinked `.beads` directory is refused; use its real directory path in the configuration.

Runner profile commands on macOS/Linux execute in an owned process group. On timeout or interruption, beadsflow sends TERM, allows a short cleanup grace period, then sends KILL to remaining group members and waits for the direct command. Timeout results retain partial output and return code 124. Output draining is bounded even if an escaped descendant retains a pipe. Descendants that deliberately leave the process group are outside the cleanup guarantee; this is not a process sandbox. This applies to runner profiles and the standalone `beadsflow review` helper.

Runner profiles, standalone reviewer commands and internal Beads commands have a hard 8 MiB combined stdout/stderr
capture limit per command. Git context/signature commands have a separate 64 MiB cap
to accommodate large tracked diffs. Dirty trees exceeding the Git cap stop runs/reviews
with an explicit exit-125 error rather than using incomplete context. Both pipes are drained together;
retained bytes stay within that limit, including during TERM/KILL cleanup. Exceeding it
stops the owned process group and returns code 125 with an overflow diagnostic. The
phase fails before interpreting stdout, so even a captured `LGTM` cannot approve work.
Failure logs retain only the bounded capture plus the diagnostic, not the full output.
The standalone helper likewise exits nonzero and prints no review comment on overflow, even if `LGTM` appears before or after the cap.

Failed profile diagnostics are stored as unique files under
`<beads_dir>/logs/beadsflow/`, with a timestamp and random suffix in each filename. New files use mode 0600; the log directory is owned by the
current user and set to 0700, including when upgrading an existing directory. Beads and
log directory symlinks are refused, and existing files are never overwritten. Logs include
argv and captured output, which may contain secrets: keep them private and delete them
manually when no longer needed. There is no automatic rotation or expiry. Previously
created logs are not rewritten; inspect their permissions and contents when upgrading.
Beads/log directory opens refuse symlinks even when swapped in during creation.
Writes stay on owned descriptors if an opened directory is renamed. The three
components below the resolved, trusted parent (`.beads`, `logs`, `beadsflow` by
default) are rechecked before reporting the log path. A detected rename or
replacement of those components is an I/O failure, so the affected path is not
published. The trusted ancestor prefix is resolved once and is not monitored for
changes. This completion check cannot prevent path changes after it; it does not
lock the directory namespace.
On write, close or identity-check failure, an empty, partial or complete file may
remain in the original owned directory, with mode 0600 under the mode-0700 log
directory. No failed log path is published. Inspect and remove these artifacts
manually, including in a renamed directory; retries create a new unique file.
If a log cannot be written safely, beadsflow still reports the original profile failure
and posts its failure comment without a log path. Normal and dry-run progress messages omit profile arguments. Commands can still print
sensitive output. Automatic runner failure comments and default console errors contain only trusted phase/exit or verdict-validation diagnostics and a private log reference; they never include raw stdout/stderr excerpts. Invalid, empty or verdict-changing truncated stdout is likewise logged privately instead of copied into a failure comment. If a comment cannot be posted, only the exception type is logged and the primary failure remains the outcome. Read the referenced local log for details. This policy covers automatic profile-failure and invalid-verdict diagnostics; configured stdout comments with valid markers are intentional publication, and command-mode profiles control their own comments.

Internal `bd` operations (`show`, comment, close, and import-only recovery) have a separate 30-second total deadline per operation, including the one permitted out-of-sync recovery and retry. Cleanup uses the same bounded TERM/KILL process-group handling as runner profiles and may add up to one second for output draining. Timeouts and launch failures become `BeadsError` diagnostics naming the operation and issue (excluding comment bodies); epic runs exit nonzero and release their lock. Timed-out commands are never retried, because a comment or close may already have taken effect. Check Beads state before retrying manually. Only an explicit out-of-sync failure triggers automatic import/retry in normal runs; other failures stop immediately. Dry-run reads disable import recovery and stop with instructions to run `bd sync --import-only` explicitly before retrying. Dry runs still create/acquire the documented lock files, but never invoke sync, profiles, comment or close. `run.command_timeout_seconds` continues to apply only to profiles.

`bd show` must return exactly one issue object whose ID exactly matches the requested
full canonical ID. Short/partial IDs are no longer accepted. A backend identity mismatch
is a hard stop before profiles, comment or close; the diagnostic does not echo the returned ID. Canonical issue, dependency
and child IDs must match the epic-lock policy: ASCII letters, digits, `.`, `_`
and `-`, starting with a letter or digit. Option-like IDs, whitespace and control
characters are refused before selection, profile execution or subsequent Beads
operations use that context. Issue/child statuses and issue types must be supported
enum values. Priority is a nonnegative JSON integer
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
reviewer with failed context. Runner signature failures stop before launching a gated
implementer or reviewer, or accepting their output. Git output is decoded without newline
translation or loss of filename bytes for signatures. Review prompts replace
invalid UTF-8 with replacement characters so non-UTF-8 text patches remain reviewable. Owned Git process groups
use the same TERM/KILL cleanup as runner profiles, with up to one second of
additional output draining; descendants that deliberately leave the group are
outside this guarantee. These budgets are separate from profile/reviewer timeouts.

Each untracked signature inventory shares an additional 30-second monotonic deadline
and 64 MiB cumulative byte allowance across all paths (regular file sizes plus
symlink target bytes). Oversized files are refused before reading; reads stop at
the opened file's snapshot size, and a detected size, timestamp or mode change
fails the signature. Deadline checks run between entries, path components and
read chunks; they do not interrupt a blocked filesystem syscall. Incomplete
evidence raises a command error without publishing approval or closing an issue,
including when revalidating a prior approval. The same budget applies to the
implementer `require_git_changes` check: large untracked trees can stop a run
before the implementer starts, and a failure after the implementer ran posts a
fixed failure comment (details stay in the local log). No partial signature is accepted.
These in-process limits are independent of each Git subprocess's budget.


Child readiness uses the refreshed child status as well as its dependencies: only `open`
(and `in_progress` when `run.resume_in_progress` is enabled) can run or close. Stale epic
summaries cannot make a blocked, closed or non-resumable child eligible; selection continues
to the next eligible child.

Before reporting an epic complete, the runner refreshes every child in an all-closed epic summary. Reopened children return to selection using the same per-iteration reads; a failed read stops completion. An empty epic is complete.

### Beads JSON admission

Every `bd --json` response is decoded with duplicate object members refused at
all nesting levels (including issue IDs, statuses and comments). Malformed JSON,
excessive nesting, integer conversion limits and decoder resource failures become
a fixed `BeadsError` diagnostic without payload text. A malformed successful
response is never retried; no profile or close/comment operation follows that
failed read. Valid responses still pass the existing issue schema and identity
checks. The shared command output cap also bounds the bytes received before
JSON decoding.
