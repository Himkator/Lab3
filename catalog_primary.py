import argparse
import logging
import threading
import time

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)
log = logging.getLogger("PRIMARY")

INITIAL_PRODUCT = {"id": 1, "name": "Laptop", "price": 450000, "stock": 10, "version": 2}

products = {1: dict(INITIAL_PRODUCT)}
lock = threading.Lock()

REPLICA_URL = "http://127.0.0.1:5002" 
ASYNC_DELAY = 5.0  
REPLICA_TIMEOUT = 5.0
RESPONSE_DELAY = 0.0 


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@app.before_request
def simulate_slow_primary():
    if request.method == "GET" and RESPONSE_DELAY > 0:
        log.info("slow mode: holding GET %s for %ss before answering", request.path, RESPONSE_DELAY)
        time.sleep(RESPONSE_DELAY)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "role": "primary"}), 200


@app.route("/products/<int:product_id>", methods=["GET"])
def get_product(product_id):
    product = products.get(product_id)
    if product is None:
        return jsonify({"error": f"Product with id {product_id} not found"}), 404
    log.info("READ product %s -> price=%s stock=%s version=%s",
             product_id, product["price"], product["stock"], product["version"])
    return jsonify(product), 200


def send_to_replica(snapshot):
    resp = requests.post(f"{REPLICA_URL}/replicate", json=snapshot, timeout=REPLICA_TIMEOUT)
    body = resp.json()
    if resp.status_code != 200 or body.get("status") != "ACK":
        raise ValueError(f"replica answered {resp.status_code}: {body}")
    return body


def deliver_later(snapshot):
    time.sleep(ASYNC_DELAY)
    try:
        send_to_replica(snapshot)
        log.info("async delivery done: version %s reached the replica (ACK received)", snapshot["version"])
    except (requests.RequestException, ValueError) as e:
        log.error("async delivery of version %s FAILED: %s", snapshot["version"], e)


@app.route("/products/<int:product_id>", methods=["PUT"])
def update_product(product_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    mode = data.get("mode", "async")
    if mode not in ("async", "sync"):
        return jsonify({"error": "Field 'mode' must be 'async' or 'sync'"}), 400

    changes = {}
    for field in ("price", "stock"):
        if field in data:
            value = data[field]
            if not is_number(value) or value < 0:
                return jsonify({"error": f"Field '{field}' must be a number >= 0"}), 400
            changes[field] = value
    if not changes:
        return jsonify({"error": "Provide at least one of: price, stock"}), 400

    with lock:
        product = products.get(product_id)
        if product is None:
            return jsonify({"error": f"Product with id {product_id} not found"}), 404
        product.update(changes)
        product["version"] += 1
        snapshot = dict(product)

    log.info("UPDATE (%s) stored on primary: %s -> version %s", mode, changes, snapshot["version"])

    if mode == "async":
        threading.Thread(target=deliver_later, args=(snapshot,), daemon=True).start()
        log.info("async: returning SUCCESS now; delivery of version %s to replica scheduled in %ss",
                 snapshot["version"], ASYNC_DELAY)
        return jsonify({
            "status": "SUCCESS", "mode": "async", "product": snapshot,
            "replication": f"version {snapshot['version']} will reach the replica in {ASYNC_DELAY}s",
        }), 200

    log.info("sync: sending version %s to replica and waiting for ACK ...", snapshot["version"])
    try:
        send_to_replica(snapshot)
    except (requests.RequestException, ValueError) as e:
        log.error("sync: no ACK from replica (%s). Returning FAILED.", e)
        return jsonify({"status": "FAILED", "mode": "sync", "product": snapshot,
                        "error": "Replica did not acknowledge the update"}), 503
    log.info("sync: ACK received for version %s -> returning SUCCESS", snapshot["version"])
    return jsonify({"status": "SUCCESS", "mode": "sync", "product": snapshot,
                    "replication": f"version {snapshot['version']} acknowledged by the replica"}), 200


@app.route("/admin/delay", methods=["POST"])
def set_delay():
    global RESPONSE_DELAY
    data = request.get_json(silent=True)
    seconds = data.get("seconds") if isinstance(data, dict) else None
    if not is_number(seconds) or seconds < 0:
        return jsonify({"error": "Body must be {\"seconds\": <number >= 0>}"}), 400
    RESPONSE_DELAY = float(seconds)
    log.info("response delay set to %ss", RESPONSE_DELAY)
    return jsonify({"response_delay_seconds": RESPONSE_DELAY}), 200


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
    parser = argparse.ArgumentParser(description="Catalog Primary")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--replica-url", default=REPLICA_URL)
    parser.add_argument("--async-delay", type=float, default=ASYNC_DELAY,
                        help="seconds before an async update reaches the replica (default 5)")
    parser.add_argument("--response-delay", type=float, default=0.0,
                        help="artificial delay for GET requests in seconds (default 0)")
    args = parser.parse_args()

    REPLICA_URL = args.replica_url.rstrip("/")
    ASYNC_DELAY = args.async_delay
    RESPONSE_DELAY = args.response_delay

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s.%(msecs)03d [%(name)s] %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    log.info("Catalog Primary on port %s (replica %s, async delay %ss, response delay %ss)",
             args.port, REPLICA_URL, ASYNC_DELAY, RESPONSE_DELAY)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)