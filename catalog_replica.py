import argparse
import logging
import threading
import time

from flask import Flask, jsonify, request

app = Flask(__name__)
log = logging.getLogger("REPLICA")

INITIAL_PRODUCT = {"id": 1, "name": "Laptop", "price": 450000, "stock": 10, "version": 2}

products = {1: dict(INITIAL_PRODUCT)}
lock = threading.Lock()

ACK_DELAY = 0.0


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "role": "replica"}), 200


@app.route("/products/<int:product_id>", methods=["GET"])
def get_product(product_id):
    product = products.get(product_id)
    if product is None:
        return jsonify({"error": f"Product with id {product_id} not found"}), 404
    log.info("READ product %s -> price=%s stock=%s version=%s",
             product_id, product["price"], product["stock"], product["version"])
    return jsonify(product), 200


@app.route("/replicate", methods=["POST"])
def replicate():
    data = request.get_json(silent=True)
    required = ("id", "name", "price", "stock", "version")
    if not isinstance(data, dict) or any(k not in data for k in required):
        return jsonify({"status": "ERROR", "error": f"Body must contain: {', '.join(required)}"}), 400

    log.info("received update from primary: version %s (price=%s stock=%s)",
             data["version"], data["price"], data["stock"])
    with lock:
        current = products.get(data["id"])
        if current is None or data["version"] > current["version"]:
            products[data["id"]] = {k: data[k] for k in required}
            applied = True
        else:
            applied = False

    if applied:
        log.info("APPLIED version %s: price=%s stock=%s", data["version"], data["price"], data["stock"])
    else:
        log.info("IGNORED version %s (replica already has version %s or newer)",
                 data["version"], current["version"])

    time.sleep(ACK_DELAY)
    log.info("sending ACK for version %s", data["version"])
    return jsonify({"status": "ACK", "version": data["version"], "applied": applied}), 200


@app.route("/reset", methods=["POST"])
def reset():
    with lock:
        products[1] = dict(INITIAL_PRODUCT)
    log.info("RESET to the starting product (price 450000, version 2)")
    return jsonify(products[1]), 200


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
    parser = argparse.ArgumentParser(description="Catalog Replica")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5002)
    parser.add_argument("--ack-delay", type=float, default=ACK_DELAY,
                        help="simulated latency before sending the ACK, in seconds (default 0)")
    args = parser.parse_args()
    ACK_DELAY = args.ack_delay

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s.%(msecs)03d [%(name)s] %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    log.info("Catalog Replica on port %s (read-only fallback)", args.port)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)