"""
main.py — orchestrator entry point.

Startup → pre-warm candles → wait for market open → main loop → EOD cleanup.
"""
import logging
import signal
import sys
import time
from datetime import date, datetime

import config

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/bot_{date.today().isoformat()}.log"),
    ],
)
logger = logging.getLogger("main")


def _market_open_time() -> datetime:
    h, m = config.MARKET_OPEN.split(":")
    return datetime.now().replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def _market_close_time() -> datetime:
    h, m = config.MARKET_CLOSE.split(":")
    return datetime.now().replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def _build_eod_summary(trade_log) -> dict:
    trades = trade_log.get_today()
    closed = [t for t in trades if t["exit_price"] is not None]
    wins = [t for t in closed if (t["pnl"] or 0) > 0]
    losses = [t for t in closed if (t["pnl"] or 0) <= 0]
    net_pnl = sum(t["pnl"] or 0 for t in closed)

    best_trade = max(closed, key=lambda t: t["pnl"] or 0, default=None)
    worst_trade = min(closed, key=lambda t: t["pnl"] or 0, default=None)

    def _label(t):
        sym = t["symbol"].split("|")[-1] if t else "N/A"
        pnl = t["pnl"] if t else 0
        sign = "+" if pnl >= 0 else ""
        return f"{sym} {sign}₹{pnl:.0f}" if t else "N/A"

    return {
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl": net_pnl,
        "best": _label(best_trade),
        "worst": _label(worst_trade),
    }


def main() -> None:
    import os
    os.makedirs(config.LOGS_DIR, exist_ok=True)

    logger.info("=== Trading Bot starting (%s mode) ===",
                "PAPER" if config.PAPER_TRADING else "LIVE")

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

    from strategy.signals import SignalEngine
    signal_engine = SignalEngine(
        ema_fast=config.EMA_FAST,
        ema_slow=config.EMA_SLOW,
        rsi_period=config.RSI_PERIOD,
        volume_period=config.VOLUME_PERIOD,
        volume_multiplier=config.VOLUME_MULTIPLIER,
        rsi_long_low=config.RSI_LONG_LOW,
        rsi_long_high=config.RSI_LONG_HIGH,
        rsi_short_low=config.RSI_SHORT_LOW,
        rsi_short_high=config.RSI_SHORT_HIGH,
        min_rr_ratio=config.MIN_RR_RATIO,
    )

    # ── 3. Data feed ──────────────────────────────────────────────────────────
    if config.PAPER_TRADING:
        # Paper mode: we still need real market data for signals
        # Build an API client with a token if available, else skip live feed
        api_client = _build_upstox_api_client()
    else:
        import upstox_client
        from pathlib import Path
        import json
        token_data = json.loads(Path(config.UPSTOX_TOKEN_FILE).read_text())
        cfg = upstox_client.Configuration()
        cfg.access_token = token_data["access_token"]
        api_client = upstox_client.ApiClient(configuration=cfg)

    from data.feed import DataFeed
    feed = DataFeed(
        api_client=api_client,
        symbols=config.SYMBOLS,
        candle_tf=config.CANDLE_TF,
        candle_tf_slow=config.CANDLE_TF_SLOW,
        prewarm_days=config.PREWARM_DAYS,
    )
    feed.start()

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

    # ── 5. Main loop ──────────────────────────────────────────────────────────
    eod_done = False

    while not _shutdown["flag"]:
        now = datetime.now()
        market_close = _market_close_time()

        # EOD handling
        if now >= market_close and not eod_done:
            logger.info("Market close reached — running EOD procedures")
            _run_eod(broker, feed, risk, trade_log, notifier)
            eod_done = True
            break

        if not risk.can_trade():
            time.sleep(config.LOOP_INTERVAL_SECS)
            continue

        # Gather current prices for exit checks
        current_prices: dict[str, float] = {}
        for sym in config.SYMBOLS:
            ltp = feed.get_ltp(sym)
            if ltp is not None:
                current_prices[sym] = ltp

        # Check exits first (highest priority)
        risk.check_exits(
            current_prices=current_prices,
            broker=broker,
            trade_log=trade_log,
            notifier=notifier,
        )

        # Signal scan
        for sym in config.SYMBOLS:
            candles_5m = feed.get_candles(sym, config.CANDLE_TF)
            candles_15m = feed.get_candles(sym, config.CANDLE_TF_SLOW)

            if candles_5m.empty or candles_15m.empty:
                continue

            sig = signal_engine.check(
                symbol=sym,
                candles_5m=candles_5m,
                candles_15m=candles_15m,
                open_positions=broker.get_positions(),
            )

            if sig is None:
                continue

            qty = risk.position_size(sig.entry, sig.sl)
            if qty <= 0:
                logger.warning("Skipping signal for %s — position size is 0", sym)
                continue

            order_id = broker.place_order(sym, sig.side, qty, sig.entry)
            if hasattr(broker, "attach_sl_target"):
                broker.attach_sl_target(sym, sig.sl, sig.target)

            trade_id = trade_log.insert(
                symbol=sym,
                side=sig.side,
                qty=qty,
                entry_price=sig.entry,
                sl=sig.sl,
                target=sig.target,
                entry_time=datetime.now(),
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
            })

        time.sleep(config.LOOP_INTERVAL_SECS)

    if not eod_done:
        _run_eod(broker, feed, risk, trade_log, notifier)

    logger.info("Bot stopped cleanly.")


def _run_eod(broker, feed, risk, trade_log, notifier) -> None:
    """Flatten all positions, send EOD summary, export CSV."""
    positions = broker.get_positions()
    for sym, pos in positions.items():
        ltp = feed.get_ltp(sym) or pos["avg_price"]
        close_side = "SELL" if pos["side"] == "BUY" else "BUY"
        broker.place_order(sym, close_side, pos["qty"], ltp)

        pnl = (ltp - pos["avg_price"]) * pos["qty"] if pos["side"] == "BUY" else (pos["avg_price"] - ltp) * pos["qty"]
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

    summary = _build_eod_summary(trade_log)
    notifier.send("END_OF_DAY", summary)
    trade_log.export_csv(date.today())
    feed.stop()
    logger.info("EOD complete. Net P&L: ₹%.2f", summary["net_pnl"])


def _build_upstox_api_client():
    """Build an Upstox API client from saved token, or a bare client if no token exists."""
    import upstox_client
    from pathlib import Path
    import json

    token_path = Path(config.UPSTOX_TOKEN_FILE)
    cfg = upstox_client.Configuration()
    if token_path.exists():
        try:
            token_data = json.loads(token_path.read_text())
            cfg.access_token = token_data.get("access_token", "")
        except Exception:
            pass
    return upstox_client.ApiClient(configuration=cfg)


if __name__ == "__main__":
    main()
