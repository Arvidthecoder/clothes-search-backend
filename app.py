# app.py
from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

app = Flask(__name__)

# -----------------------------
# Helpers
# -----------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

def polite_get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return r
    except Exception as e:
        print(f"Error requesting {url}: {e}")
    return None

# -----------------------------
# Scraperfunktioner
# -----------------------------

def scrape_vinted(category, brand, size, color, price_min, price_max, condition):
    base_url = "https://www.vinted.se"
    query_parts = [p for p in [category, brand, size, color] if p]
    query = " ".join(query_parts)
    url = f"{base_url}/sok?q={quote_plus(query)}"
    r = polite_get(url)
    if not r: return None
    soup = BeautifulSoup(r.text, "html.parser")
    product = soup.select_one("a.product-card")
    if product:
        return requests.compat.urljoin(base_url, product.get("href"))
    return None

def scrape_sellpy(category, brand, size, color, price_min, price_max, condition):
    base_url = "https://www.sellpy.se/sok"
    query_parts = [p for p in [category, brand, size, color] if p]
    query = " ".join(query_parts)
    url = f"{base_url}?q={quote_plus(query)}"
    r = polite_get(url)
    if not r: return None
    soup = BeautifulSoup(r.text, "html.parser")
    product = soup.select_one("a.product-card")
    if product:
        return requests.compat.urljoin("https://www.sellpy.se", product.get("href"))
    return None

def scrape_tradera(category, brand, size, color, price_min, price_max, condition):
    base_url = "https://www.tradera.com/sok"
    query_parts = [p for p in [category, brand, size, color] if p]
    query = " ".join(query_parts)
    url = f"{base_url}?q={quote_plus(query)}"
    r = polite_get(url)
    if not r: return None
    soup = BeautifulSoup(r.text, "html.parser")
    product = soup.select_one("a.search-result-item")
    if product:
        return requests.compat.urljoin(base_url, product.get("href"))
    return None

def scrape_blocket(category, brand, size, color, price_min, price_max, condition):
    base_url = "https://www.blocket.se/annonser/hela_sverige"
    query_parts = [p for p in [category, brand, size, color] if p]
    query = " ".join(query_parts)
    url = f"{base_url}?q={quote_plus(query)}"
    r = polite_get(url)
    if not r: return None
    soup = BeautifulSoup(r.text, "html.parser")
    product = soup.select_one("a.ads__unit__link")
    if product:
        return requests.compat.urljoin(base_url, product.get("href"))
    return None

def find_best_match(filters):
    return scrape_tradera(
        filters.get("category"),
        filters.get("brand"),
        filters.get("size"),
        filters.get("color"),
        filters.get("price_min"),
        filters.get("price_max"),
        filters.get("condition")
    )

# -----------------------------
# /search route med 4 fallback
# -----------------------------
@app.route('/search', methods=['POST'])
def search():
    data = request.get_json() or {}
    category = data.get('category')
    brand = data.get('brand')
    size = data.get('size')
    color = data.get('color')
    price_min = data.get('price_min')
    price_max = data.get('price_max')
    condition = data.get('condition')

    filters = {
        "category": category,
        "brand": brand,
        "size": size,
        "color": color,
        "price_min": price_min,
        "price_max": price_max,
        "condition": condition
    }

    # --- 1️⃣ Vinted ---
    best_link = scrape_vinted(**filters)

    # --- 2️⃣ Sellpy fallback ---
    if not best_link:
        best_link = scrape_sellpy(**filters)

    # --- 3️⃣ Tradera fallback ---
    if not best_link:
        best_link = find_best_match(filters)

    # --- 4️⃣ Blocket fallback ---
    if not best_link:
        best_link = scrape_blocket(**filters)

    # --- Returnera endast om något hittas ---
    if best_link:
        return jsonify({"best_match_link": best_link})
    else:
        return jsonify({})  # tom JSON om inget hittas

# -----------------------------
# Flask start
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True)
