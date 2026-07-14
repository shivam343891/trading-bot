"""Tests for backtest/report.py gate verdict and per-symbol breakdown logic."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime
from backtest.report import _metrics, _gate_verdict, _symbol_breakdown, _split_trades


def _make_trades(pnls: list[float], symbol: str = "NSE_EQ|INFY") -> list[dict]:
    base = datetime(2025, 1, 2, 10, 0)
    trades = []
    for i, pnl in enumerate(pnls):
        trades.append({
            "symbol": symbol,
            "pnl": pnl,
            "entry_time": f"2025-01-{2 + i:02d}T10:00:00",
            "exit_time": f"2025-01-{2 + i:02d}T11:00:00",
        })
    return trades


# ── _metrics ──────────────────────────────────────────────────────────────────

def test_metrics_empty():
    m = _metrics([])
    assert m["trade_count"] == 0
    assert m["expectancy"] == 0.0


def test_metrics_all_wins():
    trades = _make_trades([100.0, 200.0, 50.0])
    m = _metrics(trades, capital=100_000)
    assert m["trade_count"] == 3
    assert m["win_rate"] == pytest.approx(100.0)
    assert m["expectancy"] == pytest.approx(350 / 3)
    assert m["max_drawdown"] == pytest.approx(0.0)
    assert m["max_drawdown_pct"] == pytest.approx(0.0)


def test_metrics_drawdown_calculation():
    # Equity curve: +100, -200, +50 → peak=100, trough=-100, dd=200
    trades = _make_trades([100.0, -200.0, 50.0])
    m = _metrics(trades, capital=100_000)
    assert m["max_drawdown"] == pytest.approx(200.0)
    assert m["max_drawdown_pct"] == pytest.approx(0.2)  # 200/100000


# ── _gate_verdict ─────────────────────────────────────────────────────────────

import pytest

def test_gate_passes_with_good_metrics():
    # 70 trades, positive OOS expectancy, good IS/OOS ratio, small drawdown
    is_m = {"expectancy": 50.0, "trade_count": 50}
    oos_m = {"expectancy": 30.0, "trade_count": 20, "max_drawdown_pct": 5.0}
    trades = _make_trades([10.0] * 70)
    passed, reasons = _gate_verdict(trades, is_m, oos_m, capital=100_000,
                                    min_trades=60, max_drawdown_pct=0.15)
    assert passed is True
    assert any("PASS" in r for r in reasons)


def test_gate_fails_negative_oos_expectancy():
    is_m = {"expectancy": 50.0, "trade_count": 50}
    oos_m = {"expectancy": -5.0, "trade_count": 20, "max_drawdown_pct": 5.0}
    trades = _make_trades([1.0] * 70)
    passed, reasons = _gate_verdict(trades, is_m, oos_m, capital=100_000,
                                    min_trades=60, max_drawdown_pct=0.15)
    assert passed is False
    assert any("expectancy" in r.lower() and "<= 0" in r for r in reasons)


def test_gate_fails_oos_below_50pct_of_is():
    is_m = {"expectancy": 100.0, "trade_count": 50}
    oos_m = {"expectancy": 40.0, "trade_count": 20, "max_drawdown_pct": 3.0}
    trades = _make_trades([1.0] * 70)
    passed, reasons = _gate_verdict(trades, is_m, oos_m, capital=100_000,
                                    min_trades=60, max_drawdown_pct=0.15)
    assert passed is False
    assert any("50%" in r for r in reasons)


def test_gate_fails_drawdown_too_high():
    is_m = {"expectancy": 50.0, "trade_count": 50}
    oos_m = {"expectancy": 30.0, "trade_count": 20, "max_drawdown_pct": 20.0}
    trades = _make_trades([1.0] * 70)
    passed, reasons = _gate_verdict(trades, is_m, oos_m, capital=100_000,
                                    min_trades=60, max_drawdown_pct=0.15)
    assert passed is False
    assert any("drawdown" in r.lower() for r in reasons)


def test_gate_fails_too_few_trades():
    is_m = {"expectancy": 50.0, "trade_count": 30}
    oos_m = {"expectancy": 30.0, "trade_count": 10, "max_drawdown_pct": 3.0}
    trades = _make_trades([1.0] * 40)
    passed, reasons = _gate_verdict(trades, is_m, oos_m, capital=100_000,
                                    min_trades=60, max_drawdown_pct=0.15)
    assert passed is False
    assert any("40 total trades" in r for r in reasons)


def test_gate_no_oos_trades_fails():
    is_m = {"expectancy": 50.0, "trade_count": 50}
    oos_m = {"expectancy": 0.0, "trade_count": 0, "max_drawdown_pct": 0.0}
    trades = _make_trades([1.0] * 50)
    passed, reasons = _gate_verdict(trades, is_m, oos_m, capital=100_000,
                                    min_trades=60, max_drawdown_pct=0.15)
    assert passed is False


# ── _symbol_breakdown ─────────────────────────────────────────────────────────

def test_symbol_breakdown_sorts_by_expectancy():
    trades = (
        _make_trades([100.0, 50.0], symbol="NSE_EQ|INFY") +
        _make_trades([-30.0, -20.0], symbol="NSE_EQ|RELIANCE")
    )
    rows = _symbol_breakdown(trades, capital=100_000)
    assert rows[0]["symbol"] == "NSE_EQ|INFY"     # higher expectancy first
    assert rows[1]["symbol"] == "NSE_EQ|RELIANCE"


def test_symbol_breakdown_empty():
    assert _symbol_breakdown([], capital=100_000) == []


# ── _split_trades ─────────────────────────────────────────────────────────────

def test_split_trades_70_30():
    trades = _make_trades([1.0] * 10)
    is_t, oos_t = _split_trades(trades)
    assert len(is_t) == 7
    assert len(oos_t) == 3


def test_split_trades_empty():
    is_t, oos_t = _split_trades([])
    assert is_t == []
    assert oos_t == []
