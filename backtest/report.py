"""
Backtest report generator.

Produces per-strategy IS/OOS metrics and a comparison table.
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

def _metrics(trades: list[dict]) -> dict:
    if not trades:
        return {
            "trade_count": 0, "win_rate": 0.0, "expectancy": 0.0,
            "profit_factor": 0.0, "net_pnl": 0.0, "max_drawdown": 0.0,
            "avg_hold_min": 0.0, "worst_day": 0.0,
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
        "win_rate": len(wins) / len(trades) * 100 if trades else 0.0,
        "expectancy": sum(pnls) / len(pnls),
        "profit_factor": profit_factor,
        "net_pnl": sum(pnls),
        "max_drawdown": max_dd,
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


def generate(
    strategy_trades: dict[str, list[dict]],
    output_dir: str = "logs",
    date_range: str = "",
) -> None:
    """
    strategy_trades: {strategy_name: [trade_dicts]}
    Prints IS/OOS table to stdout and exports per-trade CSVs.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    print(f"\n{'='*80}")
    print(f"  BACKTEST REPORT   {date_range}")
    print(f"{'='*80}\n")

    for strat_name, trades in strategy_trades.items():
        is_trades, oos_trades = _split_trades(trades)
        is_m = _metrics(is_trades)
        oos_m = _metrics(oos_trades)

        # OOS quality warning
        oos_exp = oos_m["expectancy"]
        is_exp = is_m["expectancy"]
        quality_warn = ""
        if is_exp != 0 and oos_exp < is_exp * 0.5:
            quality_warn = " ⚠️  OOS EXPECTANCY < 50% OF IN-SAMPLE — POSSIBLE OVERFIT"

        print(f"  Strategy: {strat_name}{quality_warn}")
        print(f"  {'Metric':<22} {'In-Sample (70%)':<22} {'Out-of-Sample (30%)'}")
        print(f"  {'-'*66}")
        for key in ["trade_count", "win_rate", "expectancy", "profit_factor",
                    "net_pnl", "max_drawdown", "avg_hold_min", "worst_day"]:
            is_val = is_m[key]
            oos_val = oos_m[key]
            fmt = "{:.1f}" if key in ("win_rate", "avg_hold_min") else "{:.2f}"
            print(f"  {key:<22} {fmt.format(is_val):<22} {fmt.format(oos_val)}")
        print()

        rows.append({
            "strategy": strat_name,
            "oos_expectancy": oos_m["expectancy"],
            "oos_net_pnl": oos_m["net_pnl"],
            "oos_win_rate": oos_m["win_rate"],
            "oos_max_drawdown": oos_m["max_drawdown"],
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

    # Comparison table sorted by OOS expectancy
    rows.sort(key=lambda r: r["oos_expectancy"], reverse=True)
    print("  COMPARISON (sorted by OOS expectancy)")
    print(f"  {'Strategy':<22} {'OOS Exp ₹':<14} {'OOS P&L ₹':<14} {'OOS Win%':<12} {'IS Exp ₹'}")
    print(f"  {'-'*72}")
    for r in rows:
        print(f"  {r['strategy']:<22} {r['oos_expectancy']:<14.2f} "
              f"{r['oos_net_pnl']:<14.2f} {r['oos_win_rate']:<12.1f} {r['is_expectancy']:.2f}")
    print(f"\n{'='*80}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import json
    import sys
    from pathlib import Path

    # Allow running from project root
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
    parser.add_argument("--symbols", default=",".join(config.SYMBOLS),
                        help="Comma-separated Upstox instrument keys")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    strat_names = (
        list(config.STRATEGY_PARAMS.keys())
        if args.strategies == "all"
        else [s.strip() for s in args.strategies.split(",")]
    )
    strategies = build_strategies(strat_names, config.STRATEGY_PARAMS)

    cost_params = {
        "brokerage_flat": config.BROKERAGE_FLAT,
        "brokerage_pct": config.BROKERAGE_PCT,
        "stt_pct": config.STT_PCT,
        "exchange_txn_pct": config.EXCHANGE_TXN_PCT,
        "sebi_charge_pct": config.SEBI_CHARGE_PCT,
        "stamp_duty_pct": config.STAMP_DUTY_PCT,
        "gst_pct": config.GST_PCT,
    }

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Load cached data — fail loudly if not cached
    candles_5m: dict[str, pd.DataFrame] = {}
    candles_15m: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            candles_5m[sym] = data_store.load(sym, 5, args.from_date, args.to_date)
            candles_15m[sym] = data_store.load(sym, 15, args.from_date, args.to_date)
            logger.info("Loaded %s: %d 5-min candles", sym, len(candles_5m[sym]))
        except FileNotFoundError as e:
            print(f"ERROR: {e}")
            print("Run `python -m backtest.downloader` first to cache historical data.")
            sys.exit(1)

    strategy_trades: dict[str, list[dict]] = {}
    for strategy in strategies:
        engine = BacktestEngine(
            strategy=strategy,
            symbols=symbols,
            capital=config.CAPITAL,
            max_risk_per_trade=config.MAX_RISK_PER_TRADE,
            daily_loss_limit=config.DAILY_LOSS_LIMIT,
            cost_params=cost_params,
            slippage_bps=config.SLIPPAGE_BPS,
        )
        trades = engine.run(candles_5m, candles_15m)
        strategy_trades[strategy.name] = [
            {
                "symbol": t.symbol,
                "side": t.side,
                "qty": t.qty,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "sl": t.sl,
                "target": t.target,
                "gross_pnl": t.gross_pnl,
                "txn_cost": t.txn_cost,
                "pnl": t.pnl,
                "exit_reason": t.exit_reason,
            }
            for t in trades
        ]
        logger.info("Strategy %s: %d trades completed", strategy.name, len(trades))

    generate(
        strategy_trades,
        output_dir=config.LOGS_DIR,
        date_range=f"{args.from_date} → {args.to_date}",
    )


if __name__ == "__main__":
    _cli()
