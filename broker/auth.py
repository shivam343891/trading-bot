"""
Automated Upstox OAuth2 login via Playwright + TOTP.

Credentials sourced from environment variables / .env only — never config.py.
Required env vars:
  UPSTOX_MOBILE       — 10-digit mobile number registered with Upstox
  UPSTOX_PIN          — 6-digit Upstox PIN
  UPSTOX_TOTP_SECRET  — base32 TOTP secret from Upstox 2FA setup page

Token validity: Upstox tokens are valid until 03:30 IST next day.
ensure_token() reuses an existing token if it was issued after 03:30 today.

CLI usage:
  python -m broker.auth
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
_TOKEN_RESET_HOUR = 3  # tokens expire at 03:30 IST
_TOKEN_RESET_MINUTE = 30
_MAX_ATTEMPTS = 3
_HEADLESS_FIRST = True


def ensure_token(
    api_key: str | None = None,
    api_secret: str | None = None,
    redirect_uri: str | None = None,
    token_file: str | None = None,
    notifier=None,
) -> str:
    """
    Return a valid access token, refreshing via Playwright if needed.
    Raises SystemExit(1) on final failure after sending a Telegram alert.
    """
    import config as cfg
    api_key = api_key or cfg.UPSTOX_API_KEY
    api_secret = api_secret or cfg.UPSTOX_API_SECRET
    redirect_uri = redirect_uri or cfg.UPSTOX_REDIRECT_URI
    token_file = token_file or cfg.UPSTOX_TOKEN_FILE

    token_path = Path(token_file)

    if _token_is_fresh(token_path):
        token_data = json.loads(token_path.read_text())
        logger.info("Reusing existing Upstox token (issued today)")
        return token_data["access_token"]

    logger.info("Token missing or stale — starting automated login")
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        headless = _HEADLESS_FIRST and attempt == 1
        try:
            token = _playwright_login(api_key, api_secret, redirect_uri, headless=headless)
            token_data = {
                "access_token": token,
                "issued_at": datetime.now(tz=_IST).isoformat(),
            }
            token_path.write_text(json.dumps(token_data, indent=2))
            logger.info("Token saved to %s", token_path)
            return token
        except Exception as exc:
            logger.warning("Login attempt %d/%d failed: %s", attempt, _MAX_ATTEMPTS, exc)
            if attempt < _MAX_ATTEMPTS:
                time.sleep(5 * attempt)

    # All attempts exhausted
    manual_url = (
        "https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code&client_id={api_key}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
    )
    msg = f"⛔ AUTH FAILED after {_MAX_ATTEMPTS} attempts.\nManual login: {manual_url}"
    logger.error(msg)
    if notifier:
        try:
            notifier.send("AUTH_FAILED", {"message": msg})
        except Exception:
            pass
    sys.exit(1)


def _token_is_fresh(token_path: Path) -> bool:
    if not token_path.exists():
        return False
    try:
        data = json.loads(token_path.read_text())
        issued_str = data.get("issued_at")
        if not issued_str:
            return False
        issued = datetime.fromisoformat(issued_str)
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=_IST)
        now = datetime.now(tz=_IST)
        # Token resets at 03:30 IST — issue must be after today's 03:30
        reset_today = now.replace(hour=_TOKEN_RESET_HOUR, minute=_TOKEN_RESET_MINUTE,
                                  second=0, microsecond=0)
        return issued >= reset_today
    except Exception:
        return False


def _playwright_login(
    api_key: str,
    api_secret: str,
    redirect_uri: str,
    headless: bool = True,
) -> str:
    from playwright.sync_api import sync_playwright
    import pyotp
    import requests

    mobile = os.environ.get("UPSTOX_MOBILE", "")
    pin = os.environ.get("UPSTOX_PIN", "")
    totp_secret = os.environ.get("UPSTOX_TOTP_SECRET", "")

    if not mobile or not pin or not totp_secret:
        raise ValueError(
            "UPSTOX_MOBILE, UPSTOX_PIN, and UPSTOX_TOTP_SECRET must be set in .env"
        )

    auth_url = (
        "https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code&client_id={api_key}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
    )
    captured_code: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=["--no-sandbox"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )

        # Intercept the redirect to capture auth code
        def _handle_route(route, request):
            url = request.url
            if redirect_uri in url:
                parsed = urllib.parse.urlparse(url)
                params = urllib.parse.parse_qs(parsed.query)
                code = params.get("code", [None])[0]
                if code:
                    captured_code.append(code)
                route.abort()
            else:
                route.continue_()

        page = context.new_page()
        page.route("**/*", _handle_route)

        logger.debug("Navigating to auth URL (headless=%s)", headless)
        try:
            page.goto(auth_url, timeout=30_000)
        except Exception:
            pass  # redirect abort will fire before navigation completes

        if not captured_code:
            # Fill mobile number
            page.wait_for_selector("input[type='text']", timeout=15_000)
            page.fill("input[type='text']", mobile)
            page.click("button[type='submit'], button:has-text('Get OTP'), button:has-text('Continue')")

            # Fill TOTP
            totp = pyotp.TOTP(totp_secret).now()
            page.wait_for_selector("input[type='number'], input[placeholder*='OTP'], input[placeholder*='TOTP']",
                                   timeout=15_000)
            otp_input = page.locator("input[type='number'], input[placeholder*='OTP'], input[placeholder*='TOTP']").first
            otp_input.fill(totp)
            page.click("button[type='submit'], button:has-text('Continue'), button:has-text('Verify')")

            # Fill PIN
            page.wait_for_selector("input[type='password'], input[placeholder*='PIN']", timeout=15_000)
            page.fill("input[type='password'], input[placeholder*='PIN']", pin)
            page.click("button[type='submit'], button:has-text('Continue'), button:has-text('Login')")

            # Wait for redirect intercept
            page.wait_for_timeout(8_000)

        browser.close()

    if not captured_code:
        raise RuntimeError("Auth code not captured — login may have failed")

    # Exchange code for token
    resp = requests.post(
        "https://api.upstox.com/v2/login/authorization/token",
        data={
            "code": captured_code[0],
            "client_id": api_key,
            "client_secret": api_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    import config
    token = ensure_token(
        api_key=config.UPSTOX_API_KEY,
        api_secret=config.UPSTOX_API_SECRET,
        redirect_uri=config.UPSTOX_REDIRECT_URI,
        token_file=config.UPSTOX_TOKEN_FILE,
    )
    print(f"Token obtained successfully (first 20 chars): {token[:20]}...")


if __name__ == "__main__":
    _cli()
