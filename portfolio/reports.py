"""Turns a replayed PortfolioState plus prices into the numbers the CLI shows."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .fifo import ZERO, PortfolioState
from .prices import PriceProvider

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    """Round only for display. Internal math keeps full precision so rounding never compounds."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Holding:
    symbol: str
    quantity: Decimal
    cost_basis: Decimal
    price: Decimal | None  # None when no price is available

    @property
    def avg_cost(self) -> Decimal:
        return self.cost_basis / self.quantity

    @property
    def market_value(self) -> Decimal | None:
        return None if self.price is None else self.quantity * self.price

    @property
    def unrealized(self) -> Decimal | None:
        mv = self.market_value
        return None if mv is None else mv - self.cost_basis


@dataclass(frozen=True)
class HoldingRow:
    holding: Holding
    allocation_pct: Decimal | None


def holdings(state: PortfolioState, prices: PriceProvider) -> list[HoldingRow]:
    items = [
        Holding(sym, state.quantity(sym), state.cost_basis(sym), prices.get_price(sym))
        for sym in state.open_symbols()
    ]
    # Allocation is by market value. If any position lacks a price, percentages would
    # silently be wrong, so we leave them blank instead.
    priced = all(h.market_value is not None for h in items)
    total = sum((h.market_value for h in items), ZERO) if priced else ZERO
    return [HoldingRow(h, (h.market_value / total * 100) if priced and total > 0 else None) for h in items]


@dataclass(frozen=True)
class Summary:
    cost_basis: Decimal
    market_value: Decimal | None
    unrealized: Decimal | None
    realized: Decimal
    dividends: Decimal

    @property
    def total_return(self) -> Decimal | None:
        if self.unrealized is None:
            return None
        return self.unrealized + self.realized + self.dividends


def summary(state: PortfolioState, rows: list[HoldingRow]) -> Summary:
    cost = sum((r.holding.cost_basis for r in rows), ZERO)
    values = [r.holding.market_value for r in rows]
    mv = None if any(v is None for v in values) else sum(values, ZERO)
    return Summary(
        cost_basis=cost,
        market_value=mv,
        unrealized=None if mv is None else mv - cost,
        realized=sum((g.gain for g in state.realized), ZERO),
        dividends=sum((d.amount for d in state.dividends), ZERO),
    )


@dataclass(frozen=True)
class YearRow:
    year: int
    short_term: Decimal
    long_term: Decimal
    dividends: Decimal

    @property
    def total(self) -> Decimal:
        return self.short_term + self.long_term + self.dividends


def is_long_term(gain) -> bool:
    # US rule of thumb: held more than one year. Simplified (ignores leap-day edge cases).
    return (gain.sold - gain.acquired).days > 365


def pnl_by_year(state: PortfolioState) -> list[YearRow]:
    short: dict[int, Decimal] = defaultdict(lambda: ZERO)
    long: dict[int, Decimal] = defaultdict(lambda: ZERO)
    divs: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for g in state.realized:
        (long if is_long_term(g) else short)[g.sold.year] += g.gain
    for d in state.dividends:
        divs[d.paid.year] += d.amount
    years = sorted(set(short) | set(long) | set(divs))
    return [YearRow(y, short[y], long[y], divs[y]) for y in years]
