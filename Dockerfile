FROM python:3.11.15-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY alembic ./alembic
COPY fixtures ./fixtures

FROM base AS runtime
RUN pip install --no-cache-dir . && \
    addgroup --system app && \
    adduser --system --ingroup app app
USER app
EXPOSE 8080
CMD ["python", "-m", "telegram_trader.service"]

FROM base AS test
RUN pip install --no-cache-dir ".[test]"
COPY tests ./tests
COPY scripts ./scripts
CMD ["pytest"]
