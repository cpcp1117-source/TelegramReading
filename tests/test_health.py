from __future__ import annotations

import json
from io import BytesIO
from urllib.error import URLError

from fastapi import Response

import telegram_trader.app as app_module
import telegram_trader.healthcheck as healthcheck_module
from telegram_trader.app import liveness, readiness


def test_liveness_is_offline() -> None:
    assert liveness() == {"status": "live", "environment": "offline"}


def test_readiness_reports_database_state(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    response = Response()
    monkeypatch.setattr(app_module, "database_is_ready", lambda _engine: True)
    assert readiness(response) == {"status": "ready", "database": "available"}
    assert response.status_code == 200

    monkeypatch.setattr(app_module, "database_is_ready", lambda _engine: False)
    assert readiness(response) == {"status": "not_ready", "database": "unavailable"}
    assert response.status_code == 503


class _FakeHttpResponse:
    status = 200

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps({"status": "ready"}).encode()


def test_container_healthcheck_success_and_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        healthcheck_module, "urlopen", lambda *_args, **_kwargs: _FakeHttpResponse()
    )
    assert healthcheck_module.main() == 0

    def fail(*_args: object, **_kwargs: object) -> BytesIO:
        raise URLError("offline")

    monkeypatch.setattr(healthcheck_module, "urlopen", fail)
    assert healthcheck_module.main() == 1
