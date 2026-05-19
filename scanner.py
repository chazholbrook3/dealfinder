"""
scanner.py — smart background scan with MMR scoring and tiered alerts
"""

import logging
import os
from datetime import datetime

from models import db, SearchFilter, Lead, AppSettings
from scraper import scrape_listings
from messaging import generate_messages
from mmr import get_mmr, score_deal

log = logging.getLogger(__name__)


def run_scan(app):
    with app.app_context():
        filters = SearchFilter.query.filter_by(active=True).all()
        if not filters:
            log.info("No active search filters")
            return

        # Load adjustable thresholds from DB settings
        settings = {
            "tier1_pct": float(AppSettings.get("tier1_pct", "0")),
            "tier2_pct": float(AppSettings.get("tier2_pct", "10")),
        }

        log.info(f"Scan started — {len(filters)} filter(s) | scoring by filter target price")
        new_count  = 0
        urgent_count = 0
        opp_count    = 0

        for f in filters:
            try:
                listings = scrape_listings(f)
            except Exception as e:
                log.error(f"Scrape failed for '{f.name}': {e}")
                continue

            for listing_data in listings:
                ksl_id = listing_data.get("ksl_id")
                if not ksl_id:
                    continue

                # Deduplicate
                if Lead.query.filter_by(ksl_id=ksl_id).first():
                    continue

                # title_unknown is set by scrape_listings() for every listing.
                # Default True (unknown) so any gap in the pipeline is safe.
                title_unknown = listing_data.get("title_unknown", True)

                # MMR lookup (kept for future use; currently no credentials)
                mmr_data = get_mmr(
                    year    = listing_data.get("year", 0),
                    make    = listing_data.get("make", ""),
                    model   = listing_data.get("model", ""),
                    mileage = listing_data.get("mileage", 0),
                )

                # Deal scoring — uses filter's price_max as the target price
                score = score_deal(
                    listing_price = listing_data.get("price", 0),
                    mmr           = mmr_data.get("mmr", 0),
                    settings      = settings,
                    target_price  = f.target_price,
                )

                # Skip Tier 3 entirely — not worth pursuing
                if score["tier"] == 3:
                    log.info(f"Skipping Tier 3 listing: {listing_data.get('title')} ({score['pct_vs_mmr']:+.1f}% vs target)")
                    continue

                # Generate AI messages for Tier 1 & 2
                broker_name  = os.environ.get("BROKER_NAME", "the broker")
                broker_phone = os.environ.get("BROKER_PHONE_DISPLAY", "")
                try:
                    messages = generate_messages(listing_data, broker_name, broker_phone)
                except Exception as e:
                    log.error(f"Message gen failed: {e}")
                    messages = {"fb": "", "sms": ""}

                # Save lead
                lead = Lead(
                    filter_id      = f.id,
                    ksl_id         = ksl_id,
                    title          = listing_data.get("title", ""),
                    price          = listing_data.get("price", 0),
                    year           = listing_data.get("year", 0),
                    make           = listing_data.get("make", ""),
                    model          = listing_data.get("model", ""),
                    mileage        = listing_data.get("mileage", 0),
                    location       = listing_data.get("location", ""),
                    seller_name    = listing_data.get("seller_name", ""),
                    seller_phone   = listing_data.get("seller_phone", ""),
                    listing_url    = listing_data.get("listing_url", ""),
                    image_url      = listing_data.get("image_url", ""),
                    description    = listing_data.get("description", ""),
                    mmr            = f.target_price,
                    mmr_source     = "target_price",
                    deal_tier      = score["tier"],
                    deal_label     = score["label"],
                    pct_vs_mmr     = score["pct_vs_mmr"],
                    price_diff     = score["diff"],
                    ai_message_fb  = messages.get("fb", ""),
                    ai_message_sms = messages.get("sms", ""),
                    status         = "new",
                    title_unknown  = title_unknown,
                    found_at       = datetime.utcnow(),
                )
                db.session.add(lead)
                db.session.commit()
                new_count += 1
                if score["tier"] == 1:
                    urgent_count += 1
                elif score["tier"] == 2:
                    opp_count += 1
                log.info(f"New lead [{score['label'].upper()}]: {listing_data.get('title')} | ${listing_data.get('price',0):,} vs target ${f.target_price:,}")

        log.info(f"Scan complete — {new_count} new lead(s) ({urgent_count} urgent, {opp_count} opportunities)")

        # Send one summary SMS if any new leads were found
        if new_count > 0:
            app_url = os.environ.get("APP_URL", "your dashboard")
            parts = []
            if urgent_count:
                parts.append(f"{urgent_count} Urgent {'deal' if urgent_count == 1 else 'deals'}")
            if opp_count:
                parts.append(f"{opp_count} {'Opportunity' if opp_count == 1 else 'Opportunities'}")
            summary = ", ".join(parts) if parts else f"{new_count} new leads"
            body = f"KT Finds Scan Complete — {summary} found. Check your dashboard: {app_url}"
            try:
                from twilio.rest import Client as TwilioClient
                client = TwilioClient(
                    os.environ.get("TWILIO_ACCOUNT_SID"),
                    os.environ.get("TWILIO_AUTH_TOKEN"),
                )
                client.messages.create(
                    body=body[:1600],
                    from_=os.environ.get("TWILIO_FROM_NUMBER"),
                    to=os.environ.get("BROKER_PHONE"),
                )
                log.info(f"Summary SMS sent: {body}")
            except Exception as e:
                log.error(f"Summary SMS failed: {e}")
