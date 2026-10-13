# Portfolio Tracker CLI

[![CI](https://github.com/TrentonBankstheCoder12/portfolio-tracker-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/TrentonBankstheCoder12/portfolio-tracker-cli/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![Typer](https://img.shields.io/badge/CLI-Typer%20%2B%20Rich-6A5ACD)
![License](https://img.shields.io/badge/license-MIT-green)

A command-line **investment portfolio tracker** that turns a log of buys, sells and dividends into holdings, **FIFO cost basis**, realized and unrealized P&L, allocation, and a year-by-year gains report. This is the same lot-matching logic brokerages use to produce cost-basis statements and tax forms: every sale is matched against the oldest shares first, every number is computed with exact `Decimal` arithmetic, and the trade log is the single source of truth, so any report can be rebuilt from scratch and audited.

## Features

- **FIFO lot matching:** sells consume the oldest lots first, including partial lots and sells that span several buys
- **Realized vs. unrealized P&L:** realized from closed lot matches, unrealized from current prices
- **Short-term / long-term split** in the yearly report (held more than one year)
- **Dividends** tracked separately from capital gains, included in total return
- **Allocation %** by market value
- **Oversell protection:** a sell (even a back-dated one) that would exceed shares held on that date is rejected and nothing is written
- **CSV import** that validates every row first and reports all errors with line numbers; all-or-nothing
- **Pluggable prices:** a `PriceProvider` protocol with CSV-file and manual (`--price AAPL=190`) providers; no network required
- **Exact money math:** `Decimal` everywhere, stored as TEXT in SQLite (never as floats)

## Architecture

```
  portfolio/cli.py        Typer commands + Rich tables (thin: parse, call, render)
        │
        ├──▶ importer.py  CSV/CLI input ──▶ validated Trade objects
        ├──▶ storage.py   SQLite trade log (validate-by-replay before every write)
        │
        ▼
  portfolio/fifo.py       Pure engine: replay(trades) ──▶ open lots, realized gains, dividends
        │
        ▼
  portfolio/reports.py    Holdings, allocation, summary, P&L by year
        ▲
        │
  portfolio/prices.py     PriceProvider protocol: Manual / Csv / Chained
```

Only the trade log is persisted. Lots and P&L are always derived by replaying it, so there is no cached state that can drift out of sync.

## Quick start

```bash
pip install -r requirements-dev.txt
python -m portfolio import examples/trades.csv
python -m portfolio holdings --prices examples/prices.csv
```

Trades are stored in `portfolio.db` in the current directory; use `--db path` or `PORTFOLIO_DB=path` to change it.

## Usage

```bash
python -m portfolio buy AAPL 10 130.00 --date 2023-01-10 --fees 1
python -m portfolio sell AAPL 4 190.00 --date 2023-11-20
python -m portfolio dividend AAPL 2.40 --date 2023-08-17
python -m portfolio trades                       # raw log with ids
python -m portfolio realized                     # every sell matched to its lots
python -m portfolio report                       # P&L by year
python -m portfolio holdings -p examples/prices.csv --price AAPL=230   # --price overrides the file
python -m portfolio delete 3                     # refused if a later sell depends on it
```

Output from the sample data:

```
                                Holdings (FIFO)
┏━━━━━━━━┳━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Symbol ┃ Qty ┃ Avg cost ┃ Cost basis ┃  Price ┃ Market value ┃ Unrealized ┃ Alloc % ┃
┡━━━━━━━━╇━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━┩
│ AAPL   │  11 │   176.69 │   1,943.60 │ 225.00 │     2,475.00 │     531.40 │  30.05% │
│ MSFT   │   3 │   250.20 │     750.60 │ 420.00 │     1,260.00 │     509.40 │  15.30% │
│ VTI    │  15 │   230.50 │   3,457.50 │ 300.00 │     4,500.00 │   1,042.50 │  54.64% │
└────────┴─────┴──────────┴────────────┴────────┴──────────────┴────────────┴─────────┘
Cost basis      6,151.70
Market value    8,235.00
Unrealized P&L  2,083.30
Realized P&L    1,413.70
Dividends          22.55
Total return    3,519.55
```

### CSV format

```csv
date,type,symbol,quantity,price,fees
2023-01-10,BUY,AAPL,10,130.00,1.00
2023-08-17,DIVIDEND,AAPL,,2.40,
2023-11-20,SELL,AAPL,12,190.00,1.00
```

`type` is `BUY`, `SELL` or `DIVIDEND`. For dividends, leave `quantity` blank and put the total cash amount in `price`. `fees` is optional.

## Testing

```bash
pytest -q
ruff check .
```

28 tests cover FIFO matching (single lot, multiple buys, partial lots, sells spanning lots, fee allocation, fractional shares), oversell rejection (including back-dated sells), realized/unrealized P&L, dividends, allocation, short/long-term yearly report, CSV validation with line numbers, atomic imports, and every CLI command via Typer's `CliRunner`. A property-style test replays 200 random trade sequences and checks that cost and shares are conserved.

## What I'd add next

- Additional lot-selection methods (LIFO, specific-lot, average cost) behind a strategy interface
- Stock splits and other corporate actions
- Wash-sale detection for the yearly tax report
- A live `PriceProvider` (e.g. a market-data API) with on-disk caching
- Multi-currency positions with FX conversion

See [WALKTHROUGH.md](WALKTHROUGH.md) for the design decisions behind this project.

## License

MIT
