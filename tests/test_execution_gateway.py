from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from telegram_trader.binance_trading_client import BinanceApiError
from telegram_trader.execution_gateway import (
    PlacementAttempt,
    _emergency_close,
    _place_and_confirm_protection,
    _place_and_confirm_take_profit,
    _place_order_idempotent,
    _place_with_one_retry,
    _poll_for_fill,
    check_advisory_conflicts,
    client_order_id_for,
    close_side_for,
    entry_side_for,
    retry_client_order_id_for,
)
from telegram_trader.risk_engine import REASON_MAX_POSITIONS_REACHED, REASON_SYMBOL_CONFLICT

# --- entry_side_for / close_side_for ---


def test_entry_side_for_long_is_buy() -> None:
    assert entry_side_for("LONG") == "BUY"


def test_entry_side_for_short_is_sell() -> None:
    assert entry_side_for("SHORT") == "SELL"


def test_close_side_for_is_opposite_of_entry_side() -> None:
    assert close_side_for("LONG") == "SELL"
    assert close_side_for("SHORT") == "BUY"


# --- client_order_id_for ---


def test_client_order_id_for_is_deterministic_and_within_binance_limit() -> None:
    first = client_order_id_for("intent-1", "ENTRY")
    second = client_order_id_for("intent-1", "ENTRY")
    assert first == second
    assert len(first) <= 36


def test_client_order_id_for_differs_by_purpose_and_intent() -> None:
    entry = client_order_id_for("intent-1", "ENTRY")
    protection = client_order_id_for("intent-1", "PROTECTION")
    other_intent = client_order_id_for("intent-2", "ENTRY")
    assert entry != protection
    assert entry != other_intent
    assert entry.startswith("en")
    assert protection.startswith("pr")


# --- check_advisory_conflicts (real-data BR-007/010 recheck, never blocks) ---


def _position_row(symbol: str, amount: str) -> dict[str, Any]:
    return {"symbol": symbol, "positionAmt": amount}


def test_check_advisory_conflicts_empty_when_flat() -> None:
    positions = [_position_row("BTCUSDT", "0"), _position_row("ETHUSDT", "0")]
    advisories = check_advisory_conflicts(
        positions, symbol="XRPUSDT", side="LONG", max_concurrent_positions=3
    )
    assert advisories == []


def test_check_advisory_conflicts_flags_symbol_conflict() -> None:
    positions = [_position_row("XRPUSDT", "-100")]
    advisories = check_advisory_conflicts(
        positions, symbol="XRPUSDT", side="LONG", max_concurrent_positions=3
    )
    assert REASON_SYMBOL_CONFLICT in advisories


def test_check_advisory_conflicts_allows_same_side_same_symbol() -> None:
    positions = [_position_row("XRPUSDT", "100")]
    advisories = check_advisory_conflicts(
        positions, symbol="XRPUSDT", side="LONG", max_concurrent_positions=3
    )
    assert REASON_SYMBOL_CONFLICT not in advisories


def test_check_advisory_conflicts_flags_max_positions_reached() -> None:
    positions = [
        _position_row("BTCUSDT", "1"),
        _position_row("ETHUSDT", "1"),
        _position_row("SOLUSDT", "1"),
    ]
    advisories = check_advisory_conflicts(
        positions, symbol="XRPUSDT", side="LONG", max_concurrent_positions=3
    )
    assert REASON_MAX_POSITIONS_REACHED in advisories


def test_check_advisory_conflicts_never_blocks_just_returns_codes() -> None:
    """The whole point of the 2026-09-25 policy: this is advisory data, not a

    verdict -- the function has no concept of rejecting, only reporting.
    """
    positions = [
        _position_row("XRPUSDT", "-1"),
        _position_row("BTCUSDT", "1"),
        _position_row("ETHUSDT", "1"),
    ]
    advisories = check_advisory_conflicts(
        positions, symbol="XRPUSDT", side="LONG", max_concurrent_positions=2
    )
    assert set(advisories) == {REASON_SYMBOL_CONFLICT, REASON_MAX_POSITIONS_REACHED}


# --- _place_order_idempotent (FR-018) ---


class _FakeTradingClient:
    def __init__(self) -> None:
        self.place_calls: list[dict[str, Any]] = []
        self.get_order_calls: list[dict[str, Any]] = []
        self.place_algo_calls: list[dict[str, Any]] = []
        self.get_algo_order_calls: list[dict[str, Any]] = []
        self._place_side_effect: Exception | None = None
        self._place_response: dict[str, Any] = {"status": "NEW", "orderId": 1, "executedQty": "0"}
        self._get_order_responses: list[dict[str, Any]] = []
        self._get_order_errors: list[Exception] = []
        self._place_algo_side_effect: Exception | None = None
        self._place_algo_response: dict[str, Any] = {"algoStatus": "NEW", "algoId": 1}
        self._get_algo_order_responses: list[dict[str, Any]] = []
        self._get_algo_order_errors: list[Exception] = []

    def place_order(self, **params: Any) -> dict[str, Any]:
        self.place_calls.append(params)
        if self._place_side_effect is not None:
            raise self._place_side_effect
        return self._place_response

    def get_order(self, symbol: str, *, orig_client_order_id: str) -> dict[str, Any]:
        self.get_order_calls.append(
            {"symbol": symbol, "orig_client_order_id": orig_client_order_id}
        )
        if self._get_order_errors:
            raise self._get_order_errors.pop(0)
        if self._get_order_responses:
            return self._get_order_responses.pop(0)
        return {"status": "NEW", "executedQty": "0"}

    def place_algo_order(self, **params: Any) -> dict[str, Any]:
        self.place_algo_calls.append(params)
        if self._place_algo_side_effect is not None:
            raise self._place_algo_side_effect
        return self._place_algo_response

    def get_algo_order(self, *, client_algo_id: str) -> dict[str, Any]:
        self.get_algo_order_calls.append({"client_algo_id": client_algo_id})
        if self._get_algo_order_errors:
            raise self._get_algo_order_errors.pop(0)
        if self._get_algo_order_responses:
            return self._get_algo_order_responses.pop(0)
        return {"algoStatus": "NEW"}


def _order_does_not_exist() -> BinanceApiError:
    return BinanceApiError(400, -2013, "Order does not exist.")


def test_place_order_idempotent_places_normally_when_no_error() -> None:
    client = _FakeTradingClient()
    client._get_order_errors = [_order_does_not_exist()]
    result = _place_order_idempotent(
        client, "BTCUSDT", "abc123", side="BUY", type="MARKET", quantity="1"
    )
    assert result["orderId"] == 1
    assert len(client.place_calls) == 1
    assert len(client.get_order_calls) == 1  # the pre-submit lookup only


def test_place_order_idempotent_adopts_already_filled_order_instead_of_placing() -> None:
    """P6-NOTICE-013: Binance accepts a reused clientOrderId once the original has
    filled, so a resubmission would open a second real position -- never POST then."""
    client = _FakeTradingClient()
    client._get_order_responses = [{"status": "FILLED", "orderId": 7, "executedQty": "3287"}]

    result = _place_order_idempotent(
        client, "XRPUSDT", "abc123", side="BUY", type="MARKET", quantity="3287"
    )

    assert result["orderId"] == 7
    assert client.place_calls == []


def test_place_order_idempotent_never_places_when_pre_submit_lookup_fails() -> None:
    """Only a definite -2013 permits the POST; an unknown lookup outcome must not."""
    client = _FakeTradingClient()
    client._get_order_errors = [BinanceApiError(503, None, "service unavailable")]

    with pytest.raises(BinanceApiError):
        _place_order_idempotent(
            client, "XRPUSDT", "abc123", side="BUY", type="MARKET", quantity="1"
        )

    assert client.place_calls == []


def test_place_order_idempotent_queries_instead_of_retrying_on_failure() -> None:
    """FR-018: a network-level failure must query-before-resend, never blind-retry the POST."""
    client = _FakeTradingClient()
    client._place_side_effect = BinanceApiError(500, None, "timeout")
    client._get_order_errors = [_order_does_not_exist()]
    client._get_order_responses = [{"status": "NEW", "orderId": 42, "executedQty": "0"}]

    result = _place_order_idempotent(
        client, "BTCUSDT", "abc123", side="BUY", type="MARKET", quantity="1"
    )

    assert result["orderId"] == 42
    assert len(client.place_calls) == 1  # exactly one POST attempt, never a second
    assert len(client.get_order_calls) == 2  # pre-submit lookup + post-failure lookup


# --- _poll_for_fill ---


def test_poll_for_fill_returns_immediately_when_already_filled() -> None:
    client = _FakeTradingClient()
    client._get_order_responses = [{"status": "FILLED", "executedQty": "5"}]
    sleeps: list[float] = []

    order = _poll_for_fill(
        client,
        "BTCUSDT",
        "abc123",
        timeout_seconds=10,
        poll_interval_seconds=1,
        sleep=sleeps.append,
    )

    assert order["executedQty"] == "5"
    assert sleeps == []


def test_poll_for_fill_polls_until_filled() -> None:
    client = _FakeTradingClient()
    client._get_order_responses = [
        {"status": "NEW", "executedQty": "0"},
        {"status": "NEW", "executedQty": "0"},
        {"status": "FILLED", "executedQty": "5"},
    ]
    sleeps: list[float] = []
    times = iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])

    order = _poll_for_fill(
        client,
        "BTCUSDT",
        "abc123",
        timeout_seconds=10,
        poll_interval_seconds=1,
        sleep=sleeps.append,
        now_monotonic=lambda: next(times),
    )

    assert order["executedQty"] == "5"
    assert len(sleeps) == 2


def test_poll_for_fill_gives_up_at_timeout_still_unfilled() -> None:
    client = _FakeTradingClient()
    client._get_order_responses = [{"status": "NEW", "executedQty": "0"}] * 5
    times = iter([0.0, 1.0, 2.0, 11.0])  # exceeds a 10s timeout on the 4th check

    order = _poll_for_fill(
        client,
        "BTCUSDT",
        "abc123",
        timeout_seconds=10,
        poll_interval_seconds=1,
        sleep=lambda _seconds: None,
        now_monotonic=lambda: next(times),
    )

    assert order["executedQty"] == "0"


# --- _place_and_confirm_protection (FR-019) ---


def test_place_and_confirm_protection_returns_order_when_accepted() -> None:
    """Placed via the Algo Order endpoint -- the plain order endpoint rejects

    `STOP_MARKET` outright (confirmed live 2026-09-28, `-4120`).
    """
    client = _FakeTradingClient()
    client._place_algo_response = {"algoStatus": "NEW", "algoId": 2}

    attempt = _place_and_confirm_protection(
        client,
        symbol="BTCUSDT",
        side="LONG",
        client_order_id="pr123",
        quantity=Decimal("1"),
        stop_price=Decimal("90"),
        deadline_monotonic=5.0,
        poll_interval_seconds=1,
        sleep=lambda _seconds: None,
        now_monotonic=lambda: 0.0,
    )

    assert attempt.order is not None
    assert attempt.order["algoId"] == 2
    # Placed as a reduceOnly close on the opposite side of the entry.
    assert client.place_algo_calls[0]["side"] == "SELL"
    assert client.place_algo_calls[0]["type"] == "STOP_MARKET"
    assert client.place_algo_calls[0]["reduceOnly"] == "true"


def test_place_and_confirm_protection_returns_none_when_placement_raises() -> None:
    client = _FakeTradingClient()
    client._place_algo_side_effect = BinanceApiError(
        400, -4120, "Order type not supported for this endpoint"
    )

    attempt = _place_and_confirm_protection(
        client,
        symbol="BTCUSDT",
        side="LONG",
        client_order_id="pr123",
        quantity=Decimal("1"),
        stop_price=Decimal("90"),
        deadline_monotonic=5.0,
        poll_interval_seconds=1,
        sleep=lambda _seconds: None,
        now_monotonic=lambda: 0.0,
    )

    assert attempt.order is None
    assert attempt.failure_reason


def test_place_and_confirm_protection_returns_none_when_rejected_before_deadline() -> None:
    client = _FakeTradingClient()
    client._place_algo_response = {"algoStatus": "REJECTED"}
    client._get_algo_order_responses = [{"algoStatus": "REJECTED"}]
    times = iter([0.0, 6.0])  # exceeds the 5.0 deadline on the second check, still rejected

    attempt = _place_and_confirm_protection(
        client,
        symbol="BTCUSDT",
        side="LONG",
        client_order_id="pr123",
        quantity=Decimal("1"),
        stop_price=Decimal("90"),
        deadline_monotonic=5.0,
        poll_interval_seconds=1,
        sleep=lambda _seconds: None,
        now_monotonic=lambda: next(times),
    )

    assert attempt.order is None
    assert attempt.failure_reason


# --- _emergency_close ---


def test_emergency_close_places_reduce_only_market_order_on_opposite_side() -> None:
    client = _FakeTradingClient()
    client._place_response = {"status": "FILLED", "orderId": 3}

    result = _emergency_close(
        client, symbol="BTCUSDT", side="LONG", client_order_id="ec123", quantity=Decimal("1")
    )

    assert result["orderId"] == 3
    assert client.place_calls[0]["side"] == "SELL"
    assert client.place_calls[0]["type"] == "MARKET"
    assert client.place_calls[0]["reduceOnly"] == "true"


# --- _place_and_confirm_take_profit (added 2026-09-29, explicit user request) ---


def test_place_and_confirm_take_profit_returns_order_when_accepted() -> None:
    client = _FakeTradingClient()
    client._place_algo_response = {"algoStatus": "NEW", "algoId": 5}

    attempt = _place_and_confirm_take_profit(
        client,
        symbol="BTCUSDT",
        side="LONG",
        client_order_id="tp123",
        quantity=Decimal("1"),
        target_price=Decimal("120"),
    )

    assert attempt.order is not None
    assert attempt.order["algoId"] == 5
    assert client.place_algo_calls[0]["side"] == "SELL"
    assert client.place_algo_calls[0]["type"] == "TAKE_PROFIT_MARKET"
    assert client.place_algo_calls[0]["reduceOnly"] == "true"


def test_place_and_confirm_take_profit_returns_none_when_placement_raises() -> None:
    client = _FakeTradingClient()
    client._place_algo_side_effect = BinanceApiError(400, -4131, "some rejection")

    attempt = _place_and_confirm_take_profit(
        client,
        symbol="BTCUSDT",
        side="LONG",
        client_order_id="tp123",
        quantity=Decimal("1"),
        target_price=Decimal("120"),
    )

    assert attempt.order is None
    assert attempt.failure_reason


def test_place_and_confirm_take_profit_returns_none_when_rejected() -> None:
    """No retry/deadline loop for take-profit (unlike protection) -- a single

    immediate `REJECTED` is final.
    """
    client = _FakeTradingClient()
    client._place_algo_response = {"algoStatus": "REJECTED"}

    attempt = _place_and_confirm_take_profit(
        client,
        symbol="BTCUSDT",
        side="LONG",
        client_order_id="tp123",
        quantity=Decimal("1"),
        target_price=Decimal("120"),
    )

    assert attempt.order is None
    assert attempt.failure_reason


# --- _place_with_one_retry (2026-09-29, explicit user request) ---


def test_retry_client_order_id_differs_from_first_and_fits_binance_limit() -> None:
    first = client_order_id_for("intent-1", "PROTECTION")
    retry = retry_client_order_id_for("intent-1", "PROTECTION")
    assert retry != first
    assert len(retry) == len(first) == 26
    assert retry == retry_client_order_id_for("intent-1", "PROTECTION")


class _ScriptedPlacer:
    def __init__(self, *attempts: PlacementAttempt) -> None:
        self._attempts = list(attempts)
        self.client_order_ids: list[str] = []

    def __call__(self, client_order_id: str) -> PlacementAttempt:
        self.client_order_ids.append(client_order_id)
        return self._attempts.pop(0)


def test_place_with_one_retry_first_success_places_once_and_skips_retry_notice() -> None:
    client = _FakeTradingClient()
    placer = _ScriptedPlacer(PlacementAttempt({"algoStatus": "NEW", "algoId": 1}))
    first_failures: list[str] = []

    attempt, attempts, used_id = _place_with_one_retry(
        client,
        placer,
        client_order_id="pr1",
        retry_client_order_id="prr1",
        on_first_failure=first_failures.append,
        sleep=lambda _seconds: None,
    )

    assert attempt.order is not None
    assert (attempts, used_id) == (1, "pr1")
    assert placer.client_order_ids == ["pr1"]
    assert first_failures == []


def test_place_with_one_retry_retries_once_with_new_id_and_reports_first_failure() -> None:
    client = _FakeTradingClient()
    client._get_algo_order_errors = [BinanceApiError(400, -2013, "Order does not exist.")]
    placer = _ScriptedPlacer(
        PlacementAttempt(None, "placement raised timeout"),
        PlacementAttempt({"algoStatus": "NEW", "algoId": 9}),
    )
    first_failures: list[str] = []
    sleeps: list[float] = []

    attempt, attempts, used_id = _place_with_one_retry(
        client,
        placer,
        client_order_id="pr1",
        retry_client_order_id="prr1",
        on_first_failure=first_failures.append,
        sleep=sleeps.append,
        now_monotonic=lambda: 0.0,
    )

    assert attempt.order is not None and attempt.order["algoId"] == 9
    assert (attempts, used_id) == (2, "prr1")
    assert placer.client_order_ids == ["pr1", "prr1"]
    assert first_failures == ["placement raised timeout"]  # notified before retrying
    assert sleeps == [1.0]


def test_place_with_one_retry_reports_both_reasons_when_retry_also_fails() -> None:
    client = _FakeTradingClient()
    client._get_algo_order_errors = [BinanceApiError(400, -2013, "Order does not exist.")]
    placer = _ScriptedPlacer(
        PlacementAttempt(None, "algoStatus=REJECTED"),
        PlacementAttempt(None, "algoStatus=EXPIRED"),
    )

    attempt, attempts, _used_id = _place_with_one_retry(
        client,
        placer,
        client_order_id="pr1",
        retry_client_order_id="prr1",
        on_first_failure=lambda _reason: None,
        sleep=lambda _seconds: None,
        now_monotonic=lambda: 0.0,
    )

    assert attempt.order is None
    assert attempts == 2
    assert attempt.failure_reason == "retry: algoStatus=EXPIRED (first: algoStatus=REJECTED)"


def test_place_with_one_retry_adopts_order_that_landed_despite_reported_failure() -> None:
    """A placement that raised (e.g. a timeout) may still have been accepted; retrying
    then would stack a second reduce-only order on the same position."""
    client = _FakeTradingClient()
    client._get_algo_order_responses = [{"algoStatus": "NEW", "algoId": 4}]
    placer = _ScriptedPlacer(PlacementAttempt(None, "placement raised timeout"))
    first_failures: list[str] = []

    attempt, attempts, used_id = _place_with_one_retry(
        client,
        placer,
        client_order_id="pr1",
        retry_client_order_id="prr1",
        on_first_failure=first_failures.append,
        sleep=lambda _seconds: None,
    )

    assert attempt.order is not None and attempt.order["algoId"] == 4
    assert (attempts, used_id) == (1, "pr1")
    assert placer.client_order_ids == ["pr1"]
    assert first_failures == []


def test_place_with_one_retry_skips_retry_when_5_second_window_has_passed() -> None:
    client = _FakeTradingClient()
    client._get_algo_order_errors = [BinanceApiError(400, -2013, "Order does not exist.")]
    placer = _ScriptedPlacer(PlacementAttempt(None, "algoStatus=REJECTED"))
    times = iter([0.0, 5.5])  # failure at t=0, retry would start at t=5.5

    attempt, attempts, _used_id = _place_with_one_retry(
        client,
        placer,
        client_order_id="pr1",
        retry_client_order_id="prr1",
        on_first_failure=lambda _reason: None,
        sleep=lambda _seconds: None,
        now_monotonic=lambda: next(times),
    )

    assert attempt.order is None
    assert attempts == 1
    assert placer.client_order_ids == ["pr1"]
    assert attempt.failure_reason is not None and "not started within 5s" in attempt.failure_reason
