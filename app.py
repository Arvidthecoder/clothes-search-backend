# app.py
from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
import re

app = Flask(__name__)

# --------------------------------------------------------
# Hjälpfunktion: gemensamma headers
# --------------------------------------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/128.0 Safari/537.36"
}


# --------------------------------------------------------
# VINTED SCRAPER
# --------------------------------------------------------
def scrape_vinted(category, brand, size, color, price_min, price_max, condition):
    base_url = "https://www.vinted.se"
    query = " ".join(filter(None, [category, brand, size, color]))
    url = f"{base_url}/sok?q={quote_plus(query)}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            print("Vinted status:", r.status_code)
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        product = soup.select_one("div.feed-grid__item a")
        if not product:
            match = re.search(r'href="(/items/\d+[^"]+)"', r.text)
            if match:
                return requests.compat.urljoin(base_url, match.group(1))
            return None
        return requests.compat.urljoin(base_url, product.get("href"))
    except Exception as e:
        print("Error scraping Vinted:", e)
        return None


# --------------------------------------------------------
# SELLPY SCRAPER
# --------------------------------------------------------
def scrape_sellpy(category, brand, size, color, price_min, price_max, condition):
    base_url = "https://www.sellpy.se/sok"
    query = " ".join(filter(None, [brand, category, size, color]))
    url = f"{base_url}?q={quote_plus(query)}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            print("Sellpy status:", r.status_code)
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        product = soup.select_one("a[data-test='product-card']")
        if not product:
            match = re.search(r'href="(/produkter/\d+[^"]+)"', r.text)
            if match:
                return requests.compat.urljoin("https://www.sellpy.se", match.group(1))
            return None
        return requests.compat.urljoin("https://www.sellpy.se", product.get("href"))
    except Exception as e:
        print("Error scraping Sellpy:", e)
        return None


# --------------------------------------------------------
# TRADERA SCRAPER
# --------------------------------------------------------
def scrape_tradera(category, brand, size, color, price_min, price_max, condition):
    base_url = "https://www.tradera.com/sok"
    query = " ".join(filter(None, [category, brand, size, color]))
    url = f"{base_url}?q={quote_plus(query)}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            print("Tradera status:", r.status_code)
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        product = soup.select_one("a.item-card")
        if not product:
            match = re.search(r'href="(/item/\d+[^"]+)"', r.text)
            if match:
                return requests.compat.urljoin(base_url, match.group(1))
            return None
        return requests.compat.urljoin(base_url, product.get("href"))
    except Exception as e:
        print("Error scraping Tradera:", e)
        return None


# --------------------------------------------------------
# BLOCKET SCRAPER
# --------------------------------------------------------
def scrape_blocket(category, brand, size, color, price_min, price_max, condition):
    base_url = "https://www.blocket.se/annonser/hela_sverige"
    query = " ".join(filter(None, [brand, category, size, color]))
    url = f"{base_url}?q={quote_plus(query)}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            print("Blocket status:", r.status_code)
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        product = soup.select_one("a[href*='/annons/']")
        if not product:
            match = re.search(r'href="(/annons/[^"]+)"', r.text)
            if match:
                return requests.compat.urljoin("https://www.blocket.se", match.group(1))
            return None
        return requests.compat.urljoin("https://www.blocket.se", product.get("href"))
    except Exception as e:
        print("Error scraping Blocket:", e)
        return None


# --------------------------------------------------------
# /search route med flera fallback
# --------------------------------------------------------
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

    # --- 1️⃣ Vinted ---
    best_link = scrape_vinted(category, brand, size, color, price_min, price_max, condition)

    # --- 2️⃣ Sellpy fallback ---
    if not best_link:
        best_link = scrape_sellpy(category, brand, size, color, price_min, price_max, condition)

    # --- 3️⃣ Tradera fallback ---
    if not best_link:
        best_link = scrape_tradera(category, brand, size, color, price_min, price_max, condition)

    # --- 4️⃣ Blocket fallback ---
    if not best_link:
        best_link = scrape_blocket(category, brand, size, color, price_min, price_max, condition)

    # Returnera bara om något hittas
    if best_link:
        return jsonify({"best_match_link": best_link})
    else:
        return jsonify({})  # tom JSON = inget resultat


# --------------------------------------------------------
# Flask start
# --------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
