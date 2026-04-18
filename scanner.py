"""
scanner.py — smart background scan with MMR scoring and tiered alerts
"""

import logging
import os
from datetime import datetime

from models import db, SearchFilter, Lead, AppSettings
from scraper import scrape_listings, fetch_listing_detail
from messaging import generate_messages, send_sms_alert
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

        log.info(f"Scan started — {len(filters)} filter(s) | Tier1≤{settings['tier1_pct']}% Tier2≤{settings['tier2_pct']}%")
        new_count = 0

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

                # Fetch detail page
                if listing_data.get("listing_url"):
                    detail = fetch_listing_detail(listing_data["listing_url"])
                    listing_data.update({k: v for k, v in detail.items() if v})

                # MMR lookup
                mmr_data = get_mmr(
                    year    = listing_data.get("year", 0),
                    make    = listing_data.get("make", ""),
                    model   = listing_data.get("model", ""),
                    mileage = listing_data.get("mileage", 0),
                )

                # Deal scoring
                score = score_deal(
                    listing_price = listing_data.get("price", 0),
                    mmr           = mmr_data.get("mmr", 0),
                    settings      = settings,
                )

                # Skip Tier 3 entirely — not worth pursuing
                if score["tier"] == 3:
                    log.info(f"Skipping Tier 3 listing: {listing_data.get('title')} ({score['pct_vs_mmr']:+.1f}% vs MMR)")
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
                    mmr            = mmr_data.get("mmr", 0),
                    mmr_source     = mmr_data.get("source", ""),
                    deal_tier      = score["tier"],
                    deal_label     = score["label"],
                    pct_vs_mmr     = score["pct_vs_mmr"],
                    price_diff     = score["diff"],
                    ai_message_fb  = messages.get("fb", ""),
                    ai_message_sms = messages.get("sms", ""),
                    status         = "new",
                    found_at       = datetime.utcnow(),
                )
                db.session.add(lead)
                db.session.flush()

                # Tier 1 = URGENT — text broker immediately to call himself
                if score["tier"] == 1:
                    pct_str = f"{score['pct_vs_mmr']:+.1f}%"
                    urgent_body = (
                        f"🚨 URGENT DEAL — {listing_data.get('title', 'Vehicle')}\n"
                        f"Listed: ${listing_data.get('price', 0):,} | MMR: ${mmr_data.get('mmr', 0):,} ({pct_str} vs MMR)\n"
                        f"CALL NOW: {listing_data.get('listing_url', '')}"
                    )
                    try:
                        from twilio.rest import Client as TwilioClient
                        client = TwilioClient(
                            os.environ.get("TWILIO_ACCOUNT_SID"),
                            os.environ.get("TWILIO_AUTH_TOKEN"),
                        )
                        client.messages.create(
                            body=urgent_body[:1600],
                            from_=os.environ.get("TWILIO_FROM_NUMBER"),
                            to=os.environ.get("BROKER_PHONE"),
                        )
                        lead.sms_sent = True
                        log.info(f"URGENT SMS sent for Tier 1 deal: {listing_data.get('title')}")
                    except Exception as e:
                        log.error(f"Urgent SMS failed: {e}")

                # Tier 2 = OPPORTUNITY — standard SMS alert (no urgency)
                elif score["tier"] == 2:
                    try:
                        sent = send_sms_alert(listing_data, messages.get("sms", ""))
                        lead.sms_sent = sent
                    except Exception as e:
                        log.error(f"SMS alert error: {e}")

                db.session.commit()
                new_count += 1
                log.info(f"New lead [{score['label'].upper()}]: {listing_data.get('title')} | ${listing_data.get('price',0):,} vs MMR ${mmr_data.get('mmr',0):,}")

        log.info(f"Scan complete — {new_count} new lead(s)")
