from flask import Flask, request, jsonify
import requests
from urllib.parse import quote_plus
import re

app = Flask(__name__)

# --------------------------------------------
# Hjälpfunktioner – språkförståelse & ranking
# --------------------------------------------
def normalize_query(text):
    """
    Gör sökningen smartare:
    Lägger till synonymer så att 'baggy jeans' även hittar 'loose fit jeans'.
    """
    synonyms = {
        "baggy": ["wide", "loose", "relaxed"],
        "grisch": ["grey", "streetwear"],
        "oversized": ["loose", "relaxed"],
        "jacka": ["jacket", "coat"],
        "tröja": ["sweater", "hoodie", "shirt"],
        "byxor": ["pants", "trousers", "jeans"],
        "jeans": ["denim"],
        "skor": ["shoes", "sneakers", "boots"],
        "vintage": ["retro", "oldschool"]
    }

    text = text.lower()
    for key, words in synonyms.items():
        if key in text:
            text += " " + " ".join(words)
    return text


def parse_price(price_str):
    """Försöker hitta ett numeriskt pris i en text, t.ex. '299 kr' -> 299."""
    match = re.findall(r"\d+", str(price_str))
    return float(match[0]) if match else 999999


def rank_results(results, query):
    """
    Rankar resultat baserat på hur många ord i titeln matchar sökningen
    och vilket pris som är lägst.
    """
    if not results:
        return None

    query_words = set(query.lower().split())

    def score(item):
        title = item.get("title", "").lower()
        title_words = set(re.findall(r"\w+", title))
        common = len(query_words & title_words)
        price = parse_price(item.get("price", ""))
        return (-common, price)

    results.sort(key=score)
    return results[0]


# --------------------------------------------
# Scrapers för varje plattform
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
                "link": f"https://www.vinted.se/items/{item.get('id')}",
                "site": "vinted"
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
                "link": f"https://www.sellpy.se{links[i]}",
                "site": "sellpy"
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
                "link": f"https://www.tradera.com{links[i]}",
                "site": "tradera"
            })
        return results
    except Exception as e:
        print("Tradera error:", e)
        return []


def fetch_plick(query):
    """Plick HTML-parser."""
    try:
        base_url = "https://plick.se/sok"
        url = f"{base_url}?q={quote_plus(query)}"
        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            return []
        text = r.text

        titles = re.findall(r'<h2[^>]*>(.*?)</h2>', text)
        links = re.findall(r'href="(/item/[^"]+)"', text)
        images = re.findall(r'src="(https://[^"]+\.jpg)"', text)
        prices = re.findall(r'(\d+)\s*kr', text)

        results = []
        for i in range(min(len(links), 5)):
            results.append({
                "title": titles[i] if i < len(titles) else "Plick-produkt",
                "price": prices[i] if i < len(prices) else "",
                "image": images[i] if i < len(images) else "",
                "link": f"https://plick.se{links[i]}",
                "site": "plick"
            })
        return results
    except Exception as e:
        print("Plick error:", e)
        return []


# --------------------------------------------
# Flask endpoints
# --------------------------------------------
@app.route("/search", methods=["POST"])
def search():
    """
    Huvudfunktionen – tar emot data från Adalo,
    söker på flera sajter, filtrerar, rankar och returnerar bästa produkten.
    """
    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({"status": "error", "reason": f"Invalid JSON: {e}"}), 400

    # Filtrera baserat på input
    category = data.get("category", "")
    brand = data.get("brand", "")
    size = data.get("size", "")
    color = data.get("color", "")
    condition = data.get("condition", "")
    min_price = float(data.get("min_price", 0))
    max_price = float(data.get("max_price", 999999))

    # Bygg sökfråga
    query = " ".join(filter(None, [brand, category, size, color, condition]))
    query = normalize_query(query)

    # Hämta från flera källor
    all_results = fetch_vinted(query) + fetch_sellpy(query) + fetch_tradera(query) + fetch_plick(query)

    # Filtrera bort onödiga priser
    filtered = []
    for item in all_results:
        price = parse_price(item.get("price", ""))
        if min_price <= price <= max_price:
            filtered.append(item)

    best = rank_results(filtered or all_results, query)

    if not best:
        return jsonify({
            "status": "no_results",
            "query": query,
            "best": {},
            "results": []
        })

    return jsonify({
        "status": "success",
        "query": query,
        "best": best,
        "results_count": len(filtered or all_results)
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True)
