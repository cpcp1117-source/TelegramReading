from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.config import Settings
from telegram_trader.models import BinanceSymbolSnapshot
from telegram_trader.normalization import ExchangeSnapshot

LOGGER = logging.getLogger(__name__)

_EXCHANGE_INFO_PATH = "/fapi/v1/exchangeInfo"
_MARK_PRICE_PATH = "/fapi/v1/premiumIndex"
_KLINES_PATH = "/fapi/v1/klines"
_ACTIVE_STATUS = "TRADING"
_PERPETUAL_CONTRACT_TYPE = "PERPETUAL"


class BinanceMarketDataClient:
    """Thin synchronous wrapper over Binance's public USD(S)-M futures market data.

    No API key is used or accepted -- `exchangeInfo` and `premiumIndex` (mark
    price) are both public endpoints, per api-contract-inventory.md's public
    endpoint list. Exposes exactly the methods production code calls,
    matching this project's `FakeXClient` test convention (a
    `FakeMarketDataClient` implementing only these methods can stand in for
    this class in tests, no mocking library needed).
    """

    def __init__(self, http_client: httpx.Client) -> None:
        self._http_client = http_client

    def get_exchange_info(self) -> dict[str, Any]:
        response = self._http_client.get(_EXCHANGE_INFO_PATH, timeout=10.0)
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    def get_mark_price(self, symbol: str) -> dict[str, Any]:
        """One symbol's current mark price -- Risk Engine's freshness/sizing input.

        Distinct from `exchangeInfo` (symbol metadata, no price at all):
        this is the `market_snapshot` entity system-spec.md deferred to
        Phase 6 (see migration `0011`'s own docstring). Mark price, not last
        trade price, matches Binance's own liquidation/margin math.
        """
        response = self._http_client.get(_MARK_PRICE_PATH, params={"symbol": symbol}, timeout=10.0)
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    def get_klines(self, symbol: str, *, start_time_ms: int, limit: int = 1) -> list[list[Any]]:
        """1-minute klines starting at `start_time_ms` -- BR-006's price-deviation

        reference. Public `/fapi/v1/klines`, no credential. Each row's index
        4 is that minute's close price (string).
        """
        response = self._http_client.get(
            _KLINES_PATH,
            params={"symbol": symbol, "interval": "1m", "startTime": start_time_ms, "limit": limit},
            timeout=10.0,
        )
        response.raise_for_status()
        result: list[list[Any]] = response.json()
        return result

    def close(self) -> None:
        self._http_client.close()


def create_market_data_client(settings: Settings) -> BinanceMarketDataClient:
    http_client = httpx.Client(base_url=settings.binance_api_base_url, timeout=10.0)
    return BinanceMarketDataClient(http_client)


def parse_active_perpetual_symbols(exchange_info: dict[str, Any]) -> frozenset[str]:
    """Every symbol that is an actively trading USD(S)-M perpetual contract.

    Pure function: filters `exchange_info["symbols"]` by `status=="TRADING"`
    and `contractType=="PERPETUAL"` only -- no quote-asset filtering here,
    since real Binance data already only lists USDT/USDC-margined pairs
    under this contract type on this endpoint.
    """
    active: set[str] = set()
    for entry in exchange_info.get("symbols", []):
        if entry.get("status") == _ACTIVE_STATUS and entry.get("contractType") == (
            _PERPETUAL_CONTRACT_TYPE
        ):
            active.add(entry["symbol"])
    return frozenset(active)


@dataclass(frozen=True, slots=True)
class SymbolFilters:
    """Rounding inputs for one symbol (FR-017, Phase 6 Slice 2a) -- `tick_size`

    from `PRICE_FILTER`, `step_size` from `LOT_SIZE`. Slice 2a deliberately
    uses `LOT_SIZE` (not the separate, sometimes looser `MARKET_LOT_SIZE`)
    for both MARKET and LIMIT entries -- rounding to the stricter of the
    two step sizes is always still a valid quantity for either order type
    on real Binance configurations, and it avoids needing two separate
    rounding paths for one symbol.
    """

    tick_size: Decimal
    step_size: Decimal


def get_symbol_filters(exchange_info: dict[str, Any], symbol: str) -> SymbolFilters:
    """Raises if `symbol` is missing or lacks `PRICE_FILTER`/`LOT_SIZE` -- fail

    closed (BR-012): never silently fall back to an unrounded price/quantity.
    """
    for entry in exchange_info.get("symbols", []):
        if entry.get("symbol") != symbol:
            continue
        tick_size: Decimal | None = None
        step_size: Decimal | None = None
        for filt in entry.get("filters", []):
            if filt.get("filterType") == "PRICE_FILTER":
                tick_size = Decimal(str(filt["tickSize"]))
            elif filt.get("filterType") == "LOT_SIZE":
                step_size = Decimal(str(filt["stepSize"]))
        if tick_size is None or step_size is None:
            raise ValueError(f"{symbol!r} exchangeInfo is missing PRICE_FILTER/LOT_SIZE")
        return SymbolFilters(tick_size=tick_size, step_size=step_size)
    raise ValueError(f"{symbol!r} not found in exchangeInfo")


def _round_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Truncates down to the nearest multiple of `step` -- matches Binance's own

    step-size truncation convention (never rounds up past a filter limit).
    """
    if step == 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def round_price(price: Decimal, filters: SymbolFilters) -> Decimal:
    return _round_to_step(price, filters.tick_size)


def round_quantity(quantity: Decimal, filters: SymbolFilters) -> Decimal:
    return _round_to_step(quantity, filters.step_size)


def compute_snapshot_id(fetched_at: datetime, active_symbols: frozenset[str]) -> str:
    """Deterministic id: same `(fetched_at, active_symbols)` always hashes the same."""
    canonical = fetched_at.isoformat() + "|" + ",".join(sorted(active_symbols))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def refresh_snapshot(
    client: Any,
    session_factory: sessionmaker[Session],
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> BinanceSymbolSnapshot:
    """Fetch `exchangeInfo` once and append a new snapshot row.

    Not a dedup mechanism: `snapshot_id` bakes in `fetched_at`, so two
    close-together refreshes with an identical symbol set still produce
    distinct rows. `on_conflict_do_nothing` here is only a defensive guard
    against a literal hash collision, not intentional deduplication --
    `load_latest_snapshot` always reads the newest row by `fetched_at`, so
    duplicate-looking rows are harmless.
    """
    fetched_at = clock()
    exchange_info = client.get_exchange_info()
    active_symbols = parse_active_perpetual_symbols(exchange_info)
    snapshot_id = compute_snapshot_id(fetched_at, active_symbols)

    with session_factory.begin() as session:
        session.execute(
            pg_insert(BinanceSymbolSnapshot)
            .values(
                snapshot_id=snapshot_id,
                fetched_at=fetched_at,
                active_symbols=sorted(active_symbols),
                symbol_count=len(active_symbols),
            )
            .on_conflict_do_nothing(index_elements=["snapshot_id"])
        )
    return BinanceSymbolSnapshot(
        snapshot_id=snapshot_id,
        fetched_at=fetched_at,
        active_symbols=sorted(active_symbols),
        symbol_count=len(active_symbols),
    )


def load_latest_snapshot(
    session: Session,
    *,
    clock: Callable[[], datetime],
    max_age_seconds: float,
) -> ExchangeSnapshot | None:
    """The newest snapshot, or `None` if there is none yet or it is stale.

    Staleness is decided here, once, with an injectable clock -- the only
    place in this slice that reads a clock or a max-age threshold.
    Everything downstream (`resolve_symbol`/`normalize_message`) only ever
    sees `ExchangeSnapshot | None`, with no time dependency of its own.
    """
    row = session.scalars(
        select(BinanceSymbolSnapshot).order_by(BinanceSymbolSnapshot.fetched_at.desc()).limit(1)
    ).first()
    if row is None:
        return None
    age_seconds = (clock() - row.fetched_at).total_seconds()
    if age_seconds > max_age_seconds:
        return None
    return ExchangeSnapshot(fetched_at=row.fetched_at, active_symbols=frozenset(row.active_symbols))


@dataclass(frozen=True, slots=True)
class MarketPriceQuote:
    """One point-in-time mark-price observation -- Risk Engine's freshness/sizing input.

    Not persisted as its own versioned table (unlike `binance_symbol_snapshots`):
    a `risk_decision` row is itself already immutable and append-only, so
    recording `market_price`/`market_price_fetched_at` directly on it is
    sufficient for replay (Data Invariant #5) without a redundant snapshot
    table nothing else would ever read.
    """

    symbol: str
    price: Decimal
    fetched_at: datetime


def fetch_mark_price(
    client: Any,
    symbol: str,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> MarketPriceQuote:
    """Fetch one symbol's current mark price. Raises on any malformed response --

    fail closed, never silently substitute a stale or default price for a
    Risk Engine sizing/freshness decision (BR-012).
    """
    fetched_at = clock()
    payload = client.get_mark_price(symbol)
    raw_price = payload.get("markPrice")
    if raw_price is None:
        raise ValueError(f"Binance premiumIndex response for {symbol!r} has no markPrice")
    return MarketPriceQuote(symbol=symbol, price=Decimal(str(raw_price)), fetched_at=fetched_at)


def fetch_reference_price(client: Any, symbol: str, *, at: datetime) -> Decimal:
    """The 1-minute kline close price nearest to `at` -- BR-006's price-deviation

    reference point for a MARKET-entry signal. Replaces a raw elapsed-time
    freshness check (see risk_engine.py's `evaluate_trade_intent`): a signal
    approved several minutes after posting is not inherently unsafe if the
    price barely moved in that window, and a signal approved within seconds
    can still be unsafe if the price already jumped. Raises on any
    malformed/empty response -- fail closed (BR-012), same convention as
    `fetch_mark_price`.
    """
    start_time_ms = int(at.timestamp() * 1000)
    rows = client.get_klines(symbol, start_time_ms=start_time_ms, limit=1)
    if not rows or len(rows[0]) < 5:
        raise ValueError(f"Binance klines response for {symbol!r} at {at.isoformat()} is empty")
    return Decimal(str(rows[0][4]))
