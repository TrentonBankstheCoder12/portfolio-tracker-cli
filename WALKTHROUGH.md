# Walkthrough: Portfolio Tracker CLI

A guide to how this project works and why it was built this way. Read it top to bottom, then try to explain each section out loud without looking.

## 1. The core idea: a trade log, replayed

The database stores exactly one thing: the list of trades (buys, sells, dividends). Holdings, cost basis and P&L are never stored. They are **derived** every time by replaying the log in date order.

Why? If you stored "AAPL: 11 shares, cost $1,943.60" and also the trades, the two could disagree after a bug or a back-dated entry, and you would not know which one is right. With one source of truth, every report can be reproduced and audited. It is the same idea as an append-only ledger or event sourcing.

**Trade-off:** replaying is O(number of trades) on every command. For a personal portfolio (thousands of trades) that is instant. A brokerage with millions of accounts would snapshot lots periodically.

Code: `replay` in `portfolio/fifo.py`.

## 2. FIFO lot matching

Each BUY creates a **lot**: "10 shares acquired on 2023-01-10 for $1,301 total (including fees)". Lots for a symbol sit in a queue (`collections.deque`), oldest first.

A SELL pops from the front:

1. Take `min(shares still to sell, shares in the oldest lot)`.
2. If that uses the whole lot, remove it and use its exact remaining cost.
3. If it is a partial lot, carve off a proportional slice of cost (`cost * take / lot.quantity`) and leave the rest.
4. Record a `RealizedGain` for that slice and repeat until the sell is filled.

So selling 12 AAPL against lots of 10 @ $130 and 5 @ $180 produces **two** realized gains: 10 shares against the first lot and 2 against the second. The `realized` command shows each match, which is exactly what a 1099-B lists.

**Why FIFO?** It is the IRS default when you do not specify lots, and it is what most brokers use. Other methods (LIFO, specific identification, average cost) would be alternative strategies for step 1.

## 3. Fees and cost basis

- **Buy fees** are added to the lot's cost (you paid them to acquire the shares).
- **Sell fees** reduce proceeds. When a sell spans several lots, the fee is split across them by share count. The last slice takes "whatever is left" so the slices always add up to exactly the total proceeds.

## 4. Why `Decimal`, and why TEXT in SQLite

`0.1 + 0.2 != 0.3` with floats. In this project:

- All input (CLI arguments, CSV cells) is parsed straight from a string into `Decimal` (`parse_decimal` in `models.py`), never through `float`.
- SQLite's `REAL` type *is* a float, so amounts are stored as `TEXT` and turned back into `Decimal` on read. A test checks the round trip.
- Lots store **total remaining cost**, not cost per share. Per-share cost can be a repeating decimal (`$4 / 0.3 shares`); multiplying it back out would leak tiny errors. A test buys 0.3 shares with a $1 fee, sells it in two pieces, and asserts the realized cost is exactly `4`.
- Rounding to cents happens **only for display** (`reports.money`), so rounding error never compounds.

## 5. Rejecting oversells, including back-dated ones

A naive check ("do I hold enough right now?") misses this case: you hold 10 shares, sold 8 in June, and now record a back-dated March sale of 5. Today's position looks fine, but June's sale no longer has enough shares.

So `TradeStore.add_trades` replays *existing + new* trades before writing. If the replay raises `InsufficientSharesError`, nothing is inserted. Deleting a trade uses the same check, so you cannot delete a buy that a later sell depends on. The insert itself runs inside `with self.conn:`, which commits on success and rolls back on any exception, so a batch import is all-or-nothing.

Code: `portfolio/storage.py`.

## 6. Pluggable prices

`PriceProvider` is a `typing.Protocol` with one method, `get_price(symbol) -> Decimal | None`. The report code depends only on that interface. Shipped implementations:

- `ManualPriceProvider`: prices from a dict or `--price AAPL=190`
- `CsvPriceProvider`: reads `symbol,price`
- `ChainedPriceProvider`: asks several providers in order, so a manual override beats the file

Returning `None` for unknown symbols matters: the report shows `n/a` and blanks allocation rather than silently treating a missing price as $0, which would make your portfolio look like it crashed.

Adding a live market-data source means writing one class; nothing else changes. Tests stay offline because they use the manual provider.

## 7. CSV import validation

`parse_trades_csv` checks headers first, then validates **every** row and collects all errors with line numbers (`line 4: type must be BUY, SELL or DIVIDEND`). Stopping at the first error forces the user to fix-and-retry once per bad row. CLI commands and CSV rows go through the same `build_trade` function, so both entry points enforce identical rules.

## 8. Layering

`cli.py` only parses arguments, calls the core, and renders Rich tables. `fifo.py` is pure: no database, no prices, no I/O. That is why the hardest logic has the most tests and those tests need no setup. The CLI tests use Typer's `CliRunner` with a temporary database.

## 9. Known limitation (be ready to discuss this)

The tracker has no notion of **corporate actions**. A 4-for-1 stock split would make the old lots wrong (quantity should quadruple, per-share cost should quarter), and after the split a sell of the new share count would be rejected as an oversell. The fix is a `SPLIT` trade type that the replay applies to every open lot of that symbol. Similarly, the short/long-term split uses a simple "more than 365 days" rule and ignores wash sales, so the yearly report is informative, not tax-filing grade.

## Likely interview questions

1. **Why FIFO, and how would you support other methods?** FIFO is the IRS default and the broker norm. I would pull lot selection into a strategy (`choose_lots(lots, qty)`) so FIFO, LIFO, highest-cost or specific-lot each plug in without touching the rest of `replay`.
2. **Why not store holdings in the database?** Derived state can drift from the trades. Replaying one source of truth makes every number reproducible and auditable (section 1).
3. **How do you avoid rounding errors?** `Decimal` parsed from strings, TEXT storage, total-cost lots instead of unit cost, and rounding only at display time (section 4).
4. **How do you stop someone from selling shares they do not own?** Replay the full history including the new trade before writing; reject if any sell exceeds holdings at that date (section 5).
5. **What is the difference between realized and unrealized P&L?** Realized is locked in by a sale (proceeds minus matched lot cost). Unrealized is a paper gain on open lots at the current price and changes with the market.
6. **How would you add live prices?** Implement `PriceProvider` against an API, add caching and a timeout, and chain it after the manual provider. Tests keep using the offline providers.
7. **How would this scale to many users?** Add an account id to trades, snapshot open lots periodically so replay only covers recent trades, and move to Postgres.

## Try extending it

- Add a `SPLIT` trade type (e.g. `ratio=4`) and a test that sells post-split shares
- Add a `--method lifo` option using a lot-selection strategy
- Add an `export` command that writes the realized-gains table to CSV
- Add a time-weighted return calculation over the trade history
