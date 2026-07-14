"""
main.py — orchestrator entry point (v2).

Startup → auth → pre-warm candles → holiday/weekend check →
wait for market open → main loop → EOD cleanup.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from datetime import date, datetime

# Load .env before importing config (Phase 3)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import config

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs(config.LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"{config.LOGS_DIR}/bot_{date.today().isoformat()}.log"),
    ],
)
logger = logging.getLogger("main")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _market_open_time() -> datetime:
    h, m = config.MARKET_OPEN.split(":")
    return datetime.now().replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def _market_close_time() -> datetime:
    h, m = config.MARKET_CLOSE.split(":")
    return datetime.now().replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def _build_eod_summary(trade_log) -> dict:
    trades = trade_log.get_today()
    closed = [t for t in trades if t.get("exit_price") is not None]
    wins = [t for t in closed if (t.get("pnl") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl") or 0) <= 0]
    net_pnl = sum(t.get("pnl") or 0 for t in closed)

    best_trade = max(closed, key=lambda t: t.get("pnl") or 0, default=None)
    worst_trade = min(closed, key=lambda t: t.get("pnl") or 0, default=None)

    def _label(t):
        if t is None:
            return "N/A"
        sym = t["symbol"].split("|")[-1]
        pnl = t.get("pnl") or 0
        sign = "+" if pnl >= 0 else ""
        return f"{sym} {sign}₹{pnl:.0f}"

    expectancy = net_pnl / len(closed) if closed else 0.0

    # Paper-vs-backtest expectancy for today — auto-detect backtest CSVs
    bt_expectancy_by_strategy: dict[str, float] = {}
    today_str = date.today().isoformat()
    try:
        import csv as _csv
        from pathlib import Path as _Path
        for p in _Path(config.LOGS_DIR).glob("backtest_*.csv"):
            strat = p.stem.replace("backtest_", "")
            day_pnls = []
            with open(p, newline="", encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    et = row.get("entry_time", "")
                    if et.startswith(today_str):
                        try:
                            day_pnls.append(float(row.get("pnl", 0)))
                        except (ValueError, TypeError):
                            pass
            if day_pnls:
                bt_expectancy_by_strategy[strat] = sum(day_pnls) / len(day_pnls)
    except Exception:
        pass

    return {
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl": net_pnl,
        "expectancy": expectancy,
        "best": _label(best_trade),
        "worst": _label(worst_trade),
        "bt_expectancy": bt_expectancy_by_strategy,
    }


def _build_upstox_api_client():
    import json
    import upstox_client
    from pathlib import Path

    token_path = Path(config.UPSTOX_TOKEN_FILE)
    cfg = upstox_client.Configuration()
    if token_path.exists():
        try:
            token_data = json.loads(token_path.read_text())
            cfg.access_token = token_data.get("access_token", "")
        except Exception:
            pass
    return upstox_client.ApiClient(configuration=cfg)


# ── EOD flatten with retry ─────────────────────────────────────────────────────

def _run_eod(broker, feed, risk, trade_log, notifier) -> None:
    positions = broker.get_positions()
    failed_syms: list[str] = []

    for sym, pos in positions.items():
        ltp = feed.get_ltp(sym) or pos["avg_price"]
        close_side = "SELL" if pos["side"] == "BUY" else "BUY"

        success = False
        for attempt in range(1, 4):
            try:
                broker.place_order(sym, close_side, pos["qty"], ltp)
                pnl = ((ltp - pos["avg_price"]) * pos["qty"]
                       if pos["side"] == "BUY"
                       else (pos["avg_price"] - ltp) * pos["qty"])
                meta = risk._position_meta.get(sym, {})
                if meta:
                    trade_log.update_exit(
                        trade_id=meta.get("trade_id", 0),
                        exit_price=ltp,
                        exit_time=datetime.now(),
                        pnl=pnl,
                        exit_reason="eod_flatten",
                    )
                risk.update_daily_pnl(pnl, notifier)
                notifier.send("TRADE_EXIT_EOD", {
                    "symbol": sym,
                    "exit_price": ltp,
                    "pnl": pnl,
                    "daily_pnl": risk.daily_pnl,
                })
                success = True
                break
            except Exception as exc:
                logger.error("EOD flatten attempt %d/%d failed for %s: %s", attempt, 3, sym, exc)
                time.sleep(5)

        if not success:
            failed_syms.append(sym)

    # Alert on any unflattenned positions — keep alerting every 60s
    if failed_syms:
        _critical_alert_loop(failed_syms, notifier)

    summary = _build_eod_summary(trade_log)
    notifier.send("END_OF_DAY", summary)
    trade_log.export_csv(date.today())
    trade_log.clear_state("positions")
    trade_log.clear_state("daily_pnl")

    # Log paper-vs-backtest for today (Stage-2 health check)
    if summary.get("bt_expectancy"):
        paper_exp = summary["expectancy"]
        logger.info("EOD paper-vs-backtest expectancy comparison:")
        for strat, bt_exp in summary["bt_expectancy"].items():
            ratio = (paper_exp / bt_exp * 100) if bt_exp != 0 else float("nan")
            flag = " ⚠️ <50% ratio" if ratio < 50 else ""
            logger.info("  %s  paper ₹%.2f  backtest ₹%.2f  ratio %.0f%%%s",
                        strat, paper_exp, bt_exp, ratio, flag)

    feed.stop()
    logger.info("EOD complete. Net P&L: ₹%.2f  expectancy ₹%.2f",
                summary["net_pnl"], summary["expectancy"])


def _critical_alert_loop(syms: list[str], notifier, max_alerts: int = 10) -> None:
    msg = f"⚠️ FLATTEN FAILED for {', '.join(syms)}. Manual intervention required!"
    for _ in range(max_alerts):
        try:
            notifier.send("FLATTEN_FAILED", {"symbols": syms, "message": msg})
        except Exception:
            pass
        time.sleep(60)


# ── Crash recovery helpers ────────────────────────────────────────────────────

def _persist_state(trade_log, risk, broker) -> None:
    positions = broker.get_positions()
    trade_log.save_state("positions", positions)
    trade_log.save_state("daily_pnl", risk.daily_pnl)
    trade_log.save_state("halted", risk.halted)
    trade_log.save_state("position_meta", {
        k: {kk: str(vv) if not isinstance(vv, (int, float, str, bool, type(None))) else vv
            for kk, vv in v.items()}
        for k, v in risk._position_meta.items()
    })


def _restore_state(trade_log, risk, broker, notifier) -> bool:
    """Restore today's state from SQLite if the bot was restarted mid-session."""
    saved_pnl = trade_log.load_state("daily_pnl")
    saved_halted = trade_log.load_state("halted")
    saved_meta = trade_log.load_state("position_meta")

    if saved_pnl is None:
        return False  # no state saved for today

    risk.daily_pnl = float(saved_pnl)
    risk.halted = bool(saved_halted)

    if saved_meta:
        for sym, meta in saved_meta.items():
            risk._position_meta[sym] = {
                "sl": float(meta.get("sl", 0)),
                "target": float(meta.get("target", 0)),
                "side": str(meta.get("side", "BUY")),
                "qty": int(meta.get("qty", 0)),
                "entry_price": float(meta.get("entry_price", 0)),
                "trade_id": int(meta.get("trade_id", 0)),
            }

    if config.PAPER_TRADING:
        # Paper: restore positions dict from saved state
        saved_positions = trade_log.load_state("positions") or {}
        if hasattr(broker, "positions"):
            broker.positions = saved_positions

    logger.info(
        "State restored: daily_pnl=%.2f  halted=%s  open_positions=%d",
        risk.daily_pnl, risk.halted, len(risk._position_meta),
    )
    notifier.send("STATE_RESTORED", {
        "daily_pnl": risk.daily_pnl,
        "halted": risk.halted,
        "open_count": len(risk._position_meta),
    })
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=== Trading Bot v2 starting (%s mode) ===",
                "PAPER" if config.PAPER_TRADING else "LIVE")

    # ── Phase 3: ensure valid token before anything else ─────────────────────
    try:
        from broker.auth import ensure_token
        ensure_token(notifier=None)  # notifier not ready yet; auth failure exits
    except ImportError:
        logger.debug("broker.auth not available (Phase 3 optional dependency)")
    except SystemExit:
        raise
    except Exception as exc:
        logger.error("Auth failed: %s", exc)
        sys.exit(1)

    # ── Phase 4: holiday/weekend check ───────────────────────────────────────
    from nse_holidays import is_market_holiday
    if is_market_holiday(date.today()):
        logger.info("Today (%s) is a market holiday or weekend — exiting.", date.today())
        sys.exit(0)

    # ── 1. Broker ─────────────────────────────────────────────────────────────
    if config.PAPER_TRADING:
        from broker.paper import PaperBroker
        broker = PaperBroker(capital=config.CAPITAL)
    else:
        from broker.upstox import UpstoxBroker
        broker = UpstoxBroker(
            api_key=config.UPSTOX_API_KEY,
            api_secret=config.UPSTOX_API_SECRET,
            redirect_uri=config.UPSTOX_REDIRECT_URI,
            token_file=config.UPSTOX_TOKEN_FILE,
        )

    # ── 2. Supporting components ──────────────────────────────────────────────
    from notifications.notifier import Notifier
    notifier = Notifier(
        telegram_token=config.TELEGRAM_TOKEN,
        telegram_chat_id=config.TELEGRAM_CHAT_ID,
        notify_email=config.NOTIFY_EMAIL,
        gmail_app_password=config.GMAIL_APP_PASSWORD,
        smtp_host=config.SMTP_HOST,
        smtp_port=config.SMTP_PORT,
    )

    from storage.trade_log import TradeLog
    trade_log = TradeLog(db_path=config.TRADE_DB_PATH, logs_dir=config.LOGS_DIR)

    from risk.manager import RiskManager
    risk = RiskManager(
        capital=config.CAPITAL,
        daily_loss_limit=config.DAILY_LOSS_LIMIT,
        max_risk_per_trade=config.MAX_RISK_PER_TRADE,
        market_close=config.MARKET_CLOSE,
    )

    # ── Phase 4: crash recovery ───────────────────────────────────────────────
    _restore_state(trade_log, risk, broker, notifier)

    # ── Phase 1: strategy registry ────────────────────────────────────────────
    from strategy.registry import build_strategies
    strategy_pairs = build_strategies(
        config.ACTIVE_STRATEGIES, config.STRATEGY_PARAMS, config.SYMBOLS
    )
    logger.info("Active strategies: %s",
                {s.name: syms for s, syms in strategy_pairs})

    # ── 3. Data feed ──────────────────────────────────────────────────────────
    api_client = _build_upstox_api_client()

    from data.feed import DataFeed
    feed = DataFeed(
        api_client=api_client,
        symbols=config.SYMBOLS,
        candle_tf=config.CANDLE_TF,
        candle_tf_slow=config.CANDLE_TF_SLOW,
        prewarm_days=config.PREWARM_DAYS,
    )
    feed.start()

    # ── Phase 5: news filter ──────────────────────────────────────────────────
    try:
        from data.news_filter import NewsFilter
        news_filter = NewsFilter(config.SYMBOL_TO_NSE_CODE)
        news_filter.refresh()
    except Exception:
        news_filter = None

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    _shutdown = {"flag": False}

    def _handle_signal(signum, frame):
        logger.info("Shutdown signal received")
        _shutdown["flag"] = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ── 4. Wait for market open ───────────────────────────────────────────────
    market_open = _market_open_time()
    now = datetime.now()
    if now < market_open:
        wait_secs = (market_open - now).total_seconds()
        logger.info("Waiting %.0f seconds until market open at %s", wait_secs, config.MARKET_OPEN)
        while datetime.now() < market_open and not _shutdown["flag"]:
            time.sleep(5)

    logger.info("Market open — entering main loop")

    # ── Phase 4: signal dedup — tracks last candle timestamp per (symbol, strategy)
    last_processed: dict[tuple[str, str], datetime] = {}

    # ── 5. Main loop ──────────────────────────────────────────────────────────
    eod_done = False
    loop_count = 0

    while not _shutdown["flag"]:
        now = datetime.now()

        if now >= _market_close_time() and not eod_done:
            logger.info("Market close reached — running EOD procedures")
            _run_eod(broker, feed, risk, trade_log, notifier)
            eod_done = True
            break

        # Refresh news filter hourly
        loop_count += 1
        if loop_count % (3600 // config.LOOP_INTERVAL_SECS) == 0 and news_filter:
            try:
                news_filter.refresh()
            except Exception as exc:
                logger.warning("News filter refresh failed: %s", exc)

        try:
            if not risk.can_trade():
                time.sleep(config.LOOP_INTERVAL_SECS)
                continue

            # Gather current prices
            current_prices: dict[str, float] = {}
            for sym in config.SYMBOLS:
                ltp = feed.get_ltp(sym)
                if ltp is not None:
                    current_prices[sym] = ltp

            # Check exits first — Phase 4: unrealized P&L included in risk check
            risk.check_exits(
                current_prices=current_prices,
                broker=broker,
                trade_log=trade_log,
                notifier=notifier,
            )

            open_positions = broker.get_positions()

            # Compute day context values (first 09:15 candle of today)
            day_opens: dict[str, float] = {}
            prev_closes: dict[str, float] = {}
            for sym in config.SYMBOLS:
                df = feed.get_candles(sym, config.CANDLE_TF)
                if df.empty:
                    continue
                today_rows = df[df["timestamp"].dt.date == now.date()]
                day_opens[sym] = float(today_rows.iloc[0]["open"]) if not today_rows.empty else 0.0
                # prev day close = last candle from yesterday
                prev_rows = df[df["timestamp"].dt.date < now.date()]
                prev_closes[sym] = float(prev_rows.iloc[-1]["close"]) if not prev_rows.empty else 0.0

            # Signal scan — each strategy runs only on its configured symbols
            from strategy.base import MarketContext
            for strategy, strat_symbols in strategy_pairs:
                for sym in strat_symbols:
                    if sym not in config.SYMBOLS:
                        continue

                    candles_5m = feed.get_candles_for_signal(sym, config.CANDLE_TF)
                    candles_15m = feed.get_candles_for_signal(sym, config.CANDLE_TF_SLOW)

                    if candles_5m.empty or candles_15m.empty:
                        continue

                    last_candle_ts = candles_5m.iloc[-1]["timestamp"]
                    dedup_key = (sym, strategy.name)
                    if last_processed.get(dedup_key) == last_candle_ts:
                        continue

                    if sym in open_positions:
                        last_processed[dedup_key] = last_candle_ts
                        continue

                    has_news = news_filter.has_news(sym) if news_filter else False

                    ctx = MarketContext(
                        symbol=sym,
                        current_time=now,
                        open_position=False,
                        day_open=day_opens.get(sym, 0.0),
                        prev_day_close=prev_closes.get(sym, 0.0),
                        has_news_today=has_news,
                    )

                    try:
                        sig = strategy.generate_signal(candles_5m, candles_15m, ctx)
                    except Exception as exc:
                        logger.error("Strategy %s error on %s: %s", strategy.name, sym, exc)
                        continue
                    finally:
                        last_processed[dedup_key] = last_candle_ts

                    if sig is None:
                        continue

                    qty = risk.position_size(sig.entry, sig.sl)
                    if qty <= 0:
                        logger.warning("Skipping %s signal for %s — size=0", strategy.name, sym)
                        continue

                    try:
                        broker.place_order(sym, sig.side, qty, sig.entry)
                        if hasattr(broker, "attach_sl_target"):
                            broker.attach_sl_target(sym, sig.sl, sig.target)

                        trade_id = trade_log.insert(
                            symbol=sym,
                            side=sig.side,
                            qty=qty,
                            entry_price=sig.entry,
                            sl=sig.sl,
                            target=sig.target,
                            entry_time=now,
                            strategy_name=strategy.name,
                        )

                        risk.register_position(
                            symbol=sym,
                            side=sig.side,
                            qty=qty,
                            entry_price=sig.entry,
                            sl=sig.sl,
                            target=sig.target,
                            trade_id=trade_id,
                        )

                        notifier.send("TRADE_ENTRY", {
                            "symbol": sym,
                            "side": sig.side,
                            "entry": sig.entry,
                            "sl": sig.sl,
                            "target": sig.target,
                            "qty": qty,
                            "strategy_name": strategy.name,
                        })
                        # Mark position open so no other strategy enters same symbol this tick
                        open_positions[sym] = True
                    except Exception as exc:
                        logger.error("Order/log failed for %s %s: %s", strategy.name, sym, exc)
                        notifier.send("ORDER_ERROR", {"symbol": sym, "strategy": strategy.name, "error": str(exc)})

            # Persist state after each loop iteration
            try:
                _persist_state(trade_log, risk, broker)
            except Exception as exc:
                logger.warning("State persist failed: %s", exc)

        except Exception as exc:
            logger.error("Main loop error (continuing): %s", exc, exc_info=True)
            try:
                notifier.send("LOOP_ERROR", {"error": str(exc)})
            except Exception:
                pass

        time.sleep(config.LOOP_INTERVAL_SECS)

    if not eod_done:
        _run_eod(broker, feed, risk, trade_log, notifier)

    logger.info("Bot stopped cleanly.")


if __name__ == "__main__":
    main()
