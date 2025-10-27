# app.py
from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

app = Flask(__name__)

# -----------------------------
# Hjälpfunktion: GET med user-agent
# -----------------------------
def polite_get(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return r
    except Exception as e:
        print("Fel vid GET:", e)
    return None

# -----------------------------
# Scraper: Vinted
# -----------------------------
def scrape_vinted(query):
    base_url = "https://www.vinted.se"
    url = f"{base_url}/sok?q={quote_plus(query)}"
    r = polite_get(url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "/items/" in href:
            results.append(requests.compat.urljoin(base_url, href))
    return results[:5]

# -----------------------------
# Scraper: Sellpy
# -----------------------------
def scrape_sellpy(query):
    base_url = "https://www.sellpy.se/sok"
    url = f"{base_url}?q={quote_plus(query)}"
    r = polite_get(url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for link in soup.find_all("a", href=True):
        if "/produkt/" in link["href"]:
            results.append(requests.compat.urljoin("https://www.sellpy.se", link["href"]))
    return results[:5]

# -----------------------------
# Scraper: Tradera
# -----------------------------
def scrape_tradera(query):
    base_url = "https://www.tradera.com/sok"
    url = f"{base_url}?q={quote_plus(query)}"
    r = polite_get(url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for link in soup.find_all("a", href=True):
        if "/item/" in link["href"]:
            results.append(requests.compat.urljoin(base_url, link["href"]))
    return results[:5]

# -----------------------------
# Scraper: Plick
# -----------------------------
def scrape_plick(query):
    base_url = "https://plick.se/sok"
    url = f"{base_url}?q={quote_plus(query)}"
    r = polite_get(url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for link in soup.find_all("a", href=True):
        if "/p/" in link["href"]:
            results.append(requests.compat.urljoin("https://plick.se", link["href"]))
    return results[:5]

# -----------------------------
# Rankering: välj mest relevant länk
# -----------------------------
def rank_results(all_results, query):
    query_words = query.lower().split()
    scored = []
    for url in all_results:
        score = sum(word in url.lower() for word in query_words)
        scored.append((score, url))
    scored.sort(reverse=True)
    if scored:
        return scored[0][1]
    return None

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
    price_min = data.get("price_min", "")
    price_max = data.get("price_max", "")
    condition = data.get("condition", "")

    # Kombinera till söksträng
    query_parts = [brand, category, size, color, condition]
    query = " ".join([p for p in query_parts if p])

    # Samla resultat från alla sidor
    all_results = []
    all_results += scrape_vinted(query)
    all_results += scrape_sellpy(query)
    all_results += scrape_tradera(query)
    all_results += scrape_plick(query)

    best_link = rank_results(all_results, query)

    # --- alltid returnera 'best_match_link' som text ---
    if not best_link:
        return jsonify({
            "status": "no_results",
            "best_match_link": ""
        })

    return jsonify({
        "status": "success",
        "best_match_link": best_link
    })

# -----------------------------
# Flask start
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True)

