"""
scraper.py — fetches KSL car listings via KSL's internal Next.js API.
"""

import os
import re
import json
import logging
import requests

log = logging.getLogger(__name__)

KSL_API  = "https://classifieds.ksl.com/nextjs-api/proxy"
KSL_BASE = "https://classifieds.ksl.com"

API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Referer": "https://classifieds.ksl.com/",
    "Origin": "https://classifieds.ksl.com",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


def get_proxies():
    host = os.environ.get("BRIGHTDATA_HOST")
    port = os.environ.get("BRIGHTDATA_PORT")
    user = os.environ.get("BRIGHTDATA_USER")
    pwd  = os.environ.get("BRIGHTDATA_PASS")
    if all([host, port, user, pwd]):
        proxy_url = f"http://{user}:{pwd}@{host}:{port}"
        log.info("Using Bright Data residential proxy")
        return {"http": proxy_url, "https": proxy_url}
    log.warning("No proxy configured")
    return None


def build_api_params(f):
    params = {}
    if f.make:               params["make"]      = [f.make]
    if f.model:              params["model"]     = [f.model]
    if f.year_min:           params["yearFrom"]  = str(f.year_min)
    if f.year_max < 9999:    params["yearTo"]    = str(f.year_max)
    if f.price_min:          params["priceFrom"] = str(f.price_min)
    if f.price_max < 999999: params["priceTo"]   = str(f.price_max)
    if f.miles_max < 999999: params["mileageTo"] = str(f.miles_max)
    if f.zip_code:           params["zip"]       = f.zip_code
    if f.radius_mi:          params["miles"]     = str(f.radius_mi)
    params["perPage"] = 20
    params["page"]    = 1
    return params


def scrape_listings(search_filter, max_results=20):
    body = build_api_params(search_filter)
    log.info(f"Calling KSL API for '{search_filter.name}': {body}")

    try:
        resp = requests.post(
            KSL_API,
            params={"endpoint": "/classifieds/cars/search/searchByUrlParams"},
            json=body,
            headers=API_HEADERS,
            proxies=get_proxies(),
            timeout=20,
            verify=False,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"KSL API call failed: {e}")
        return []

    try:
        data = resp.json()
    except Exception as e:
        log.error(f"KSL API non-JSON response: {e} — {resp.text[:300]}")
        return []

    items = (
        data.get("data", {}).get("items") or
        data.get("items") or
        data.get("results") or
        data.get("listings") or
        []
    )

    if not items:
        log.warning(f"KSL API no items. Keys={list(data.keys())} sample={str(data)[:300]}")
        return []

    listings = []
    for item in items[:max_results]:
        try:
            listing = _parse_api_item(item)
            if listing:
                listings.append(listing)
        except Exception as e:
            log.warning(f"Skipping item: {e}")

    log.info(f"Found {len(listings)} listings for '{search_filter.name}'")
    return listings


def _parse_api_item(item):
    listing_id = str(item.get("id") or item.get("listingId") or "")
    if not listing_id:
        return None

    price_raw = item.get("price") or item.get("askingPrice") or 0
    try:
        price = float(str(price_raw).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        price = 0.0

    location = item.get("location") or {}
    city  = location.get("city", "") if isinstance(location, dict) else ""
    state = location.get("state", "") if isinstance(location, dict) else ""

    title = item.get("title") or item.get("name") or ""
    year, make, model = _parse_title(title)

    mileage_raw = item.get("mileage") or item.get("miles") or 0
    try:
        mileage = int(str(mileage_raw).replace(",", ""))
    except (ValueError, TypeError):
        mileage = 0

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
        "mileage":      mileage,
        "city":         city,
        "state":        state,
        "location":     f"{city}, {state}".strip(", "),
        "image_url":    image_url,
        "listing_url":  f"{KSL_BASE}/listing/{listing_id}",
        "url":          f"{KSL_BASE}/listing/{listing_id}",
        "description":  item.get("description", ""),
        "seller_name":  item.get("sellerName", ""),
        "seller_phone": item.get("phone", ""),
        "seller_type":  item.get("sellerType", ""),
    }


def fetch_listing_detail(url):
    try:
        resp = requests.get(url, headers=API_HEADERS, proxies=get_proxies(), timeout=20, verify=False)
        resp.raise_for_status()
        match = re.search(r'"description"\s*:\s*"(.*?)"(?=[,}])', resp.text)
        description = match.group(1) if match else ""
        match2 = re.search(r'"mileage"\s*:\s*(\d+)', resp.text)
        mileage = int(match2.group(1)) if match2 else 0
        return {"description": description, "mileage": mileage}
    except Exception as e:
        log.warning(f"Detail fetch failed for {url}: {e}")
        return {}


def _parse_price(text):
    nums = re.findall(r"\d+", str(text).replace(",", ""))
    return int(nums[0]) if nums else 0


def _parse_mileage(text):
    nums = re.findall(r"[\d,]+", str(text).replace(",", ""))
    return int(nums[0]) if nums else 0


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
