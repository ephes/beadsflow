set dotenv-load := true

default:
    @just --list

# Install/sync the project environment (includes dev group by default).
sync *ARGS="":
    uv sync {{ARGS}}

test:
    if uv sync; then uv run pytest; elif [ -x .venv/bin/python ]; then .venv/bin/python -m pytest; else echo "uv sync failed and .venv/bin/python is missing" >&2; exit 1; fi

cov:
    uv sync
    uv run pytest --cov=beadsflow --cov-report=term-missing --cov-report=html --cov-report=xml

typecheck:
    if uv sync; then uv run mypy src tests; elif [ -x .venv/bin/python ]; then .venv/bin/python -m mypy src tests; else echo "uv sync failed and .venv/bin/python is missing" >&2; exit 1; fi

lint:
    if uv sync; then uv run ruff check . && uv run ruff format --check .; elif [ -x .venv/bin/ruff ]; then .venv/bin/ruff check . && .venv/bin/ruff format --check .; else echo "uv sync failed and .venv/bin/ruff is missing" >&2; exit 1; fi

fmt:
    uv sync
    uv run ruff format .

pre-commit:
    uv sync
    uv run pre-commit run -a

docs:
    uv sync --group docs
    uv run sphinx-build -b html docs docs/_build/html
