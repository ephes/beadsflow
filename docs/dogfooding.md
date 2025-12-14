# Dogfooding beadsflow

This guide documents a practical workflow for using `beadsflow` to develop `beadsflow` itself using this repo’s **repo-local** Beads database at `.beads/`.

Goals:

- Keep Beads operations local and deterministic (no daemon).
- Avoid accidental sync conflicts when using git worktrees.
- Start with a safe run (`--dry-run` / `--once`) before running continuously.

## Recommended environment

For day-to-day work, keep the Beads daemon disabled and point `bd` at this repo’s `.beads/` directory.

**bash/zsh**
```bash
export BEADS_NO_DAEMON=1
export BEADS_DIR="$PWD/.beads"
```

**fish**
```fish
set -gx BEADS_NO_DAEMON 1
set -gx BEADS_DIR "$PWD/.beads"
```

Optional (useful when running from subdirectories or multiple worktrees):

**bash/zsh**
```bash
export BEADSFLOW_CONFIG="$PWD/examples/beadsflow.dogfood.toml"
```

**fish**
```fish
set -gx BEADSFLOW_CONFIG "$PWD/examples/beadsflow.dogfood.toml"
```

## Read a bead + dependency chain

When you pick up a bead, start by reading its context and dependency chain.

**bash/zsh**
```bash
issue="<issue-id>"
bd --no-daemon --no-db show "$issue"
bd --no-daemon --no-db dep tree "$issue"
```

**fish**
```fish
set issue <issue-id>
bd --no-daemon --no-db show $issue
bd --no-daemon --no-db dep tree $issue
```

If the bead references a spec/PRD, read it before changing code.

## Sync guidance (especially for worktrees)

This repo is often used with git worktrees. The safest approach is:

- Disable the daemon (`BEADS_NO_DAEMON=1`).
- In linked worktrees, only do **flush-only** sync.
- Run full sync from the primary checkout only.
- This repo’s `sync.branch` is configured to `beads/sync` (avoid syncing from `main`).

**Primary checkout**
```bash
bd --no-daemon sync
```

**Linked worktree (flush-only)**

**bash/zsh**
```bash
bd --no-daemon sync --flush-only
```

**fish**
```fish
bd --no-daemon sync --flush-only
```

## Safe first run

Before letting `beadsflow` loop, verify selection + command wiring with a dry run, then try a single iteration.

Dry run (recommended first):

```bash
uv run beadsflow run beadsflow-cpp --dry-run --verbose
```

Run one iteration and exit:

```bash
uv run beadsflow run beadsflow-cpp --once --verbose
```

If that looks correct, you can switch to polling mode (see `quickstart.md`).
