"""How a filled position's quantity is split across the signal's take-profit targets.

Pure functions, no I/O. Decided with the user (2026-10-05): every target gets an equal share of the
filled quantity, rounded down to the exchange's lot step, with the rounding remainder added to the
last level. If the shares would fall below the exchange's minimum quantity, the farthest targets
are dropped (their share is absorbed by the last kept level) until every level is placeable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from telegram_trader.binance_market_data import SymbolFilters, round_price

MAX_TAKE_PROFIT_LEVELS = 4


@dataclass(frozen=True, slots=True)
class TakeProfitLevel:
    index: int  # 0-based; level 0 is the first (nearest) target as given by the signal
    price: Decimal
    quantity: Decimal


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def plan_take_profit_levels(
    targets: list[Decimal], quantity: Decimal, filters: SymbolFilters
) -> list[TakeProfitLevel]:
    """Prices rounded to the tick, duplicates merged (order kept), at most 4 levels.

    Returns `[]` when there is nothing to place (no targets, or the quantity is not positive).
    """
    prices: list[Decimal] = []
    for target in targets:
        price = round_price(target, filters)
        if price > 0 and price not in prices:
            prices.append(price)
    prices = prices[:MAX_TAKE_PROFIT_LEVELS]
    if not prices or quantity <= 0:
        return []

    levels = len(prices)
    share = _floor_to_step(quantity / levels, filters.step_size)
    while levels > 1 and (share <= 0 or share < filters.min_qty):
        levels -= 1
        share = _floor_to_step(quantity / levels, filters.step_size)

    quantities = [share] * (levels - 1)
    quantities.append(quantity - share * (levels - 1))  # the last level absorbs the remainder
    return [
        TakeProfitLevel(index=i, price=prices[i], quantity=quantities[i]) for i in range(levels)
    ]
