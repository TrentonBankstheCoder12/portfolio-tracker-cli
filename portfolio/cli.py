"""Command-line interface. Thin layer: parse arguments, call the core modules, render tables."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import reports
from .fifo import replay
from .importer import build_trade, parse_trades_csv
from .models import PortfolioError, Trade, ValidationError, normalize_symbol, parse_decimal
from .prices import ChainedPriceProvider, CsvPriceProvider, ManualPriceProvider
from .storage import TradeStore

app = typer.Typer(help="Track a portfolio from a trade log with FIFO cost basis.", no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)

DbOption = Annotated[
    Path, typer.Option("--db", envvar="PORTFOLIO_DB", help="SQLite file holding your trades.")
]
DEFAULT_DB = Path("portfolio.db")


def fail(message: str) -> typer.Exit:
    err_console.print(f"[bold red]Error:[/] {message}")
    return typer.Exit(code=1)


def fmt(value: Decimal | None, *, color: bool = False, suffix: str = "") -> str:
    if value is None:
        return "[dim]n/a[/]"
    text = f"{reports.money(value):,}{suffix}"
    if color and value != 0:
        return f"[green]{text}[/]" if value > 0 else f"[red]{text}[/]"
    return text


def _save(db: Path, trades: list[Trade]) -> list[Trade]:
    try:
        with TradeStore(db) as store:
            return store.add_trades(trades)
    except PortfolioError as e:
        raise fail(str(e)) from None


def _record(db: Path, trade_date: str, kind: str, symbol: str, qty: str, price: str, fees: str) -> None:
    try:
        trade = build_trade(trade_date, kind, symbol, qty, price, fees)
    except ValidationError as e:
        raise fail(str(e)) from None
    saved = _save(db, [trade])[0]
    console.print(f"[green]Recorded[/] #{saved.id}: {kind} {symbol.upper()} on {saved.trade_date}")


@app.command()
def buy(
    symbol: str,
    quantity: str,
    price: Annotated[str, typer.Argument(help="Price per share.")],
    date: Annotated[str, typer.Option("--date", "-d", help="YYYY-MM-DD")],
    fees: Annotated[str, typer.Option(help="Commission, added to cost basis.")] = "0",
    db: DbOption = DEFAULT_DB,
):
    """Record a purchase."""
    _record(db, date, "BUY", symbol, quantity, price, fees)


@app.command()
def sell(
    symbol: str,
    quantity: str,
    price: Annotated[str, typer.Argument(help="Price per share.")],
    date: Annotated[str, typer.Option("--date", "-d", help="YYYY-MM-DD")],
    fees: Annotated[str, typer.Option(help="Commission, subtracted from proceeds.")] = "0",
    db: DbOption = DEFAULT_DB,
):
    """Record a sale. Rejected if you would sell more shares than you hold on that date."""
    _record(db, date, "SELL", symbol, quantity, price, fees)


@app.command()
def dividend(
    symbol: str,
    amount: Annotated[str, typer.Argument(help="Total cash received.")],
    date: Annotated[str, typer.Option("--date", "-d", help="YYYY-MM-DD")],
    db: DbOption = DEFAULT_DB,
):
    """Record a cash dividend."""
    _record(db, date, "DIVIDEND", symbol, "0", amount, "0")


@app.command("import")
def import_csv(file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)], db: DbOption = DEFAULT_DB):
    """Import trades from a CSV (columns: date,type,symbol,quantity,price[,fees]). All-or-nothing."""
    try:
        trades = parse_trades_csv(file)
    except ValidationError as e:
        raise fail(str(e)) from None
    saved = _save(db, trades)
    console.print(f"[green]Imported {len(saved)} trades[/] from {file}")


@app.command("trades")
def list_trades(
    symbol: Annotated[str | None, typer.Option(help="Filter by symbol.")] = None, db: DbOption = DEFAULT_DB
):
    """List the raw trade log."""
    with TradeStore(db) as store:
        trades = store.all_trades()
    if symbol:
        trades = [t for t in trades if t.symbol == symbol.upper()]
    table = Table(title="Trade log")
    for col in ("#", "Date", "Type", "Symbol", "Qty", "Price/Amount", "Fees"):
        table.add_column(col, justify="right" if col in {"#", "Qty", "Price/Amount", "Fees"} else "left")
    for t in trades:
        table.add_row(
            str(t.id),
            str(t.trade_date),
            t.kind.value,
            t.symbol,
            str(t.quantity) if t.quantity else "",
            f"{t.price:,}",
            f"{t.fees:,}",
        )
    console.print(table)


@app.command()
def delete(trade_id: int, db: DbOption = DEFAULT_DB):
    """Delete a trade by id (refused if a later sell depends on it)."""
    try:
        with TradeStore(db) as store:
            found = store.delete_trade(trade_id)
    except PortfolioError as e:
        raise fail(f"cannot delete #{trade_id}: {e}") from None
    if not found:
        raise fail(f"no trade with id {trade_id}")
    console.print(f"Deleted trade #{trade_id}")


def _price_provider(prices_file: Path | None, overrides: list[str]) -> ChainedPriceProvider:
    manual = ManualPriceProvider()
    for item in overrides:
        sym, sep, raw = item.partition("=")
        if not sep:
            raise fail(f"--price expects SYMBOL=PRICE, got {item!r}")
        try:
            manual.set_price(normalize_symbol(sym), parse_decimal(raw, f"price for {sym}"))
        except ValidationError as e:
            raise fail(str(e)) from None
    providers = [manual]
    if prices_file:
        try:
            providers.append(CsvPriceProvider(prices_file))
        except ValidationError as e:
            raise fail(str(e)) from None
    return ChainedPriceProvider(*providers)


def _load_state(db: Path):
    with TradeStore(db) as store:
        try:
            return replay(store.all_trades())
        except PortfolioError as e:  # only possible if the DB was edited by hand
            raise fail(f"trade log is inconsistent: {e}") from None


@app.command()
def holdings(
    prices: Annotated[
        Path | None, typer.Option("--prices", "-p", exists=True, help="CSV of symbol,price.")
    ] = None,
    price: Annotated[
        list[str] | None, typer.Option("--price", help="Override, e.g. --price AAPL=190.5")
    ] = None,
    db: DbOption = DEFAULT_DB,
):
    """Show open positions with FIFO cost basis, unrealized P&L and allocation."""
    state = _load_state(db)
    rows = reports.holdings(state, _price_provider(prices, price or []))

    table = Table(title="Holdings (FIFO)")
    table.add_column("Symbol")
    for col in ("Qty", "Avg cost", "Cost basis", "Price", "Market value", "Unrealized", "Alloc %"):
        table.add_column(col, justify="right")
    for r in rows:
        h = r.holding
        table.add_row(
            h.symbol,
            f"{h.quantity:,}",
            fmt(h.avg_cost),
            fmt(h.cost_basis),
            fmt(h.price),
            fmt(h.market_value),
            fmt(h.unrealized, color=True),
            fmt(r.allocation_pct, suffix="%"),
        )
    console.print(table)

    s = reports.summary(state, rows)
    totals = Table.grid(padding=(0, 2))
    totals.add_column()
    totals.add_column(justify="right")
    totals.add_row("Cost basis", fmt(s.cost_basis))
    totals.add_row("Market value", fmt(s.market_value))
    totals.add_row("Unrealized P&L", fmt(s.unrealized, color=True))
    totals.add_row("Realized P&L", fmt(s.realized, color=True))
    totals.add_row("Dividends", fmt(s.dividends, color=True))
    totals.add_row("[bold]Total return[/]", fmt(s.total_return, color=True))
    console.print(totals)
    missing = [r.holding.symbol for r in rows if r.holding.price is None]
    if missing:
        console.print(f"[yellow]No price for {', '.join(missing)}; pass --prices or --price.[/]")


@app.command()
def report(db: DbOption = DEFAULT_DB):
    """Realized P&L and dividends by calendar year, split short-term / long-term."""
    state = _load_state(db)
    table = Table(title="P&L by year")
    table.add_column("Year")
    for col in ("Short-term", "Long-term", "Dividends", "Total"):
        table.add_column(col, justify="right")
    for row in reports.pnl_by_year(state):
        table.add_row(
            str(row.year),
            fmt(row.short_term, color=True),
            fmt(row.long_term, color=True),
            fmt(row.dividends, color=True),
            fmt(row.total, color=True),
        )
    console.print(table)


@app.command()
def realized(db: DbOption = DEFAULT_DB):
    """Show every closed lot match: which buy each sell was matched against."""
    state = _load_state(db)
    table = Table(title="Realized gains (lot matches)")
    for col in ("Symbol", "Acquired", "Sold", "Qty", "Proceeds", "Cost", "Gain", "Term"):
        table.add_column(col, justify="left" if col in {"Symbol", "Acquired", "Sold", "Term"} else "right")
    for g in state.realized:
        table.add_row(
            g.symbol,
            str(g.acquired),
            str(g.sold),
            f"{g.quantity:,}",
            fmt(g.proceeds),
            fmt(g.cost),
            fmt(g.gain, color=True),
            "long" if reports.is_long_term(g) else "short",
        )
    console.print(table)


if __name__ == "__main__":
    app()
