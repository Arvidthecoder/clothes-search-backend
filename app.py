from flask import Flask, request, jsonify
import requests
from urllib.parse import quote_plus
import re

app = Flask(__name__)

# --------------------------------------------
# Hjälpfunktioner för smart sökning
# --------------------------------------------
def normalize_query(text):
    """Förenkla text (t.ex. 'baggy jeans' -> 'wide jeans')."""
    synonyms = {
        "baggy": ["wide", "loose", "relaxed"],
        "grisch": ["grey", "streetwear"],
        "oversized": ["loose", "relaxed"],
        "jacka": ["jacket", "coat"],
        "tröja": ["sweater", "hoodie", "shirt"],
        "byxor": ["pants", "trousers", "jeans"]
    }
    text = text.lower()
    for key, words in synonyms.items():
        if key in text:
            text += " " + " ".join(words)
    return text


def rank_results(results, query):
    """Välj mest relevant och billigast produkt."""
    if not results:
        return None

    query_words = set(query.lower().split())

    def score(item):
        title = item.get("title", "").lower()
        title_words = set(re.findall(r"\w+", title))
        common = len(query_words & title_words)
        price = float(item.get("price", 999999)) if str(item.get("price", "")).isdigit() else 999999
        return (-common, price)

    results.sort(key=score)
    return results[0]


# --------------------------------------------
# Scrapers
# --------------------------------------------
def fetch_vinted(query):
    """Vinted API."""
    try:
        url = f"https://www.vinted.se/api/v2/catalog/items?q={quote_plus(query)}"
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        data = r.json()
        items = data.get("items", [])
        results = []
        for item in items[:10]:
            results.append({
                "title": item.get("title", "okänd produkt"),
                "price": item.get("price_numeric", ""),
                "image": item.get("photo", {}).get("url", ""),
                "link": f"https://www.vinted.se/items/{item.get('id')}"
            })
        return results
    except Exception as e:
        print("Vinted error:", e)
        return []


def fetch_sellpy(query):
    """Sellpy HTML-parser."""
    try:
        base_url = "https://www.sellpy.se/sok"
        url = f"{base_url}?q={quote_plus(query)}"
        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            return []
        text = r.text

        titles = re.findall(r'<h2[^>]*>(.*?)</h2>', text)
        links = re.findall(r'href="(/produkt/[^"]+)"', text)
        images = re.findall(r'src="(https://[^"]+\.jpg)"', text)
        prices = re.findall(r'(\d+)\s*kr', text)

        results = []
        for i in range(min(len(links), 5)):
            results.append({
                "title": titles[i] if i < len(titles) else "Sellpy-produkt",
                "price": prices[i] if i < len(prices) else "",
                "image": images[i] if i < len(images) else "",
                "link": f"https://www.sellpy.se{links[i]}"
            })
        return results
    except Exception as e:
        print("Sellpy error:", e)
        return []


def fetch_tradera(query):
    """Tradera HTML-parser."""
    try:
        base_url = "https://www.tradera.com/sok"
        url = f"{base_url}?q={quote_plus(query)}"
        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            return []
        text = r.text

        titles = re.findall(r'alt="([^"]+)"', text)
        links = re.findall(r'href="(/item/[^"]+)"', text)
        prices = re.findall(r'(\d+)\s*kr', text)
        images = re.findall(r'src="(https://[^"]+\.jpg)"', text)

        results = []
        for i in range(min(len(links), 5)):
            results.append({
                "title": titles[i] if i < len(titles) else "Tradera-produkt",
                "price": prices[i] if i < len(prices) else "",
                "image": images[i] if i < len(images) else "",
                "link": f"https://www.tradera.com{links[i]}"
            })
        return results
    except Exception as e:
        print("Tradera error:", e)
        return []


# --------------------------------------------
# Flask endpoint
# --------------------------------------------
@app.route("/search", methods=["POST"])
def search():
    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({"status": "error", "reason": f"Invalid JSON: {e}"}), 400

    # Hämta filter
    category = data.get("category", "")
    brand = data.get("brand", "")
    size = data.get("size", "")
    color = data.get("color", "")
    condition = data.get("condition", "")
    query = " ".join(filter(None, [brand, category, size, color, condition]))
    query = normalize_query(query)

    all_results = fetch_vinted(query)
    if not all_results:
        all_results = fetch_sellpy(query)
    if not all_results:
        all_results = fetch_tradera(query)

    best = rank_results(all_results, query)

    if not best:
        return jsonify({
            "status": "no_results",
            "title": "",
            "price": "",
            "image": "",
            "best_match_link": "",
            "results": []
        })

    return jsonify({
        "status": "success",
        "title": best["title"],
        "price": best["price"],
        "image": best["image"],
        "best_match_link": best["link"],
        "results": all_results
    })


if __name__ == "__main__":
    app.run(debug=True)
