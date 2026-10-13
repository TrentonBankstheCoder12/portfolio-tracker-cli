import random
from decimal import Decimal

import pytest

from portfolio.fifo import replay
from portfolio.models import InsufficientSharesError
from tests.conftest import make

D = Decimal


def test_single_buy_creates_one_lot_with_fees_in_cost():
    state = replay([make("2024-01-02", "BUY", "AAPL", "10", "100", fees="5")])
    assert state.quantity("AAPL") == D("10")
    assert state.cost_basis("AAPL") == D("1005")


def test_sell_consumes_oldest_lot_first():
    state = replay(
        [
            make("2024-01-02", "BUY", "AAPL", "10", "100"),
            make("2024-02-02", "BUY", "AAPL", "10", "200"),
            make("2024-03-02", "SELL", "AAPL", "10", "250"),
        ]
    )
    [gain] = state.realized
    assert gain.cost == D("1000")  # the $100 lot, not the $200 one
    assert gain.gain == D("1500")
    assert state.cost_basis("AAPL") == D("2000")


def test_partial_lot_leaves_remainder_with_proportional_cost():
    state = replay(
        [
            make("2024-01-02", "BUY", "MSFT", "10", "50"),
            make("2024-02-02", "SELL", "MSFT", "4", "60"),
        ]
    )
    assert state.realized[0].cost == D("200")
    assert state.realized[0].gain == D("40")
    [lot] = state.lots["MSFT"]
    assert lot.quantity == D("6")
    assert lot.cost == D("300")


def test_sell_spanning_multiple_lots_splits_into_one_gain_per_lot():
    state = replay(
        [
            make("2024-01-02", "BUY", "VTI", "5", "100"),
            make("2024-01-03", "BUY", "VTI", "5", "110"),
            make("2024-01-04", "BUY", "VTI", "5", "120"),
            make("2024-06-01", "SELL", "VTI", "12", "130", fees="12"),
        ]
    )
    assert [g.quantity for g in state.realized] == [D("5"), D("5"), D("2")]
    assert [g.cost for g in state.realized] == [D("500"), D("550"), D("240")]
    # Sell fee is spread across the matched lots, and the pieces add up exactly.
    assert sum(g.proceeds for g in state.realized) == D("12") * D("130") - D("12")
    assert state.quantity("VTI") == D("3")
    assert state.cost_basis("VTI") == D("360")


def test_selling_more_than_held_is_rejected():
    with pytest.raises(InsufficientSharesError, match="only 5 held"):
        replay([make("2024-01-02", "BUY", "AAPL", "5", "1"), make("2024-01-03", "SELL", "AAPL", "6", "1")])


def test_sell_before_buy_date_is_rejected_even_if_buy_exists_later():
    with pytest.raises(InsufficientSharesError):
        replay([make("2024-05-01", "BUY", "AAPL", "5", "1"), make("2024-01-01", "SELL", "AAPL", "1", "1")])


def test_fractional_shares_and_awkward_fees_stay_exact():
    state = replay(
        [
            make("2024-01-02", "BUY", "BTC", "0.3", "10", fees="1"),  # cost 4, unit cost 13.333...
            make("2024-01-03", "SELL", "BTC", "0.1", "20"),
            make("2024-01-04", "SELL", "BTC", "0.2", "20"),
        ]
    )
    # Closing the whole position must realize the exact total cost, with no stray fractions.
    assert sum(g.cost for g in state.realized) == D("4")
    assert state.quantity("BTC") == 0


def test_dividends_are_recorded_and_do_not_change_cost_basis():
    state = replay(
        [make("2024-01-02", "BUY", "KO", "10", "60"), make("2024-04-01", "DIVIDEND", "KO", "0", "4.60")]
    )
    assert state.dividends[0].amount == D("4.60")
    assert state.cost_basis("KO") == D("600")


def test_invariant_cost_is_conserved_across_random_trade_sequences():
    """Property-style check: for any valid sequence of buys and sells,
    total cost bought == cost still held + cost realized, and shares are conserved too."""
    rng = random.Random(42)
    for _ in range(200):
        trades, held = [], D("0")
        for day in range(1, 29):
            if held > 0 and rng.random() < 0.4:
                qty = D(rng.randint(1, int(held * 10))) / 10
                trades.append(
                    make(f"2024-02-{day:02d}", "SELL", "X", str(qty), str(rng.randint(1, 500)), "1")
                )
                held -= qty
            else:
                qty = D(rng.randint(1, 100)) / 10
                trades.append(make(f"2024-02-{day:02d}", "BUY", "X", str(qty), str(rng.randint(1, 500)), "1"))
                held += qty
        state = replay(trades)

        bought_cost = sum(t.quantity * t.price + t.fees for t in trades if t.kind == "BUY")
        bought_qty = sum(t.quantity for t in trades if t.kind == "BUY")
        sold_qty = sum(t.quantity for t in trades if t.kind == "SELL")
        realized_cost = sum(g.cost for g in state.realized)

        assert state.quantity("X") == bought_qty - sold_qty == held
        assert abs(state.cost_basis("X") + realized_cost - bought_cost) < D("1e-20")
