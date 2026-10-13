from decimal import Decimal
from pathlib import Path

import pytest

from portfolio.importer import CsvImportError, parse_trades_csv
from portfolio.models import InsufficientSharesError, TradeKind
from portfolio.storage import TradeStore

EXAMPLE = Path(__file__).parent.parent / "examples" / "trades.csv"


def write(tmp_path, text: str) -> Path:
    p = tmp_path / "t.csv"
    p.write_text(text)
    return p


def test_example_file_parses():
    trades = parse_trades_csv(EXAMPLE)
    assert len(trades) == 11
    assert trades[3].kind is TradeKind.DIVIDEND and trades[3].price == Decimal("2.40")


def test_import_reports_every_bad_row_with_line_numbers(tmp_path):
    f = write(
        tmp_path,
        "date,type,symbol,quantity,price\n"
        "2024-01-02,BUY,AAPL,10,100\n"
        "2024-13-01,BUY,AAPL,10,100\n"
        "2024-01-03,HOLD,AAPL,10,100\n"
        "2024-01-04,BUY,AAPL,-5,100\n"
        "2024-01-05,BUY,AAPL,abc,100\n",
    )
    with pytest.raises(CsvImportError) as exc:
        parse_trades_csv(f)
    assert [e.split(":")[0] for e in exc.value.errors] == ["line 3", "line 4", "line 5", "line 6"]


def test_import_rejects_missing_columns(tmp_path):
    with pytest.raises(CsvImportError, match="missing column"):
        parse_trades_csv(write(tmp_path, "date,symbol,quantity\n2024-01-01,AAPL,1\n"))


def test_dividend_row_must_not_have_quantity(tmp_path):
    with pytest.raises(CsvImportError, match="DIVIDEND"):
        parse_trades_csv(write(tmp_path, "date,type,symbol,quantity,price\n2024-01-01,DIVIDEND,KO,5,1\n"))


def test_storage_round_trips_decimals_exactly(db_path):
    with TradeStore(db_path) as store:
        store.add_trades(parse_trades_csv(EXAMPLE))
    with TradeStore(db_path) as store:
        trades = store.all_trades()
    assert trades[0].price == Decimal("130.00")
    assert [t.id for t in trades] == sorted(t.id for t in trades)


def test_oversell_in_batch_writes_nothing(db_path, tmp_path):
    f = write(
        tmp_path, "date,type,symbol,quantity,price\n2024-01-02,BUY,AAPL,1,100\n2024-01-03,SELL,AAPL,2,100\n"
    )
    with TradeStore(db_path) as store:
        with pytest.raises(InsufficientSharesError):
            store.add_trades(parse_trades_csv(f))
        assert store.all_trades() == []


def test_backdated_sell_that_breaks_a_later_sell_is_rejected(db_path, tmp_path):
    with TradeStore(db_path) as store:
        store.add_trades(
            parse_trades_csv(
                write(
                    tmp_path,
                    "date,type,symbol,quantity,price\n2024-01-02,BUY,X,10,1\n2024-06-01,SELL,X,8,1\n",
                )
            )
        )
        late = parse_trades_csv(write(tmp_path, "date,type,symbol,quantity,price\n2024-03-01,SELL,X,5,1\n"))
        with pytest.raises(InsufficientSharesError):
            store.add_trades(late)
        assert len(store.all_trades()) == 2
