# main.py
# ClothesFinder — Flask API that scrapes multiple marketplaces and returns the best matching product.
# IMPORTANT: scraping selectors can break if sites change. Use official APIs where available.
# Designed for Render/Gunicorn; binds to 0.0.0.0:10000 so Render detects the port.

from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
import concurrent.futures
import time
import re
import threading

app = Flask(__name__)

# --- CONFIG ---
USER_AGENT = "Mozilla/5.0 (compatible; ClothesFinder/1.0; +https://example.com)"
REQUEST_TIMEOUT = 8  # seconds
MAX_WORKERS = 6
CACHE_TTL = 300  # seconds

# List of sites (modular). Add more entries to expand.
# Each entry is (site_name, scraper_function)
# Scraper functions are defined below.
sites = []

# --- Simple in-memory cache ---
_cache = {}
_cache_lock = threading.Lock()

def cache_get(key):
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        ts, value = item
        if time.time() - ts > CACHE_TTL:
            del _cache[key]
            return None
        return value

def cache_set(key, value):
    with _cache_lock:
        _cache[key] = (time.time(), value)

# --- Helper utilities ---
def clean_text(t):
    if not t:
        return ""
    return re.sub(r'\s+', ' ', t).strip()

def parse_price(text):
    if not text:
        return None
    # Extract numbers like 1 299, 1299, 1299.00
    m = re.search(r'(\d+[ \d\.]*\d)', text.replace('\u00A0',' '))
    if not m:
        return None
    num = m.group(1)
    num = num.replace(' ', '').replace('.', '').replace(',', '')
    try:
        return int(num)
    except:
        try:
            return float(num)
        except:
            return None

def http_get(url, params=None):
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception as e:
        # Could log here
        return None

# --- Site-specific scraper implementations ---
# NOTE: CSS selectors may need adjustments. These implementations try to be defensive and generic.

def search_vinted(query, filters):
    """Search Vinted and return list of dicts {title, price, url, used?}"""
    results = []
    q = requests.utils.quote(query)
    url = f"https://www.vinted.se/catalog?search_text={q}"
    html = http_get(url)
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    # Vinted structure: links with class like 'catalog-item__link', fallback to 'a' with '/item/'
    anchors = soup.select("a.catalog-item__link")
    if not anchors:
        anchors = [a for a in soup.find_all("a", href=True) if "/item/" in a.get("href", "")]
    for a in anchors[:12]:
        href = a.get("href")
        if href and href.startswith("/"):
            link = "https://www.vinted.se" + href
        else:
            link = href
        title = clean_text(a.get_text() or a.get("title") or "")
        price_tag = a.select_one(".catalog-item__price") if a else None
        price_text = price_tag.get_text() if price_tag else ""
        price = parse_price(price_text)
        results.append({"site":"Vinted", "title": title, "price": price, "url": link, "used": True})
    return results

def search_tradera(query, filters):
    results = []
    q = requests.utils.quote(query)
    url = f"https://www.tradera.com/search?q={q}"
    html = http_get(url)
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    # Try typical listing link classes
    anchors = soup.select("a.listing-card__link")
    if not anchors:
        anchors = [a for a in soup.find_all("a", href=True) if "/item/" in a.get("href", "")]
    for a in anchors[:12]:
        href = a.get("href")
        link = href if href.startswith("http") else "https://www.tradera.com" + href
        title = clean_text(a.get_text() or a.get("title") or "")
        price_tag = a.select_one(".listing-card__price")
        price_text = price_tag.get_text() if price_tag else ""
        price = parse_price(price_text)
        results.append({"site":"Tradera", "title": title, "price": price, "url": link, "used": True})
    return results

def search_blocket(query, filters):
    results = []
    q = requests.utils.quote(query)
    url = f"https://www.blocket.se/annonser/hela_sverige?q={q}"
    html = http_get(url)
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("a.AdItem_link__")  # fallback; may not match
    if not anchors:
        anchors = [a for a in soup.find_all("a", href=True) if "/annons/" in a.get("href","")]
    for a in anchors[:12]:
        href = a.get("href")
        link = href if href.startswith("http") else "https://www.blocket.se" + href
        title = clean_text(a.get_text() or a.get("title") or "")
        # blocket price parse heuristics
        parent_text = a.parent.get_text() if a.parent else ""
        price = parse_price(parent_text)
        results.append({"site":"Blocket", "title": title, "price": price, "url": link, "used": True})
    return results

def search_generic_store(query, filters, base_url, product_path_contains=None):
    # Generic fallback: fetch base_url + search param and find product anchors
    results = []
    q = requests.utils.quote(query)
    url = base_url + q
    html = http_get(url)
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=True)
    for a in anchors[:30]:
        href = a.get("href")
        if not href:
            continue
        if product_path_contains and product_path_contains not in href:
            continue
        link = href if href.startswith("http") else base_url.split('?')[0].rstrip('/') + href
        title = clean_text(a.get_text() or a.get("title") or "")
        # Attempt to find price near anchor
        price = None
        # sibling/parent search
        for p in (a.parent, a.parent.parent if a.parent else None):
            if p:
                price = parse_price(p.get_text())
                if price:
                    break
        results.append({"site": base_url.split("//")[1].split("/")[0], "title": title, "price": price, "url": link, "used": False})
    return results

# Register scrapers
sites = [
    ("Vinted", search_vinted),
    ("Tradera", search_tradera),
    ("Blocket", search_blocket),
    # Example generic stores (can add many)
    ("Zalando", lambda q, f: search_generic_store(q, f, "https://www.zalando.se/catalog/?q=")),
    ("Amazon_se", lambda q, f: search_generic_store(q, f, "https://www.amazon.se/s?k=")),
    # add more as needed ...
]

# --- Scoring logic ---
def score_product(prod, filters):
    """
    Compute a score for a product dict based on filters.
    Higher = better.
    """
    score = 0
    title = (prod.get("title") or "").lower()
    brand = (filters.get("brand") or "").lower()
    item = (filters.get("item") or "").lower()
    size = (filters.get("size") or "").lower()
    color = (filters.get("color") or "").lower()
    price_max = filters.get("price_max")
    used_filter = filters.get("used")  # expecting True/False/None

    # brand exact match gets high points
    if brand and brand in title:
        score += 40
    # item match
    if item and item in title:
        score += 25
    # color and size weaker points
    if color and color in title:
        score += 10
    if size and size in title:
        score += 10
    # price closer to or below target increases score
    try:
        price = prod.get("price")
        if price is not None and price_max:
            # if under max price add points proportionally
            try:
                price_val = float(price)
                if price_val <= float(price_max):
                    score += 20
                    # cheaper is slightly better
                    score += int(max(0, (float(price_max) - price_val) // 10))
            except:
                pass
    except:
        pass
    # used preference
    if used_filter is not None:
        prod_used = prod.get("used")
        if prod_used is False and used_filter is False:
            score += 10
        if prod_used is True and used_filter is True:
            score += 10
        # penalize mismatch
        if prod_used is True and used_filter is False:
            score -= 5
        if prod_used is False and used_filter is True:
            score -= 5
    # prefer items with URL and title
    if prod.get("url"):
        score += 2
    if prod.get("title"):
        score += 1
    return score

# --- Coordinator: run scrapers concurrently and choose best product ---
def find_best_across_sites(query, filters):
    cache_key = f"{query}|{filters}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    results = []
    # run scrapers concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = []
        for site_name, scraper_fn in sites:
            futures.append(ex.submit(_call_scraper_safe, site_name, scraper_fn, query, filters))
        for fut in concurrent.futures.as_completed(futures, timeout=30):
            try:
                site_results = fut.result()
                if site_results:
                    results.extend(site_results)
            except Exception:
                # ignore a failing site
                continue

    # score each
    best = None
    best_score = -10**9
    for prod in results:
        try:
            s = score_product(prod, filters)
        except Exception:
            s = 0
        prod['_score'] = s
        if s > best_score:
            best_score = s
            best = prod

    # If no results, return None
    cache_set(cache_key, best)
    return best

def _call_scraper_safe(site_name, fn, query, filters):
    try:
        return fn(query, filters)
    except Exception:
        return []

# --- Flask endpoints ---
@app.route("/")
def home():
    return jsonify({"message":"ClothesFinder API is running", "routes": {"/find_item":"POST"}})

@app.route("/find_item", methods=["POST"])
def find_item_route():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error":"No JSON payload provided"}), 400

    # Accept flexible payload: either a single "query" or detailed filters
    query = data.get("query", "")
    filters = {
        "brand": data.get("brand"),
        "item": data.get("item"),
        "size": data.get("size"),
        "color": data.get("color"),
        "price_max": data.get("price_max"),
        "used": data.get("used")  # True/False/None
    }

    # If query empty, build from brand+item
    if not query:
        parts = []
        if filters.get("brand"): parts.append(filters["brand"])
        if filters.get("item"): parts.append(filters["item"])
        query = " ".join(parts)

    if not query:
        return jsonify({"error":"No query or filters provided"}), 400

    best = find_best_across_sites(query, filters)
    if not best:
        return jsonify({"link": None, "message":"Ingen produkt hittades"}), 404

    # Build short response
    resp = {
        "site": best.get("site"),
        "title": best.get("title"),
        "price": best.get("price"),
        "url": best.get("url"),
        "score": best.get("_score", 0)
    }
    return jsonify({"best_match": resp})

if __name__ == "__main__":
    # Bind to 0.0.0.0 so Render can detect the port
    app.run(host="0.0.0.0", port=10000)
