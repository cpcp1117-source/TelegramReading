from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
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
_ACTIVE_STATUS = "TRADING"
_PERPETUAL_CONTRACT_TYPE = "PERPETUAL"


class BinanceMarketDataClient:
    """Thin synchronous wrapper over Binance's public USD(S)-M futures market data.

    No API key is used or accepted -- `exchangeInfo` is a public endpoint.
    Exposes exactly the one method production code calls, matching this
    project's `FakeXClient` test convention (a `FakeMarketDataClient`
    implementing only `get_exchange_info` can stand in for this class in
    tests, no mocking library needed).
    """

    def __init__(self, http_client: httpx.Client) -> None:
        self._http_client = http_client

    def get_exchange_info(self) -> dict[str, Any]:
        response = self._http_client.get(_EXCHANGE_INFO_PATH, timeout=10.0)
        response.raise_for_status()
        result: dict[str, Any] = response.json()
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
