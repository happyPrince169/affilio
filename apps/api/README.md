# AFFILIO API

FastAPI backend for AFFILIO, organized as a modular monolith. Dependencies are managed with
[uv](https://docs.astral.sh/uv/); the `package.json` in this directory only exposes Turborepo
task adapters and installs nothing from npm.

```bash
uv sync                       # create .venv and install (Python 3.12 is auto-provisioned)
uv run uvicorn affilio.main:app --reload --port 8000
uv run alembic upgrade head   # apply migrations
uv run python -m affilio.scripts.seed_dev
uv run pytest                 # unit tests; set TEST_DATABASE_URL to include DB tests
uv run ruff check . && uv run ruff format --check .
```

See the repository root `README.md` and `docs/architecture/` for the full picture.
