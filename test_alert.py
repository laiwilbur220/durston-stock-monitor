"""Quick test — simulates a restock alert to verify the Discord webhook."""

import sys
sys.path.insert(0, ".")
from monitor import send_discord_alert, send_email_alert, PRODUCT_PAGE_URL, log

# Fake variant data mimicking what the Shopify .js endpoint returns when in stock
fake_variant = {
    "id": 49897121382691,
    "title": "Regular / Carbon",
    "option1": "Regular",
    "option2": "Carbon",
    "price": 41900,       # cents
    "available": True,
}

fake_image = (
    "//cdn.shopify.com/s/files/1/0693/2008/1699/files/"
    "Durston_X-Dome_1__Ultralight_Backpacking_Tent_Main.jpg?v=1768057046"
)

log.info("Sending TEST restock alert to Discord and Email…")
success_discord = send_discord_alert(
    product_title="X-Dome 1+ (⚠️ TEST)",
    variant=fake_variant,
    product_url=PRODUCT_PAGE_URL,
    image_url=fake_image,
)

success_email = send_email_alert(
    product_title="X-Dome 1+ (⚠️ TEST)",
    variant=fake_variant,
    product_url=PRODUCT_PAGE_URL,
)

if success_discord:
    log.info("🎉  Discord: Test alert sent! Check your Discord channel.")
else:
    log.error("❌  Discord: Test alert failed. Check your webhook URL.")

if success_email:
    log.info("🎉  Email: Test alert sent! Check your inbox.")
else:
    log.warning("⚠️  Email: Test alert skipped or failed. (Is email configured?)")
