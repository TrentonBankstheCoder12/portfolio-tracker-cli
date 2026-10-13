"""The FIFO engine: replays a trade log and produces lots, realized gains and dividends.

This module is pure (no database, no prices), which makes it easy to test exhaustively.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .models import InsufficientSharesError, Trade, TradeKind

ZERO = Decimal("0")


@dataclass
class Lot:
    """Shares bought in a single BUY that have not been sold yet.

    We track the lot's *total* remaining cost rather than a per-share cost. Per-share cost
    can be a repeating decimal (e.g. $1 fee / 3 shares), and multiplying it back out would
    leak fractions of a cent. Carving cost off the total keeps the books exact.
    """

    symbol: str
    acquired: date
    quantity: Decimal
    cost: Decimal  # includes buy fees

    @property
    def unit_cost(self) -> Decimal:
        return self.cost / self.quantity


@dataclass(frozen=True)
class RealizedGain:
    """The result of matching (part of) a sell against one lot."""

    symbol: str
    acquired: date
    sold: date
    quantity: Decimal
    proceeds: Decimal  # net of the share of sell fees
    cost: Decimal

    @property
    def gain(self) -> Decimal:
        return self.proceeds - self.cost


@dataclass(frozen=True)
class Dividend:
    symbol: str
    paid: date
    amount: Decimal


@dataclass
class PortfolioState:
    lots: dict[str, deque[Lot]] = field(default_factory=dict)
    realized: list[RealizedGain] = field(default_factory=list)
    dividends: list[Dividend] = field(default_factory=list)

    def quantity(self, symbol: str) -> Decimal:
        return sum((lot.quantity for lot in self.lots.get(symbol, ())), ZERO)

    def cost_basis(self, symbol: str) -> Decimal:
        return sum((lot.cost for lot in self.lots.get(symbol, ())), ZERO)

    def open_symbols(self) -> list[str]:
        return sorted(s for s, lots in self.lots.items() if lots)


def _sort_key(trade: Trade) -> tuple:
    # Same-day trades are applied in the order they were recorded.
    return (trade.trade_date, trade.id if trade.id is not None else float("inf"))


def replay(trades: list[Trade]) -> PortfolioState:
    """Apply every trade in date order. Raises InsufficientSharesError on an oversell.

    Replaying the full history (instead of keeping running totals in the DB) means a
    back-dated trade is handled correctly: we simply recompute from scratch.
    """
    state = PortfolioState()
    for trade in sorted(trades, key=_sort_key):
        if trade.kind is TradeKind.BUY:
            _apply_buy(state, trade)
        elif trade.kind is TradeKind.SELL:
            _apply_sell(state, trade)
        else:
            state.dividends.append(Dividend(trade.symbol, trade.trade_date, trade.price))
    return state


def _apply_buy(state: PortfolioState, trade: Trade) -> None:
    cost = trade.quantity * trade.price + trade.fees
    state.lots.setdefault(trade.symbol, deque()).append(
        Lot(trade.symbol, trade.trade_date, trade.quantity, cost)
    )


def _apply_sell(state: PortfolioState, trade: Trade) -> None:
    held = state.quantity(trade.symbol)
    if trade.quantity > held:
        raise InsufficientSharesError(
            f"cannot sell {trade.quantity} {trade.symbol} on {trade.trade_date}: only {held} held"
        )

    lots = state.lots[trade.symbol]
    remaining = trade.quantity
    net_proceeds_left = trade.quantity * trade.price - trade.fees

    while remaining > 0:
        lot = lots[0]  # FIFO: always consume the oldest lot first
        take = min(remaining, lot.quantity)

        if take == lot.quantity:
            cost = lot.cost  # whole lot: use exact remaining cost, no division
            lots.popleft()
        else:
            cost = lot.cost * take / lot.quantity
            lot.cost -= cost
            lot.quantity -= take

        # Spread proceeds (and sell fees) across matched lots by share count. The final
        # slice takes whatever is left so the parts always add up to the whole.
        remaining -= take
        proceeds = net_proceeds_left if remaining == 0 else net_proceeds_left * take / (remaining + take)
        net_proceeds_left -= proceeds

        state.realized.append(
            RealizedGain(trade.symbol, lot.acquired, trade.trade_date, take, proceeds, cost)
        )
