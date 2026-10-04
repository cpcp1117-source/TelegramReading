"""Web Console: login-protected pages for settings and records.

Holds only its own login credentials and a database connection -- never an
exchange, Telegram or OpenAI secret (ADR-0002). Signal approval stays in the
Telegram Control Bot; nothing here approves or places a trade.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader import channel_admin, runtime_settings
from telegram_trader.audit import append_audit_event
from telegram_trader.binance_market_data import BinanceMarketDataClient, fetch_mark_price
from telegram_trader.config import Settings

# The Control Bot's own definition of "a failure the user must act on" and of the
# receipt its 「我已處理」 button writes, so the overview counts exactly what it reminds about.
from telegram_trader.control_bot import (
    _EXECUTION_FAILURE_EVENT_TYPES as FAILURE_EVENT_TYPES,
)
from telegram_trader.control_bot import (
    _EXECUTION_HANDLED_CONSUMER_NAME as HANDLED_CONSUMER_NAME,
)
from telegram_trader.daily_loss import utc_day_start
from telegram_trader.db import create_session_factory, database_is_ready
from telegram_trader.runtime_settings import SettingValueError
from telegram_trader.web_console import records
from telegram_trader.web_console.auth import (
    SESSION_COOKIE_NAME,
    LoginThrottle,
    issue_session_token,
    read_session_token,
    verify_password,
)
from telegram_trader.web_console.records import MarkPriceFetcher, Page

LOGGER = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
# Trading day for "today" and the daily PnL table: UTC 00:00 = Taiwan 08:00, the same
# day the daily-loss kill switch counts (user decision 2026-10-04).
TRADING_DAY_TZ_NAME = "UTC"
AUDIT_AGGREGATE_TYPE = "web_console_session"

Clock = Callable[[], float]


@dataclass(frozen=True)
class ConsoleAuth:
    username: str
    password_hash: str
    session_secret: str
    ttl_seconds: float
    clock: Clock


def current_user(request: Request) -> str | None:
    auth: ConsoleAuth = request.app.state.auth
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is None:
        return None
    username = read_session_token(token, auth.session_secret, now=auth.clock())
    return username if username == auth.username else None


def require_user(request: Request) -> str:
    username = current_user(request)
    if username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登入")
    return username


CurrentUser = Annotated[str, Depends(require_user)]


def db_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


Db = Annotated[Session, Depends(db_session)]


SETTING_GROUPS = ("risk", "runtime")


class SettingsUpdate(BaseModel):
    # key -> display value, or null for "back to the .env default"
    changes: dict[str, str | None] = Field(max_length=50)
    note: str | None = Field(default=None, max_length=500)


def _setting_group(group: str) -> str:
    if group not in SETTING_GROUPS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="沒有這個設定頁")
    return group


def _readonly_settings(settings: Settings) -> list[dict[str, str]]:
    """Shown on the runtime page; changed only in .env (or are secrets held elsewhere)."""
    return [
        {"label": "交易環境", "value": settings.execution_environment},
        {"label": "Binance 行情 API", "value": settings.binance_api_base_url},
        {"label": "Binance 下單 API（Testnet）", "value": settings.binance_testnet_base_url},
        {
            "label": "監聽的 Telegram 頻道",
            "value": "、".join(target.label for target in settings.telegram_target_channels),
        },
        {"label": "管理介面登入有效時間", "value": f"{settings.web_console_session_hours:g} 小時"},
        {
            "label": "API Key 與密碼",
            "value": "存在 .env，各服務只拿到自己需要的；網頁不顯示也不修改",
        },
    ]


class LoginRequest(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=1024)


def _record_audit(
    session_factory: sessionmaker[Session], event_type: str, username: str, payload: dict[str, Any]
) -> None:
    with session_factory() as session, session.begin():
        append_audit_event(
            session,
            event_id=f"web-{uuid.uuid4().hex}",
            event_type=event_type,
            aggregate_type=AUDIT_AGGREGATE_TYPE,
            aggregate_id=username[:200],
            payload=payload,
        )


def testnet_mark_price_fetcher(settings: Settings) -> MarkPriceFetcher:
    """Public premiumIndex on the Testnet host the positions live on -- no API key."""
    client = BinanceMarketDataClient(
        httpx.Client(base_url=settings.binance_testnet_base_url, timeout=5.0)
    )

    def fetch(symbol: str) -> Decimal | None:
        try:
            return fetch_mark_price(client, symbol).price
        except (httpx.HTTPError, ValueError) as exc:
            LOGGER.warning("mark price unavailable", extra={"symbol": symbol, "error": str(exc)})
            return None

    return fetch


def _not_found(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到這筆記錄")
    return value


def create_app(
    settings: Settings,
    engine: Engine,
    *,
    clock: Clock = time.time,
    mark_price: MarkPriceFetcher | None = None,
) -> FastAPI:
    if (
        settings.web_console_username is None
        or settings.web_console_password_hash is None
        or settings.web_console_session_secret is None
    ):
        raise ValueError("Web Console login settings are not configured")
    auth = ConsoleAuth(
        username=settings.web_console_username,
        password_hash=settings.web_console_password_hash.get_secret_value(),
        session_secret=settings.web_console_session_secret.get_secret_value(),
        ttl_seconds=settings.web_console_session_hours * 3600,
        clock=clock,
    )
    session_factory = create_session_factory(engine)
    throttle = LoginThrottle()
    fetch_mark = mark_price or testnet_mark_price_fetcher(settings)
    app = FastAPI(title="Telegram Trader Web Console", docs_url=None, redoc_url=None)
    app.state.auth = auth
    app.state.session_factory = session_factory
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/health/live")
    def liveness() -> dict[str, str]:
        return {"status": "live", "environment": settings.environment}

    @app.get("/health/ready")
    def readiness(response: Response) -> dict[str, str]:
        if not database_is_ready(engine):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "not_ready", "database": "unavailable"}
        return {"status": "ready", "database": "available"}

    @app.get("/login", response_model=None)
    def login_page(request: Request) -> Response:
        if current_user(request) is not None:
            return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
        return FileResponse(STATIC_DIR / "login.html")

    @app.get("/", response_model=None)
    def index_page(request: Request) -> Response:
        if current_user(request) is None:
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        return FileResponse(STATIC_DIR / "index.html")

    @app.post("/api/login")
    def login(body: LoginRequest, response: Response) -> dict[str, str]:
        now = clock()
        if throttle.is_locked(now):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="登入失敗次數過多，請稍後再試",
            )
        # Always run the hash check so a wrong username takes as long as a wrong password.
        password_ok = verify_password(body.password, auth.password_hash)
        if not (password_ok and body.username == auth.username):
            throttle.record_failure(now)
            _record_audit(session_factory, "web_console_login_failed", body.username, {})
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="帳號或密碼錯誤")
        throttle.record_success()
        _record_audit(session_factory, "web_console_login_succeeded", body.username, {})
        response.set_cookie(
            SESSION_COOKIE_NAME,
            issue_session_token(
                body.username, auth.session_secret, ttl_seconds=auth.ttl_seconds, now=now
            ),
            max_age=int(auth.ttl_seconds),
            httponly=True,
            samesite="strict",
            path="/",
        )
        return {"username": body.username}

    @app.post("/api/logout")
    def logout(username: CurrentUser, response: Response) -> dict[str, str]:
        _record_audit(session_factory, "web_console_logout", username, {})
        response.delete_cookie(SESSION_COOKIE_NAME, path="/")
        return {"status": "logged_out"}

    @app.get("/api/me")
    def me(username: CurrentUser) -> dict[str, str]:
        return {"username": username}

    @app.get("/api/overview")
    def overview(_username: CurrentUser, session: Db) -> dict[str, Any]:
        if not database_is_ready(engine):
            return {"database": "unavailable"}
        now = datetime.fromtimestamp(clock(), UTC)
        counts = records.overview_counts(
            session,
            failure_event_types=FAILURE_EVENT_TYPES,
            handled_consumer=HANDLED_CONSUMER_NAME,
            today_start=utc_day_start(now),
            now=now,
        )
        return {"database": "available", **counts}

    def settings_page(session: Session, group: str) -> dict[str, Any]:
        return {
            "items": runtime_settings.describe(session, settings, group),
            "history": runtime_settings.history(session, group),
            "readonly": _readonly_settings(settings) if group == "runtime" else [],
        }

    @app.get("/api/settings/{group}")
    def get_settings_page(_username: CurrentUser, session: Db, group: str) -> dict[str, Any]:
        return settings_page(session, _setting_group(group))

    @app.post("/api/settings/{group}")
    def save_settings(
        username: CurrentUser, session: Db, group: str, body: SettingsUpdate
    ) -> dict[str, Any]:
        group = _setting_group(group)
        for key in body.changes:
            spec = runtime_settings.SPECS.get(key)
            if spec is None or spec.group != group:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=f"不能在這頁修改：{key}"
                )
        try:
            with session.begin():
                diffs = runtime_settings.record_changes(
                    session,
                    settings,
                    dict(body.changes),
                    actor=username,
                    note=(body.note or "").strip() or None,
                    now=datetime.fromtimestamp(clock(), UTC),
                )
        except SettingValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
            ) from error
        return {"changes": diffs, **settings_page(session, group)}

    def channel_choices() -> dict[str, Any]:
        return {
            "channel_types": channel_admin.CHANNEL_TYPES,
            "authorizations": channel_admin.AUTHORIZATIONS,
            "gate_decisions": channel_admin.GATE_DECISIONS,
            "symbol_scopes": channel_admin.SYMBOL_SCOPES,
            "content_types": list(channel_admin.CONTENT_TYPES),
        }

    def channel_error(error: channel_admin.ChannelValueError) -> HTTPException:
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))

    @app.get("/api/channel-policies")
    def channel_policies(_username: CurrentUser, session: Db) -> dict[str, Any]:
        return {
            "items": channel_admin.list_channel_policies(session),
            "choices": channel_choices(),
        }

    @app.post("/api/channel-policies")
    def create_channel_policy(
        username: CurrentUser, session: Db, body: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            with session.begin():
                policy = channel_admin.create_channel(
                    session, body, actor=username, now=datetime.fromtimestamp(clock(), UTC)
                )
                created = channel_admin.channel_row(policy)
        except channel_admin.ChannelValueError as error:
            raise channel_error(error) from error
        return {"channel": created}

    @app.put("/api/channel-policies/{channel_id}/{topic_id}")
    def update_channel_policy(
        username: CurrentUser,
        session: Db,
        channel_id: int,
        topic_id: int,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            with session.begin():
                changes = channel_admin.update_channel(
                    session,
                    channel_id,
                    topic_id,
                    body,
                    actor=username,
                    now=datetime.fromtimestamp(clock(), UTC),
                )
        except channel_admin.ChannelValueError as error:
            raise channel_error(error) from error
        except LookupError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="找不到這個頻道"
            ) from error
        return {"changes": changes}

    @app.get("/api/pnl/summary")
    def pnl_summary(
        _username: CurrentUser,
        session: Db,
        symbol: str | None = None,
        channel_id: int | None = None,
        close_reason: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, Any]:
        flt = records.PnlFilter(symbol, channel_id, close_reason, since, until)
        return {
            "summary": records.pnl_summary(session, flt),
            "daily": records.pnl_daily(session, flt, TRADING_DAY_TZ_NAME),
        }

    @app.get("/api/pnl")
    def pnl(
        _username: CurrentUser,
        session: Db,
        symbol: str | None = None,
        channel_id: int | None = None,
        close_reason: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        flt = records.PnlFilter(symbol, channel_id, close_reason, since, until)
        return records.list_pnl(session, flt, Page(limit, offset))

    @app.get("/api/channels")
    def channels(_username: CurrentUser, session: Db) -> list[dict[str, Any]]:
        return records.list_channels(session)

    @app.get("/api/signals/facets")
    def signal_facets(_username: CurrentUser, session: Db) -> dict[str, Any]:
        return {"parser_versions": records.parser_versions(session)}

    @app.get("/api/signals")
    def signals(
        _username: CurrentUser,
        session: Db,
        parser_version: str | None = None,
        status_: Annotated[str | None, Query(alias="status")] = None,
        symbol: str | None = None,
        channel_id: int | None = None,
        decision: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        flt = records.SignalFilter(
            parser_version=parser_version,
            status=status_,
            symbol=symbol,
            channel_id=channel_id,
            decision=decision,
        )
        return records.list_signals(session, flt, Page(limit, offset))

    @app.get("/api/signals/{signal_row_id}")
    def signal(_username: CurrentUser, session: Db, signal_row_id: str) -> dict[str, Any]:
        return _not_found(records.signal_detail(session, signal_row_id))

    @app.get("/api/orders")
    def orders(
        _username: CurrentUser,
        session: Db,
        symbol: str | None = None,
        verdict: str | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        flt = records.OrderFilter(symbol=symbol, verdict=verdict, state=state)
        return records.list_orders(session, flt, Page(limit, offset))

    @app.get("/api/orders/{intent_id}")
    def order(_username: CurrentUser, session: Db, intent_id: str) -> dict[str, Any]:
        return _not_found(records.order_detail(session, intent_id))

    @app.get("/api/positions")
    def positions(_username: CurrentUser, session: Db) -> list[dict[str, Any]]:
        return records.list_open_positions(session, fetch_mark)

    @app.get("/api/theses")
    def theses(
        _username: CurrentUser,
        session: Db,
        channel_id: int | None = None,
        direction: str | None = None,
        status_: Annotated[str | None, Query(alias="status")] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        flt = records.ThesisFilter(channel_id=channel_id, direction=direction, status=status_)
        return records.list_theses(session, flt, Page(limit, offset))

    @app.get("/api/theses/{thesis_row_id}")
    def thesis(_username: CurrentUser, session: Db, thesis_row_id: str) -> dict[str, Any]:
        return _not_found(records.thesis_detail(session, thesis_row_id))

    @app.get("/api/audit/facets")
    def audit_facets(_username: CurrentUser, session: Db) -> dict[str, Any]:
        return records.audit_facets(session)

    @app.get("/api/audit")
    def audit(
        _username: CurrentUser,
        session: Db,
        event_type: str | None = None,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        flt = records.AuditFilter(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            since=since,
            until=until,
        )
        return records.list_audit(session, flt, Page(limit, offset))

    return app
