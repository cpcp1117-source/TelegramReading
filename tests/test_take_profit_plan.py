from __future__ import annotations

from decimal import Decimal

from telegram_trader.binance_market_data import SymbolFilters
from telegram_trader.take_profit_plan import plan_take_profit_levels

D = Decimal
FILTERS = SymbolFilters(tick_size=D("0.0001"), step_size=D("0.1"), min_qty=D("0.1"))


def _plan(targets: list[str], quantity: str, filters: SymbolFilters = FILTERS):  # type: ignore[no-untyped-def]
    return plan_take_profit_levels([D(t) for t in targets], D(quantity), filters)


def test_single_target_keeps_the_whole_quantity() -> None:
    [level] = _plan(["1.10"], "1005.0")

    assert (level.index, level.price, level.quantity) == (0, D("1.1000"), D("1005.0"))


def test_equal_split_with_the_remainder_on_the_last_level() -> None:
    levels = _plan(["1.10", "1.05", "1.00"], "1005.0")

    assert [lv.quantity for lv in levels] == [D("335.0"), D("335.0"), D("335.0")]
    levels = _plan(["1.10", "1.05"], "1005.1")
    assert [lv.quantity for lv in levels] == [D("502.5"), D("502.6")]
    assert sum(lv.quantity for lv in levels) == D("1005.1")


def test_prices_are_rounded_to_the_tick_and_duplicates_merged() -> None:
    levels = _plan(["1.10004", "1.10001", "1.0500"], "100.0")

    assert [lv.price for lv in levels] == [D("1.1000"), D("1.0500")]
    assert sum(lv.quantity for lv in levels) == D("100.0")


def test_levels_are_capped_at_four() -> None:
    levels = _plan(["5", "4", "3", "2", "1"], "100.0")

    assert [lv.price for lv in levels] == [D("5.0000"), D("4.0000"), D("3.0000"), D("2.0000")]
    assert sum(lv.quantity for lv in levels) == D("100.0")


def test_farthest_targets_are_dropped_when_shares_fall_below_min_qty() -> None:
    filters = SymbolFilters(tick_size=D("0.01"), step_size=D("0.001"), min_qty=D("0.002"))

    levels = plan_take_profit_levels([D("100"), D("95"), D("90")], D("0.005"), filters)

    # 0.005 / 3 -> 0.001 < min 0.002, so two levels (0.002 and 0.003), nearest targets kept
    assert [lv.price for lv in levels] == [D("100.00"), D("95.00")]
    assert [lv.quantity for lv in levels] == [D("0.002"), D("0.003")]


def test_falls_back_to_one_level_holding_everything() -> None:
    filters = SymbolFilters(tick_size=D("0.01"), step_size=D("0.001"), min_qty=D("0.004"))

    [level] = plan_take_profit_levels([D("100"), D("95")], D("0.005"), filters)

    assert (level.price, level.quantity) == (D("100.00"), D("0.005"))


def test_nothing_to_place() -> None:
    assert _plan([], "100.0") == []
    assert _plan(["1.1"], "0") == []
    assert _plan(["0"], "100.0") == []
