# app.py
from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

app = Flask(__name__)

# -----------------------------
# Hjälpfunktion: polite_get (säker request)
# -----------------------------
def polite_get(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return r
    except Exception as e:
        print("Request error:", e)
    return None

# -----------------------------
# Scraperfunktioner (plats för framtida)
# -----------------------------
def scrape_vinted(query):
    base_url = "https://www.vinted.se"
    url = f"{base_url}/sok?q={quote_plus(query)}"
    r = polite_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    products = soup.select("a.product-card")
    results = []
    for p in products[:3]:
        href = p.get("href")
        if href:
            results.append(requests.compat.urljoin(base_url, href))
    return results

def scrape_sellpy(query):
    base_url = "https://www.sellpy.se/sok?q="
    url = f"{base_url}{quote_plus(query)}"
    r = polite_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    products = soup.select("a.product-card")
    results = []
    for p in products[:3]:
        href = p.get("href")
        if href:
            results.append(requests.compat.urljoin("https://www.sellpy.se", href))
    return results

def scrape_tradera(query):
    base_url = "https://www.tradera.com/sok?q="
    url = f"{base_url}{quote_plus(query)}"
    r = polite_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    products = soup.select("a.search-result-item")
    results = []
    for p in products[:3]:
        href = p.get("href")
        if href:
            results.append(requests.compat.urljoin("https://www.tradera.com", href))
    return results

# -----------------------------
# /search endpoint (setup version för Adalo)
# -----------------------------
@app.route("/search", methods=["POST"])
def search():
    data = request.get_json() or {}
    category = data.get("category", "")
    brand = data.get("brand", "")
    size = data.get("size", "")
    color = data.get("color", "")
    condition = data.get("condition", "")

    # Kombinera till söksträng
    query_parts = [brand, category, size, color, condition]
    query = " ".join([p for p in query_parts if p])

    # --- Här kommer testdata för att Adalo ska känna igen outputs ---
    return jsonify({
        "status": "success",
        "best_match_link": "https://example.com/test-product",
        "results": [
            "https://example.com/test1",
            "https://example.com/test2",
            "https://example.com/test3"
        ]
    })

# -----------------------------
# Flask start
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True)
