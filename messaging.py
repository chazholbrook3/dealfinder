"""
messaging.py — AI message generation (Anthropic) + SMS alerts (Twilio)
"""

import os
import logging
import anthropic
from twilio.rest import Client as TwilioClient

log = logging.getLogger(__name__)


# ── Anthropic ─────────────────────────────────────────────────────────────────

def generate_messages(listing: dict, broker_name: str, broker_phone: str) -> dict:
    """
    Generate both a Facebook DM version and an SMS version of an outreach message.
    Returns {"fb": "...", "sms": "..."} or {"fb": "", "sms": ""} on error.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    title       = listing.get("title", "the vehicle")
    price       = f"${listing['price']:,}" if listing.get("price") else "listed price"
    year        = listing.get("year", "")
    make        = listing.get("make", "")
    model       = listing.get("model", "")
    location    = listing.get("location", "")
    mileage     = f"{listing['mileage']:,} miles" if listing.get("mileage") else ""
    description = listing.get("description", "")[:300]
    seller      = listing.get("seller_name", "")

    context = f"""
Car: {title}
Price: {price}
{f"Year: {year}" if year else ""}
{f"Make/model: {make} {model}" if make else ""}
{f"Mileage: {mileage}" if mileage else ""}
{f"Location: {location}" if location else ""}
{f"Seller name: {seller}" if seller else ""}
{f"Description excerpt: {description}" if description else ""}
    """.strip()

    fb_prompt = f"""You are helping a professional car broker named {broker_name} reach out to a private seller on KSL Classifieds.

Listing details:
{context}

Write a friendly, genuine Facebook Messenger message to this seller. 2-4 sentences. 
- Express real interest specific to THIS car (mention the year/make/model or price)
- Ask if it's still available
- Keep it conversational — not salesy
- Sign off: "{broker_name}" {f"— {broker_phone}" if broker_phone else ""}
Return ONLY the message text, nothing else."""

    sms_prompt = f"""You are helping a professional car broker named {broker_name} reach out to a private seller on KSL Classifieds.

Listing details:
{context}

Write a very short, friendly SMS text message. Under 160 characters total.
- Mention the specific car
- Ask if it's still available  
- Sign off with just the name: {broker_name}
Return ONLY the message text, nothing else."""

    results = {"fb": "", "sms": ""}

    try:
        fb_resp = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=300,
            messages=[{"role": "user", "content": fb_prompt}]
        )
        results["fb"] = fb_resp.content[0].text.strip()
    except Exception as e:
        log.error(f"FB message generation failed: {e}")

    try:
        sms_resp = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=100,
            messages=[{"role": "user", "content": sms_prompt}]
        )
        results["sms"] = sms_resp.content[0].text.strip()[:160]
    except Exception as e:
        log.error(f"SMS message generation failed: {e}")

    return results


# ── Twilio ────────────────────────────────────────────────────────────────────

def send_sms_alert(listing: dict, message_preview: str) -> bool:
    """
    Send an SMS alert to the broker when a new matching listing is found.
    Returns True on success.
    """
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token  = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    to_number   = os.environ.get("BROKER_PHONE")

    if not all([account_sid, auth_token, from_number, to_number]):
        log.warning("Twilio credentials not fully set — skipping SMS alert")
        return False

    title = listing.get("title", "New listing")
    price = f"${listing['price']:,}" if listing.get("price") else ""
    url   = listing.get("listing_url", "")

    body = (
        f"🚗 New DealFinder match!\n"
        f"{title}"
        f"{' — ' + price if price else ''}\n"
        f"{url}"
    )
    body = body[:1600]  # Twilio max

    try:
        client = TwilioClient(account_sid, auth_token)
        client.messages.create(body=body, from_=from_number, to=to_number)
        log.info(f"SMS alert sent for listing {listing.get('ksl_id')}")
        return True
    except Exception as e:
        log.error(f"Twilio SMS failed: {e}")
        return False
