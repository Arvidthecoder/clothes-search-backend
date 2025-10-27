# app.py
from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
import random

app = Flask(__name__)

# --------------------------------------
# Hjälpfunktion: gör enkel GET-förfrågan
# --------------------------------------
def safe_get(url):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            return r
    except Exception as e:
        print("Request error:", e)
    return None


# --------------------------------------
# Scraper-funktioner
# --------------------------------------
def scrape_vinted(query):
    base = "https://www.vinted.se"
    url = f"{base}/sok?q={quote_plus(query)}"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    links = [a.get("href") for a in soup.select("a") if a.get("href") and "/items/" in a.get("href")]
    return [base + l for l in links[:3]]


def scrape_sellpy(query):
    base = "https://www.sellpy.se"
    url = f"{base}/sok?q={quote_plus(query)}"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    links = [a.get("href") for a in soup.select("a") if a.get("href") and "/products/" in a.get("href")]
    return [base + l for l in links[:3]]


def scrape_tradera(query):
    base = "https://www.tradera.com"
    url = f"{base}/sok?q={quote_plus(query)}"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    links = [a.get("href") for a in soup.select("a") if a.get("href") and "/item/" in a.get("href")]
    return [base + l for l in links[:3]]


def scrape_plick(query):
    base = "https://plick.se"
    url = f"{base}/sok?q={quote_plus(query)}"
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    links = [a.get("href") for a in soup.select("a") if a.get("href") and "/p/" in a.get("href")]
    return [base + l for l in links[:3]]


# --------------------------------------
# Enkel "ranking" – ta första rimliga länk
# --------------------------------------
def rank_results(results, query):
    if not results:
        return None
    # slumpa bland toppresultaten för test
    return random.choice(results[:3])


# --------------------------------------
# /search endpoint
# --------------------------------------
@app.route("/search", methods=["POST"])
def search():
    data = request.get_json() or {}
    category = data.get("category", "")
    brand = data.get("brand", "")
    size = data.get("size", "")
    color = data.get("color", "")
    condition = data.get("condition", "")

    # Gör en bred söksträng
    query_parts = [brand, category, size, color, condition]
    query = " ".join([p for p in query_parts if p])

    # Samla resultat
    all_results = []
    all_results += scrape_vinted(query)
    if not all_results:
        all_results += scrape_sellpy(query)
    if not all_results:
        all_results += scrape_tradera(query)
    if not all_results:
        all_results += scrape_plick(query)

    best_link = rank_results(all_results, query)

    # Returnera alltid JSON med båda fält
    if not best_link:
        return jsonify({
            "status": "no_results",
            "best_match_link": ""
        })

    return jsonify({
        "status": "success",
        "best_match_link": best_link
    })


# --------------------------------------
# Flask start
# --------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
