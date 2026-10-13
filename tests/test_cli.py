from pathlib import Path

import pytest
from typer.testing import CliRunner

from portfolio.cli import app

EXAMPLES = Path(__file__).parent.parent / "examples"
runner = CliRunner()


@pytest.fixture()
def cli(db_path):
    def run(*args: str):
        return runner.invoke(app, [*args, "--db", str(db_path)], env={"COLUMNS": "200"})

    return run


def test_buy_sell_and_holdings(cli):
    assert cli("buy", "aapl", "10", "100", "--date", "2024-01-02", "--fees", "1").exit_code == 0
    assert cli("sell", "AAPL", "4", "150", "--date", "2024-02-02").exit_code == 0
    result = cli("holdings", "--price", "AAPL=120")
    assert result.exit_code == 0, result.output
    assert "AAPL" in result.output
    assert "600.60" in result.output  # cost 6 * 100.1 = 600.60, value 720 -> unrealized 119.40
    assert "119.40" in result.output
    assert "199.60" in result.output  # realized: 600 - 400.40


def test_oversell_exits_with_error_and_message(cli):
    cli("buy", "AAPL", "1", "100", "--date", "2024-01-02")
    result = cli("sell", "AAPL", "2", "100", "--date", "2024-01-03")
    assert result.exit_code == 1
    assert "only 1 held" in result.output


def test_invalid_input_is_rejected_cleanly(cli):
    result = cli("buy", "AAPL", "ten", "100", "--date", "2024-01-02")
    assert result.exit_code == 1
    assert "quantity must be a number" in result.output


def test_import_then_report_and_realized(cli):
    result = cli("import", str(EXAMPLES / "trades.csv"))
    assert result.exit_code == 0 and "Imported 11 trades" in result.output
    report = cli("report")
    assert "2023" in report.output and "617.60" in report.output
    realized = cli("realized")
    assert "598.17" in realized.output and "long" in realized.output


def test_holdings_with_price_file_shows_allocation(cli):
    cli("import", str(EXAMPLES / "trades.csv"))
    result = cli("holdings", "--prices", str(EXAMPLES / "prices.csv"))
    assert result.exit_code == 0
    assert "54.64%" in result.output  # VTI
    assert "3,519.55" in result.output  # total return


def test_bad_csv_import_writes_nothing(cli, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("date,type,symbol,quantity,price\n2024-01-02,BUY,AAPL,1,100\n2024-01-03,BUY,AAPL,x,1\n")
    result = cli("import", str(bad))
    assert result.exit_code == 1 and "line 3" in result.output
    assert "AAPL" not in cli("trades").output


def test_delete_refuses_to_orphan_a_sell(cli):
    cli("buy", "AAPL", "5", "100", "--date", "2024-01-02")
    cli("sell", "AAPL", "5", "110", "--date", "2024-01-03")
    assert cli("delete", "1").exit_code == 1
    assert cli("delete", "2").exit_code == 0
    assert cli("delete", "99").exit_code == 1
