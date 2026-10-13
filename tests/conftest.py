from datetime import date
from decimal import Decimal

import pytest

from portfolio.models import Trade, TradeKind


def make(
    day: str, kind: str, symbol: str, qty: str, price: str, fees: str = "0", id: int | None = None
) -> Trade:
    return Trade(
        date.fromisoformat(day), TradeKind(kind), symbol, Decimal(qty), Decimal(price), Decimal(fees), id
    )


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "test.db"
