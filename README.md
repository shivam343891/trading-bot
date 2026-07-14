# Autonomous Intraday Trading Bot — Upstox Edition

Fully autonomous intraday bot using EMA + VWAP + RSI + volume signals.
Paper trading by default — flip one flag to go live.

## Project structure

```
trading-bot/
├── main.py                  # entry point, orchestrator loop
├── config.py                # ALL tunables — edit this first
├── broker/
│   ├── base.py              # abstract broker interface
│   ├── paper.py             # paper trading (no real orders)
│   └── upstox.py            # live Upstox wrapper
├── data/
│   ├── feed.py              # WebSocket → OHLCV candle aggregator
│   └── indicators.py        # EMA, RSI, VWAP, avg volume
├── strategy/
│   └── signals.py           # signal logic → Signal dataclass
├── risk/
│   └── manager.py           # position sizing, SL/target exits, halt
├── notifications/
│   └── notifier.py          # Telegram + email alerts
├── storage/
│   └── trade_log.py         # SQLite trade journal + CSV export
├── dashboard/
│   └── app.py               # Streamlit live P&L viewer
├── Dockerfile
└── docker-compose.yml
```

## Quick start (local)

### 1. One-time setup

```bash
# Clone / download this repo, then:
cd trading-bot
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure credentials

Edit `config.py`:

| Field | Where to get it |
|---|---|
| `UPSTOX_API_KEY` / `UPSTOX_API_SECRET` | [developer.upstox.com](https://developer.upstox.com) → create app, set redirect URI to `http://localhost:8000/callback` |
| `TELEGRAM_TOKEN` | [@BotFather](https://t.me/BotFather) on Telegram |
| `TELEGRAM_CHAT_ID` | Send `/start` to your bot, then `https://api.telegram.org/bot<TOKEN>/getUpdates` |
| `GMAIL_APP_PASSWORD` | Google Account → Security → App Passwords |

### 3. Run

```bash
# Paper trading (default — safe to run anytime)
python main.py

# Dashboard (separate terminal)
streamlit run dashboard/app.py
```

On first run in live mode (`PAPER_TRADING = False`), a browser opens for Upstox OAuth login.
The token is saved to `token.json` and auto-reused on subsequent days.

### 4. Go live

When you're satisfied with paper results:

```python
# config.py
PAPER_TRADING = False   # ← only change needed
```

## Docker (cloud deployment)

```bash
# Copy token.json to project root first (needed for live mode)
docker compose up -d

# Bot logs
docker logs -f trading_bot

# Dashboard at http://localhost:8501
```

## Strategy logic

```
15-min EMA9 > EMA21  →  bullish bias
5-min:  VWAP reclaim + RSI 40–55 + volume > 1.5× avg  →  BUY signal
5-min:  VWAP breakdown + RSI 45–60 + volume > 1.5× avg  →  SELL signal

SL     = signal candle low (long) or high (short)
Target = entry ± risk × 1.5  (MIN_RR_RATIO)
```

## Risk controls

- **Daily loss limit** (`DAILY_LOSS_LIMIT = ₹2,000`): bot halts automatically
- **Per-trade risk** (`MAX_RISK_PER_TRADE = 1%`): position sized to risk exactly 1% of capital
- **EOD flatten** at `15:15`: all open positions closed regardless of P&L
- **No position stacking**: only one position per symbol at a time

## Notifications

Every trade fires a Telegram message instantly. EOD summary goes to email as well.

| Event | Channel |
|---|---|
| Trade entry / exit | Telegram |
| Daily halt | Telegram |
| EOD summary | Telegram + Email |

## Tuning parameters (all in config.py)

| Param | Default | Effect |
|---|---|---|
| `CAPITAL` | 1,00,000 | Total capital |
| `DAILY_LOSS_LIMIT` | 2,000 | Daily drawdown cap |
| `MAX_RISK_PER_TRADE` | 0.01 | 1% risk per trade |
| `MIN_RR_RATIO` | 1.5 | Minimum risk:reward |
| `EMA_FAST / EMA_SLOW` | 9 / 21 | Trend bias |
| `RSI_LONG_LOW/HIGH` | 40 / 55 | RSI filter for longs |
| `VOLUME_MULTIPLIER` | 1.5 | Volume confirmation threshold |

## Total cost: ₹0
