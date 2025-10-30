# main.py
# ClothesFinder — Flask API for scraping multiple marketplaces
# Ready for Render/Gunicorn; binds to 0.0.0.0:10000

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
REQUEST_TIMEOUT = 8
MAX_WORKERS = 4
CACHE_TTL = 300

# --- In-memory cache ---
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

# --- Helpers ---
def clean_text(t):
    if not t: return ""
    return re.sub(r'\s+', ' ', t).strip()

def parse_price(text):
    if not text: return None
    m = re.search(r'(\d+[ \d\.]*\d)', text.replace('\u00A0',' '))
    if not m: return None
    num = m.group(1).replace(' ', '').replace('.', '').replace(',', '')
    try:
        return int(num)
    except:
        try:
            return float(num)
        except:
            return None

def parse_boolean(text):
    if not text: return None
    text = str(text).lower()
    if text in ["ja", "true", "yes"]: return True
    if text in ["nej", "false", "no"]: return False
    return None

def http_get(url, params=None):
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.text
    except:
        return None

# --- Scrapers ---
def search_vinted(query, filters):
    results = []
    q = requests.utils.quote(query)
    url = f"https://www.vinted.se/catalog?search_text={q}"
    html = http_get(url)
    if not html: return results
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("a.catalog-item__link")
    if not anchors:
        anchors = [a for a in soup.find_all("a", href=True) if "/item/" in a.get("href","")]
    for a in anchors[:12]:
        href = a.get("href")
        link = "https://www.vinted.se" + href if href.startswith("/") else href
        title = clean_text(a.get_text() or a.get("title") or "")
        price_tag = a.select_one(".catalog-item__price") if a else None
        price = parse_price(price_tag.get_text() if price_tag else "")
        results.append({"site":"Vinted", "title": title, "price": price, "url": link, "used": True})
    return results

def search_tradera(query, filters):
    results = []
    q = requests.utils.quote(query)
    url = f"https://www.tradera.com/search?q={q}"
    html = http_get(url)
    if not html: return results
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("a.listing-card__link")
    if not anchors:
        anchors = [a for a in soup.find_all("a", href=True) if "/item/" in a.get("href","")]
    for a in anchors[:12]:
        href = a.get("href")
        link = href if href.startswith("http") else "https://www.tradera.com" + href
        title = clean_text(a.get_text() or a.get("title") or "")
        price_tag = a.select_one(".listing-card__price")
        price = parse_price(price_tag.get_text() if price_tag else "")
        results.append({"site":"Tradera", "title": title, "price": price, "url": link, "used": True})
    return results

def search_blocket(query, filters):
    results = []
    q = requests.utils.quote(query)
    url = f"https://www.blocket.se/annonser/hela_sverige?q={q}"
    html = http_get(url)
    if not html: return results
    soup = BeautifulSoup(html, "html.parser")
    anchors = [a for a in soup.find_all("a", href=True) if "/annons/" in a.get("href","")]
    for a in anchors[:12]:
        href = a.get("href")
        link = href if href.startswith("http") else "https://www.blocket.se" + href
        title = clean_text(a.get_text() or a.get("title") or "")
        parent_text = a.parent.get_text() if a.parent else ""
        price = parse_price(parent_text)
        results.append({"site":"Blocket", "title": title, "price": price, "url": link, "used": True})
    return results

# Register scrapers
sites = [
    ("Vinted", search_vinted),
    ("Tradera", search_tradera),
    ("Blocket", search_blocket)
]

# --- Scoring ---
def score_product(prod, filters):
    score = 0
    title = (prod.get("title") or "").lower()
    brand = (filters.get("brand") or "").lower()
    item = (filters.get("item") or "").lower()
    size = (filters.get("size") or "").lower()
    color = (filters.get("color") or "").lower()
    gender = (filters.get("gender"))
    kids = filters.get("kids")
    used_filter = filters.get("used")
    price_max = filters.get("price_max")

    if brand and brand in title: score += 40
    if item and item in title: score += 25
    if color and color in title: score += 10
    if size and size in title: score += 10
    if price_max and prod.get("price") is not None:
        try:
            price_val = float(prod["price"])
            if price_val <= float(price_max):
                score += 20 + int(max(0, (float(price_max)-price_val)//10))
        except:
            pass
    if used_filter is not None:
        prod_used = prod.get("used")
        if prod_used is True and used_filter: score += 10
        if prod_used is False and not used_filter: score += 10
        if prod_used is True and not used_filter: score -=5
        if prod_used is False and used_filter: score -=5
    if prod.get("url"): score += 2
    if prod.get("title"): score +=1
    return score

# --- Coordinator ---
def _call_scraper_safe(site_name, fn, query, filters):
    try:
        return fn(query, filters)
    except:
        return []

def find_best_across_sites(query, filters):
    cache_key = f"{query}|{filters}"
    cached = cache_get(cache_key)
    if cached: return cached

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(_call_scraper_safe, name, fn, query, filters) for name, fn in sites]
        for fut in concurrent.futures.as_completed(futures, timeout=30):
            try:
                site_results = fut.result()
                if site_results: results.extend(site_results)
            except: continue

    best = None
    best_score = -10**9
    for prod in results:
        try:
            s = score_product(prod, filters)
        except:
            s = 0
        prod['_score'] = s
        if s > best_score:
            best_score = s
            best = prod

    cache_set(cache_key, best)
    return best

# --- Flask Endpoints ---
@app.route("/")
def home():
    return jsonify({"message":"ClothesFinder API running", "routes": {"/find_item":"POST"}})

@app.route("/find_item", methods=["POST"])
def find_item_route():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error":"No JSON payload provided"}), 400

    brand = data.get("brand")
    item = data.get("item")
    query = data.get("query") or f"{brand or ''} {item or ''}".strip()
    filters = {
        "brand": brand,
        "item": item,
        "size": data.get("size"),
        "color": data.get("color"),
        "gender": data.get("gender"),
        "kids": parse_boolean(data.get("kids")),
        "price_max": data.get("price_max"),
        "used": parse_boolean(data.get("used"))
    }

    if not query:
        return jsonify({"error":"No query or filters provided"}), 400

    best = find_best_across_sites(query, filters)
    if not best:
        return jsonify({"link": None, "message":"Ingen produkt hittades"}), 404

    resp = {
        "site": best.get("site"),
        "title": best.get("title"),
        "price": best.get("price"),
        "url": best.get("url"),
        "score": best.get("_score",0)
    }
    return jsonify({"best_match": resp})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
