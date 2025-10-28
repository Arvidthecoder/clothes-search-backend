from flask import Flask, request, jsonify
import requests
from urllib.parse import quote_plus

app = Flask(__name__)

# ------------------------------------
# HJÄLPFUNKTIONER
# ------------------------------------
def fetch_vinted(query):
    """Söker produkter på Vinted via deras offentliga API."""
    try:
        url = f"https://www.vinted.se/api/v2/catalog/items?q={quote_plus(query)}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        items = data.get("items", [])
        results = []
        for item in items[:10]:
            results.append({
                "title": item.get("title", "okänt"),
                "price": item.get("price_numeric", ""),
                "link": f"https://www.vinted.se/items/{item.get('id')}"
            })
        return results
    except Exception as e:
        print("Vinted error:", e)
        return []

def fetch_sellpy(query):
    """Scrape från Sellpy (public sida)."""
    try:
        base_url = "https://www.sellpy.se/sok"
        url = f"{base_url}?q={quote_plus(query)}"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return []
        # Litet trick: Sellpy renderar mycket via JS, men vi tar titlar ur HTML ändå
        text = r.text
        links = []
        for part in text.split('"productCard__link" href="')[1:5]:
            link = part.split('"')[0]
            links.append(f"https://www.sellpy.se{link}")
        return [{"title": "Sellpy-resultat", "price": "okänt", "link": l} for l in links]
    except Exception as e:
        print("Sellpy error:", e)
        return []

def fetch_tradera(query):
    """Scrape från Tradera (förenklad)."""
    try:
        base_url = "https://www.tradera.com/sok"
        url = f"{base_url}?q={quote_plus(query)}"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return []
        text = r.text
        links = []
        for part in text.split('href="/item/')[1:5]:
            link = part.split('"')[0]
            links.append(f"https://www.tradera.com/item/{link}")
        return [{"title": "Tradera-resultat", "price": "okänt", "link": l} for l in links]
    except Exception as e:
        print("Tradera error:", e)
        return []

def rank_results(results, query):
    """Returnerar mest relevant produkt."""
    if not results:
        return None
    # Enkel ranking: billigast först, sedan närmast match i titel
    results = sorted(results, key=lambda x: (str(x.get("price", "")), x.get("title", "").lower().count(query.lower())))
    return results[0]

# ------------------------------------
# API ENDPOINT
# ------------------------------------
@app.route("/search", methods=["POST"])
def search():
    try:
        data = request.get_json(force=True) or {}
    except Exception as e:
        return jsonify({"status": "error", "reason": f"Invalid JSON: {e}"}), 400

    category = data.get("category", "")
    brand = data.get("brand", "")
    size = data.get("size", "")
    color = data.get("color", "")
    price_min = data.get("price_min", "")
    price_max = data.get("price_max", "")
    condition = data.get("condition", "")

    query = " ".join(filter(None, [brand, category, size, color, condition]))

    all_results = []
    all_results += fetch_vinted(query)
    if not all_results:
        all_results += fetch_sellpy(query)
    if not all_results:
        all_results += fetch_tradera(query)

    best = rank_results(all_results, query)

    if not best:
        return jsonify({
            "status": "no_results",
            "title": "",
            "price": "",
            "best_match_link": "",
            "results": []
        })

    return jsonify({
        "status": "success",
        "title": best["title"],
        "price": best["price"],
        "best_match_link": best["link"],
        "results": all_results
    })

if __name__ == "__main__":
    app.run(debug=True)
