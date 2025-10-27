# app.py
from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

app = Flask(__name__)

# -----------------------------
# Hjälpfunktion: säker GET-request
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
# Scraperfunktioner för varje plattform
# -----------------------------
def scrape_vinted(query):
    base_url = "https://www.vinted.se"
    url = f"{base_url}/sok?q={quote_plus(query)}"
    r = polite_get(url)
    results = []
    if not r:
        return results
    soup = BeautifulSoup(r.text, "html.parser")
    products = soup.select("a.product-card")
    for p in products[:5]:
        href = p.get("href")
        if href:
            results.append(requests.compat.urljoin(base_url, href))
    return results

def scrape_sellpy(query):
    base_url = "https://www.sellpy.se/sok?q="
    url = f"{base_url}{quote_plus(query)}"
    r = polite_get(url)
    results = []
    if not r:
        return results
    soup = BeautifulSoup(r.text, "html.parser")
    products = soup.select("a.product-card")
    for p in products[:5]:
        href = p.get("href")
        if href:
            results.append(requests.compat.urljoin("https://www.sellpy.se", href))
    return results

def scrape_tradera(query):
    base_url = "https://www.tradera.com/sok?q="
    url = f"{base_url}{quote_plus(query)}"
    r = polite_get(url)
    results = []
    if not r:
        return results
    soup = BeautifulSoup(r.text, "html.parser")
    products = soup.select("a.search-result-item")
    for p in products[:5]:
        href = p.get("href")
        if href:
            results.append(requests.compat.urljoin("https://www.tradera.com", href))
    return results

# -----------------------------
# Rankning av resultat: returnerar första match
# -----------------------------
def rank_results(all_results, query):
    if not all_results:
        return None
    # Enklaste logiken: första resultat är mest relevant
    return all_results[0]

# -----------------------------
# /search endpoint
# -----------------------------
@app.route("/search", methods=["POST"])
def search():
    data = request.get_json() or {}
    category = data.get("category", "")
    brand = data.get("brand", "")
    size = data.get("size", "")
    color = data.get("color", "")
    condition = data.get("condition", "")

    # Kombinera filter till söksträng
    query_parts = [brand, category, size, color, condition]
    query = " ".join([p for p in query_parts if p])

    all_results = []

    # --- 1️⃣ Vinted ---
    all_results += scrape_vinted(query)

    # --- 2️⃣ Sellpy fallback ---
    if not all_results:
        all_results += scrape_sellpy(query)

    # --- 3️⃣ Tradera fallback ---
    if not all_results:
        all_results += scrape_tradera(query)

    # --- Rankning ---
    best_link = rank_results(all_results, query)

    # --- Returnera JSON för Adalo ---
    if not best_link:
        return jsonify({
            "status": "no_results",
            "best_match_link": "",
            "results": []
        }), 404

    return jsonify({
        "status": "success",
        "best_match_link": best_link,
        "results": all_results
    })

# -----------------------------
# Flask start
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True)
