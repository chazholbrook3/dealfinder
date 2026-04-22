"""
scraper.py — scrapes KSL Classifieds by fetching the search page HTML
and extracting the embedded Next.js JSON payload.
"""

import os
import re
import json
import logging
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from urllib.parse import urlencode

log = logging.getLogger(__name__)

KSL_BASE = "https://classifieds.ksl.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}


def get_proxies():
    host = os.environ.get("BRIGHTDATA_HOST")
    port = os.environ.get("BRIGHTDATA_PORT")
    user = os.environ.get("BRIGHTDATA_USER")
    pwd  = os.environ.get("BRIGHTDATA_PASS")
    if all([host, port, user, pwd]):
        import random; session_id = random.randint(1, 999999); proxy_url = f"http://{user}-session-{session_id}-country-us:{pwd}@{host}:{port}"
        log.info("Using Bright Data residential proxy")
        return {"http": proxy_url, "https": proxy_url}
    log.warning("No proxy configured")
    return None


def build_search_url(f):
    params = {"category": "cars-trucks"}
    if f.make:               params["make"]      = f.make
    if f.model:              params["model"]     = f.model
    if f.year_min:           params["yearFrom"]  = f.year_min
    if f.year_max < 9999:    params["yearTo"]    = f.year_max
    if f.price_min:          params["priceFrom"] = f.price_min
    if f.price_max < 999999: params["priceTo"]   = f.price_max
    if f.miles_max < 999999: params["mileageTo"] = f.miles_max
    if f.zip_code:           params["zip"]       = f.zip_code
    if f.radius_mi:          params["miles"]     = f.radius_mi
    return f"{KSL_BASE}/search/?{urlencode(params)}"


def scrape_listings(search_filter, max_results=20):
    url = build_search_url(search_filter)
    log.info(f"Scraping KSL: {url}")

    try:
        resp = requests.get(
            url,
            headers=HEADERS,
            proxies=get_proxies(),
            timeout=20,
            verify=False,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"KSL fetch failed: {e}")
        return []

    listings = _extract_from_html(resp.text, max_results)
    log.info(f"Found {len(listings)} listings for '{search_filter.name}'")
    return listings


def _extract_from_html(html, max_results=20):
    listings = []
    match = re.search(r'\\"results\\":\[\[(.*?)\]\]', html, re.DOTALL)
    if not match:
        log.warning("Could not find results in page")
        return []
    raw = match.group(1).replace('\\"', '"').replace('\\\\', '\\')
    try:
        items = json.loads(f"[{raw}]")
    except Exception as e:
        log.error(f"Failed to parse listing JSON: {e}")
        return []
    for item in items[:max_results]:
        try:
            listing = _parse_item(item)
            if listing:
                listings.append(listing)
        except Exception as e:
            log.warning(f"Skipping item: {e}")
    return listings


def _parse_item(item):
    listing_id = str(item.get("id") or "")
    if not listing_id:
        return None
    price_raw = item.get("price") or 0
    try:
        price = float(str(price_raw).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        price = 0.0
    location = item.get("location") or {}
    city  = location.get("city", "") if isinstance(location, dict) else ""
    state = location.get("state", "") if isinstance(location, dict) else ""
    title = item.get("title") or ""
    year, make, model = _parse_title(title)
    image_url = ""
    primary = item.get("primaryImage")
    if isinstance(primary, dict):
        image_url = primary.get("url", "")
    elif isinstance(primary, str):
        image_url = primary
    return {
        "listing_id":   listing_id,
        "ksl_id":       listing_id,
        "title":        title,
        "price":        price,
        "year":         year,
        "make":         make,
        "model":        model,
        "mileage":      0,
        "city":         city,
        "state":        state,
        "location":     f"{city}, {state}".strip(", "),
        "image_url":    image_url,
        "listing_url":  f"{KSL_BASE}/listing/{listing_id}",
        "url":          f"{KSL_BASE}/listing/{listing_id}",
        "description":  item.get("description", ""),
        "seller_name":  "",
        "seller_phone": "",
        "seller_type":  item.get("sellerType", ""),
    }


def fetch_listing_detail(url):
    try:
        resp = requests.get(url, headers=HEADERS, proxies=get_proxies(), timeout=20, verify=False)
        resp.raise_for_status()
        match = re.search(r'"description"\s*:\s*"(.*?)"(?=[,}])', resp.text)
        description = match.group(1) if match else ""
        match2 = re.search(r'"mileage"\s*:\s*(\d+)', resp.text)
        mileage = int(match2.group(1)) if match2 else 0
        return {"description": description, "mileage": mileage}
    except Exception as e:
        log.warning(f"Detail fetch failed for {url}: {e}")
        return {}


def _parse_title(title):
    year_match = re.search(r"\b(19|20)\d{2}\b", str(title))
    year = int(year_match.group()) if year_match else 0
    makes = [
        "Honda", "Toyota", "Ford", "Chevrolet", "Chevy", "Dodge", "RAM",
        "Nissan", "Hyundai", "Kia", "Subaru", "Mazda", "Jeep", "GMC",
        "Volkswagen", "BMW", "Mercedes", "Audi", "Lexus", "Acura",
        "Infiniti", "Cadillac", "Buick", "Lincoln", "Volvo", "Tesla",
        "Mitsubishi", "Chrysler", "Ram", "Pontiac", "Saturn", "Isuzu",
    ]
    make = ""
    for m in makes:
        if m.lower() in str(title).lower():
            make = m
            break
    model = ""
    if make:
        idx = title.lower().find(make.lower())
        after = title[idx + len(make):].strip()
        words = after.split()
        model = words[0] if words else ""
    return year, make, model
