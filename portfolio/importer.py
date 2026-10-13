"""CSV trade import with all-or-nothing validation.

Every row is validated first and *all* problems are reported together with line numbers,
so the user can fix the file in one pass. Nothing is written unless the whole file is clean.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .models import Trade, TradeKind, ValidationError, normalize_symbol, parse_decimal

REQUIRED_COLUMNS = {"date", "type", "symbol", "quantity", "price"}


class CsvImportError(ValidationError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s) in CSV:\n" + "\n".join(f"  - {e}" for e in errors))


def build_trade(trade_date: str, kind: str, symbol: str, quantity: str, price: str, fees: str = "0") -> Trade:
    """Turn raw strings (from CSV or CLI) into a validated Trade. Shared by both entry points."""
    try:
        parsed_date = date.fromisoformat(trade_date.strip())
    except ValueError:
        raise ValidationError(f"date must be YYYY-MM-DD, got {trade_date!r}") from None
    if parsed_date > date.today():
        raise ValidationError(f"date {parsed_date} is in the future")

    try:
        trade_kind = TradeKind(kind.strip().upper())
    except ValueError:
        raise ValidationError(f"type must be BUY, SELL or DIVIDEND, got {kind!r}") from None

    if trade_kind is TradeKind.DIVIDEND:
        qty = parse_decimal(quantity or "0", "quantity", allow_zero=True)
        if qty != 0:
            raise ValidationError("DIVIDEND rows put the cash amount in 'price' and leave quantity blank")
        amount = parse_decimal(price, "dividend amount")
        return Trade(parsed_date, trade_kind, normalize_symbol(symbol), qty, amount)

    return Trade(
        parsed_date,
        trade_kind,
        normalize_symbol(symbol),
        parse_decimal(quantity, "quantity"),
        parse_decimal(price, "price"),
        parse_decimal(fees or "0", "fees", allow_zero=True),
    )


def parse_trades_csv(path: Path) -> list[Trade]:
    errors: list[str] = []
    trades: list[Trade] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        headers = {h.strip().lower() for h in (reader.fieldnames or [])}
        missing = REQUIRED_COLUMNS - headers
        if missing:
            raise CsvImportError([f"missing column(s): {', '.join(sorted(missing))}"])

        for line_no, raw in enumerate(reader, start=2):  # line 1 is the header
            row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k is not None}
            if not any(row.values()):
                continue  # tolerate blank lines
            try:
                trades.append(
                    build_trade(
                        row["date"],
                        row["type"],
                        row["symbol"],
                        row["quantity"],
                        row["price"],
                        row.get("fees", ""),
                    )
                )
            except ValidationError as e:
                errors.append(f"line {line_no}: {e}")

    if errors:
        raise CsvImportError(errors)
    if not trades:
        raise CsvImportError(["file contains no trades"])
    return trades
