"""
Backtest report generator.

Outputs:
  - Per-strategy IS/OOS metric table
  - Per-(strategy, symbol) expectancy breakdown
  - Stage-1 gate verdict per strategy (pass/fail with reasons)
  - Summary comparison table sorted by OOS expectancy

CLI: python -m backtest --strategies all --from 2025-01-01 --to 2026-07-01
"""
from __future__ import annotations

import argparse
import csv
import logging
import math
import sys
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_TRAIN_FRAC = 0.70  # first 70% = in-sample, last 30% = out-of-sample


# ── Metrics ──────────────────────────────────────────────────────────────────

def _metrics(trades: list[dict], capital: float = 100_000.0) -> dict:
    if not trades:
        return {
            "trade_count": 0, "win_rate": 0.0, "expectancy": 0.0,
            "profit_factor": 0.0, "net_pnl": 0.0, "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0, "avg_hold_min": 0.0, "worst_day": 0.0,
        }

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    # Drawdown from cumulative P&L peak
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    # Average holding time
    hold_times = []
    for t in trades:
        if t.get("entry_time") and t.get("exit_time"):
            try:
                et = pd.Timestamp(t["entry_time"])
                xt = pd.Timestamp(t["exit_time"])
                hold_times.append((xt - et).total_seconds() / 60)
            except Exception:
                pass
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0.0

    # Worst single day
    df = pd.DataFrame(trades)
    df["day"] = pd.to_datetime(df["entry_time"]).dt.date
    daily = df.groupby("day")["pnl"].sum()
    worst_day = float(daily.min()) if not daily.empty else 0.0

    return {
        "trade_count": len(trades),
        "win_rate": len(wins) / len(trades) * 100,
        "expectancy": sum(pnls) / len(pnls),
        "profit_factor": profit_factor,
        "net_pnl": sum(pnls),
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd / capital * 100,
        "avg_hold_min": avg_hold,
        "worst_day": worst_day,
    }


def _split_trades(trades: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split into in-sample (first 70%) and out-of-sample (last 30%) by date."""
    if not trades:
        return [], []
    sorted_t = sorted(trades, key=lambda t: t.get("entry_time", ""))
    split_idx = math.ceil(len(sorted_t) * _TRAIN_FRAC)
    return sorted_t[:split_idx], sorted_t[split_idx:]


# ── Gate verdict ──────────────────────────────────────────────────────────────

def _gate_verdict(
    trades: list[dict],
    is_m: dict,
    oos_m: dict,
    capital: float,
    min_trades: int,
    max_drawdown_pct: float,
) -> tuple[bool, list[str]]:
    """
    Evaluate Stage-1 pass criteria. Returns (passed, reasons_list).

    Pass criteria (all must hold on OOS cohort):
      1. OOS expectancy > 0
      2. OOS expectancy >= 50% of in-sample expectancy
      3. OOS max drawdown < max_drawdown_pct of capital
      4. Total trades in full period >= min_trades
    """
    reasons: list[str] = []

    if oos_m["trade_count"] == 0:
        reasons.append("FAIL: no OOS trades")
        return False, reasons

    if oos_m["expectancy"] <= 0:
        reasons.append(f"FAIL: OOS expectancy ₹{oos_m['expectancy']:.2f} <= 0")

    if is_m["expectancy"] != 0 and oos_m["expectancy"] < is_m["expectancy"] * 0.5:
        ratio = oos_m["expectancy"] / is_m["expectancy"] * 100 if is_m["expectancy"] != 0 else 0
        reasons.append(
            f"FAIL: OOS expectancy is {ratio:.0f}% of IS ({oos_m['expectancy']:.2f} vs {is_m['expectancy']:.2f}) — <50%"
        )

    if oos_m["max_drawdown_pct"] >= max_drawdown_pct * 100:
        reasons.append(
            f"FAIL: OOS drawdown {oos_m['max_drawdown_pct']:.1f}% >= {max_drawdown_pct*100:.0f}% limit"
        )

    total_trades = len(trades)
    if total_trades < min_trades:
        reasons.append(f"FAIL: only {total_trades} total trades, need >= {min_trades}")

    if not reasons:
        reasons.append(
            f"PASS: expectancy ₹{oos_m['expectancy']:.2f}, drawdown {oos_m['max_drawdown_pct']:.1f}%, "
            f"{total_trades} trades"
        )
        return True, reasons

    return False, reasons


# ── Per-symbol breakdown ──────────────────────────────────────────────────────

def _symbol_breakdown(trades: list[dict], capital: float) -> list[dict]:
    """Compute metrics per symbol and return sorted by expectancy desc."""
    if not trades:
        return []
    df = pd.DataFrame(trades)
    rows = []
    for sym, grp in df.groupby("symbol"):
        t_list = grp.to_dict("records")
        m = _metrics(t_list, capital)
        rows.append({
            "symbol": sym,
            "trades": m["trade_count"],
            "expectancy": m["expectancy"],
            "net_pnl": m["net_pnl"],
            "win_rate": m["win_rate"],
            "max_drawdown_pct": m["max_drawdown_pct"],
        })
    rows.sort(key=lambda r: r["expectancy"], reverse=True)
    return rows


# ── Report generator ──────────────────────────────────────────────────────────

def generate(
    strategy_trades: dict[str, list[dict]],
    output_dir: str = "logs",
    date_range: str = "",
    capital: float = 100_000.0,
    min_trades: int = 60,
    max_drawdown_pct: float = 0.15,
) -> dict[str, bool]:
    """
    Generate the full backtest report.

    Returns {strategy_name: passed_gate} — callers can use this to disable
    strategies that failed Stage-1.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    gate_results: dict[str, bool] = {}
    summary_rows: list[dict] = []

    print(f"\n{'='*80}")
    print(f"  BACKTEST REPORT   {date_range}")
    print(f"  Capital: ₹{capital:,.0f}  |  Gate: ≥{min_trades} trades, drawdown <{max_drawdown_pct*100:.0f}%, OOS exp >0 & ≥50% IS")
    print(f"{'='*80}\n")

    for strat_name, trades in strategy_trades.items():
        is_trades, oos_trades = _split_trades(trades)
        is_m = _metrics(is_trades, capital)
        oos_m = _metrics(oos_trades, capital)

        passed, gate_reasons = _gate_verdict(
            trades, is_m, oos_m, capital, min_trades, max_drawdown_pct
        )
        gate_results[strat_name] = passed

        verdict_str = "✅ STAGE-1 PASS" if passed else "❌ STAGE-1 FAIL"
        print(f"  Strategy: {strat_name}  [{verdict_str}]")
        for r in gate_reasons:
            print(f"    {r}")
        print()

        print(f"  {'Metric':<24} {'In-Sample (70%)':<22} {'Out-of-Sample (30%)'}")
        print(f"  {'-'*68}")
        for key, label in [
            ("trade_count",       "trade_count"),
            ("win_rate",          "win_rate %"),
            ("expectancy",        "expectancy ₹"),
            ("profit_factor",     "profit_factor"),
            ("net_pnl",           "net_pnl ₹"),
            ("max_drawdown",      "max_drawdown ₹"),
            ("max_drawdown_pct",  "max_drawdown %"),
            ("avg_hold_min",      "avg_hold_min"),
            ("worst_day",         "worst_day ₹"),
        ]:
            is_val = is_m[key]
            oos_val = oos_m[key]
            fmt = "{:.1f}" if key in ("win_rate", "avg_hold_min", "max_drawdown_pct") else "{:.2f}"
            print(f"  {label:<24} {fmt.format(is_val):<22} {fmt.format(oos_val)}")
        print()

        # Per-symbol breakdown
        sym_rows = _symbol_breakdown(trades, capital)
        if sym_rows:
            print(f"  Per-symbol breakdown ({strat_name})")
            print(f"  {'Symbol':<30} {'Trades':<8} {'Exp ₹':<12} {'Net P&L ₹':<14} {'Win%':<8} {'DD%'}")
            print(f"  {'-'*76}")
            for row in sym_rows:
                sym_short = row["symbol"].split("|")[-1] if "|" in str(row["symbol"]) else str(row["symbol"])
                ok = "" if row["expectancy"] > 0 else " ✗"
                print(
                    f"  {sym_short:<30} {row['trades']:<8} {row['expectancy']:<12.2f} "
                    f"{row['net_pnl']:<14.2f} {row['win_rate']:<8.1f} {row['max_drawdown_pct']:.1f}{ok}"
                )
            print()

        summary_rows.append({
            "strategy": strat_name,
            "gate": "PASS" if passed else "FAIL",
            "oos_expectancy": oos_m["expectancy"],
            "oos_net_pnl": oos_m["net_pnl"],
            "oos_win_rate": oos_m["win_rate"],
            "oos_max_drawdown_pct": oos_m["max_drawdown_pct"],
            "is_expectancy": is_m["expectancy"],
            "is_net_pnl": is_m["net_pnl"],
            "total_trades": len(trades),
        })

        # Export per-trade CSV
        if trades:
            csv_path = Path(output_dir) / f"backtest_{strat_name}.csv"
            fieldnames = list(trades[0].keys())
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(trades)
            logger.info("Exported %d trades to %s", len(trades), csv_path)

    # Summary comparison table sorted by OOS expectancy
    summary_rows.sort(key=lambda r: r["oos_expectancy"], reverse=True)
    print("  STRATEGY COMPARISON (sorted by OOS expectancy)")
    print(f"  {'Strategy':<22} {'Gate':<8} {'OOS Exp ₹':<12} {'OOS P&L ₹':<12} {'OOS Win%':<10} {'OOS DD%':<10} {'IS Exp ₹'}")
    print(f"  {'-'*84}")
    for r in summary_rows:
        print(
            f"  {r['strategy']:<22} {r['gate']:<8} {r['oos_expectancy']:<12.2f} "
            f"{r['oos_net_pnl']:<12.2f} {r['oos_win_rate']:<10.1f} "
            f"{r['oos_max_drawdown_pct']:<10.1f} {r['is_expectancy']:.2f}"
        )

    # Export summary CSV
    summary_csv = Path(output_dir) / "backtest_summary.csv"
    with open(summary_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()) if summary_rows else [])
        writer.writeheader()
        writer.writerows(summary_rows)

    passers = [r["strategy"] for r in summary_rows if r["gate"] == "PASS"]
    failers = [r["strategy"] for r in summary_rows if r["gate"] == "FAIL"]
    print(f"\n  Stage-1 survivors (copy to ACTIVE_STRATEGIES): {passers or '—'}")
    if failers:
        print(f"  Disable in config (do not delete): {failers}")
    print(f"\n{'='*80}\n")

    return gate_results


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    import config
    from backtest import data_store
    from backtest.engine import BacktestEngine
    from strategy.registry import build_strategies

    parser = argparse.ArgumentParser(description="Run backtest on cached Parquet data")
    parser.add_argument("--strategies", default="all",
                        help="Comma-separated strategy names or 'all'")
    parser.add_argument("--from", dest="from_date", required=True,
                        help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", required=True,
                        help="End date YYYY-MM-DD")
    parser.add_argument("--symbols", default=None,
                        help="Comma-separated Upstox instrument keys (default: per-strategy config)")
    args = parser.parse_args()

    # Build strategy+symbol pairs from config
    if args.strategies == "all":
        active = config.ACTIVE_STRATEGIES
    else:
        names = [s.strip() for s in args.strategies.split(",")]
        active = {n: config.ACTIVE_STRATEGIES.get(n, "all") for n in names}

    strategy_pairs = build_strategies(active, config.STRATEGY_PARAMS, config.SYMBOLS)

    # Collect all unique symbols needed
    all_syms: set[str] = set()
    if args.symbols:
        override = [s.strip() for s in args.symbols.split(",")]
        all_syms.update(override)
    else:
        for _, syms in strategy_pairs:
            all_syms.update(syms)

    cost_params = {
        "brokerage_flat": config.BROKERAGE_FLAT,
        "brokerage_pct":  config.BROKERAGE_PCT,
        "stt_pct":        config.STT_PCT,
        "exchange_txn_pct": config.EXCHANGE_TXN_PCT,
        "sebi_charge_pct":  config.SEBI_CHARGE_PCT,
        "stamp_duty_pct":   config.STAMP_DUTY_PCT,
        "gst_pct":          config.GST_PCT,
    }

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Load cached data — fail loudly if not cached
    candles_5m: dict[str, pd.DataFrame] = {}
    candles_15m: dict[str, pd.DataFrame] = {}
    for sym in sorted(all_syms):
        try:
            candles_5m[sym]  = data_store.load(sym, 5,  args.from_date, args.to_date)
            candles_15m[sym] = data_store.load(sym, 15, args.from_date, args.to_date)
            logger.info("Loaded %s: %d 5-min candles", sym, len(candles_5m[sym]))
        except FileNotFoundError as e:
            print(f"ERROR: {e}")
            print("Run `python -m backtest.downloader` first to cache historical data.")
            sys.exit(1)

    strategy_trades: dict[str, list[dict]] = {}
    for strategy, strat_syms in strategy_pairs:
        # Only pass the symbols this strategy is configured for
        active_syms = [s for s in strat_syms if s in candles_5m]
        engine = BacktestEngine(
            strategy=strategy,
            symbols=active_syms,
            capital=config.CAPITAL,
            max_risk_per_trade=config.MAX_RISK_PER_TRADE,
            daily_loss_limit=config.DAILY_LOSS_LIMIT,
            cost_params=cost_params,
            slippage_bps=config.SLIPPAGE_BPS,
        )
        trades = engine.run(candles_5m, candles_15m)
        strategy_trades[strategy.name] = [
            {
                "symbol":       t.symbol,
                "strategy":     t.strategy_name,
                "side":         t.side,
                "qty":          t.qty,
                "entry_price":  t.entry_price,
                "exit_price":   t.exit_price,
                "entry_time":   t.entry_time.isoformat(),
                "exit_time":    t.exit_time.isoformat(),
                "sl":           t.sl,
                "target":       t.target,
                "gross_pnl":    t.gross_pnl,
                "txn_cost":     t.txn_cost,
                "pnl":          t.pnl,
                "exit_reason":  t.exit_reason,
            }
            for t in trades
        ]
        logger.info("Strategy %s: %d trades", strategy.name, len(trades))

    generate(
        strategy_trades,
        output_dir=config.LOGS_DIR,
        date_range=f"{args.from_date} → {args.to_date}",
        capital=config.CAPITAL,
        min_trades=config.GATE_MIN_TRADES,
        max_drawdown_pct=config.GATE_MAX_DRAWDOWN_PCT,
    )


if __name__ == "__main__":
    _cli()
