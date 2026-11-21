import argparse
import logging
import threading

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)
log = logging.getLogger("ORDER")

orders = {}
next_id = 1
lock = threading.Lock()

PRIMARY_URL = "http://127.0.0.1:5001"
REPLICA_URL = "http://127.0.0.1:5002"
PRIMARY_TIMEOUT = 1.0 
REPLICA_TIMEOUT = 2.0 


def is_positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def read_from_primary(product_id):
    try:
        resp = requests.get(f"{PRIMARY_URL}/products/{product_id}", timeout=PRIMARY_TIMEOUT)
    except requests.exceptions.Timeout:
        return None, (f"timeout after {PRIMARY_TIMEOUT}s: no answer in time "
                      f"(the primary may be stopped, overloaded or just slow)")
    except requests.exceptions.ConnectionError:
        return None, "connection refused: nothing is listening on the primary port"
    except requests.exceptions.RequestException as e:
        return None, f"request error: {e.__class__.__name__}"
    if resp.status_code >= 500:
        return None, f"primary returned HTTP {resp.status_code}"
    return resp, None


@app.route("/orders", methods=["POST"])
def create_order():
    global next_id

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400
    product_id = data.get("product_id")
    quantity = data.get("quantity", 1)
    if not is_positive_int(product_id):
        return jsonify({"error": "Field 'product_id' is required and must be a positive integer"}), 400
    if not is_positive_int(quantity):
        return jsonify({"error": "Field 'quantity' must be a positive integer"}), 400

    source = "primary"
    resp, primary_error = read_from_primary(product_id)

    if resp is None:
        log.warning("PRIMARY FAILED (%s) -> retrying the read from the REPLICA", primary_error)
        source = "replica"
        try:
            resp = requests.get(f"{REPLICA_URL}/products/{product_id}", timeout=REPLICA_TIMEOUT)
        except requests.exceptions.RequestException:
            log.error("REPLICA FAILED too: no catalogue available")
            return jsonify({"error": "Both catalogue services are unavailable. Please try again later.",
                            "primary_error": primary_error}), 503

    if resp.status_code == 404:
        return jsonify({"error": f"Product with id {product_id} not found in catalog {source}"}), 404
    if resp.status_code != 200:
        return jsonify({"error": f"Catalog {source} returned an unexpected error."}), 503

    try:
        product = resp.json()
        name, price = product["name"], product["price"]
        stock, version = product["stock"], product["version"]
    except (ValueError, KeyError, TypeError):
        return jsonify({"error": f"Catalog {source} returned invalid data."}), 503

    with lock:
        order = {
            "order_id": next_id,
            "product_id": product_id,
            "product_name": name,
            "product_price": price,
            "product_stock": stock,
            "quantity": quantity,
            "total": price * quantity,
            "catalog_source": source,
            "catalog_version": version,
            "primary_error": primary_error, 
        }
        orders[next_id] = order
        next_id += 1

    log.info("ORDER #%s created from %s: price=%s version=%s total=%s",
             order["order_id"], source.upper(), price, version, order["total"])
    return jsonify(order), 201


@app.route("/orders/<int:order_id>", methods=["GET"])
def get_order(order_id):
    order = orders.get(order_id)
    if order is None:
        return jsonify({"error": f"Order with id {order_id} not found"}), 404
    return jsonify(order), 200


@app.route("/catalog-status", methods=["GET"])
def catalog_status():
    """Probe both catalogues' /health. A failed probe means 'suspected down', not proof."""
    result = {}
    for name, url, timeout in (("primary", PRIMARY_URL, PRIMARY_TIMEOUT),
                               ("replica", REPLICA_URL, REPLICA_TIMEOUT)):
        try:
            r = requests.get(f"{url}/health", timeout=timeout)
            result[name] = {"status": "ok" if r.status_code == 200 else "unhealthy",
                            "http_status": r.status_code}
        except requests.exceptions.Timeout:
            result[name] = {"status": "suspected_down", "detail": f"no answer within {timeout}s"}
        except requests.exceptions.RequestException:
            result[name] = {"status": "suspected_down", "detail": "connection refused"}
    return jsonify(result), 200


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
    parser = argparse.ArgumentParser(description="Order Service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5003)
    parser.add_argument("--primary-url", default=PRIMARY_URL)
    parser.add_argument("--replica-url", default=REPLICA_URL)
    parser.add_argument("--primary-timeout", type=float, default=PRIMARY_TIMEOUT,
                        help="seconds to wait for the primary before falling back (default 1)")
    args = parser.parse_args()
    PRIMARY_URL = args.primary_url.rstrip("/")
    REPLICA_URL = args.replica_url.rstrip("/")
    PRIMARY_TIMEOUT = args.primary_timeout

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s.%(msecs)03d [%(name)s] %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    log.info("Order Service on port %s (primary timeout %ss; fallback to replica %s)",
             args.port, PRIMARY_TIMEOUT, REPLICA_URL)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)