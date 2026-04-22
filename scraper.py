"""
scraper.py — scrapes KSL Classifieds for car listings matching a SearchFilter.

KSL's public search URL structure:
  https://classifieds.ksl.com/search/?
    category=cars-trucks
    &make=Honda
    &model=Civic
    &yearFrom=2015
    &yearTo=2022
    &priceFrom=3000
    &priceTo=12000
    &mileageTo=120000
    &zip=84101
    &miles=100
"""

import re
import os
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

KSL_BASE = "https://classifieds.ksl.com"


def get_proxies():
    host = os.environ.get("BRIGHTDATA_HOST")
    port = os.environ.get("BRIGHTDATA_PORT")
    user = os.environ.get("BRIGHTDATA_USER")
    pwd  = os.environ.get("BRIGHTDATA_PASS")
    if all([host, port, user, pwd]):
        proxy_url = f"http://{user}:{pwd}@{host}:{port}"
        return {"http": proxy_url, "https": proxy_url}
    return None



def _extract_listings_from_nextjs(html, max_results=20):
    """
    KSL uses Next.js which embeds listing data as JSON inside self.__next_f.push script tags.
    """
    import json
    listings = []

    chunks = re.findall(r'self\\.__next_f\\.push\\(\\[1,\\s*"(.*?)"\\]\\)', html, re.DOTALL)

    combined = ""
    for chunk in chunks:
        try:
            combined += chunk.encode().decode('unicode_escape')
        except Exception:
            combined += chunk

    match = re.search(r'"results":\\[\\[(.*?)\\]\\]', combined, re.DOTALL)
    if not match:
        log.warning("Could not find results array in Next.js payload")
        return []

    try:
        items = json.loads(f"[{match.group(1)}]")
    except Exception as e:
        log.error(f"Failed to parse results JSON: {e}")
        return []

    for item in items[:max_results]:
        try:
            loc = item.get("location", {})
            listings.append({
                "listing_id":  str(item["id"]),
                "title":       item.get("title", ""),
                "price":       float(item.get("price", 0)),
                "city":        loc.get("city", ""),
                "state":       loc.get("state", ""),
                "url":         f"https://classifieds.ksl.com/listing/{item['id']}",
                "image_url":   (item.get("primaryImage") or {}).get("url", ""),
                "seller_type": item.get("sellerType", ""),
            })
        except Exception as e:
            log.warning(f"Skipping malformed listing: {e}")

    log.info(f"Extracted {len(listings)} listings from Next.js payload")
    return listings

def build_search_url(f):
    """Build KSL search URL from a SearchFilter object."""
    params = {"category": "cars-trucks"}
    if f.make:       params["make"]       = f.make
    if f.model:      params["model"]      = f.model
    if f.year_min:   params["yearFrom"]   = f.year_min
    if f.year_max < 9999: params["yearTo"] = f.year_max
    if f.price_min:  params["priceFrom"]  = f.price_min
    if f.price_max < 999999: params["priceTo"] = f.price_max
    if f.miles_max < 999999: params["mileageTo"] = f.miles_max
    if f.zip_code:   params["zip"]        = f.zip_code
    if f.radius_mi:  params["miles"]      = f.radius_mi
    return f"{KSL_BASE}/search/?{urlencode(params)}"


def scrape_listings(search_filter, max_results=20):
    """
    Scrape KSL search results for a given SearchFilter.
    Returns a list of dicts with listing data.
    """
    url = build_search_url(search_filter)
    log.info(f"Scraping KSL: {url}")

    try:
        resp = requests.get(url, headers=HEADERS, proxies=get_proxies(), timeout=20, verify=False)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"KSL fetch failed: {e}")
        return []

    listings = _extract_listings_from_nextjs(resp.text, max_results)

    if False:  # old HTML parsing disabled
        soup = BeautifulSoup(resp.text, "html.parser")
        listings_old = []

    # KSL listing cards — each is an <li> or <div> with a data-id attribute
    cards = soup.select("li.search-result, div.listing-item, article.listing")

    # Fallback: find all links that look like /listing/XXXXXX/
    if not cards:
        links = soup.find_all("a", href=re.compile(r"/listing/\d+/"))
        seen_hrefs = set()
        for link in links:
            href = link.get("href", "")
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            listing_id = re.search(r"/listing/(\d+)/", href)
            if not listing_id:
                continue

            card_data = _parse_card_from_link(link, listing_id.group(1))
            if card_data:
                listings.append(card_data)
                if len(listings) >= max_results:
                    break
    else:
        for card in cards[:max_results]:
            card_data = _parse_card(card)
            if card_data:
                listings.append(card_data)

    log.info(f"Found {len(listings)} listings for filter '{search_filter.name}'")
    return listings


def _parse_card(card):
    """Parse a listing card element."""
    try:
        link_el = card.find("a", href=re.compile(r"/listing/\d+/"))
        if not link_el:
            return None

        href = link_el.get("href", "")
        listing_id_match = re.search(r"/listing/(\d+)/", href)
        if not listing_id_match:
            return None

        listing_id = listing_id_match.group(1)
        url = href if href.startswith("http") else KSL_BASE + href

        title_el = card.select_one(".title, h2, h3, .listing-title")
        title = title_el.get_text(strip=True) if title_el else ""

        price_el = card.select_one(".price, .listing-price, [class*='price']")
        price_text = price_el.get_text(strip=True) if price_el else ""
        price = _parse_price(price_text)

        img_el = card.find("img")
        image_url = img_el.get("src", "") or img_el.get("data-src", "") if img_el else ""

        location_el = card.select_one(".location, .city, [class*='location']")
        location = location_el.get_text(strip=True) if location_el else ""

        year, make, model = _parse_title(title)

        return {
            "ksl_id":      listing_id,
            "title":       title,
            "price":       price,
            "year":        year,
            "make":        make,
            "model":       model,
            "mileage":     0,
            "location":    location,
            "image_url":   image_url,
            "listing_url": url,
            "description": "",
            "seller_name": "",
            "seller_phone": "",
        }
    except Exception as e:
        log.warning(f"Card parse error: {e}")
        return None


def _parse_card_from_link(link, listing_id):
    """Fallback parser when card structure isn't found."""
    try:
        href = link.get("href", "")
        url = href if href.startswith("http") else KSL_BASE + href

        # Try to get title from link text or nearby heading
        title = link.get_text(strip=True)
        if not title or len(title) < 5:
            parent = link.parent
            heading = parent.find(["h2", "h3", "h4"]) if parent else None
            title = heading.get_text(strip=True) if heading else f"Listing #{listing_id}"

        year, make, model = _parse_title(title)

        # Try to find price near this link
        parent = link.parent
        price_text = ""
        if parent:
            price_el = parent.find(string=re.compile(r"\$[\d,]+"))
            if price_el:
                price_text = str(price_el)

        return {
            "ksl_id":       listing_id,
            "title":        title,
            "price":        _parse_price(price_text),
            "year":         year,
            "make":         make,
            "model":        model,
            "mileage":      0,
            "location":     "",
            "image_url":    "",
            "listing_url":  url,
            "description":  "",
            "seller_name":  "",
            "seller_phone": "",
        }
    except Exception as e:
        log.warning(f"Link parse error: {e}")
        return None


def fetch_listing_detail(url):
    """
    Fetch the detail page for a single listing to get description,
    seller name, phone, and mileage.
    Returns a dict of extra fields.
    """
    try:
        resp = requests.get(url, headers=HEADERS, proxies=get_proxies(), timeout=20, verify=False)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        desc_el = soup.select_one(".description, .listing-description, [class*='description']")
        description = desc_el.get_text(strip=True)[:1000] if desc_el else ""

        seller_el = soup.select_one(".seller-name, .contact-name, [class*='seller']")
        seller_name = seller_el.get_text(strip=True) if seller_el else ""

        phone_el = soup.find(string=re.compile(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}"))
        seller_phone = str(phone_el).strip() if phone_el else ""

        miles_el = soup.find(string=re.compile(r"[\d,]+\s*miles?", re.I))
        mileage = _parse_mileage(str(miles_el)) if miles_el else 0

        return {
            "description":  description,
            "seller_name":  seller_name,
            "seller_phone": seller_phone,
            "mileage":      mileage,
        }
    except Exception as e:
        log.warning(f"Detail fetch failed for {url}: {e}")
        return {}


def _parse_price(text):
    nums = re.findall(r"\d+", text.replace(",", ""))
    return int(nums[0]) if nums else 0


def _parse_mileage(text):
    nums = re.findall(r"[\d,]+", text.replace(",", ""))
    return int(nums[0]) if nums else 0


def _parse_title(title):
    """Extract year, make, model from a title like '2018 Honda Civic LX'."""
    year_match = re.search(r"\b(19|20)\d{2}\b", title)
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
        if m.lower() in title.lower():
            make = m
            break

    # Model is the word(s) after make
    model = ""
    if make:
        idx = title.lower().find(make.lower())
        after = title[idx + len(make):].strip()
        words = after.split()
        model = words[0] if words else ""

    return year, make, model
