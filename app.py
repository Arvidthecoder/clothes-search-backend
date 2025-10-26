# app.py
from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
import time

app = Flask(__name__)

# -----------------------------
# Scraperfunktioner
# -----------------------------

def scrape_vinted(category, brand, size, color, price_min, price_max, condition):
    """
    Enkel Vinted-scraper: returnerar första matchande produktlänk.
    """
    base_url = "https://www.vinted.se"
    query_parts = []
    if category: query_parts.append(category)
    if brand: query_parts.append(brand)
    if size: query_parts.append(size)
    if color: query_parts.append(color)
    query = " ".join(query_parts)
    url = f"{base_url}/sok?q={quote_plus(query)}"

    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        product = soup.select_one("a.product-card")  # OBS: kan behöva uppdateras
        if product:
            return requests.compat.urljoin(base_url, product.get("href"))
    except Exception as e:
        print("Error scraping Vinted:", e)
    return None


def scrape_sellpy(category, brand, size, color, price_min, price_max, condition):
    """
    Enkel Sellpy-scraper: returnerar första matchande produktlänk.
    """
    base_url = "https://www.sellpy.se/sok"
    query_parts = []
    if brand: query_parts.append(brand)
    if category: query_parts.append(category)
    if size: query_parts.append(size)
    if color: query_parts.append(color)
    query = " ".join(query_parts)
    url = f"{base_url}?q={quote_plus(query)}"

    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        product = soup.select_one("a.product-card")  # OBS: kan behöva uppdateras
        if product:
            return requests.compat.urljoin("https://www.sellpy.se", product.get("href"))
    except Exception as e:
        print("Error scraping Sellpy:", e)
    return None


def scrape_tradera(category, brand, size, color, price_min, price_max, condition):
    """
    Exempel Tradera-scraper: returnerar första matchande produktlänk.
    """
    base_url = "https://www.tradera.com/sok"
    query_parts = []
    if category: query_parts.append(category)
    if brand: query_parts.append(brand)
    if size: query_parts.append(size)
    if color: query_parts.append(color)
    query = " ".join(query_parts)
    url = f"{base_url}?q={quote_plus(query)}"

    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        product = soup.select_one("a.search-result-item")  # OBS: kan behöva uppdateras
        if product:
            return requests.compat.urljoin(base_url, product.get("href"))
    except Exception as e:
        print("Error scraping Tradera:", e)
    return None


def find_best_match(filters):
    """
    Wrapper för Tradera-scraper, använder filter-dict.
    """
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
# /search route med flera fallback
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

    # Förbered filters dict
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
    best_link = scrape_vinted(category, brand, size, color, price_min, price_max, condition)

    # --- 2️⃣ Sellpy fallback ---
    if not best_link:
        best_link = scrape_sellpy(category, brand, size, color, price_min, price_max, condition)

    # --- 3️⃣ Tradera fallback ---
    if not best_link:
        best_link = find_best_match(filters)

    # --- Om fortfarande inget hittas ---
    if not best_link:
        return jsonify({"best_match_link": "No results"}), 404

    # --- Returnera bästa länk ---
    return jsonify({"best_match_link": best_link})


# -----------------------------
# Flask start
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True)
