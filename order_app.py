import argparse
import threading

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

orders = {} 
next_id = 1
lock = threading.Lock()

CATALOG_URL = "http://127.0.0.1:5001"
CATALOG_TIMEOUT = 2

@app.route("/orders", methods=["POST"])
def create_order():
    global next_id

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    product_id = data.get("product_id")
    quantity = data.get("quantity")

    try:
        resp = requests.get(f"{CATALOG_URL}/products/{product_id}", timeout=CATALOG_TIMEOUT)
    except requests.exceptions.Timeout:
        return jsonify({"error": "Catalog service timed out. Please try again later."}), 503
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Catalog service is unavailable. Please try again later."}), 503
    except requests.exceptions.RequestException:
        return jsonify({"error": "Could not reach the catalog service."}), 503

    if resp.status_code == 404:
        return jsonify({"error": f"Product with id {product_id} not found in catalog"}), 404
    if resp.status_code != 200:
        return jsonify({"error": "Catalog service returned an unexpected error."}), 503

    try:
        product = resp.json()
        name = product["name"]
        price = product["price"]
    except (ValueError, KeyError, TypeError):
        return jsonify({"error": "Catalog service returned invalid data."}), 503

    with lock:
        order = {
            "id": next_id,
            "product_id": product_id,
            "product_name": name,
            "product_price": price,
            "quantity": quantity,
            "total": round(price * quantity, 2),
        }
        orders[next_id] = order
        next_id += 1

    return jsonify(order), 201


@app.route("/orders/<int:order_id>", methods=["GET"])
def get_order(order_id):
    order = orders.get(order_id)
    if order is None:
        return jsonify({"error": f"Order with id {order_id} not found"}), 404
    return jsonify(order), 200


@app.errorhandler(404)
def handle_404(e):
    return jsonify({"error": "Resource not found"}), 404


@app.errorhandler(405)
def handle_405(e):
    return jsonify({"error": "Method not allowed for this endpoint"}), 405


@app.errorhandler(500)
def handle_500(e):
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orders service (Service B)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5002)
    parser.add_argument("--catalog-url", default=CATALOG_URL)
    args = parser.parse_args()
    CATALOG_URL = args.catalog_url.rstrip("/")
    app.run(host=args.host, port=args.port, debug=False)