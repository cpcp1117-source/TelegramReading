from __future__ import annotations

from fastapi import FastAPI, Response, status

from telegram_trader.config import get_settings
from telegram_trader.db import create_db_engine, database_is_ready

settings = get_settings()
engine = create_db_engine(settings)

app = FastAPI(title="Telegram Trading Monitor Offline Foundation", version="0.1.0")


@app.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "live", "environment": settings.environment}


@app.get("/health/ready")
def readiness(response: Response) -> dict[str, str]:
    if not database_is_ready(engine):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "database": "unavailable"}
    return {"status": "ready", "database": "available"}
