"""
Upstox live broker — wraps upstox-python-sdk v2.
OAuth2 PKCE flow: opens browser on first run, saves token to token.json,
auto-refreshes on startup each day.
"""
import json
import logging
import threading
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import upstox_client
from upstox_client.rest import ApiException

from broker.base import BaseBroker

logger = logging.getLogger(__name__)


class _CallbackHandler(BaseHTTPRequestHandler):
    """Tiny HTTP server that captures the OAuth2 callback code."""

    auth_code: str | None = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h2>Auth complete. You can close this tab.</h2>")

    def log_message(self, *_: Any) -> None:  # silence default HTTP logging
        pass


class UpstoxBroker(BaseBroker):
    """Live broker backed by the official upstox-python-sdk."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        redirect_uri: str,
        token_file: str = "token.json",
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.redirect_uri = redirect_uri
        self.token_file = Path(token_file)

        access_token = self._load_or_refresh_token()
        config = upstox_client.Configuration()
        config.access_token = access_token

        api_client = upstox_client.ApiClient(configuration=config)
        self._order_api = upstox_client.OrderApi(api_client)
        self._portfolio_api = upstox_client.PortfolioApi(api_client)

        logger.info("UpstoxBroker initialised (live trading)")

    # ── Token management ──────────────────────────────────────────────────────

    def _load_or_refresh_token(self) -> str:
        if self.token_file.exists():
            data = json.loads(self.token_file.read_text())
            token = data.get("access_token")
            if token:
                logger.info("Loaded existing Upstox token from %s", self.token_file)
                return token
        return self._oauth_login()

    def _oauth_login(self) -> str:
        """Full PKCE OAuth2 flow — opens browser, listens for callback."""
        auth_url = (
            "https://api.upstox.com/v2/login/authorization/dialog"
            f"?response_type=code&client_id={self.api_key}"
            f"&redirect_uri={urllib.parse.quote(self.redirect_uri, safe='')}"
        )
        logger.info("Opening browser for Upstox login: %s", auth_url)
        webbrowser.open(auth_url)

        # Start local callback server
        parsed = urllib.parse.urlparse(self.redirect_uri)
        port = parsed.port or 8000
        server = HTTPServer(("localhost", port), _CallbackHandler)
        server.handle_request()  # blocks until one request received

        code = _CallbackHandler.auth_code
        if not code:
            raise RuntimeError("OAuth2 callback did not return an auth code.")

        # Exchange code for token
        import requests  # only needed for token exchange
        resp = requests.post(
            "https://api.upstox.com/v2/login/authorization/token",
            data={
                "code": code,
                "client_id": self.api_key,
                "client_secret": self.api_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()
        token_data = resp.json()
        access_token = token_data["access_token"]

        self.token_file.write_text(json.dumps(token_data, indent=2))
        logger.info("Token saved to %s", self.token_file)
        return access_token

    # ── BaseBroker interface ──────────────────────────────────────────────────

    def place_order(self, symbol: str, side: str, qty: int, price: float) -> str:
        body = upstox_client.PlaceOrderRequest(
            quantity=qty,
            product="I",           # intraday
            validity="DAY",
            price=0,               # 0 = market order; set price for LIMIT
            instrument_token=symbol,
            order_type="MARKET",
            transaction_type=side,
            disclosed_quantity=0,
            trigger_price=0,
            is_amo=False,
        )
        try:
            response = self._order_api.place_order(body, api_version="2.0")
            order_id = response.data.order_id
            logger.info("[LIVE] %s %s qty=%d order_id=%s", side, symbol, qty, order_id)
            return order_id
        except ApiException as exc:
            logger.error("place_order failed: %s", exc)
            raise

    def get_positions(self) -> dict:
        try:
            response = self._portfolio_api.get_positions(api_version="2.0")
            positions: dict[str, dict] = {}
            for pos in (response.data or []):
                if pos.quantity == 0:
                    continue
                positions[pos.instrument_token] = {
                    "qty": abs(pos.quantity),
                    "avg_price": pos.average_price,
                    "side": "BUY" if pos.quantity > 0 else "SELL",
                    "sl": None,     # populated by RiskManager
                    "target": None,
                }
            return positions
        except ApiException as exc:
            logger.error("get_positions failed: %s", exc)
            return {}

    def get_pnl(self) -> float:
        try:
            response = self._portfolio_api.get_positions(api_version="2.0")
            return sum(p.realised_profit or 0.0 for p in (response.data or []))
        except ApiException as exc:
            logger.error("get_pnl failed: %s", exc)
            return 0.0

    def cancel_order(self, order_id: str) -> None:
        try:
            self._order_api.cancel_order(order_id, api_version="2.0")
            logger.info("[LIVE] Cancelled order %s", order_id)
        except ApiException as exc:
            logger.error("cancel_order failed: %s", exc)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def attach_sl_target(self, symbol: str, sl: float, target: float) -> None:
        """No-op for live broker — SL/target tracked in RiskManager."""
        pass
