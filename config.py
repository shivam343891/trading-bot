# ── Upstox credentials ────────────────────────────────────────────────────────
UPSTOX_API_KEY       = "YOUR_API_KEY"
UPSTOX_API_SECRET    = "YOUR_API_SECRET"
UPSTOX_REDIRECT_URI  = "http://localhost:8000/callback"
UPSTOX_TOKEN_FILE    = "token.json"

# ── Capital & risk ────────────────────────────────────────────────────────────
CAPITAL              = 100_000       # total capital in ₹ (paper trades at this notional)
DAILY_LOSS_LIMIT     = 2_000        # halt if daily P&L hits -₹2000
MAX_RISK_PER_TRADE   = 0.01         # 1% of capital per trade
MIN_RR_RATIO         = 1.5          # minimum risk:reward ratio to enter

# ── Timing ────────────────────────────────────────────────────────────────────
CANDLE_TF            = 5            # primary candle timeframe in minutes
CANDLE_TF_SLOW       = 15           # slow timeframe for bias check (minutes)
MARKET_OPEN          = "09:30"      # skip 09:15–09:30 opening noise
MARKET_CLOSE         = "15:15"      # flatten all positions at this time
LOOP_INTERVAL_SECS   = 30           # main loop polling interval

# ── Symbol universe ───────────────────────────────────────────────────────────
# Nifty-50-grade liquidity. ISINs sourced from Upstox master contract file.
# Roadmap: keep universe small (~10) to limit feed load + multiple-testing bias.
# Expansion is a config change, not a code change.
SYMBOLS = [
    "NSE_EQ|INE040A01034",   # HDFCBANK
    "NSE_EQ|INE090A01021",   # ICICIBANK
    "NSE_EQ|INE062A01020",   # SBIN
    "NSE_EQ|INE009A01021",   # INFY
    "NSE_EQ|INE467B01029",   # TCS
    "NSE_EQ|INE002A01018",   # RELIANCE
    "NSE_EQ|INE155A01022",   # TATAMOTORS
    "NSE_EQ|INE296A01024",   # BAJFINANCE
    "NSE_EQ|INE423A01024",   # ADANIENT
]

# Maps Upstox instrument key → NSE ticker (for news filter + reporting)
SYMBOL_TO_NSE_CODE = {
    "NSE_EQ|INE040A01034": "HDFCBANK",
    "NSE_EQ|INE090A01021": "ICICIBANK",
    "NSE_EQ|INE062A01020": "SBIN",
    "NSE_EQ|INE009A01021": "INFY",
    "NSE_EQ|INE467B01029": "TCS",
    "NSE_EQ|INE002A01018": "RELIANCE",
    "NSE_EQ|INE155A01022": "TATAMOTORS",
    "NSE_EQ|INE296A01024": "BAJFINANCE",
    "NSE_EQ|INE423A01024": "ADANIENT",
}

# ── Indicators ────────────────────────────────────────────────────────────────
EMA_FAST             = 9
EMA_SLOW             = 21
RSI_PERIOD           = 14
VOLUME_PERIOD        = 20           # periods for avg volume baseline
VOLUME_MULTIPLIER    = 1.5          # volume must exceed this × avg to trigger

# RSI filter windows
RSI_LONG_LOW         = 40
RSI_LONG_HIGH        = 55
RSI_SHORT_LOW        = 45
RSI_SHORT_HIGH       = 60

# ── Historical pre-warm ───────────────────────────────────────────────────────
PREWARM_DAYS         = 5            # days of historical data to fetch on startup

# ── Notifications ─────────────────────────────────────────────────────────────
TELEGRAM_TOKEN       = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID     = "YOUR_TELEGRAM_CHAT_ID"
NOTIFY_EMAIL         = "you@gmail.com"
GMAIL_APP_PASSWORD   = "YOUR_GMAIL_APP_PASSWORD"
SMTP_HOST            = "smtp.gmail.com"
SMTP_PORT            = 465

# ── Storage ───────────────────────────────────────────────────────────────────
TRADE_DB_PATH        = "storage/trades.db"
LOGS_DIR             = "logs"

# ── Strategy selection (per-strategy symbol lists) ────────────────────────────
# Each strategy runs only on its listed symbols. Use "all" to apply to every
# symbol in SYMBOLS. Strategies that fail Stage-1 backtesting are disabled
# here, not deleted.
#
# Roadmap symbol specialisation:
#   ORB:            index-sensitive / gap-prone (banks, INFY, TCS, RELIANCE)
#   mean_reversion: high-beta names only (TATAMOTORS, BAJFINANCE, ADANIENT)
#   ema_vwap_rsi:   all liquid names
ACTIVE_STRATEGIES = {
    "ema_vwap_rsi":   "all",
    "orb":            ["NSE_EQ|INE009A01021",   # INFY
                       "NSE_EQ|INE467B01029",   # TCS
                       "NSE_EQ|INE002A01018",   # RELIANCE
                       "NSE_EQ|INE040A01034",   # HDFCBANK
                       "NSE_EQ|INE090A01021"],  # ICICIBANK
    "mean_reversion": ["NSE_EQ|INE155A01022",   # TATAMOTORS
                       "NSE_EQ|INE296A01024",   # BAJFINANCE
                       "NSE_EQ|INE423A01024"],  # ADANIENT
}

STRATEGY_PARAMS = {
    "ema_vwap_rsi": {
        "ema_fast":          EMA_FAST,
        "ema_slow":          EMA_SLOW,
        "rsi_period":        RSI_PERIOD,
        "volume_period":     VOLUME_PERIOD,
        "volume_multiplier": VOLUME_MULTIPLIER,
        "rsi_long_low":      RSI_LONG_LOW,
        "rsi_long_high":     RSI_LONG_HIGH,
        "rsi_short_low":     RSI_SHORT_LOW,
        "rsi_short_high":    RSI_SHORT_HIGH,
        "min_rr_ratio":      MIN_RR_RATIO,
    },
    "orb": {
        "orb_minutes":   30,     # minutes after 09:15 that define the opening range
        "gap_min":       0.003,  # minimum gap % vs prev close to trade ORB
        "rvol_min":      1.3,    # minimum relative volume at OR completion
        "vol_lookback":  20,     # days used to compute average OR volume
        "min_rr_ratio":  MIN_RR_RATIO,
    },
    "mean_reversion": {
        "move_pct":       0.02,  # % move from day open that triggers fade
        "rsi_period":     14,
        "rsi_oversold":   25,
        "rsi_overbought": 75,
        "retrace_frac":   0.5,   # target = 50% retrace of move back to day open
        "min_rr_ratio":   MIN_RR_RATIO,
    },
}

# ── News filter blackout dates ────────────────────────────────────────────────
# Dates where mean-reversion is blocked regardless of news filter (budget, RBI, expiry)
NEWS_BLACKOUT_DATES  = [
    # "2026-02-01",  # Union Budget
]

# ── Backtest cost model (Indian intraday equity) ───────────────────────────────
BROKERAGE_FLAT       = 20.0          # ₹ per order (Zerodha-style flat fee)
BROKERAGE_PCT        = 0.0005        # 0.05% — whichever is lower applies
STT_PCT              = 0.00025       # 0.025% on sell side only
EXCHANGE_TXN_PCT     = 0.0000297     # NSE transaction charge both legs
SEBI_CHARGE_PCT      = 0.000001      # ₹10 per crore
STAMP_DUTY_PCT       = 0.00003       # 0.003% on buy side
GST_PCT              = 0.18          # 18% GST on (brokerage + exchange charges)
SLIPPAGE_BPS         = 5             # slippage in basis points each way

# ── Stage gate criteria (roadmap) ─────────────────────────────────────────────
# Used by backtest/report.py to emit pass/fail verdicts.
GATE_MIN_TRADES      = 60            # minimum trades in full period to be valid
GATE_MAX_DRAWDOWN_PCT = 0.15         # max drawdown must be < 15% of capital
# OOS expectancy > 0 AND >= 50% of in-sample expectancy — checked in report.py

# ── Mode ──────────────────────────────────────────────────────────────────────
PAPER_TRADING        = True         # flip to False to go live with real orders
