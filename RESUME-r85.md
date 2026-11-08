# Round 85 / B24 resume note

Both slices are implemented on `fix/r85-bounded-review-evidence`, based on
`origin/main` at `8a5c907d491e56c49d3299187cc12f212be9f676`:

1. `beadsflow-standalone-review-output-admission`: use the existing bounded
   command runner for standalone reviews; refuse output overflow before verdict
   parsing and clean up the owned process group.
2. `beadsflow-untracked-fingerprint-work-budget`: share a 64 MiB allowance and
   30-second monotonic deadline across an untracked inventory; bound reads to the
   opened snapshot size and fail closed on detected file changes or exhausted
   budgets, preserving approval state.

Both findings were rechecked against the fresh default branch and remained
unfixed. All three standalone regression cases and all nine fingerprint cases
failed on their respective original implementations, then passed with the fixes.
All fixtures are synthetic. Quickstart, PRD and Unreleased security notes match
these changes. No version bump or workflow changes.

## Local validation

Frozen Python 3.11 environment. Ruff lint/format and mypy src/tests pass;
full pytest: **419 passed**. The Sphinx HTML build also passes. The full test
command requires the README's virtualenv PATH so existing fixtures can invoke
`python`:

```sh
UV_CACHE_DIR="$PWD/../.cache/uv" uv sync --frozen --python 3.11
export PATH="$PWD/.venv/bin:$PATH"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src tests
.venv/bin/pytest
```

## Review blocker and next steps

The required `claude-review-loop` invocation with `--model claude-opus-5-5
--effort high` returned `PROVIDER_ERROR` / `authentication_failed` before
reviewing: **OAuth session expired and could not be refreshed**. This is not a
review verdict; no clean review exists. The harness removed its temporary copy.
No merge or default-branch push was performed. This feature branch was pushed
only to park unfinished work, as explicitly authorized for this failure.

After authentication is restored, fetch and rebase onto current `origin/main`,
rerun the local checks, then review the entire bundle delta against main. The
implementation is committed, so run the harness on a temporary review checkout
with these commits applied as uncommitted changes over current main; reviewing
only this clean branch worktree would omit the implementation diff. Include this
note and local validation results as review context. Use Opus 5.5 at high effort.
Fix and re-review while findings shrink; stop at four rounds or a recurring
Critical/Warning, per the owner. Do not merge without a passing review.

Before an authorized clean merge, fetch again; rebase and rerun local checks if
origin moved. Merge locally with `--no-ff` into main, run the full local checks
after merging, push only main and verify with `git ls-remote`. Do not use GitHub
Actions, create PRs, deploy, tag, release, or delete remote branches.
