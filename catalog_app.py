import argparse
import threading

from flask import Flask, jsonify, request

app = Flask(__name__)

products = {} 
next_id = 1
lock = threading.Lock()

@app.route("/products", methods=["POST"])
def create_product():
    global next_id

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    name = data.get("name")
    price = data.get("price")

    with lock:
        product = {"id": next_id, "name": name.strip(), "price": price}
        products[next_id] = product
        next_id += 1

    return jsonify(product), 201


@app.route("/products/<int:product_id>", methods=["GET"])
def get_product(product_id):
    product = products.get(product_id)
    if product is None:
        return jsonify({"error": f"Product with id {product_id} not found"}), 404
    return jsonify(product), 200


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
    parser = argparse.ArgumentParser(description="Catalog service (Service A)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=False)