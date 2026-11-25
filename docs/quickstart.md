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

In TOML, `beads_dir` must be a nonblank string. Relative and absolute paths are
accepted as written, including spaces. The setting is checked before beadsflow
uses it to locate the Beads store. With an explicit config path, validation
precedes repository discovery. For the default config, beadsflow first locates
the project root to find `beadsflow.toml`.

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
- `require_git_changes` (default: `false`): for implementers, fail the run if the Git signature did not change. The signature includes HEAD, tracked/staged patches, and Git-reported untracked file bytes and modes. Committed changes and edits to already-untracked files count; ignored files do not. Symlinks contribute their link target text without reading the target. Only regular files and symlinks are supported as untracked entries. A Git-reported directory (including an untracked nested repository), FIFO, socket or other special file stops the signature with a command error; its mode alone cannot establish unchanged content. Track or ignore nested repositories deliberately before retrying.
- Runner review now requires Git on PATH and an initialized Git worktree; non-Git Beads-only projects stop before reviewer launch (discovery, dry runs and ungated implementation remain supported).
- Runner reviewers must leave the Git signature unchanged during execution, regardless of `require_git_changes`. Before stdout publication or phase acceptance, beadsflow compares HEAD, tracked/staged diffs and untracked bytes/modes with their pre-review state. A changed tree stops the run without closing the issue or posting runner approval; edits are retained, the refused result is logged privately and a safe `Changes requested:` comment is attempted. A failed signature stops before launching the reviewer or accepting its result. Ignored files, the runner execution lock and the configured Beads metadata subdirectory are excluded from review signatures, including tracked JSONL comment exports. The rest of the entire Git worktree is fenced, even for a nested Beads project. Implementer change signatures still include Beads metadata. Transient changes restored before the final signature are not detected. Use `comment_mode = "stdout"` so the runner owns publication; command-mode profiles are responsible for comments they post themselves. Inspect any directly posted approval before retrying after a rejected review.
- After an unchanged successful review, the runner appends an `LGTM` comment with a `Beadsflow reviewed content v2:` digest. Both stdout and command-mode reviewers use this runner-recorded evidence. Automatic close (including a later `--once` invocation) requires the latest coordination comment to carry that digest and the current Git signature and issue requirements to match. Changed HEAD, tracked/staged content, untracked bytes/modes or issue requirements require review again. The v2 digest includes canonical ID, title, issue type, description, acceptance criteria and sorted dependency ID/type pairs; priority, dependency status, timestamps and comments are excluded. Requirements are re-read during review publication and after collecting Git evidence before close. The final issue must still be ready (including closed blocking dependencies), and a fresh epic read must still contain its explicit parent-child edge. Changed readiness or membership defers work to the next selection pass without close or new review comments. Old Git-only evidence is stale. Legacy/manual `LGTM` comments without this evidence also require review: a normal run posts `Ready for review:` and runs the configured reviewer; a dry run only previews review. Keep a reviewer configured when resuming older work. Ignored files, the configured Beads metadata subdirectory and the runner execution lock (`.beadsflow/locks/execution.lock`) remain excluded. Other unignored `.beadsflow/` files are still covered. This digest records content freshness; any Beads writer can reproduce it, so it does not authenticate the approval author. The comparison occurs immediately before close; external editors are not locked out.
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
and the complete staged/unstaged patch plus untracked source contents. Collection
covers the whole Git worktree, including outside nested project roots, with the
same configured Beads-directory and execution-lock exclusions as review signatures.
Untracked sources must be regular UTF-8 files without NUL; symlinks, directories
(including nested repositories), special files and binary/invalid UTF-8 sources
are refused before reviewer launch. Reads share the fingerprint path, generation,
byte and deadline checks, with source bytes additionally bounded by the smaller
of the patch limit and 64 KiB prompt limit. Track or ignore unsupported files
deliberately before retrying. Tracked patches are rendered as text (`git diff --text`),
so binary classification or a `-diff` attribute cannot hide a change; their raw bytes,
including carriage returns, are shown, and invalid UTF-8 or NUL content refuses the
review the same way.

The complete patch and source sections must fit the positive `--diff-max-bytes`
limit (default 8000); trailing patch whitespace is preserved and patches are never clipped into an approval-capable review.
Overflow stops before reviewer launch and prints no review comment. Reduce the
slice or raise the patch limit within the complete prompt budget. Comment bodies
use the separate `--comment-max-bytes` limit (default 4000). Comment truncation
preserves UTF-8 and adds a notice when space permits. Defaults apply when the
options are omitted. Both options require positive integers, and invalid values
fail before any Beads or Git command runs. Synthetic orchestration tests
exercise this assembly, request forwarding, Git failure handling and conservative
verdict normalization without running Beads or an agent.

```toml
[reviewers.claude]
command = "beadsflow review --cli claude"
comment_mode = "stdout"
```

`--cli` is parsed with `shlex.split(...)`, so you can pass a full command (e.g., `--cli "claude --model opus"`).
Use `--prompt-arg` to match the flag your CLI uses to receive the prompt (default: `-p`).

`beadsflow review` uses `--beads-no-db` first, then the first set variable in
`BEADSFLOW_BEADS_NO_DB`, `BEADS_NO_DB` order. If neither variable is set and
the flag is absent, database mode is the default. The selected variable accepts
`1/0`, `true/false`, `yes/no`, and `on/off` with case and surrounding
whitespace ignored. Invalid values, including empty strings, fail before any
Beads read.

The helper reads Beads with automatic import disabled. If `bd show` reports
that the database is out of sync, review exits before launching the reviewer or
printing a comment. Run `bd sync --import-only` explicitly against the same
Beads directory before retrying.

The configured reviewer interface passes the prompt as one command argument. The
**complete prompt** must fit 64 KiB of UTF-8, including title, description,
acceptance criteria, comment headers, Git status/stat and formatting. This cap is
separate from the patch admission and comment truncation settings. An oversized, unencodable or
NUL-containing prompt is refused before reviewer launch, with a nonzero exit and
no review comment/approval. Requirements are never silently shortened to fit.
Reduce the issue or Git context and retry; this interface does not configure a
stdin/file prompt transport.

The standalone helper limits its reviewer command to 3600 seconds by default, matching the runner default. Set `beadsflow review --timeout-seconds 120` to override (positive integer). Configure this explicitly alongside `run.command_timeout_seconds` when using a different runner deadline; allow the outer runner enough time for context collection and cleanup. A timeout exits nonzero, writes a diagnostic to stderr, and prints no review comment or approval, even if the command emitted partial output. The shared command runner terminates the owned process group and waits for the direct reviewer process, using the TERM/KILL cleanup described below.

### Command execution semantics

- `command` is **not** run via an implicit shell; it is parsed with `shlex.split(...)` into argv and executed directly.
- Each parsed argument, including the executable, must be encodable for subprocess
  execution and free of NUL characters. Invalid arguments fail configuration
  loading before locking or launching processes. The diagnostic omits argument values.
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
- Concurrency: each execution run first takes a worktree execution lock at `<worktree_root>/.beadsflow/locks/execution.lock`, independent of the configured Beads directory or nested Beads project, then the epic lock at `<beads_dir>/locks/beadsflow-<epic-id>.lock`. Contention fails immediately before any automation, including for distinct epics in one worktree. Locks are held for the full execution run and released on failure/interruption. Separate linked worktrees have separate execution locks and may run distinct epics concurrently; a shared Beads directory still serializes the same epic. This does not lock out external editors or commands. The worktree root is the nearest Git ancestor of the discovered project root (or the project root outside Git). The execution lock directory is distinct from the Beads database; add `.beadsflow/` to the target repository’s ignore rules to keep lock metadata out of Git status. Epic IDs may contain only letters, digits, `.`, `_` and `-` and must start with a letter or digit; anything else (path separators, a leading dot) is refused before the lock path is built.

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

If zellij is not installed, `beadsflow session start` prints an equivalent
manual `uv run beadsflow run ...` command to stderr. You can paste it into a
POSIX-compatible shell. The quoting preserves arguments containing spaces and
empty arguments.

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
Inventory capture has a combined 1 MiB stdout/stderr budget. Overflow fails with a fixed diagnostic, without printing captured output; timeout and overflow retire the owned inventory process group. Interactive commands retain the terminal.

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

The shared command runner requires a finite, positive timeout in seconds. It
raises `ValueError` before starting a subprocess if the timeout is invalid.
The timeout starts before process creation. If process creation returns after the
deadline, the runner immediately begins group cleanup and reports a timeout.
The platform's process creation call can block past the deadline. Output draining
and exit observation must finish before the same deadline. A late exit
observation reports a timeout and retains captured output. Cleanup has its own
allowance.

Profile commands, standalone reviewers and internal Beads, Git and Zellij
inventory commands run noninteractively with stdin connected to the null device.
Reads return EOF without consuming the caller's input. Configure profiles and
reviewers to run without terminal prompts. Interactive `beadsflow session start`
and `beadsflow session attach` retain the terminal.

Runner profile commands on macOS/Linux execute in an owned process group. On timeout, overflow or interruption, beadsflow sends TERM, allows a short cleanup grace period, then sends KILL to remaining group members and gives the direct command a finite reap allowance. Cleanup adds at most 1.5 seconds, including up to one second of output draining; ordinary completion allows up to 0.5 seconds to reap. If reaping cannot complete, an ordinary completion raises a command error instead of returning success. Timeout/output-limit results retain codes 124/125 and append a cleanup-uncertainty diagnostic; the child may still be alive, so inspect it before retrying. Timeout results retain partial output and return code 124. Output draining is bounded even if an escaped descendant retains a pipe. Descendants that deliberately leave the process group are outside the cleanup guarantee; this is not a process sandbox. On ordinary completion (including a nonzero exit), remaining group members also receive TERM/KILL before the result returns or execution locks are released. The leader stays unreaped until the last signal, preventing process-group ID reuse, and its real exit code/output are preserved after reaping. Commands must not rely on background group members continuing after the leader exits. This applies to runner profiles and the standalone `beadsflow review` helper.

Cleanup attempts TERM, bounded draining, KILL, bounded reaping and each pipe close
independently, even if an earlier step fails. An initiating exception, including
`KeyboardInterrupt`, is re-raised unchanged with a bounded cleanup note containing
only fixed stage categories. Timeout/output-limit results keep their original
124/125 status and append the same diagnostic. If ordinary cleanup fails after a
nonzero exit, the result retains the exit code and captured output with a bounded
cleanup diagnostic. A zero or unknown exit status with failed cleanup raises a
command error. An interruption (`KeyboardInterrupt` or
`SystemExit`) caught during cleanup is never converted into a result or command
error: the remaining stages still run, then it is re-raised with the cleanup
note (an initiating interruption keeps precedence). Cleanup uncertainty means that owned processes
or pipe resources may remain; inspect them before retrying. Raw cleanup exception
messages are never copied into these notes or diagnostics.

On timeout, captured stderr is followed by a notice with the command's deadline.
That notice and the structured timeout status apply only when the runner stops
the command. A child that exits with code 124 keeps its own stderr.

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
default) are rechecked before reporting the log path. The final filename must
still refer directly to the inode that received the log. A missing or replaced
entry, including a symlink back to the original file, suppresses the log path.
The check covers those entries at completion. The trusted ancestor prefix is
resolved once, and later path changes remain possible.
Before opening a staging file, beadsflow checks the full log basename against
the opened directory's filename byte limit. An overlong basename or unavailable
limit produces a fixed diagnostic without echoing the issue or phase.
Failed profile diagnostics are written to hidden mode-0600 staging files in the
private log directory. Beadsflow closes the stream and syncs the staged file
before linking the `.log` name. A file already at that name is preserved.
Beadsflow attempts to remove the staging name, then syncs the log directory and
each parent up to and including the trusted parent before reporting the path.
If a directory sync fails after linking, beadsflow reports uncertain durability,
keeps the `.log` file, and omits its path. Other failures after linking can also
leave a complete `.log` without a reported path. A cleanup failure or abrupt exit
can leave a hidden `.tmp` file. Inspect and remove these artifacts manually,
including in a renamed directory. Retries use a fresh name.
If a log cannot be written safely, beadsflow still reports the original profile failure
and attempts its failure comment without a log path. If the log content cannot be
encoded as UTF-8, the runner emits a fixed encoding error that includes no log content.
If a logging handler raises while emitting a failure diagnostic, the runner still
attempts the Beads comment and preserves the original failure.
Normal and dry-run progress messages omit profile arguments. Commands can still print
sensitive output. Automatic runner failure comments and default console errors contain only trusted phase/exit or verdict-validation diagnostics and a private log reference; they never include raw stdout/stderr excerpts. Invalid, empty or verdict-changing truncated stdout is likewise logged privately instead of copied into a failure comment. If a comment cannot be posted, only the exception type is logged and the primary failure remains the outcome. Read the referenced local log for details. Standalone reviewer failures and internal `bd` failures (including import recovery)
also expose only bounded operation/exit/category metadata. Their public errors
and exception chains omit raw stdout/stderr, echoed prompts, comment bodies and
raw launch/parse exceptions. These paths do not create a separate diagnostic
capture; existing runner private logs remain governed by the owned-file policy.
This policy covers automatic profile-failure and invalid-verdict diagnostics; configured stdout comments with valid markers are intentional publication, and command-mode profiles control their own comments.

Internal `bd` operations (`show`, comment, close, and import-only recovery) have a separate 30-second total deadline per operation, including the one permitted out-of-sync recovery and retry. Cleanup uses the same bounded TERM/KILL process-group handling as runner profiles and may add up to 1.5 seconds for output draining and reaping. Timeouts, launch failures and cleanup failures after completion become `BeadsError` diagnostics naming the operation and issue (excluding comment bodies); epic runs exit nonzero and release their lock. A reaped child's nonzero status appears in the cleanup error. Failed cleanup also prevents automatic import and retry. Timed-out commands are never retried, because a comment or close may already have taken effect. Check Beads state before retrying manually. Only an explicit out-of-sync `show` failure triggers automatic import/retry in normal runs. Comment and close failures never trigger recovery/replay; their outcome is uncertain, so read back the issue/comments before any manual retry; other failures stop immediately. Dry-run reads disable import recovery and stop with instructions to run `bd sync --import-only` explicitly before retrying. Dry runs leave the worktree and epic lock paths untouched and never invoke sync, profiles, comment or close. A preview uses the Beads and Git state observed during its reads. Concurrent changes can affect those reads or change what a later execution selects. `run.command_timeout_seconds` continues to apply only to profiles.

Beads command labels escape control and other nonprintable characters on a
single line. The escaped issue display is capped at 200 characters. The
subprocess receives the original arguments, including any comment body.

Successful `bd` stdout is decoded as strict UTF-8 before JSON admission. Malformed
or truncated sequences fail with a `BeadsError` naming only the operation.
Comment and close errors include the uncertain-outcome readback instruction.
Valid multibyte text split across process reads is preserved. On failure, the
runner preserves the exit code and bounded diagnostics even if stdout is malformed.
The adapter can still classify an out-of-sync read using stderr. Timeout and
output-cap results keep their corresponding status. Stderr and profile display
output retain their tolerant decoding.

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
`require_git_changes` each have a separate 30-second execution budget per command
(at most eight commands for context, five for a signature). Diff collection disables
external diff and textconv helpers and forces submodule changes to remain visible
regardless of repository ignore settings. A timeout, launch failure or unsuccessful Git command stops the operation
with a command error; the review helper prints no comment and never launches its
reviewer with failed context. Runner signature failures stop before launching a gated
implementer or reviewer, or accepting their output. Git output is decoded without newline
translation or loss of filename bytes for signatures. Review status and
statistics replace invalid UTF-8 with replacement characters; tracked patches are
refused instead, because a reviewer cannot approve bytes it did not see. Owned Git process groups
use the same TERM/KILL cleanup as runner profiles, with up to 1.5 seconds of
additional output draining and reaping; descendants that deliberately leave the group are
outside this guarantee. These budgets are separate from profile/reviewer timeouts.

Each untracked signature inventory shares an additional 30-second monotonic deadline
and 64 MiB cumulative byte allowance across all paths (regular file sizes plus
symlink target bytes). Oversized files are refused before reading; reads stop at
the opened file's snapshot size. Regular files must keep the same device, inode,
size, timestamps and mode from the initial no-follow stat through the opened
descriptor and its final stat, then the final no-follow stat of the name in the
admitted parent directory. Symlinks receive the same before/after name check
around reading their link text. Detected replacement (even with identical bytes
or link text), mutation or disappearance fails the signature without retrying;
stabilize the worktree and explicitly rerun the review/check. These observations
do not lock the filesystem namespace or prevent later changes.
Both fingerprinting and untracked review-source collection retain the opened
directory descriptors and recheck each ancestor's name, device, inode and mode,
from the selected resolved worktree root through the leaf's parent, before
returning evidence. Renaming or replacing any of those directories (including
substitution with a symlink) refuses evidence even if the detached leaf is
unchanged. All retained descriptors close on success or failure; ancestor
traversal and revalidation share the existing fingerprint deadline.
Deadline checks run between entries, path components and
read chunks; they do not interrupt a blocked filesystem syscall. Incomplete
evidence raises a command error without publishing approval or closing an issue,
including when revalidating a prior approval. The same budget applies to the
implementer `require_git_changes` check: large untracked trees can stop a run
before the implementer starts, and a failure after the implementer ran posts a
fixed failure comment (details stay in the local log). No partial signature is accepted.
These in-process limits are independent of each Git subprocess's budget.

Submodules are supported only as clean gitlink commit references. Before collecting
review context or a runner signature, beadsflow checks Git's NUL-delimited
porcelain-v2 submodule flags with `--ignore-submodules=none`. Tracked/index edits
and untracked content inside a submodule refuse evidence, including when Git's
local or `.gitmodules` ignore policy would otherwise hide them. Nested source
contents are not reviewed recursively: commit or clean those changes before
retrying. A clean submodule checked out at another commit, and staged gitlink
updates, remain supported through their commit IDs and ordinary diffs. Ignored
nested files remain outside Git evidence. The same refusal applies before reviewer
launch, before accepting output, when resuming an approval for automatic close,
and to implementer `require_git_changes` signatures. A failed check does not
publish runner approval, replace existing evidence or close an issue. Configured
Beads-directory exclusions continue to apply to review evidence.
Because runner signatures record this porcelain-v2 status, approvals recorded
by an earlier beadsflow version on uncommitted changes are treated as stale
after upgrading and need a fresh review before automatic close; approvals over a
clean worktree are unaffected.


Child readiness uses the refreshed child status as well as its dependencies: only `open`
(and `in_progress` when `run.resume_in_progress` is enabled) can run or close. Stale epic
summaries cannot make a blocked, closed or non-resumable child eligible; selection continues
to the next eligible child.

Only dependents whose explicit `dependency_type` is exactly `parent-child`
belong to the epic: other edges (including `blocks` and `discovered-from`) are
neither selected nor counted toward completion. The adapter retains reverse edge
types and refuses missing, null, blank or non-text dependent relationships rather
than guessing ownership. This stricter requirement applies to reverse dependents;
legacy missing forward dependency types remain blocking as documented above.
External blocking dependencies still gate an actual child's readiness.

Before reporting an epic complete, the runner refreshes every child in an all-closed
epic summary. Reopened children return to selection using the same per-iteration
reads. If all children are still closed, the runner rereads the epic and confirms
that the set of parent-child IDs is unchanged. An empty epic also gets this
membership check. A failed read stops completion. A membership change defers
selection and completion to the next iteration, while `--once` ends without
reporting completion.

### Beads JSON admission

Every `bd --json` response is decoded with duplicate object members refused at
all nesting levels (including issue IDs, statuses and comments). Decoded
responses may nest at most 64 levels below the root; a bounded iterative walk
enforces this on every Python version, including otherwise unused fields.
Malformed JSON, excessive nesting, integer conversion limits and decoder resource failures become
a fixed `BeadsError` diagnostic without payload text. A malformed successful
response is never retried; no profile or close/comment operation follows that
failed read. Valid responses still pass the existing issue schema and identity
checks. The shared command output cap also bounds the bytes received before
JSON decoding.
