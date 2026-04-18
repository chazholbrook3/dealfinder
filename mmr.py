"""
mmr.py — Mannheim Market Report (MMR) API integration

Mannheim uses OAuth2. You need:
  - MANNHEIM_CLIENT_ID
  - MANNHEIM_CLIENT_SECRET

API docs: https://developer.coxautoinc.com/

The MMR endpoint returns wholesale auction values:
  - average (the MMR)
  - above (above average condition)
  - below (below average condition)
  - mileage adjustment

We cache tokens in memory to avoid re-authenticating on every request.
"""

import os
import time
import logging
import requests

log = logging.getLogger(__name__)

# ── Token cache ───────────────────────────────────────────────────────────────
_token_cache = {"access_token": None, "expires_at": 0}

MANNHEIM_TOKEN_URL = "https://api.manheim.com/id/oauth2/token"
MANNHEIM_MMR_URL   = "https://api.manheim.com/mmr/v1/vehicles/values"


def _get_access_token():
    """Get a valid OAuth access token, refreshing if expired."""
    now = time.time()
    if _token_cache["access_token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["access_token"]

    client_id     = os.environ.get("MANNHEIM_CLIENT_ID")
    client_secret = os.environ.get("MANNHEIM_CLIENT_SECRET")

    if not client_id or not client_secret:
        log.warning("Mannheim credentials not set — MMR lookup unavailable")
        return None

    try:
        resp = requests.post(
            MANNHEIM_TOKEN_URL,
            data={
                "grant_type":    "client_credentials",
                "client_id":     client_id,
                "client_secret": client_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        _token_cache["access_token"] = data["access_token"]
        _token_cache["expires_at"]   = now + data.get("expires_in", 3600)
        log.info("Mannheim OAuth token refreshed")
        return _token_cache["access_token"]
    except Exception as e:
        log.error(f"Mannheim token fetch failed: {e}")
        return None


def get_mmr(year: int, make: str, model: str, mileage: int = 0) -> dict:
    """
    Look up the Mannheim Market Report value for a vehicle.

    Returns:
        {
            "mmr":      int,    # average wholesale value
            "above":    int,    # above-average condition value
            "below":    int,    # below-average condition value
            "source":   str,    # "mannheim" or "unavailable"
        }

    Returns {"mmr": 0, "source": "unavailable"} if lookup fails.
    """
    if not year or not make or not model:
        return {"mmr": 0, "source": "unavailable", "above": 0, "below": 0}

    token = _get_access_token()
    if not token:
        return {"mmr": 0, "source": "unavailable", "above": 0, "below": 0}

    params = {
        "year":    year,
        "make":    make,
        "model":   model,
    }
    if mileage:
        params["odometer"] = mileage
        params["odometerUnit"] = "MI"

    try:
        resp = requests.get(
            MANNHEIM_MMR_URL,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # Parse response — Mannheim returns items array
        items = data.get("items") or data.get("results") or []
        if not items:
            log.warning(f"No MMR data for {year} {make} {model}")
            return {"mmr": 0, "source": "no_data", "above": 0, "below": 0}

        best = items[0]
        prices = best.get("prices", best)

        mmr   = int(prices.get("average", prices.get("mmr", 0)))
        above = int(prices.get("above",   mmr * 1.1))
        below = int(prices.get("below",   mmr * 0.9))

        log.info(f"MMR for {year} {make} {model}: ${mmr:,}")
        return {"mmr": mmr, "above": above, "below": below, "source": "mannheim"}

    except requests.HTTPError as e:
        log.error(f"Mannheim MMR HTTP error: {e} — {e.response.text[:200] if e.response else ''}")
        return {"mmr": 0, "source": "unavailable", "above": 0, "below": 0}
    except Exception as e:
        log.error(f"Mannheim MMR lookup failed: {e}")
        return {"mmr": 0, "source": "unavailable", "above": 0, "below": 0}


def score_deal(listing_price: int, mmr: int, settings: dict) -> dict:
    """
    Score a listing against MMR and return its tier.

    Tiers:
        1 = URGENT  — listed significantly below MMR, broker should call NOW
        2 = OPPORTUNITY — listed at or slightly above MMR, AI outreach worth it
        3 = SKIP    — listed too far above MMR, not worth pursuing

    settings keys:
        tier1_pct  — % below MMR for Tier 1 (e.g. 0 = at MMR or below)
        tier2_pct  — % above MMR still worth outreach (e.g. 10 = up to 10% above)

    Returns:
        {
            "tier":       int,
            "label":      str,
            "pct_vs_mmr": float,  # negative = below MMR
            "diff":       int,    # $ difference vs MMR
        }
    """
    if not mmr or not listing_price:
        return {"tier": 2, "label": "unknown", "pct_vs_mmr": 0, "diff": 0}

    pct = ((listing_price - mmr) / mmr) * 100  # negative = below MMR
    diff = listing_price - mmr

    tier1_threshold = float(settings.get("tier1_pct", 0))    # at or below MMR
    tier2_threshold = float(settings.get("tier2_pct", 10))   # up to 10% above

    if pct <= tier1_threshold:
        tier, label = 1, "urgent"
    elif pct <= tier2_threshold:
        tier, label = 2, "opportunity"
    else:
        tier, label = 3, "skip"

    return {
        "tier":       tier,
        "label":      label,
        "pct_vs_mmr": round(pct, 1),
        "diff":       diff,
    }
