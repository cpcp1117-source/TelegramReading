from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from typing import Any
from urllib.parse import urlencode

import httpx

_SERVER_TIME_PATH = "/fapi/v1/time"
_ACCOUNT_PATH = "/fapi/v2/account"
_POSITION_RISK_PATH = "/fapi/v2/positionRisk"
_LEVERAGE_BRACKET_PATH = "/fapi/v1/leverageBracket"
_COMMISSION_RATE_PATH = "/fapi/v1/commissionRate"
_PREMIUM_INDEX_PATH = "/fapi/v1/premiumIndex"
_OPEN_ORDERS_PATH = "/fapi/v1/openOrders"
_USER_TRADES_PATH = "/fapi/v1/userTrades"


def _sign(secret: str, query_string: str) -> str:
    return hmac.new(secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()


def _signed_get(
    client: httpx.Client,
    path: str,
    *,
    api_key: str,
    api_secret: str,
    server_time_offset_ms: int,
    params: dict[str, str] | None = None,
    recv_window: int = 5000,
) -> Any:
    query: dict[str, str] = dict(params or {})
    query["timestamp"] = str(int(time.time() * 1000) + server_time_offset_ms)
    query["recvWindow"] = str(recv_window)
    query_string = urlencode(query)
    signature = _sign(api_secret, query_string)
    response = client.get(
        f"{path}?{query_string}&signature={signature}",
        headers={"X-MBX-APIKEY": api_key},
        timeout=10.0,
    )
    if response.status_code >= 400:
        raise SystemExit(f"{path} -> HTTP {response.status_code}: {response.text}")
    return response.json()


def _fetch_server_time_offset(client: httpx.Client) -> int:
    """Signed calls fail closed (`-1021`) if the local clock drifts from Binance's.

    Fetched once via the public, unsigned `/fapi/v1/time` endpoint -- no
    credential needed for this call.
    """
    local_before_ms = int(time.time() * 1000)
    response = client.get(_SERVER_TIME_PATH, timeout=10.0)
    response.raise_for_status()
    server_time_ms = int(response.json()["serverTime"])
    return server_time_ms - local_before_ms


def _load_credentials() -> tuple[str, str, str]:
    """Read Testnet credentials from the process environment only.

    Deliberately does not go through `telegram_trader.config.Settings`:
    per docs/phase-0/credential-handoff.md §5, the Binance Testnet key must
    be scoped to the Execution Gateway only, and this spike script has no
    Execution Gateway identity of its own -- it is a throwaway, read-only
    diagnostic run by hand, not a long-running service. Keeping it outside
    `Settings` means collector/control-bot/app never gain a code path that
    could accidentally read this credential.
    """
    api_key = os.environ.get("BINANCE_TESTNET_API_KEY")
    api_secret = os.environ.get("BINANCE_TESTNET_API_SECRET")
    base_url = os.environ.get("BINANCE_TESTNET_BASE_URL", "https://demo-fapi.binance.com")
    if not api_key or not api_secret:
        raise SystemExit(
            "BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET are not set in the "
            "process environment. Run this via scripts/binance-testnet-spike.ps1, which "
            "loads them from the local .env without echoing them."
        )
    return api_key, api_secret, base_url


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 6 Slice 2 contract spike: read-only signed GET calls against Binance "
            "USD(S)-M Futures Testnet to capture real account/position/leverage-bracket "
            "response shapes (CS-BN-001) and position-mode (CS-BN-002 precondition). "
            "Places no order of any kind."
        )
    )
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
        help="Symbol to fetch a leverage bracket for (default: BTCUSDT)",
    )
    args = parser.parse_args()

    api_key, api_secret, base_url = _load_credentials()

    with httpx.Client(base_url=base_url) as client:
        offset_ms = _fetch_server_time_offset(client)
        print(f"# server_time_offset_ms={offset_ms} base_url={base_url}", file=sys.stderr)

        account = _signed_get(
            client,
            _ACCOUNT_PATH,
            api_key=api_key,
            api_secret=api_secret,
            server_time_offset_ms=offset_ms,
        )
        position_risk = _signed_get(
            client,
            _POSITION_RISK_PATH,
            api_key=api_key,
            api_secret=api_secret,
            server_time_offset_ms=offset_ms,
        )
        leverage_bracket = _signed_get(
            client,
            _LEVERAGE_BRACKET_PATH,
            api_key=api_key,
            api_secret=api_secret,
            server_time_offset_ms=offset_ms,
            params={"symbol": args.symbol},
        )
        commission_rate = _signed_get(
            client,
            _COMMISSION_RATE_PATH,
            api_key=api_key,
            api_secret=api_secret,
            server_time_offset_ms=offset_ms,
            params={"symbol": args.symbol},
        )
        premium_index = client.get(
            _PREMIUM_INDEX_PATH, params={"symbol": args.symbol}, timeout=10.0
        )
        premium_index.raise_for_status()
        open_orders = _signed_get(
            client,
            _OPEN_ORDERS_PATH,
            api_key=api_key,
            api_secret=api_secret,
            server_time_offset_ms=offset_ms,
        )
        user_trades = _signed_get(
            client,
            _USER_TRADES_PATH,
            api_key=api_key,
            api_secret=api_secret,
            server_time_offset_ms=offset_ms,
            params={"symbol": args.symbol, "limit": "20"},
        )

    # account.positions/position_risk repeat all ~700+ listed symbols (mostly
    # flat/zero on a fresh Testnet account) -- only nonzero positions plus one
    # representative sample are worth keeping for a human/doc-writing pass.
    position_risk_nonzero = [p for p in position_risk if float(p.get("positionAmt", 0)) != 0]
    result = {
        "account": {k: v for k, v in account.items() if k != "positions"},
        "account_positions_sample": account["positions"][0] if account.get("positions") else None,
        "account_positions_count": len(account.get("positions", [])),
        "position_risk_sample": position_risk[0] if position_risk else None,
        "position_risk_nonzero": position_risk_nonzero,
        "position_risk_count": len(position_risk),
        "leverage_bracket": leverage_bracket,
        "commission_rate": commission_rate,
        "premium_index": premium_index.json(),
        "open_orders": open_orders,
        "user_trades": user_trades,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
