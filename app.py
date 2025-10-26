# app.py
from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
import time
import re
from urllib.parse import quote_plus

app = Flask(__name__)

# --- Konfiguration / "politeness" ---
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ClothesFinderBot/1.0; +https://yourdomain.example/)"
}
REQUEST_TIMEOUT = 10  # sekunder
MIN_DELAY_BETWEEN_REQUESTS = 1.0  # sekund (enkel rate limiting)
_last_request_time = 0.0

# Enkel cache (i-minnet) för att undvika för många requests under test
simple_cache = {}

def polite_get(url, params=None):
    """GET med enkel rate-limiting, retries och timeout."""
    global _last_request_time
    # rate-limit
    elapsed = time.time() - _last_request_time
    if elapsed < MIN_DELAY_BETWEEN_REQUESTS:
        time.sleep(MIN_DELAY_BETWEEN_REQUESTS - elapsed)
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)
        _last_request_time = time.time()
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        # retries en gång till kort efter fel
        time.sleep(0.5)
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)
            _last_request_time = time.time()
            r.raise_for_status()
            return r
        except Exception:
            # returnera None vid fel
            return None

# --- HJÄLPFUNKTIONER FÖR MATCHNING ---
def normalize_text(s: str):
    if not s:
        return ""
    return re.sub(r'\s+', ' ', s.strip().lower())

def token_in_text(token, text):
    """kollar token (en str) mot text (str), både exakt och fuzzy-ish (delvis)"""
    token = token.lower().strip()
    text = text.lower()
    # enkel exakt substring-match
    if token in text:
        return True
    # kolla utan specialtecken
    token_clean = re.sub(r'[^a-z0-9åäö]', '', token)
    text_clean = re.sub(r'[^a-z0-9åäö]', '', text)
    return token_clean and token_clean in text_clean

# --- TRADERA SCRAPER ---
def build_tradera_search_url(filters):
    """
    Bygger en Tradera-sök-URL utifrån filters.
    Vi använder deras sök-param 'query' för enkel test-sökning.
    """
    q_parts = []
    if filters.get("brand"):
        q_parts.append(filters["brand"])
    if filters.get("category"):
        q_parts.append(filters["category"])
    if filters.get("size"):
        q_parts.append(filters["size"])
    if filters.get("color"):
        q_parts.append(filters["color"])
    # extra fritext (if any)
    if filters.get("query"):
        q_parts.append(filters["query"])

    query = " ".join(q_parts).strip()
    if not query:
        query = ""  # tom söker globalt
    # Tradera sök-url (publik söksida)
    # Exempel: https://www.tradera.com/search?q=jeans+nike
    url = f"https://www.tradera.com/search?q={quote_plus(query)}"
    return url

def parse_price_from_text(price_text):
    """
    Försöker plocka ut ett numeriskt pris från text, t.ex. "SEK 299" eller "299 kr".
    Returnerar float eller None.
    """
    if not price_text:
        return None
    # ta bort icke-numeriska utom punkt/komma
    m = re.search(r'(\d+[.,]?\d*)', price_text.replace('\u00A0',' '))
    if m:
        num = m.group(1).replace(',', '.')
        try:
            return float(num)
        except:
            return None
    return None

def fetch_products_from_tradera(filters, max_pages=2):
    """
    Scrapar Tradera sökresultat och returnerar en lista med produkter:
    [{"title":..., "link":..., "price":..., "raw_price_text":...}, ...]
    - max_pages: hur många sökresultatsidor vi går igenom (håll lågt i början)
    OBS: selectors kan behöva justeras om Tradera ändrar layout.
    """
    cache_key = ("tradera", frozenset(filters.items()))
    if cache_key in simple_cache:
        return simple_cache[cache_key]

    url = build_tradera_search_url(filters)
    products = []

    # Loop över (några) sökresultatsidor
    for page in range(1, max_pages + 1):
        page_url = url + f"&page={page}"
        r = polite_get(page_url)
        if not r:
            break
        soup = BeautifulSoup(r.text, "html.parser")

        # Heuristisk: hitta länkar som ser ut som produktlänkar.
        # Vi söker efter <a> som innehåller '/item/' eller '/auction/' eller '/product/'.
        anchors = soup.find_all("a", href=True)
        seen_links = set()
        for a in anchors:
            href = a["href"]
            href_l = href.lower()
            if any(x in href_l for x in ["/item/", "/auction/", "/auktion/", "/product/"]) or re.search(r'/\d{3,}', href):
                # gör link absolut om behövs
                if href.startswith("//"):
                    link = "https:" + href
                elif href.startswith("http"):
                    link = href
                else:
                    link = requests.compat.urljoin("https://www.tradera.com", href)
                # undvik dubbletter
                if link in seen_links:
                    continue
                seen_links.add(link)
                # få titel (från a eller dess parent)
                title = a.get_text(separator=" ", strip=True)
                if not title:
                    parent = a.find_parent()
                    title = parent.get_text(separator=" ", strip=True) if parent else ""
                title = normalize_text(title)

                # försök hitta pris nära länken (söka i samma parent)
                price = None
                raw_price_text = None
                parent = a.find_parent()
                if parent:
                    price_candidates = parent.find_all(text=re.compile(r'\d+[.,]?\d*\s*(kr|sek|eur)?', re.I))
                    for pc in price_candidates:
                        pval = parse_price_from_text(pc)
                        if pval is not None:
                            price = pval
                            raw_price_text = pc.strip()
                            break
                # fallback: sök i hela sidan efter pris-element i närheten (svag heuristik)
                if price is None:
                    nearby = a.find_next(string=re.compile(r'\d+[.,]?\d*\s*(kr|sek)?', re.I))
                    if nearby:
                        pval = parse_price_from_text(nearby)
                        if pval is not None:
                            price = pval
                            raw_price_text = nearby.strip()

                products.append({
                    "title": title,
                    "link": link,
                    "price": price,
                    "raw_price_text": raw_price_text
                })

        # enkel stoppregel: om inga anchors hittades på sida -> avbryt
        if not anchors:
            break

    # dedupliera via link
    unique = {}
    for p in products:
        if p["link"] not in unique:
            unique[p["link"]] = p
    products = list(unique.values())

    # cache:a kort tid (i-minnet)
    simple_cache[cache_key] = products
    return products

# --- URVAL / MATCHNING ---
def score_product_against_filters(product, filters):
    """
    Ger en poäng (lägre = bättre) baserat på hur väl produkt matchar filters.
    Heuristik: avvikelse från pris + matchning av brand/category/size/color i title.
    """
    score = 0.0
    title = product.get("title", "")

    # Price preference: vill ligga inom bounds
    price = product.get("price")
    if price is None:
        score += 1000  # dåligt om vi inte hittar pris
    else:
        if filters.get("price_min") is not None:
            try:
                if price < float(filters["price_min"]):
                    score += (float(filters["price_min"]) - price) * 2
            except:
                pass
        if filters.get("price_max") is not None:
            try:
                if price > float(filters["price_max"]):
                    score += (price - float(filters["price_max"])) * 2
            except:
                pass
        # prefer lower price
        score += price / 1000.0

    # brand match
    if filters.get("brand"):
        if token_in_text(filters["brand"], title):
            score -= 5
        else:
            score += 5

    # color match
    if filters.get("color"):
        if token_in_text(filters["color"], title):
            score -= 2
        else:
            score += 1

    # size match (ofta i title)
    if filters.get("size"):
        if token_in_text(filters["size"], title):
            score -= 1
        else:
            score += 1

    # category match
    if filters.get("category"):
        if token_in_text(filters["category"], title):
            score -= 3
        else:
            score += 2

    # kortare titlar kan vara mindre tydliga -> liten penalty
    if len(title) < 10:
        score += 1

    return score

def find_best_match(filters):
    """
    Huvudfunktion: hämtar produkter från Tradera (scrapning), scorer dem och returnerar bästa länk.
    """
    # Hämta produkter (begränsat antal sidor för att vara snäll mot sajten)
    items = fetch_products_from_tradera(filters, max_pages=2)
    if not items:
        return None

    # Räkna score för varje item
    scored = []
    for it in items:
        s = score_product_against_filters(it, filters)
        scored.append((s, it))
    scored.sort(key=lambda x: x[0])  # lägst score är bäst

    best = scored[0][1]
    return best.get("link")

# --- FLASK ENDPOINT ---
@app.route('/search', methods=['POST'])
def search():
    filters = request.json or {}
    # rensa whitespace etc
    filters = {k: (v.strip() if isinstance(v, str) else v) for k, v in filters.items()}

    # Kör sök + matchning
    try:
        best_link = find_best_match(filters)
    except Exception as e:
        # vid oväntat fel, logga och returnera 500
        print("Error in find_best_match:", e)
        return jsonify({"error": "internal_error"}), 500

    if not best_link:
        return jsonify({"message": "Ingen produkt hittad"}), 404

    return jsonify({"best_match_link": best_link})

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
