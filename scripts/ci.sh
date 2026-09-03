#!/bin/sh
set -eu

uv sync --dev --locked --no-editable --reinstall-package telegram-trading-monitor
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -m "not integration"
uv run python scripts/secret_scan.py --root .
uv run pip-audit . --strict --progress-spinner=off
