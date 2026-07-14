"""
Streamlit live P&L dashboard.

Run with:
    streamlit run dashboard/app.py

Reads directly from storage/trades.db — no bot process needed.
Auto-refreshes every 30 seconds.
"""
import sys
from pathlib import Path

# Allow imports from project root when launched as `streamlit run dashboard/app.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time
from datetime import date

import pandas as pd
import streamlit as st

import config
from storage.trade_log import TradeLog

st.set_page_config(
    page_title="Trading Bot Dashboard",
    page_icon="📈",
    layout="wide",
)

REFRESH_SECS = 30

_trade_log = TradeLog(config.TRADE_DB_PATH, config.LOGS_DIR)


def _load_today() -> pd.DataFrame:
    rows = _trade_log.get_today()
    if not rows:
        return pd.DataFrame(columns=[
            "id", "symbol", "side", "qty", "entry_price", "exit_price",
            "sl", "target", "entry_time", "exit_time", "pnl", "exit_reason",
        ])
    df = pd.DataFrame(rows)
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["exit_time"] = pd.to_datetime(df["exit_time"], errors="coerce")
    return df


def _pnl_color(val: float | None) -> str:
    if val is None or pd.isna(val):
        return "gray"
    return "green" if val >= 0 else "red"


# ── Header ────────────────────────────────────────────────────────────────────
st.title("📈 Intraday Trading Bot — Live Dashboard")
st.caption(f"Paper trading: {'ON' if config.PAPER_TRADING else 'OFF'} · Auto-refresh every {REFRESH_SECS}s")

df = _load_today()

# ── Top KPI row ───────────────────────────────────────────────────────────────
closed = df[df["exit_price"].notna()]
open_trades = df[df["exit_price"].isna()]

net_pnl = float(closed["pnl"].sum()) if not closed.empty else 0.0
total_trades = len(closed)
wins = int((closed["pnl"] > 0).sum()) if not closed.empty else 0
losses = int((closed["pnl"] <= 0).sum()) if not closed.empty else 0
win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
open_count = len(open_trades)

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Net P&L", f"₹{net_pnl:,.0f}", delta_color="normal")
col2.metric("Trades", total_trades)
col3.metric("Win Rate", f"{win_rate:.0f}%")
col4.metric("Open Positions", open_count)
col5.metric("Daily Limit", f"₹{config.DAILY_LOSS_LIMIT:,}", delta=f"₹{net_pnl:,.0f} used")

st.divider()

# ── Cumulative P&L chart ──────────────────────────────────────────────────────
st.subheader("Cumulative P&L")
if not closed.empty:
    pnl_series = closed.sort_values("exit_time")[["exit_time", "pnl"]].copy()
    pnl_series["cumulative_pnl"] = pnl_series["pnl"].cumsum()
    st.line_chart(pnl_series.set_index("exit_time")["cumulative_pnl"])
else:
    st.info("No closed trades yet today.")

# ── Win / Loss donut ──────────────────────────────────────────────────────────
col_a, col_b = st.columns(2)

with col_a:
    st.subheader("Win / Loss Split")
    if total_trades > 0:
        donut_df = pd.DataFrame({"Result": ["Wins", "Losses"], "Count": [wins, losses]})
        st.bar_chart(donut_df.set_index("Result"))
    else:
        st.info("No closed trades yet.")

with col_b:
    st.subheader("P&L by Symbol")
    if not closed.empty:
        by_sym = closed.groupby("symbol")["pnl"].sum().reset_index()
        by_sym["symbol"] = by_sym["symbol"].str.split("|").str[-1]
        st.bar_chart(by_sym.set_index("symbol")["pnl"])
    else:
        st.info("No closed trades yet.")

# ── Open positions table ──────────────────────────────────────────────────────
st.subheader("Open Positions")
if not open_trades.empty:
    display_open = open_trades[["symbol", "side", "qty", "entry_price", "sl", "target", "entry_time"]].copy()
    display_open["symbol"] = display_open["symbol"].str.split("|").str[-1]
    st.dataframe(display_open, use_container_width=True)
else:
    st.info("No open positions.")

# ── Today's trade log ─────────────────────────────────────────────────────────
st.subheader("Today's Trades")
if not closed.empty:
    display = closed[["symbol", "side", "qty", "entry_price", "exit_price", "pnl", "exit_reason", "entry_time", "exit_time"]].copy()
    display["symbol"] = display["symbol"].str.split("|").str[-1]
    display = display.sort_values("entry_time", ascending=False)

    def _color_row(row: pd.Series) -> list[str]:
        color = "background-color: #d4edda" if row["pnl"] >= 0 else "background-color: #f8d7da"
        return [color] * len(row)

    st.dataframe(
        display.style.apply(_color_row, axis=1),
        use_container_width=True,
    )
else:
    st.info("No completed trades today.")

# ── Auto-refresh ──────────────────────────────────────────────────────────────
st.caption(f"Last updated: {pd.Timestamp.now().strftime('%H:%M:%S')}")
time.sleep(REFRESH_SECS)
st.rerun()
