FROM python:3.11.16-slim-trixie AS base

COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src ./src
COPY alembic ./alembic
COPY fixtures ./fixtures

FROM base AS runtime-builder
RUN uv sync --locked --no-dev --no-editable

FROM python:3.11.16-slim-trixie AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"
WORKDIR /app
COPY --from=runtime-builder /app/.venv /app/.venv
COPY --from=runtime-builder /app/alembic.ini ./alembic.ini
COPY --from=runtime-builder /app/alembic ./alembic
COPY --from=runtime-builder /app/fixtures ./fixtures
RUN addgroup --system app && \
    adduser --system --ingroup app app
USER app
EXPOSE 8080
CMD ["python", "-m", "telegram_trader.service"]

FROM base AS test
ENV PATH="/app/.venv/bin:${PATH}"
RUN uv sync --locked --no-dev --extra test --no-editable
COPY tests ./tests
COPY scripts ./scripts
CMD ["pytest"]
