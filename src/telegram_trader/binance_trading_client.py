from __future__ import annotations

import contextlib
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from telegram_trader.config import Settings

_SERVER_TIME_PATH = "/fapi/v1/time"
_ACCOUNT_PATH = "/fapi/v2/account"
_POSITION_RISK_PATH = "/fapi/v2/positionRisk"
_LEVERAGE_PATH = "/fapi/v1/leverage"
_MARGIN_TYPE_PATH = "/fapi/v1/marginType"
_ORDER_PATH = "/fapi/v1/order"
_ALGO_ORDER_PATH = "/fapi/v1/algoOrder"

_MARGIN_TYPE_ALREADY_SET = -4046


class BinanceApiError(Exception):
    """A signed request's HTTP response carried a Binance error code/msg.

    `code`/`msg` are Binance's own fields (see `/fapi/*` error responses),
    not reformatted -- callers that need to tolerate a specific code (e.g.
    `set_margin_type`'s `-4046`) match on `code` directly.
    """

    def __init__(self, status_code: int, code: int | None, msg: str) -> None:
        self.status_code = status_code
        self.code = code
        self.msg = msg
        super().__init__(f"HTTP {status_code} code={code}: {msg}")


class BinanceTradingClient:
    """Signed Binance USDⓈ-M Futures Testnet client (FR-017/018/019, Phase 6 Slice 2a).

    Deliberately does not duplicate `binance_market_data.BinanceMarketDataClient`'s
    public endpoints (`exchangeInfo`/mark price) -- those stay unsigned and
    injected separately, preserving the public/signed trust boundary
    architecture.md draws. Every method here uses the account's own
    API key/secret and can move real (Testnet) money -- never merged with
    the public client.
    """

    def __init__(self, http_client: httpx.Client, *, api_key: str, api_secret: str) -> None:
        self._http_client = http_client
        self._api_key = api_key
        self._api_secret = api_secret
        self._server_time_offset_ms = self._fetch_server_time_offset()

    def _fetch_server_time_offset(self) -> int:
        """Signed calls fail closed (`-1021`) if the local clock drifts from Binance's.

        Fetched once at construction via the public, unsigned
        `/fapi/v1/time` endpoint -- no credential needed for this call.
        """
        local_before_ms = int(time.time() * 1000)
        response = self._http_client.get(_SERVER_TIME_PATH, timeout=10.0)
        response.raise_for_status()
        server_time_ms = int(response.json()["serverTime"])
        return server_time_ms - local_before_ms

    def _sign(self, query_string: str) -> str:
        return hmac.new(
            self._api_secret.encode(), query_string.encode(), hashlib.sha256
        ).hexdigest()

    def _signed_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        recv_window: int = 5000,
    ) -> Any:
        query: dict[str, str] = {
            key: str(value) for key, value in (params or {}).items() if value is not None
        }
        query["timestamp"] = str(int(time.time() * 1000) + self._server_time_offset_ms)
        query["recvWindow"] = str(recv_window)
        query_string = urlencode(query)
        signature = self._sign(query_string)
        url = f"{path}?{query_string}&signature={signature}"
        response = self._http_client.request(
            method, url, headers={"X-MBX-APIKEY": self._api_key}, timeout=10.0
        )
        if response.status_code >= 400:
            body: dict[str, Any] = {}
            with contextlib.suppress(ValueError):
                body = response.json()
            raise BinanceApiError(
                response.status_code, body.get("code"), body.get("msg", response.text)
            )
        return response.json()

    def get_account(self) -> dict[str, Any]:
        result: dict[str, Any] = self._signed_request("GET", _ACCOUNT_PATH)
        return result

    def get_position_risk(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        result: list[dict[str, Any]] = self._signed_request(
            "GET", _POSITION_RISK_PATH, params=params
        )
        return result

    def set_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        result: dict[str, Any] = self._signed_request(
            "POST", _LEVERAGE_PATH, params={"symbol": symbol, "leverage": leverage}
        )
        return result

    def set_margin_type(self, symbol: str, margin_type: str) -> dict[str, Any]:
        """`margin_type` is `"ISOLATED"` or `"CROSSED"`. Tolerates Binance's

        idempotent-no-op error (`-4046`, "No need to change margin type")
        as success, not failure -- calling this every run must be safe.
        """
        try:
            result: dict[str, Any] = self._signed_request(
                "POST", _MARGIN_TYPE_PATH, params={"symbol": symbol, "marginType": margin_type}
            )
            return result
        except BinanceApiError as error:
            if error.code == _MARGIN_TYPE_ALREADY_SET:
                return {"code": error.code, "msg": error.msg}
            raise

    def place_order(self, **params: Any) -> dict[str, Any]:
        result: dict[str, Any] = self._signed_request("POST", _ORDER_PATH, params=params)
        return result

    def get_order(
        self,
        symbol: str,
        *,
        orig_client_order_id: str | None = None,
        order_id: int | None = None,
    ) -> dict[str, Any]:
        if orig_client_order_id is None and order_id is None:
            raise ValueError("get_order requires orig_client_order_id or order_id")
        params: dict[str, Any] = {"symbol": symbol}
        if orig_client_order_id is not None:
            params["origClientOrderId"] = orig_client_order_id
        if order_id is not None:
            params["orderId"] = order_id
        result: dict[str, Any] = self._signed_request("GET", _ORDER_PATH, params=params)
        return result

    def place_algo_order(self, **params: Any) -> dict[str, Any]:
        """`POST /fapi/v1/algoOrder`, `algoType=CONDITIONAL` -- the *only* way to place a

        `STOP_MARKET`/`TAKE_PROFIT_MARKET` conditional order via the public
        USDⓈ-M Futures API. Confirmed live 2026-09-28: `place_order` (the
        plain `/fapi/v1/order` endpoint) rejects `type=STOP_MARKET` outright
        with `-4120 Order type not supported for this endpoint. Please use
        the Algo Order API endpoints instead.` -- this overturns this
        project's earlier (incorrect) conclusion from observing an
        already-placed order's *shape* via `GET /fapi/v1/openOrders`, which
        never actually exercised the *placement* contract. See
        api-contract-inventory.md's CS-BN-002 correction.
        """
        params.setdefault("algoType", "CONDITIONAL")
        result: dict[str, Any] = self._signed_request("POST", _ALGO_ORDER_PATH, params=params)
        return result

    def get_algo_order(
        self, *, client_algo_id: str | None = None, algo_id: int | None = None
    ) -> dict[str, Any]:
        if client_algo_id is None and algo_id is None:
            raise ValueError("get_algo_order requires client_algo_id or algo_id")
        params: dict[str, Any] = {}
        if client_algo_id is not None:
            params["clientAlgoId"] = client_algo_id
        if algo_id is not None:
            params["algoId"] = algo_id
        result: dict[str, Any] = self._signed_request("GET", _ALGO_ORDER_PATH, params=params)
        return result

    def close(self) -> None:
        self._http_client.close()


def create_trading_client(settings: Settings) -> BinanceTradingClient:
    if settings.binance_testnet_api_key is None or settings.binance_testnet_api_secret is None:
        raise ValueError("Binance Testnet credentials are unavailable")
    http_client = httpx.Client(base_url=settings.binance_testnet_base_url, timeout=10.0)
    return BinanceTradingClient(
        http_client,
        api_key=settings.binance_testnet_api_key.get_secret_value(),
        api_secret=settings.binance_testnet_api_secret.get_secret_value(),
    )
