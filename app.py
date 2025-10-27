# app.py
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# -----------------------------
# Funktion: Sök på Vinted
# -----------------------------
def search_vinted(category, brand, size, color, condition):
    """
    Söker produkter på Vinted via deras API.
    Returnerar en lista med (titel, pris, länk).
    """
    base_url = "https://www.vinted.se/api/v2/catalog/items"
    params = {
        "search_text": " ".join(filter(None, [brand, category, size, color])),
        "status": "used" if condition.lower() == "begagnad" else "new",
        "per_page": 10
    }

    try:
        r = requests.get(base_url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        results = []
        for item in data.get("items", []):
            title = item.get("title", "")
            price = item.get("price_cents", 0) / 100
            link = f"https://www.vinted.se/items/{item.get('id')}"
            results.append({"title": title, "price": price, "link": link})

        # Sortera efter lägsta pris
        results.sort(key=lambda x: x["price"])
        return results

    except Exception as e:
        print("Fel vid Vinted-anrop:", e)
        return []

# -----------------------------
# Flask endpoint /search
# -----------------------------
@app.route("/search", methods=["POST"])
def search():
    data = request.get_json() or {}
    category = data.get("category", "")
    brand = data.get("brand", "")
    size = data.get("size", "")
    color = data.get("color", "")
    condition = data.get("condition", "")

    results = search_vinted(category, brand, size, color, condition)

    if not results:
        return jsonify({
            "status": "no_results",
            "title": "",
            "price": "",
            "best_match_link": ""
        })

    # Välj billigaste/mest relevanta
    best = results[0]

    return jsonify({
        "status": "success",
        "title": best["title"],
        "price": best["price"],
        "best_match_link": best["link"]
    })

# -----------------------------
# Flask start
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True)
