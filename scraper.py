"""
scraper.py — scrapes KSL Cars (cars.ksl.com) via its Next.js RSC payload.

cars.ksl.com renders full structured listing data server-side at path-based URLs:
    https://cars.ksl.com/v2/search/make/Toyota/model/Camry/yearFrom/2018/...

The RSC payload (self.__next_f.push blocks) embeds JSON with price, make, model,
year, mileage, location, image — no JavaScript execution needed.
"""

import os
import re
import json
import logging
import requests
import warnings
warnings.filterwarnings("ignore")

log = logging.getLogger(__name__)

KSL_CARS_BASE = "https://cars.ksl.com"
KSL_SEARCH_BASE = f"{KSL_CARS_BASE}/v2/search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def get_proxies():
    host = os.environ.get("BRIGHTDATA_HOST")
    port = os.environ.get("BRIGHTDATA_PORT")
    user = os.environ.get("BRIGHTDATA_USER")
    pwd  = os.environ.get("BRIGHTDATA_PASS")
    if all([host, port, user, pwd]):
        import random
        session_id = random.randint(1, 999999)
        proxy_url = f"http://{user}-session-{session_id}-country-us:{pwd}@{host}:{port}"
        log.info("Using Bright Data residential proxy")
        return {"http": proxy_url, "https": proxy_url}
    return None


def build_search_url(f):
    """Build a cars.ksl.com /v2/search path-based URL from a SearchFilter."""
    parts = [KSL_SEARCH_BASE]

    if f.make:
        parts += ["make", f.make]
    if f.model:
        parts += ["model", f.model]
    if f.year_min and f.year_min > 0:
        parts += ["yearFrom", str(f.year_min)]
    if f.year_max and f.year_max < 9999:
        parts += ["yearTo", str(f.year_max)]
    if f.price_min and f.price_min > 0:
        parts += ["priceFrom", str(f.price_min)]
    if f.price_max and f.price_max < 999999:
        parts += ["priceTo", str(f.price_max)]
    if f.miles_max and f.miles_max < 999999:
        parts += ["mileageTo", str(f.miles_max)]
    if f.zip_code:
        parts += ["zip", str(f.zip_code)]
        if f.radius_mi:
            parts += ["miles", str(f.radius_mi)]

    return "/".join(parts)


def scrape_listings(search_filter, max_results=20):
    url = build_search_url(search_filter)
    log.info(f"Scraping KSL Cars: {url}")

    try:
        resp = requests.get(
            url,
            headers=HEADERS,
            proxies=get_proxies(),
            timeout=25,
            allow_redirects=True,
            verify=False,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"KSL Cars fetch failed: {e}")
        return []

    listings = _extract_from_rsc(resp.text, max_results)
    log.info(f"Found {len(listings)} listings for '{search_filter.name}'")
    return listings


def _extract_from_rsc(html, max_results=20):
    """Parse listings from the Next.js RSC payload embedded in the HTML."""
    blocks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)
    if not blocks:
        log.warning("No RSC blocks found in page")
        return []

    for block in blocks:
        try:
            decoded = json.loads(f'"{block}"')
        except Exception:
            decoded = block

        if '"initialState"' not in decoded or '"results"' not in decoded:
            continue

        match = re.search(r'"results":\[\[(.*?)\]\]', decoded, re.DOTALL)
        if not match:
            continue

        try:
            items = json.loads(f"[{match.group(1)}]")
        except Exception as e:
            log.error(f"Failed to parse RSC results JSON: {e}")
            return []

        listings = []
        for item in items[:max_results]:
            try:
                listing = _parse_item(item)
                if listing:
                    listings.append(listing)
            except Exception as e:
                log.warning(f"Skipping item {item.get('id')}: {e}")
        return listings

    log.warning("RSC results block not found in page")
    return []


def _parse_item(item):
    listing_id = str(item.get("id") or "")
    if not listing_id:
        return None

    location = item.get("location") or {}
    city  = location.get("city", "") if isinstance(location, dict) else ""
    state = location.get("state", "") if isinstance(location, dict) else ""

    image_url = ""
    primary = item.get("primaryImage")
    if isinstance(primary, dict):
        image_url = primary.get("url", "")

    return {
        "listing_id":   listing_id,
        "ksl_id":       listing_id,
        "title":        item.get("title", ""),
        "price":        float(item.get("price") or 0),
        "year":         int(item.get("makeYear") or 0),
        "make":         item.get("make", ""),
        "model":        item.get("model", ""),
        "mileage":      int(item.get("mileage") or 0),
        "city":         city,
        "state":        state,
        "location":     f"{city}, {state}".strip(", "),
        "image_url":    image_url,
        "listing_url":  f"{KSL_CARS_BASE}/listing/{listing_id}",
        "url":          f"{KSL_CARS_BASE}/listing/{listing_id}",
        "description":  "",
        "seller_name":  item.get("dealerName", ""),
        "seller_phone": "",
        "seller_type":  item.get("sellerType", ""),
        "vin":          item.get("vin", ""),
        "trim":         item.get("trim", ""),
    }


def fetch_listing_detail(url):
    """Fetch the detail page and extract phone, description, and mileage."""
    try:
        resp = requests.get(
            url,
            headers=HEADERS,
            proxies=get_proxies(),
            timeout=20,
            allow_redirects=True,
            verify=False,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"Detail fetch failed for {url}: {e}")
        return {}

    return _extract_detail_from_rsc(resp.text)


def _extract_detail_from_rsc(html):
    blocks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)

    for block in blocks:
        try:
            decoded = json.loads(f'"{block}"')
        except Exception:
            decoded = block

        if '"listingType":"CAR"' not in decoded:
            continue

        result = {}

        # Phone number — appears as bare number string in the RSC
        phone_match = re.search(r'"phone[^"]*":\s*"(\d{7,15})"', decoded)
        if phone_match:
            result["seller_phone"] = phone_match.group(1)

        # Description
        desc_match = re.search(r'"description":\s*"((?:[^"\\]|\\.)*)"', decoded)
        if desc_match:
            desc = desc_match.group(1).replace("\\n", "\n").replace('\\"', '"')
            if len(desc) > 5:
                result["description"] = desc

        # Mileage (more accurate on detail page)
        mile_match = re.search(r'"mileage":\s*(\d+)', decoded)
        if mile_match:
            result["mileage"] = int(mile_match.group(1))

        # Seller name from detail
        name_match = re.search(r'"contactName":\s*"([^"]+)"', decoded)
        if name_match:
            result["seller_name"] = name_match.group(1)

        if result:
            return result

    return {}
