# app.py
from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, urljoin
import re
import math
import time
from typing import List, Dict, Any

# Playwright (synchronous)
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

app = Flask(__name__)

# ---------------------------
# Konfiguration
# ---------------------------
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
PLAYWRIGHT_TIMEOUT = 15000  # ms

# Viktningsfaktorer för score (justera vid behov)
WEIGHT_TEXT_MATCH = 1.0
WEIGHT_PRICE = 1.0
SOURCE_BONUS = {
    "vinted": 1.0,
    "tradera": 0.9,
    "sellpy": 0.8,
    "blocket": 0.85,
    "plick": 0.7
}

# ---------------------------
# Hjälpare
# ---------------------------
def safe_get(url: str, headers=None, timeout=10):
    try:
        r = requests.get(url, headers=headers or HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r
    except Exception as e:
        print(f"safe_get error for {url}: {e}")
    return None

def normalize_text(s: str) -> str:
    return re.sub(r'\s+', ' ', (s or "").strip().lower())

def parse_price_from_text(s: str):
    if not s: 
        return None
    # försök hitta tal med eller utan kr, SEK, commas
    m = re.search(r'(\d{2,3}(?:[ \,]?\d{3})?)', s.replace('\xa0',' '))
    if m:
        num = m.group(1).replace(' ', '').replace(',', '')
        try:
            return int(num)
        except:
            return None
    return None

# ---------------------------
# Playwright-renderad fetch (för JS-sidor)
# ---------------------------
def fetch_page_with_playwright(url: str, selector_for_items: str = None, first_href_pattern: str = None):
    """
    Return: raw HTML text or None
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=PLAYWRIGHT_TIMEOUT)
            if selector_for_items:
                try:
                    page.wait_for_selector(selector_for_items, timeout=8000)
                except PlaywrightTimeout:
                    # fortsätt ändå, kanske finns i html
                    pass
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        print(f"Playwright fetch error for {url}: {e}")
        return None

# ---------------------------
# Scrapers — returnerar lista av kandidater: {title, price, url, source}
# ---------------------------
def scrape_vinted(query: str) -> List[Dict[str,Any]]:
    base = "https://www.vinted.se"
    url = f"{base}/sok?q={quote_plus(query)}"
    html = fetch_page_with_playwright(url, selector_for_items="a[href*='/items/']")
    if not html:
        # fallback: requests
        r = safe_get(url)
        html = r.text if r else None
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    # försök hitta länkar till /items/
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if "/items/" in href:
            full = urljoin(base, href)
            title = a.get_text(strip=True) or ""
            # försök hitta pris i parent element
            parent = a.find_parent()
            price = None
            if parent:
                price = parse_price_from_text(parent.get_text(" ", strip=True))
            candidates.append({"title": normalize_text(title), "price": price, "url": full, "source": "vinted"})
    # unika och begränsa
    seen = set()
    out = []
    for c in candidates:
        if c["url"] in seen: continue
        seen.add(c["url"])
        out.append(c)
        if len(out) >= 10: break
    return out

def scrape_tradera(query: str) -> List[Dict[str,Any]]:
    base = "https://www.tradera.com"
    url = f"{base}/sok?q={quote_plus(query)}"
    html = fetch_page_with_playwright(url, selector_for_items="a[href*='/item/']")
    if not html:
        r = safe_get(url)
        html = r.text if r else None
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if "/item/" in href:
            full = urljoin(base, href)
            title = a.get_text(strip=True) or ""
            parent = a.find_parent()
            price = None
            if parent:
                price = parse_price_from_text(parent.get_text(" ", strip=True))
            candidates.append({"title": normalize_text(title), "price": price, "url": full, "source": "tradera"})
    seen = set()
    out = []
    for c in candidates:
        if c["url"] in seen: continue
        seen.add(c["url"])
        out.append(c)
        if len(out) >= 10: break
    return out

def scrape_sellpy(query: str) -> List[Dict[str,Any]]:
    base = "https://www.sellpy.se"
    url = f"{base}/sok?q={quote_plus(query)}"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    candidates = []
    # Sellpy struktur varierar - hitta länkar som innehåller '/produkter' eller '/produkt'
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if "/produkt" in href or "/produkter" in href:
            full = urljoin(base, href)
            title = a.get_text(strip=True) or ""
            parent = a.find_parent()
            price = parse_price_from_text(parent.get_text(" ", strip=True)) if parent else None
            candidates.append({"title": normalize_text(title), "price": price, "url": full, "source": "sellpy"})
    seen = set()
    out = []
    for c in candidates:
        if c["url"] in seen: continue
        seen.add(c["url"])
        out.append(c)
        if len(out) >= 8: break
    return out

def scrape_blocket(query: str) -> List[Dict[str,Any]]:
    base = "https://www.blocket.se"
    url = f"{base}/annonser/hela_sverige?q={quote_plus(query)}"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if "/annons/" in href:
            full = urljoin(base, href)
            title = a.get_text(strip=True) or ""
            parent = a.find_parent()
            price = parse_price_from_text(parent.get_text(" ", strip=True)) if parent else None
            candidates.append({"title": normalize_text(title), "price": price, "url": full, "source": "blocket"})
    # dedupe
    seen = set()
    out = []
    for c in candidates:
        if c["url"] in seen: continue
        seen.add(c["url"])
        out.append(c)
        if len(out) >= 8: break
    return out

def scrape_plick(query: str) -> List[Dict[str,Any]]:
    base = "https://plick.se"
    url = f"{base}/sok?q={quote_plus(query)}"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if "/p/" in href:
            full = urljoin(base, href)
            title = a.get_text(strip=True) or ""
            parent = a.find_parent()
            price = parse_price_from_text(parent.get_text(" ", strip=True)) if parent else None
            candidates.append({"title": normalize_text(title), "price": price, "url": full, "source": "plick"})
    seen = set()
    out = []
    for c in candidates:
        if c["url"] in seen: continue
        seen.add(c["url"])
        out.append(c)
        if len(out) >= 6: break
    return out

# ---------------------------
# Relevans / scoring
# ---------------------------
def score_candidate(candidate: Dict[str,Any], query: str, price_min=None, price_max=None) -> float:
    score = 0.0
    qwords = [w for w in normalize_text(query).split() if w]
    text = (candidate.get("title","") + " " + candidate.get("url","")).lower()
    # text match: antalet query-ord som förekommer
    match_count = sum(1 for w in qwords if w in text)
    score += WEIGHT_TEXT_MATCH * match_count
    # price proximity
    price = candidate.get("price")
    if price and price_min is not None and price_max is not None and price_min != "" and price_max != "":
        try:
            pm = int(price_min); pM = int(price_max)
            # om utanför intervall -> minuspkt
            if price < pm or price > pM:
                # avstånd i procent
                dist = min(abs(price - pm), abs(price - pM))
                score -= WEIGHT_PRICE * (dist / (pM - pm + 1))
            else:
                # inne i range -> belöna
                score += WEIGHT_PRICE * 1.0
        except:
            pass
    # source bonus
    source = candidate.get("source", "")
    score *= SOURCE_BONUS.get(source, 0.8)
    return score

# ---------------------------
# /search route (huvudlogik)
# ---------------------------
@app.route("/search", methods=["POST"])
def search():
    data = request.get_json() or {}
    category = data.get("category", "") or ""
    brand = data.get("brand", "") or ""
    size = data.get("size", "") or ""
    color = data.get("color", "") or ""
    price_min = data.get("price_min", None)
    price_max = data.get("price_max", None)
    condition = data.get("condition", "") or ""

    query_parts = [brand, category, size, color, condition]
    query = " ".join([p for p in query_parts if p])

    print(f"[search] query='{query}' price_min={price_min} price_max={price_max}")

    # samla kandidater från flera källor (asynk/parallel skulle vara bättre i prod)
    candidates = []
    # prioritera Playwright-sidor först
    candidates += scrape_vinted(query)
    candidates += scrape_tradera(query)
    # sedan requests-baserade
    candidates += scrape_sellpy(query)
    candidates += scrape_blocket(query)
    candidates += scrape_plick(query)

    # dedupe by url
    unique = {}
    for c in candidates:
        if not c.get("url"): continue
        if c["url"] not in unique:
            unique[c["url"]] = c

    candidates = list(unique.values())

    # scorea varje kandidat
    scored = []
    for c in candidates:
        s = score_candidate(c, query, price_min, price_max)
        c_out = {
            "title": c.get("title",""),
            "price": c.get("price"),
            "url": c.get("url"),
            "source": c.get("source"),
            "score": round(s,3)
        }
        scored.append((s, c_out))

    # sortera desc
    scored.sort(key=lambda x: x[0], reverse=True)

    results = [item for _, item in scored]

    # plocka bästa
    best = results[0] if results and results[0].get("score",0) > 0 else None

    # Always return strings so Adalo can pick up outputs
    if not best:
        return jsonify({
            "status": "no_results",
            "best_match_link": "",
            "results": results  # empty or helpful for debugging
        })

    return jsonify({
        "status": "success",
        "best_match_link": best.get("url",""),
        "results": results
    })


# ---------------------------
# Flask start
# ---------------------------
if __name__ == "__main__":
    app.run(debug=True)
