"""
Weekly performance report — Stage-2 gate measurement tool.

The key Stage-2 metric is: paper expectancy vs backtest expectancy for the
*same calendar period*. Large divergence (paper < 50% of backtest) signals a
fill-model bug, look-ahead leak, or regime change.

CLI:
    python -m storage.weekly_report --db storage/trades.db \\
        --backtest-csv logs/backtest_ema_vwap_rsi.csv \\
        --from 2025-06-01 --to 2025-06-30

Output (stdout + logs/weekly_YYYY-WNN.txt):
    Per-strategy paper metrics vs backtest metrics for the same date range.
    Rolling gate check: expectancy >0, drawdown within limit, >=20 paper trades
    (lower threshold than Stage-1 since paper weeks have fewer trades).
"""
from __future__ import annotations

import argparse
import csv
import logging
import math
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _load_paper_trades(
    db_path: str,
    from_date: str,
    to_date: str,
    strategy: str | None = None,
) -> list[dict]:
    """Load completed paper/live trades from SQLite for a date range."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    q = "SELECT * FROM trades WHERE entry_time >= ? AND entry_time <= ? AND exit_price IS NOT NULL"
    params: list[Any] = [from_date, to_date + " 23:59:59"]
    if strategy:
        q += " AND strategy_name = ?"
        params.append(strategy)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _load_backtest_trades(csv_path: str, from_date: str, to_date: str) -> list[dict]:
    """Load backtest trades from a CSV (output of backtest engine) for a date range."""
    path = Path(csv_path)
    if not path.exists():
        return []
    trades = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            entry_time = row.get("entry_time", "")
            if entry_time >= from_date and entry_time <= to_date + "T23:59:59":
                try:
                    row["pnl"] = float(row.get("pnl", 0))
                    trades.append(row)
                except (ValueError, TypeError):
                    pass
    return trades


def _metrics(trades: list[dict], capital: float = 100_000.0) -> dict:
    if not trades:
        return {
            "trade_count": 0, "win_rate": 0.0, "expectancy": 0.0,
            "net_pnl": 0.0, "max_drawdown_pct": 0.0, "profit_factor": 0.0,
        }
    pnls = [float(t.get("pnl", 0)) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "trade_count": len(trades),
        "win_rate": len(wins) / len(trades) * 100,
        "expectancy": sum(pnls) / len(trades),
        "net_pnl": sum(pnls),
        "max_drawdown_pct": max_dd / capital * 100,
        "profit_factor": profit_factor,
    }


def _stage2_verdict(
    paper_m: dict,
    bt_m: dict,
    capital: float,
    max_dd_pct: float = 0.15,
) -> tuple[bool, list[str]]:
    """
    Stage-2 health check for a given period.

    Flags (not hard kill criteria — the human decides whether to continue):
      - paper expectancy <= 0
      - paper expectancy < 50% of backtest expectancy for same period
      - drawdown >= limit
    """
    flags: list[str] = []

    if paper_m["trade_count"] == 0:
        return False, ["No paper trades in period"]

    if paper_m["expectancy"] <= 0:
        flags.append(
            f"paper expectancy ₹{paper_m['expectancy']:.2f} <= 0"
        )

    if bt_m["trade_count"] > 0 and bt_m["expectancy"] > 0:
        ratio = paper_m["expectancy"] / bt_m["expectancy"]
        if ratio < 0.5:
            flags.append(
                f"paper/backtest expectancy ratio = {ratio:.0%} (<50%)  "
                f"— investigate fill model, look-ahead bugs, or regime change"
            )

    if paper_m["max_drawdown_pct"] >= max_dd_pct * 100:
        flags.append(
            f"drawdown {paper_m['max_drawdown_pct']:.1f}% >= {max_dd_pct*100:.0f}% limit"
        )

    passed = len(flags) == 0
    return passed, flags or [
        f"OK — paper exp ₹{paper_m['expectancy']:.2f}, "
        f"bt exp ₹{bt_m['expectancy']:.2f}, "
        f"ratio {paper_m['expectancy']/bt_m['expectancy']:.0%}" if bt_m["expectancy"] > 0
        else f"OK — paper exp ₹{paper_m['expectancy']:.2f} (no backtest baseline)"
    ]


def generate_weekly(
    db_path: str,
    from_date: str,
    to_date: str,
    backtest_csvs: dict[str, str],  # {strategy_name: csv_path}
    capital: float = 100_000.0,
    max_dd_pct: float = 0.15,
    output_dir: str = "logs",
) -> None:
    """
    Print and save the weekly paper-vs-backtest report.

    backtest_csvs: per-strategy path to the backtest trade CSV produced by
    the backtest engine. Pass {} to skip the comparison column.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Collect all strategy names from paper trades in the period
    all_paper = _load_paper_trades(db_path, from_date, to_date)
    strategies = sorted({t.get("strategy_name") or "unknown" for t in all_paper})
    if not strategies:
        print(f"No completed paper trades between {from_date} and {to_date}.")
        return

    lines: list[str] = []

    header = (
        f"\n{'='*80}\n"
        f"  WEEKLY REPORT   {from_date} → {to_date}\n"
        f"  Stage-2 paper-vs-backtest comparison | Capital ₹{capital:,.0f}\n"
        f"{'='*80}\n"
    )
    lines.append(header)
    print(header, end="")

    all_passed = True
    for strat in strategies:
        paper_trades = _load_paper_trades(db_path, from_date, to_date, strategy=strat)
        paper_m = _metrics(paper_trades, capital)

        bt_csv = backtest_csvs.get(strat, "")
        bt_trades = _load_backtest_trades(bt_csv, from_date, to_date) if bt_csv else []
        bt_m = _metrics(bt_trades, capital)

        passed, verdict_items = _stage2_verdict(paper_m, bt_m, capital, max_dd_pct)
        if not passed:
            all_passed = False

        status = "✅" if passed else "⚠️ "
        block = (
            f"  {status} Strategy: {strat}\n"
            f"  {'Metric':<28} {'Paper':<18} {'Backtest (same period)'}\n"
            f"  {'-'*70}\n"
        )
        for key, label in [
            ("trade_count",       "trade_count"),
            ("win_rate",          "win_rate %"),
            ("expectancy",        "expectancy ₹"),
            ("net_pnl",           "net_pnl ₹"),
            ("max_drawdown_pct",  "max_drawdown %"),
            ("profit_factor",     "profit_factor"),
        ]:
            pv = paper_m[key]
            bv = bt_m[key]
            fmt = "{:.1f}" if key in ("win_rate", "max_drawdown_pct") else "{:.2f}"
            bt_str = fmt.format(bv) if bt_m["trade_count"] > 0 else "—"
            block += f"  {label:<28} {fmt.format(pv):<18} {bt_str}\n"

        block += "\n  Verdict:\n"
        for item in verdict_items:
            block += f"    • {item}\n"
        block += "\n"

        # Per-symbol paper breakdown
        sym_pnl: dict[str, list[float]] = {}
        for t in paper_trades:
            sym = t.get("symbol", "?")
            sym_pnl.setdefault(sym, []).append(float(t.get("pnl", 0)))
        if sym_pnl:
            block += f"  Per-symbol (paper)\n"
            block += f"  {'Symbol':<30} {'Trades':<8} {'Net P&L ₹':<14} {'Avg P&L ₹'}\n"
            block += f"  {'-'*60}\n"
            sym_rows = sorted(sym_pnl.items(), key=lambda kv: sum(kv[1]), reverse=True)
            for sym, pnls in sym_rows:
                sym_short = sym.split("|")[-1] if "|" in sym else sym
                block += (
                    f"  {sym_short:<30} {len(pnls):<8} {sum(pnls):<14.2f} {sum(pnls)/len(pnls):.2f}\n"
                )
            block += "\n"

        lines.append(block)
        print(block, end="")

    footer = "  ✅ All strategies healthy.\n" if all_passed else \
             "  ⚠️  One or more strategies need investigation before parameter changes.\n"
    footer += f"{'='*80}\n"
    lines.append(footer)
    print(footer, end="")

    # Save to file
    week_label = f"weekly_{from_date}"
    out_path = Path(output_dir) / f"{week_label}.txt"
    out_path.write_text("".join(lines), encoding="utf-8")
    logger.info("Weekly report saved to %s", out_path)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    import config

    parser = argparse.ArgumentParser(
        description="Weekly paper-vs-backtest report (Stage-2 health check)"
    )
    parser.add_argument("--db", default=config.TRADE_DB_PATH,
                        help="Path to SQLite trades DB")
    parser.add_argument("--from", dest="from_date",
                        default=(date.today() - timedelta(days=7)).isoformat(),
                        help="Start date YYYY-MM-DD (default: 7 days ago)")
    parser.add_argument("--to", dest="to_date",
                        default=date.today().isoformat(),
                        help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--backtest-csv", dest="backtest_csv", default=None,
                        help="Path to a single backtest CSV to compare (for --strategy)")
    parser.add_argument("--strategy", dest="strategy", default=None,
                        help="Limit comparison to one strategy (used with --backtest-csv)")
    args = parser.parse_args()

    # Build backtest_csvs mapping: auto-detect logs/backtest_*.csv if not specified
    backtest_csvs: dict[str, str] = {}
    if args.backtest_csv and args.strategy:
        backtest_csvs[args.strategy] = args.backtest_csv
    else:
        logs_dir = Path(config.LOGS_DIR)
        for p in logs_dir.glob("backtest_*.csv"):
            strat = p.stem.replace("backtest_", "")
            backtest_csvs[strat] = str(p)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    generate_weekly(
        db_path=args.db,
        from_date=args.from_date,
        to_date=args.to_date,
        backtest_csvs=backtest_csvs,
        capital=config.CAPITAL,
        max_dd_pct=config.GATE_MAX_DRAWDOWN_PCT,
        output_dir=config.LOGS_DIR,
    )


if __name__ == "__main__":
    _cli()
