# Beadsflow v0.1 PRD

Status: approved  
Owner: beadsflow  
Related Beads: `beadsflow-3cx` (epic), `beadsflow-4ba` (this PRD)

## Problem statement

Beads workflows (epic → child beads with dependency ordering) benefit from automation:

- Select the next “ready” child bead under an epic.
- Run an implementer loop until the child bead is ready for review.
- Run a reviewer loop until changes are accepted or further work is requested.
- Repeat until the epic is complete, with a minimal and safe human-in-the-loop.

The existing autopilot work lives in `ops-meta` and is tailored to that repo’s structure. `beadsflow` v0.1 is a standalone tool/package that works against a **repo-local** Beads database (`.beads`) with **no `ops-meta` dependency**.

## Goals

- Standalone CLI (`beadsflow`) that operates on a repo-local `.beads` database.
- Primary command: run an automation loop for a single epic.
- Optional command: manage a dedicated terminal session for running the loop.
- Configurable implementer and reviewer (commands/agents are pluggable).
- Safe-by-default concurrency: **one epic per process/session**, with explicit locking.
- Works well with `uv` / `uvx` for execution and distribution.

## Non-goals (v0.1)

- Replacing `bd` (Beads) or re-implementing Beads storage.
- Multi-repo orchestration or cross-repo dependency resolution.
- A general “agent framework” (this is purpose-built for Beads epics).
- Background daemons (v0.1 uses `bd --no-daemon` semantics).
- Automatic PR creation/merging, CI orchestration, or release management.
- Support for running multiple epics concurrently in the *same* working directory (use multiple workspaces/sessions instead).

## Key terms

- **Epic**: A Beads issue of type `epic` that has child beads.
- **Child bead**: A task/story under an epic, potentially with dependencies on sibling beads.
- **Implementer**: The command/agent that makes code changes for a bead.
- **Reviewer**: The command/agent that reviews the changes for a bead.
- **Session**: A named terminal multiplexer session (zellij) used to run one epic loop.

## CLI surface

### `beadsflow run`

Runs the automation loop for an epic in the current repository.

Proposed usage:

```bash
beadsflow run <epic-id> [--once] [--interval 30] [--beads-dir .beads] [--config beadsflow.toml]
```
Note: the CLI flags are shell-agnostic; fish users run the same command.

Required behavior (v0.1):

- Require full canonical IDs. Validate `<epic-id>` exists and is an epic; each `bd show` response must exactly match its requested identity or stop before automation.
- Enforce “one epic per lock” (see Safety/locking).
- Determine the next actionable child bead under the epic:
  - Prefer an `open` child bead that is not blocked by dependencies.
  - Support `in_progress` child beads as resumable work (if configured).
- Run one iteration of:
  - Implementer phase (for one selected child bead).
  - Reviewer phase (for that child bead).
  - Update bead comments/statuses as the coordination channel.
- Repeat until:
  - `--once` is set (single iteration then exit), or
  - the epic is complete (all children closed at time of check; see “Epic completeness”), or
  - a blocking condition is encountered (e.g., no ready children).

Core flags (v0.1):

- `--config <path>`: Path to config file (TOML; see Config model).
- `--beads-dir <path>`: Repo-local Beads directory; default: `.beads`.
- `--once`: Do exactly one pick+run iteration and exit.
- `--interval <seconds>`: Sleep between iterations when not `--once`.
- `--dry-run`: Print what would run (selected child bead, commands) but do not execute implementer/reviewer commands, sync/import recovery or write Beads content. Out-of-sync reads stop with explicit manual import guidance; the documented lock-file writes remain intentional.
- `--implementer <name>` / `--reviewer <name>`: Select configured profiles.
- `--max-iterations <n>`: Safety cap for long-running loops.
- `--verbose` / `--quiet`: Control logging verbosity (useful for debugging/CI).

Exit codes (v0.1):

- `0`: Completed successfully (epic complete, or `--once` iteration completed without error).
- `2`: No ready work found (useful for schedulers).
- Non-zero other: runtime error (lock failure, command failure, invalid config, etc.).

### Child selection (v0.1)

When multiple child beads are ready, `beadsflow run` must choose deterministically.

Default selection strategy (`priority_then_oldest`):

1. Consider only children under the epic that are `open` (and, if enabled, `in_progress`).
2. Refresh each candidate and recheck that its current status is `open` (or configured resumable `in_progress`); skip candidates whose epic summary is stale. Every blocking dependency on that same refreshed issue must be closed, including dependencies outside the epic.
3. Sort candidates by:
   1. Priority (P1 before P2 before P3 …)
   2. Creation time (oldest first)
   3. Bead ID (lexicographic) as a final tie-breaker

This strategy should be configurable in the config file for v0.1 (see Config model), but v0.1 does not need to support multiple strategies beyond the default.

### Epic completeness (v0.1)

“Epic complete” is evaluated dynamically: on each iteration, re-enumerate the epic’s current children. Before declaring completion from all-closed summaries, refresh every listed child from `bd show`. A reopened child participates in selection using that same per-iteration read; read failures stop the run. The epic is complete only when all these authoritative children are closed. An empty epic is complete.

### `beadsflow session`

Manages a zellij-based session intended to run exactly one epic loop.

Proposed usage:

```bash
beadsflow session start <session-name> --epic <epic-id> [--] [run flags...]
beadsflow session attach <session-name>
beadsflow session stop <session-name>
beadsflow session status <session-name>
```

Notes:

- `session start` should create (or reuse) a zellij session with panes tailored to the workflow (e.g., log output, `bd` status view).
- v0.1 should treat zellij as optional: if not installed, print a clear error and an equivalent `beadsflow run ...` command.

## Config model

### Precedence

Highest to lowest:

1. CLI flags
2. Environment variables
3. Config file
4. Defaults

### Config discovery (v0.1)

- Default config filename: `beadsflow.toml` at repo root. Config admission uses one nonblocking regular-file descriptor, a 64 KiB byte limit, bounded reads and at most 64 decoded nesting levels below the root; detected growth/change, nonregular inputs and UTF-8/TOML depth/resource failures stop before locking or automation with a value-free configuration error. A missing optional default still uses defaults.
- Override via `--config <path>` or `BEADSFLOW_CONFIG`.

### Environment variables (v0.1)

- `BEADSFLOW_CONFIG`: Path to config file.
- `BEADSFLOW_BEADS_DIR`: Path to Beads directory (default `.beads`).
- `BEADSFLOW_IMPLEMENTER`: Implementer profile name.
- `BEADSFLOW_REVIEWER`: Reviewer profile name.
- `BEADSFLOW_INTERVAL`: Default interval seconds.

Beads-related environment (expected):

- `BEADS_NO_DAEMON=1` (beadsflow should set this when invoking `bd`)
- `BEADS_DIR=<repo>/.beads` (beadsflow should set this from `--beads-dir`)

### Config file format (v0.1)

v0.1 uses TOML with a stable schema.

Proposed `beadsflow.toml`:

```toml
beads_dir = ".beads"
interval_seconds = 30
log_level = "info" # "debug" | "info" | "warning" | "error"

[implementers.codex]
command = "codex"

[reviewers.claude]
command = "claude"

[run]
max_iterations = 500
resume_in_progress = true
selection_strategy = "priority_then_oldest"
on_command_failure = "stop" # "stop" (v0.1), "skip" (future)
command_timeout_seconds = 3600
```

Command templating (v0.1):

- Commands run at the discovered repository root, including nested-directory invocation. They are executed as shell-argv (no implicit shell); optionally support `{epic_id}` and `{issue_id}` substitutions.
- `command_timeout_seconds` applies to implementer and reviewer command execution; on timeout, treat as a command failure (see Failure handling).
- Environment passed to implementer/reviewer should include:
  - `BEADSFLOW_EPIC_ID`, `BEADSFLOW_ISSUE_ID`
  - `BEADS_DIR`, `BEADS_NO_DAEMON=1`

## Safety and locking assumptions

### Concurrency model

- One `beadsflow run` process owns one epic at a time.
- Multiple epics may be run concurrently **only when each has its own workspace/session** (separate working directories).

### Locking

- Acquire the worktree execution lock at `<worktree_root>/.beadsflow/locks/execution.lock` first, then the epic lock, before any automation. The execution lock is independent of the configured Beads database or nested Beads project; it uses the nearest Git ancestor of the discovered project root, or that project root outside Git. Separate linked worktrees can run distinct epics; a shared database still excludes simultaneous runs of the same epic.
- Lock location: `${BEADS_DIR}/locks/beadsflow-<epic-id>.lock` (directory created if missing).
- Both locks are held for the full run, including dry runs, and released on failure or interruption.
- Before PID writes or automation, require a current-user-owned single-link regular inode and recheck file/directory name identities through pinned descriptors after `flock`. Detected replacements fail closed without unlinking other entries. Lock names/directories must remain stable during a run; the namespace is not locked against external changes after admission.
- If lock acquisition fails, exit with a dedicated non-zero code and print which PID/host holds the lock when possible.

### “Safe defaults”

- Default to `--no-daemon` access patterns via `bd` invocation.
- Provide `--dry-run` and `--once` for safer initial adoption.
- Avoid destructive operations:
  - Do not delete beads.
  - Do not auto-close epics unless explicitly configured (v0.1 can optionally call `bd epic close-eligible` but should default off).

## How beadsflow works with repo-local `.beads`

### Beads discovery

- The default Beads directory is `<repo-root>/.beads`.
- `beadsflow` should validate that the directory looks like a Beads repo (`.beads/beads.db` or `.beads/issues.jsonl` present).

### `bd` invocation rules

All internal calls should be made with:

```bash
export BEADS_NO_DAEMON=1
export BEADS_DIR="$PWD/.beads"
bd --no-daemon <command> ...
```

Fish note: set the same variables with `set -gx BEADS_NO_DAEMON 1` and `set -gx BEADS_DIR "$PWD/.beads"`.

Recommended data flow (v0.1):

- Use `bd ... --json` where available for machine parsing.
- Use `bd show <id>` to load epic metadata and enumerate children.
- Use `bd dep tree <epic-id>` (or per-child dependency inspection) to determine readiness ordering.
- Use `bd comments add <id> ...` to post coordination markers and results.
- Use `bd update <id> ...` / `bd close <id>` / `bd reopen <id>` as needed for lifecycle transitions (exact policy is part of the implementer/reviewer protocol).

## Implementer/reviewer protocol (v0.1)

Coordination is done via Beads comments on the child bead:

- Implementer signals completion with a comment that starts with `Ready for review:`.
- Untracked signature collection shares a 64 MiB cumulative byte budget and a 30-second monotonic deadline, with reads limited to each opened snapshot size. Detected file changes or exhausted budgets fail closed without accepting partial evidence or changing approval/close state. The budget also applies to implementer `require_git_changes` checks; a post-implementer failure posts a fixed failure comment.
- Runner review results are accepted/published only if the pre/post Git signature (excluding a configured Beads metadata subdirectory and `.beadsflow/locks/execution.lock`) is unchanged; reviewers require an available Git worktree, including for Beads-only projects that previously ran without Git; edits are preserved on refusal. Successful approval receives a runner-recorded `LGTM` comment carrying a digest of the reviewed Git signature and canonical issue requirements. This checks content freshness and does not authenticate authorship; Beads writers can reproduce it. Bind the digest to canonical issue ID/title/type/description/acceptance criteria and sorted dependency ID/type pairs, excluding comments, timestamps, priority and dependency status. Re-read requirements after review, before evidence publication and after Git collection immediately before close; refuse changed requirements and old Git-only evidence. Recompare the current signature immediately before automatic close, including resumed runs; changed content or missing/invalid evidence requires review again. Prefer stdout publication; command-mode profiles own direct Beads side effects.
- Reviewer responds with either:
  - `LGTM`, or
  - `Changes requested:` followed by actionable notes.

`beadsflow run` determines the next action by scanning recent comments on the child bead and applying simple state rules (e.g., last marker wins).

### Resume behavior (`resume_in_progress`) (v0.1)

If `resume_in_progress = true`, an `in_progress` child bead may be selected by the child selection rules.

When a bead is selected, the next phase is determined by the most recent coordination marker on that bead:

- If the most recent marker is `Ready for review:` and there is no later `LGTM`, run the reviewer phase.
- If the most recent marker is `Changes requested:` (or there are no markers), run the implementer phase.
- If the most recent marker is `LGTM`, close only when that comment carries runner-recorded Git and issue-requirements evidence matching current content (or skip if already closed). Legacy/manual approvals without evidence and stale evidence cause a new `Ready for review:` request and reviewer phase. Dry runs only preview that review and publish no request.

### Failure handling (v0.1)

If the implementer or reviewer command fails (non-zero exit, timeout, or other execution error):

- Post a comment on the child bead with trusted phase/exit or verdict-validation error and a private local log reference when available. Raw process output and invalid-verdict excerpts remain in protected logs, never automatic failure comments/default console errors. Comment-posting failures do not replace the primary outcome.
- Default behavior is to stop the run and exit non-zero (`on_command_failure = "stop"`).
- No automatic retries in v0.1 (retries/backoff can be added later once the basic behavior is stable).

## Packaging and distribution plan (uv / uvx)

Target: a small Python package with a console entrypoint `beadsflow`.

Project structure (proposed):

- `pyproject.toml` with `project.scripts = { beadsflow = "beadsflow.entrypoints.cli:main" }`
- `src/beadsflow/` implementation

Execution modes:

- From a checkout (developer workflow):
  - `uv run beadsflow run <epic-id> ...`
- As a tool (user workflow):
  - `uvx --from <git-url-or-published-package> beadsflow run <epic-id> ...`

Versioning:

- v0.1 is a “usable MVP” with stable CLI flags and config schema.
- Follow semver from v0.1 onward; breaking changes require major bump.

## Open questions / follow-ups

- Should `beadsflow` perform any `bd sync` behavior, or delegate syncing entirely to the human/workspace workflow?
- What is the minimum portable locking primitive across macOS/Linux (Python `fcntl` / portalocker / `flock`)?
