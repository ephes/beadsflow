# beadsflow

[![Documentation Status](https://readthedocs.org/projects/beadsflow/badge/?version=latest)](https://beadsflow.readthedocs.io/en/latest/?badge=latest)

Standalone tool/package for automating Beads-driven work (epic → child beads) in a repo-local `.beads` database.

Worktree and epic locks admit only single-link regular files owned by the current
user. Hardlinks, symlinks and nonregular files are refused before PID metadata is
written; an aliased file is left untouched. See the [locking notes](docs/quickstart.md)
for concurrency and filesystem assumptions.

## Dev quickstart

```bash
uv sync
just test
just typecheck
just lint
```

## Docs

Hosted docs: https://beadsflow.readthedocs.io/en/latest/

```bash
just docs
```

## Automated quality checks

The `Quality` GitHub Actions workflow runs on pushes and pull requests with read-only repository permissions. It installs Python 3.11 and uv 0.13.0, syncs the committed lock with `uv sync --frozen`, then invokes installed tools directly. Sync or check failures fail the job; CI does not use the Justfile fallback. Reproduce it locally:

```bash
uv sync --frozen --python 3.11
export PATH="$PWD/.venv/bin:$PATH"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src tests
.venv/bin/pytest
```

Unit tests mock Beads and agent interactions; subprocess fixtures run only local Python commands in temporary directories. Regression fixtures cover combined output limits, process cleanup, fail-closed approvals, private log permissions, uniqueness and symlink refusal. No Beads initialization, real database, agent login, owner environment, or subscription command is needed. Treat failed quality checks as blocking review evidence. This workflow does not publish or deploy.

The runtime has no third-party dependencies. The committed lock also covers development
and documentation tools; auditing it does not audit isolated pre-commit hook environments.
To audit all locked groups (including platform-marked requirements):

```bash
uv export --frozen --all-groups --no-hashes --no-emit-project -o /tmp/beadsflow-requirements.txt
uvx --from pip-audit==2.10.1 pip-audit --disable-pip --no-deps -r /tmp/beadsflow-requirements.txt
```

Lock refreshes must preserve the dependency-free runtime. After tooling refreshes,
run the quality checks above and `just docs` to validate tooling compatibility.
