from flask import Flask, request, jsonify

app = Flask(__name__)

# Dummy-data
products = [
    {
        "link": "https://example.com/product/123",
        "category": "jeans",
        "brand": "Nike",
        "size": "M",
        "color": "svart",
        "price": 300,
        "condition": "begagnad"
    }
]

@app.route('/search', methods=['POST'])
def search():
    filters = request.json
    # För prototypen returnerar vi alltid samma produkt
    return jsonify({"best_match_link": products[0]["link"]})

if __name__ == '__main__':
    app.run(debug=True)
