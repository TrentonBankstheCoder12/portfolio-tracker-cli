from decimal import Decimal

from portfolio import reports
from portfolio.fifo import replay
from portfolio.prices import ChainedPriceProvider, CsvPriceProvider, ManualPriceProvider
from tests.conftest import make

D = Decimal


def _state():
    return replay(
        [
            make("2023-01-02", "BUY", "AAPL", "10", "100"),
            make("2023-01-02", "BUY", "MSFT", "10", "300"),
            make("2023-06-01", "SELL", "AAPL", "4", "150"),  # short-term +200
            make("2024-03-01", "SELL", "MSFT", "5", "200"),  # long-term -500
            make("2024-04-01", "DIVIDEND", "MSFT", "0", "7.50"),
        ]
    )


def test_unrealized_pnl_and_allocation():
    rows = reports.holdings(_state(), ManualPriceProvider({"AAPL": D("125"), "MSFT": D("310")}))
    by_symbol = {r.holding.symbol: r for r in rows}
    assert by_symbol["AAPL"].holding.unrealized == D("150")  # 6 * (125 - 100)
    assert by_symbol["MSFT"].holding.unrealized == D("50")  # 5 * (310 - 300)
    assert sum(r.allocation_pct for r in rows) == D("100")
    assert by_symbol["AAPL"].allocation_pct == D("750") / D("2300") * 100


def test_summary_totals_combine_realized_unrealized_and_dividends():
    state = _state()
    rows = reports.holdings(state, ManualPriceProvider({"AAPL": D("125"), "MSFT": D("310")}))
    s = reports.summary(state, rows)
    assert s.realized == D("-300")
    assert s.dividends == D("7.50")
    assert s.unrealized == D("200")
    assert s.total_return == D("-92.50")


def test_missing_price_leaves_values_blank_instead_of_guessing():
    rows = reports.holdings(_state(), ManualPriceProvider({"AAPL": D("125")}))
    msft = next(r for r in rows if r.holding.symbol == "MSFT")
    assert msft.holding.market_value is None
    assert all(r.allocation_pct is None for r in rows)


def test_pnl_by_year_splits_short_and_long_term():
    years = {r.year: r for r in reports.pnl_by_year(_state())}
    assert years[2023].short_term == D("200") and years[2023].long_term == 0
    assert years[2024].long_term == D("-500")
    assert years[2024].dividends == D("7.50")
    assert years[2024].total == D("-492.50")


def test_chained_provider_prefers_first_and_csv_provider_reads_file(tmp_path):
    f = tmp_path / "prices.csv"
    f.write_text("symbol,price\naapl,190.10\nMSFT,400\n")
    chain = ChainedPriceProvider(ManualPriceProvider({"AAPL": D("1")}), CsvPriceProvider(f))
    assert chain.get_price("AAPL") == D("1")
    assert chain.get_price("MSFT") == D("400")
    assert chain.get_price("NOPE") is None
