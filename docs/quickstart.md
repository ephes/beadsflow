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

Environment overrides (optional):

**bash/zsh**
```bash
export BEADSFLOW_CONFIG="$PWD/beadsflow.toml"
export BEADSFLOW_IMPLEMENTER=codex
export BEADSFLOW_REVIEWER=claude
```

**fish**
```fish
set -gx BEADSFLOW_CONFIG "$PWD/beadsflow.toml"
set -gx BEADSFLOW_IMPLEMENTER codex
set -gx BEADSFLOW_REVIEWER claude
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

`beadsflow session` subcommands exist but are currently placeholders; for now, use your terminal multiplexer directly.

Example with zellij:

```bash
zellij -s beadsflow-<epic-id> -c -- uv run beadsflow run <epic-id> --interval 30
```

Attach later:

```bash
zellij attach beadsflow-<epic-id>
```

Stop the session:

```bash
zellij kill-session beadsflow-<epic-id>
```

