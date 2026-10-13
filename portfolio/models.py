"""Plain data types shared by every layer. No I/O happens here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class PortfolioError(Exception):
    """Base class for every error the CLI should show to the user as a clean message."""


class ValidationError(PortfolioError):
    pass


class InsufficientSharesError(PortfolioError):
    pass


class TradeKind(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"


@dataclass(frozen=True)
class Trade:
    """One line of the trade log.

    For BUY/SELL, `quantity` is shares and `price` is per share.
    For DIVIDEND, `quantity` is 0 and `price` holds the total cash received,
    so one table can store every event in date order.
    """

    trade_date: date
    kind: TradeKind
    symbol: str
    quantity: Decimal
    price: Decimal
    fees: Decimal = Decimal("0")
    id: int | None = None  # assigned by storage; also breaks ties between same-day trades

    @property
    def dividend_amount(self) -> Decimal:
        return self.price if self.kind is TradeKind.DIVIDEND else Decimal("0")


def parse_decimal(raw: str, field: str, *, allow_zero: bool = False) -> Decimal:
    """Parse user input into a Decimal. We never go through float, so '0.1' stays exactly 0.1."""
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        raise ValidationError(f"{field} must be a number, got {raw!r}") from None
    if not value.is_finite():
        raise ValidationError(f"{field} must be a finite number, got {raw!r}")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValidationError(f"{field} must be {'zero or ' if allow_zero else ''}positive, got {raw}")
    return value


def normalize_symbol(raw: str) -> str:
    symbol = raw.strip().upper()
    if not symbol or len(symbol) > 12 or not all(c.isalnum() or c in ".-" for c in symbol):
        raise ValidationError(f"invalid symbol {raw!r}")
    return symbol
