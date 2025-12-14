set dotenv-load := true

default:
    @just --list

# Install/sync the project environment (includes dev group by default).
sync *ARGS="":
    uv sync {{ARGS}}

test:
    uv sync
    uv run pytest

typecheck:
    uv sync
    uv run mypy src tests

lint:
    uv sync
    uv run ruff check .
    uv run ruff format --check .

fmt:
    uv sync
    uv run ruff format .

pre-commit:
    uv sync
    uv run pre-commit run -a

docs:
    uv sync --group docs
    uv run sphinx-build -b html docs docs/_build/html

