from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine

import telegram_trader.web_console.app as app_module
from telegram_trader.config import Settings
from telegram_trader.web_console.app import create_app
from telegram_trader.web_console.auth import (
    SESSION_COOKIE_NAME,
    LoginThrottle,
    hash_password,
    issue_session_token,
    read_session_token,
    verify_password,
)

SECRET = "s" * 40
PLAIN = "correct horse battery"


def test_password_hash_round_trip_and_rejects_wrong_or_malformed() -> None:
    encoded = hash_password(PLAIN)
    assert "$" not in encoded
    assert verify_password(PLAIN, encoded)
    assert not verify_password("wrong", encoded)
    assert not verify_password(PLAIN, "garbage")
    assert not verify_password(PLAIN, "bcrypt:1:1:1:00:00")


def test_session_token_valid_expired_and_tampered() -> None:
    token = issue_session_token("admin", SECRET, ttl_seconds=60, now=1000)
    assert read_session_token(token, SECRET, now=1059) == "admin"
    assert read_session_token(token, SECRET, now=1060) is None
    assert read_session_token(token, "x" * 40, now=1000) is None
    body, _, signature = token.partition(".")
    assert read_session_token(f"{body}x.{signature}", SECRET, now=1000) is None
    assert read_session_token("no-dot", SECRET, now=1000) is None


def test_throttle_locks_after_repeated_failures() -> None:
    throttle = LoginThrottle(max_failures=2, lockout_seconds=10)
    throttle.record_failure(100)
    assert not throttle.is_locked(100)
    throttle.record_failure(100)
    assert throttle.is_locked(105)
    assert not throttle.is_locked(110)


def test_web_console_environment_requires_login_settings() -> None:
    with pytest.raises(ValidationError, match="WEB_CONSOLE_USERNAME"):
        Settings(environment="web_console")
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(
            environment="web_console",
            web_console_username="admin",
            web_console_password_hash=SecretStr("scrypt:x"),
            web_console_session_secret=SecretStr("short"),
        )


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def audit_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    recorded: list[tuple[str, str]] = []

    def fake_record(_factory: Any, event_type: str, username: str, _payload: Any) -> None:
        recorded.append((event_type, username))

    monkeypatch.setattr(app_module, "_record_audit", fake_record)
    monkeypatch.setattr(app_module, "database_is_ready", lambda _engine: True)
    return recorded


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def client(audit_events: list[tuple[str, str]], clock: _Clock) -> TestClient:
    settings = Settings(
        environment="web_console",
        web_console_username="admin",
        web_console_password_hash=SecretStr(hash_password(PLAIN)),
        web_console_session_secret=SecretStr(SECRET),
        web_console_session_hours=1,
    )
    engine = create_engine("postgresql+psycopg://postgres@localhost/unused")
    return TestClient(create_app(settings, engine, clock=clock))


def _login(client: TestClient, username: str = "admin", secret: str = PLAIN) -> Any:
    return client.post("/api/login", json={"username": username, "password": secret})


def test_pages_and_api_require_login(client: TestClient) -> None:
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 303
    assert root.headers["location"] == "/login"
    assert client.get("/login").status_code == 200
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/overview").status_code == 401
    assert client.get("/health/live").json()["environment"] == "web_console"


def test_login_success_sets_cookie_and_audits(
    client: TestClient, audit_events: list[tuple[str, str]]
) -> None:
    response = _login(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert SESSION_COOKIE_NAME in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert client.get("/api/me").json() == {"username": "admin"}
    assert client.get("/api/overview").json() == {"database": "available"}
    assert client.get("/", follow_redirects=False).status_code == 200
    assert client.get("/login", follow_redirects=False).status_code == 303
    assert audit_events == [("web_console_login_succeeded", "admin")]


def test_wrong_credentials_are_rejected_and_audited(
    client: TestClient, audit_events: list[tuple[str, str]]
) -> None:
    assert _login(client, secret="wrong").status_code == 401
    assert _login(client, username="root").status_code == 401
    assert client.get("/api/me").status_code == 401
    assert audit_events == [
        ("web_console_login_failed", "admin"),
        ("web_console_login_failed", "root"),
    ]


def test_login_locks_after_five_failures(client: TestClient, clock: _Clock) -> None:
    for _ in range(5):
        assert _login(client, secret="wrong").status_code == 401
    assert _login(client).status_code == 429
    clock.now += 301
    assert _login(client).status_code == 200


def test_session_expires(client: TestClient, clock: _Clock) -> None:
    assert _login(client).status_code == 200
    clock.now += 3601
    assert client.get("/api/me").status_code == 401


def test_logout_clears_session(client: TestClient, audit_events: list[tuple[str, str]]) -> None:
    _login(client)
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/me").status_code == 401
    assert audit_events[-1] == ("web_console_logout", "admin")


def test_create_app_refuses_missing_login_settings() -> None:
    engine = create_engine("postgresql+psycopg://postgres@localhost/unused")
    with pytest.raises(ValueError, match="not configured"):
        create_app(Settings(), engine)


def test_readiness_reports_database_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get("/health/ready").status_code == 200
    monkeypatch.setattr(app_module, "database_is_ready", lambda _engine: False)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


def test_hash_password_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import telegram_trader.web_console.hash_password as cli

    answers = iter([PLAIN, PLAIN])
    monkeypatch.setattr("getpass.getpass", lambda _prompt: next(answers))
    assert cli.main() == 0
    assert verify_password(PLAIN, capsys.readouterr().out.strip())

    answers = iter([PLAIN, "different value"])
    assert cli.main() == 1

    answers = iter(["short"])
    assert cli.main() == 1


def test_entry_point_serves_console_without_migrating(monkeypatch: pytest.MonkeyPatch) -> None:
    import telegram_trader.web_console.__main__ as entry

    served: list[Any] = []
    monkeypatch.setattr(entry, "get_settings", lambda: _console_settings())
    monkeypatch.setattr(entry, "run", lambda app, **kwargs: served.append((app, kwargs)))
    entry.main()
    assert served[0][0].title == "Telegram Trader Web Console"
    assert served[0][1]["port"] == 8080


def _console_settings() -> Settings:
    return Settings(
        environment="web_console",
        web_console_username="admin",
        web_console_password_hash=SecretStr(hash_password(PLAIN)),
        web_console_session_secret=SecretStr(SECRET),
    )
