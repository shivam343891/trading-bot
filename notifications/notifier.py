"""
Notification dispatcher.

Primary:  Telegram  (near-instant, every trade event)
Fallback: Gmail SMTP (EOD summary + any Telegram failures)
"""
import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from typing import Any

logger = logging.getLogger(__name__)

# Try to import telegram; gracefully degrade if not installed yet
try:
    import asyncio
    import telegram as tg
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not installed — Telegram notifications disabled")


class Notifier:
    def __init__(
        self,
        telegram_token: str,
        telegram_chat_id: str,
        notify_email: str,
        gmail_app_password: str,
        smtp_host: str,
        smtp_port: int,
    ) -> None:
        self._tg_token = telegram_token
        self._tg_chat_id = str(telegram_chat_id)
        self._email = notify_email
        self._gmail_pw = gmail_app_password
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port

        self._bot = tg.Bot(token=telegram_token) if _TELEGRAM_AVAILABLE and telegram_token != "YOUR_TELEGRAM_BOT_TOKEN" else None

    # ── Public API ────────────────────────────────────────────────────────────

    def send(self, event: str, payload: dict[str, Any]) -> None:
        message = self._format(event, payload)
        if not message:
            return
        self._send_telegram(message)
        if event == "END_OF_DAY":
            self._send_email(subject="Trading Bot — EOD Summary", body=message)

    def halt(self, daily_pnl: float, limit: float) -> None:
        self.send("DAILY_HALT", {"daily_pnl": daily_pnl, "limit": limit})

    # ── Message templates ────────────────────────────────────────────────────

    def _format(self, event: str, p: dict) -> str:
        sym = p.get("symbol", "")
        short_sym = sym.split("|")[-1] if "|" in sym else sym  # strip instrument key prefix

        if event == "TRADE_ENTRY":
            side_emoji = "🟢" if p["side"] == "BUY" else "🔴"
            action = "BUY" if p["side"] == "BUY" else "SELL"
            risk = abs(p["entry"] - p["sl"]) * p["qty"]
            return (
                f"{side_emoji} {action} {short_sym} @ ₹{p['entry']:.2f}\n"
                f"SL: ₹{p['sl']:.2f} | Target: ₹{p['target']:.2f}\n"
                f"Qty: {p['qty']} | Risk: ₹{risk:.0f}"
            )

        if event == "TRADE_EXIT_TARGET":
            pnl_sign = "+" if p["pnl"] >= 0 else ""
            dpnl_sign = "+" if p["daily_pnl"] >= 0 else ""
            return (
                f"✅ EXITED {short_sym} @ ₹{p['exit_price']:.2f}\n"
                f"P&L: {pnl_sign}₹{p['pnl']:.0f} | Reason: Target hit\n"
                f"Daily P&L: {dpnl_sign}₹{p['daily_pnl']:.0f}"
            )

        if event == "TRADE_EXIT_SL":
            pnl_sign = "+" if p["pnl"] >= 0 else ""
            dpnl_sign = "+" if p["daily_pnl"] >= 0 else ""
            return (
                f"🔴 EXITED {short_sym} @ ₹{p['exit_price']:.2f}\n"
                f"P&L: {pnl_sign}₹{p['pnl']:.0f} | Reason: SL hit\n"
                f"Daily P&L: {dpnl_sign}₹{p['daily_pnl']:.0f}"
            )

        if event == "TRADE_EXIT_EOD":
            pnl_sign = "+" if p["pnl"] >= 0 else ""
            dpnl_sign = "+" if p["daily_pnl"] >= 0 else ""
            return (
                f"🕒 EXITED {short_sym} @ ₹{p['exit_price']:.2f}\n"
                f"P&L: {pnl_sign}₹{p['pnl']:.0f} | Reason: EOD flatten\n"
                f"Daily P&L: {dpnl_sign}₹{p['daily_pnl']:.0f}"
            )

        if event == "DAILY_HALT":
            return (
                f"⛔ BOT HALTED\n"
                f"Daily loss ₹{abs(p['daily_pnl']):.0f} exceeded limit of ₹{p['limit']:.0f}\n"
                f"No more trades today."
            )

        if event == "END_OF_DAY":
            trades = p.get("trades", 0)
            wins = p.get("wins", 0)
            losses = p.get("losses", 0)
            net_pnl = p.get("net_pnl", 0.0)
            win_rate = (wins / trades * 100) if trades > 0 else 0
            pnl_sign = "+" if net_pnl >= 0 else ""
            best = p.get("best", "N/A")
            worst = p.get("worst", "N/A")
            return (
                f"📊 SESSION SUMMARY\n"
                f"Trades: {trades} | Wins: {wins} | Losses: {losses}\n"
                f"Net P&L: {pnl_sign}₹{net_pnl:.0f} | Win rate: {win_rate:.0f}%\n"
                f"Best: {best} | Worst: {worst}"
            )

        return ""

    # ── Transport ─────────────────────────────────────────────────────────────

    def _send_telegram(self, text: str) -> None:
        if not self._bot:
            logger.debug("Telegram not configured — skipping: %s", text[:60])
            return
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(
                self._bot.send_message(chat_id=self._tg_chat_id, text=text)
            )
            loop.close()
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)
            self._send_email(subject="Trading Bot Alert (Telegram fallback)", body=text)

    def _send_email(self, subject: str, body: str) -> None:
        if not self._gmail_pw or self._gmail_pw == "YOUR_GMAIL_APP_PASSWORD":
            logger.debug("Email not configured — skipping")
            return
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = self._email
            msg["To"] = self._email
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(self._smtp_host, self._smtp_port, context=ctx) as server:
                server.login(self._email, self._gmail_pw)
                server.sendmail(self._email, self._email, msg.as_string())
        except Exception as exc:
            logger.error("Email send failed: %s", exc)
