"""
╔══════════════════════════════════════════════════════════════════╗
║  Durston Gear X-Dome 1+ — Carbon Pole Restock Monitor            ║
║  Polls the Shopify .js product endpoint for variant              ║
║  availability and sends a Discord webhook alert on restock.      ║
╚══════════════════════════════════════════════════════════════════╝

Usage:
    1.  Set DISCORD_WEBHOOK_URL below (or as an environment variable).
    2.  pip install requests
    3.  python monitor.py
"""

import os
import sys
import json
import time
import random
import logging
from datetime import datetime
import smtplib
from email.message import EmailMessage

import requests

# ─────────────────────────── CONFIGURATION ───────────────────────────

# Discord Webhook — set via environment variable or paste directly here
DISCORD_WEBHOOK_URL: str = os.environ.get(
    "DISCORD_WEBHOOK_URL",
)

# Email Notification Settings
# Paste your credentials here, or set them as environment variables
EMAIL_SENDER: str = os.environ.get("EMAIL_SENDER", "laiwilbur@gmail.com")
EMAIL_PASSWORD: str = os.environ.get("EMAIL_PASSWORD", "dxxodfbmxmzyxcvl")  # Use an App Password, not your real password!
EMAIL_RECIPIENT: str = os.environ.get("EMAIL_RECIPIENT", "laiwilbur@gmail.com")
SMTP_SERVER: str = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT: int = int(os.environ.get("SMTP_PORT", 587))

# Product endpoint — Shopify .js gives us the `available` boolean per variant
PRODUCT_URL: str = (
    "https://durstongear.com/products/"
    "x-dome-1-plus-ultralight-backpacking-tent.js"
)

# Human-friendly link for the Discord message
PRODUCT_PAGE_URL: str = (
    "https://durstongear.com/products/"
    "x-dome-1-plus-ultralight-backpacking-tent"
)

# The exact variant we're watching
TARGET_VARIANT_ID: int = 49897121382691          # Regular / Carbon
TARGET_VARIANT_TITLE: str = "Regular / Carbon"   # For display / fallback matching

# Polling cadence — randomised between these bounds (seconds)
MIN_DELAY: int = 3500   # ~58 minutes
MAX_DELAY: int = 3700   # ~62 minutes

# Request timeout in seconds
REQUEST_TIMEOUT: int = 15

# Cooldown after a successful alert to avoid spamming (seconds)
# If the item stays in stock the bot will re-alert after this window.
RESTOCK_ALERT_COOLDOWN: int = 300  # 5 minutes

# ─────────────────────────── USER-AGENTS ─────────────────────────────

USER_AGENTS: list[str] = [
    # Chrome – Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Chrome – macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Firefox – Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
    "Gecko/20100101 Firefox/126.0",
    # Firefox – macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) "
    "Gecko/20100101 Firefox/126.0",
    # Safari – macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    # Edge – Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    # Chrome – Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Firefox – Linux
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) "
    "Gecko/20100101 Firefox/126.0",
]

# ─────────────────────────── LOGGING ─────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("stock-monitor")

# ─────────────────────────── HELPERS ─────────────────────────────────


def _random_headers() -> dict[str, str]:
    """Return request headers with a randomly chosen User-Agent."""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": PRODUCT_PAGE_URL,
        "DNT": "1",
        "Connection": "keep-alive",
    }


def fetch_product_data() -> dict | None:
    """
    GET the Shopify .js endpoint and return the parsed JSON dict.
    Returns None on any transient / network error so the loop can retry.
    """
    try:
        resp = requests.get(
            PRODUCT_URL,
            headers=_random_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        log.warning("HTTP error %s — will retry next cycle.", status)
    except requests.exceptions.ConnectionError:
        log.warning("Connection error — site may be down. Will retry.")
    except requests.exceptions.Timeout:
        log.warning("Request timed out after %ss. Will retry.", REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        log.warning("Unexpected request error: %s", exc)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("Failed to parse JSON response: %s", exc)

    return None


def find_target_variant(data: dict) -> dict | None:
    """
    Locate the target variant inside the product JSON.
    Tries matching by variant ID first, then falls back to title matching.
    """
    variants: list[dict] = data.get("variants", [])

    # Primary: match by known variant ID
    for v in variants:
        if v.get("id") == TARGET_VARIANT_ID:
            return v

    # Fallback: match by title string
    for v in variants:
        if v.get("title", "").lower() == TARGET_VARIANT_TITLE.lower():
            return v

    return None


def send_discord_alert(
    product_title: str,
    variant: dict,
    product_url: str,
    image_url: str | None = None,
) -> bool:
    """
    Post a rich embed to the Discord webhook.
    Returns True on success, False otherwise.
    """
    if DISCORD_WEBHOOK_URL.startswith("YOUR_"):
        log.error(
            "⚠  Discord webhook URL is not configured! "
            "Set DISCORD_WEBHOOK_URL in the script or as an env var."
        )
        return False

    variant_title = variant.get("title", TARGET_VARIANT_TITLE)
    price_raw = variant.get("price", 0)
    # Shopify .js returns price in cents
    price = f"${price_raw / 100:.2f}" if isinstance(price_raw, (int, float)) else str(price_raw)
    variant_id = variant.get("id", TARGET_VARIANT_ID)
    direct_link = f"{product_url}?variant={variant_id}"

    embed = {
        "title": f"🏕️  {product_title} — BACK IN STOCK!",
        "url": direct_link,
        "color": 0x2ECC71,  # green
        "description": (
            f"The **{variant_title}** variant is now **available**!\n\n"
            f"**Price:** {price}\n"
            f"**Variant ID:** `{variant_id}`\n\n"
            f"🔗  [**Buy Now → Add to Cart**]({direct_link})"
        ),
        "fields": [
            {
                "name": "Interior",
                "value": variant.get("option1", "—"),
                "inline": True,
            },
            {
                "name": "Pole Set",
                "value": variant.get("option2", "—"),
                "inline": True,
            },
        ],
        "footer": {
            "text": "Durston Gear Stock Monitor • Act fast!",
        },
        "timestamp": datetime.utcnow().isoformat(),
    }

    if image_url:
        # Ensure protocol-relative URLs become absolute
        if image_url.startswith("//"):
            image_url = "https:" + image_url
        embed["thumbnail"] = {"url": image_url}

    payload = {
        "content": "@everyone 🚨 **RESTOCK ALERT** 🚨",
        "embeds": [embed],
    }

    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code in (200, 204):
            log.info("✅  Discord alert sent successfully!")
            return True
        else:
            log.warning(
                "Discord webhook returned status %s: %s",
                resp.status_code,
                resp.text[:200],
            )
    except requests.exceptions.RequestException as exc:
        log.error("Failed to send Discord alert: %s", exc)

    return False


def send_email_alert(product_title: str, variant: dict, product_url: str) -> bool:
    """
    Send an email alert using standard SMTP.
    Returns True on success, False otherwise.
    """
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECIPIENT:
        # Silently skip if email isn't configured
        return False
        
    variant_title = variant.get("title", TARGET_VARIANT_TITLE)
    price_raw = variant.get("price", 0)
    price = f"${price_raw / 100:.2f}" if isinstance(price_raw, (int, float)) else str(price_raw)
    variant_id = variant.get("id", TARGET_VARIANT_ID)
    direct_link = f"{product_url}?variant={variant_id}"
    
    msg = EmailMessage()
    msg['Subject'] = f"🏕️ RESTOCK ALERT: {product_title} - {variant_title}"
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECIPIENT
    msg.set_content(
        f"The {variant_title} variant is now available!\n\n"
        f"Price: {price}\n"
        f"Variant ID: {variant_id}\n\n"
        f"Buy Now -> {direct_link}"
    )
    
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
        log.info("✅  Email alert sent successfully!")
        return True
    except Exception as exc:
        log.error("❌  Failed to send Email alert: %s", exc)
        return False


# ─────────────────────────── MAIN LOOP ───────────────────────────────


def main() -> None:
    log.info("=" * 60)
    log.info("  Durston Gear X-Dome 1+ — Carbon Pole Restock Monitor (GitHub Actions)")
    log.info("=" * 60)
    log.info("Target variant : %s  (ID: %s)", TARGET_VARIANT_TITLE, TARGET_VARIANT_ID)
    log.info("Product URL    : %s", PRODUCT_URL)
    log.info("=" * 60)

    if DISCORD_WEBHOOK_URL.startswith("YOUR_"):
        log.warning(
            "⚠  DISCORD_WEBHOOK_URL is not set! "
            "Alerts will be logged but not sent."
        )

    data = fetch_product_data()
    if data is None:
        log.info("No data retrieved from Shopify. Exiting.")
        return

    product_title: str = data.get("title", "X-Dome 1+")
    variant = find_target_variant(data)

    if variant is None:
        log.warning(
            "Target variant '%s' not found in product data! "
            "The product may have been restructured.",
            TARGET_VARIANT_TITLE,
        )
        return

    is_available: bool = variant.get("available", False)
    variant_title: str = variant.get("title", TARGET_VARIANT_TITLE)

    if is_available:
        log.info(
            "🟢  IN STOCK  │ %s — %s",
            product_title,
            variant_title,
        )
        
        # Grab thumbnail from the product's featured image
        image_url: str | None = data.get("featured_image")
        
        send_discord_alert(
            product_title=product_title,
            variant=variant,
            product_url=PRODUCT_PAGE_URL,
            image_url=image_url,
        )
        send_email_alert(
            product_title=product_title,
            variant=variant,
            product_url=PRODUCT_PAGE_URL,
        )
    else:
        log.info(
            "🔴  OUT OF STOCK  │ %s — %s",
            product_title,
            variant_title,
        )

if __name__ == "__main__":
    main()

