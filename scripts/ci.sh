#!/bin/sh
set -eu

pytest_temp_root=".pytest_tmp/posix"
mkdir -p "$pytest_temp_root"

uv sync --dev --locked --no-editable --reinstall-package telegram-trading-monitor
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
uv run --no-sync pytest -m "not integration" \
  --basetemp "$pytest_temp_root/run" \
  -o "cache_dir=$pytest_temp_root/cache"
uv run --no-sync python scripts/secret_scan.py --root .
uv run --no-sync pip-audit . --strict --progress-spinner=off
