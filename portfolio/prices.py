"""Price providers. The rest of the app only depends on the PriceProvider protocol,
so swapping in a live market-data API later means writing one new class."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from .models import ValidationError, normalize_symbol, parse_decimal


class PriceProvider(Protocol):
    def get_price(self, symbol: str) -> Decimal | None:
        """Latest price for `symbol`, or None if unknown (we never guess a price)."""
        ...


class ManualPriceProvider:
    """Prices supplied in code or on the command line. Handy for tests and quick what-ifs."""

    def __init__(self, prices: dict[str, Decimal] | None = None):
        self._prices = {normalize_symbol(s): p for s, p in (prices or {}).items()}

    def set_price(self, symbol: str, price: Decimal) -> None:
        self._prices[normalize_symbol(symbol)] = price

    def get_price(self, symbol: str) -> Decimal | None:
        return self._prices.get(symbol.upper())


class CsvPriceProvider(ManualPriceProvider):
    """Reads a `symbol,price` CSV. No network access, so results are reproducible."""

    def __init__(self, path: Path):
        super().__init__()
        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or {"symbol", "price"} - {h.strip().lower() for h in reader.fieldnames}:
                raise ValidationError(f"{path}: price file needs 'symbol' and 'price' columns")
            for line_no, row in enumerate(reader, start=2):
                row = {k.strip().lower(): (v or "") for k, v in row.items()}
                try:
                    self.set_price(row["symbol"], parse_decimal(row["price"], "price"))
                except ValidationError as e:
                    raise ValidationError(f"{path} line {line_no}: {e}") from None


class ChainedPriceProvider:
    """Ask each provider in turn; the first one that knows the symbol wins.
    Lets a manual `--price AAPL=190` override a price file."""

    def __init__(self, *providers: PriceProvider):
        self._providers = providers

    def get_price(self, symbol: str) -> Decimal | None:
        for provider in self._providers:
            price = provider.get_price(symbol)
            if price is not None:
                return price
        return None
