# ── Upstox credentials ────────────────────────────────────────────────────────
UPSTOX_API_KEY       = "YOUR_API_KEY"
UPSTOX_API_SECRET    = "YOUR_API_SECRET"
UPSTOX_REDIRECT_URI  = "http://localhost:8000/callback"
UPSTOX_TOKEN_FILE    = "token.json"

# ── Capital & risk ────────────────────────────────────────────────────────────
CAPITAL              = 100_000       # total capital in ₹
DAILY_LOSS_LIMIT     = 2_000        # halt if daily P&L hits -₹2000
MAX_RISK_PER_TRADE   = 0.01         # 1% of capital per trade
MIN_RR_RATIO         = 1.5          # minimum risk:reward ratio to enter

# ── Timing ────────────────────────────────────────────────────────────────────
CANDLE_TF            = 5            # primary candle timeframe in minutes
CANDLE_TF_SLOW       = 15           # slow timeframe for bias check (minutes)
MARKET_OPEN          = "09:30"      # skip 09:15–09:30 opening noise
MARKET_CLOSE         = "15:15"      # flatten all positions at this time
LOOP_INTERVAL_SECS   = 30           # main loop polling interval

# ── Symbols (Upstox instrument key format) ────────────────────────────────────
SYMBOLS = [
    "NSE_EQ|INE009A01021",   # INFOSYS
    "NSE_EQ|INE002A01018",   # RELIANCE
    # "NSE_INDEX|Nifty 50",  # index — add when supported by your account
]

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

# ── Mode ──────────────────────────────────────────────────────────────────────
PAPER_TRADING        = True         # flip to False to go live with real orders
