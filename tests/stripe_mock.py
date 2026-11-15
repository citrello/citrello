"""A pretend Stripe API for tests (the bridge's STRIPE_API_BASE points here): prices by lookup key, Checkout Sessions
that create a subscription, subscriptions, the customer portal. STATE lets a test change a subscription (switch plan,
cancel) before sending the bridge the webhook Stripe would send."""
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PRICES = {"citrello_student_monthly": ("price_sm", 600, "month"), "citrello_student_yearly": ("price_sy", 5000, "year"),
          "citrello_researcher_monthly": ("price_rm", 1800, "month"), "citrello_researcher_yearly": ("price_ry", 15000, "year")}
BY_ID = {v[0]: k for k, v in PRICES.items()}
STATE = {"sessions": {}, "subs": {}, "seen": []}


def price_obj(key):
    pid, amount, interval = PRICES[key]
    return {"id": pid, "object": "price", "lookup_key": key, "unit_amount": amount, "currency": "gbp", "recurring": {"interval": interval}}


def set_plan(sub_id, key=None, status=None):
    sub = STATE["subs"][sub_id]
    if key:
        sub["items"]["data"][0]["price"] = price_obj(key)
    if status:
        sub["status"] = status


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        STATE["seen"].append(("GET", u.path, q, self.headers.get("Authorization"), self.headers.get("Stripe-Version")))
        if u.path == "/v1/prices":
            keys = [v[0] for k, v in q.items() if k.startswith("lookup_keys")]
            return self.reply(200, {"object": "list", "data": [price_obj(k) for k in keys if k in PRICES]})
        if u.path.startswith("/v1/checkout/sessions/"):
            s = STATE["sessions"].get(u.path.rsplit("/", 1)[1])
            return self.reply(200, s) if s else self.reply(404, {"error": {"message": "No such session"}})
        if u.path.startswith("/v1/subscriptions/"):
            s = STATE["subs"].get(u.path.rsplit("/", 1)[1])
            return self.reply(200, s) if s else self.reply(404, {"error": {"message": "No such subscription"}})
        self.reply(404, {"error": {"message": "unknown"}})

    def do_POST(self):
        body = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
        f = {k: v[0] for k, v in body.items()}
        STATE["seen"].append(("POST", self.path, f, self.headers.get("Authorization"), self.headers.get("Stripe-Version")))
        if self.path == "/v1/checkout/sessions":
            n = len(STATE["sessions"]) + 1
            sid, sub_id, t = f"cs_test_{n}", f"sub_{n}", int(time.time())
            STATE["subs"][sub_id] = {"id": sub_id, "object": "subscription", "status": "active", "customer": f"cus_{n}",
                                     "start_date": t, "metadata": {"user_id": f.get("subscription_data[metadata][user_id]")},
                                     "items": {"data": [{"price": price_obj(BY_ID[f["line_items[0][price]"]]),
                                                         "current_period_start": t, "current_period_end": t + 30 * 86400}]}}
            STATE["sessions"][sid] = {"id": sid, "object": "checkout.session", "subscription": sub_id,
                                      "client_reference_id": f.get("client_reference_id"), "params": f}
            return self.reply(200, {"id": sid, "url": f"https://checkout.stripe.test/{sid}"})
        if self.path == "/v1/billing_portal/sessions":
            return self.reply(200, {"id": "bps_1", "url": "https://billing.stripe.test/portal?c=" + f.get("customer", "")})
        self.reply(404, {"error": {"message": "unknown"}})


class Server(ThreadingHTTPServer):
    allow_reuse_address = True

    def stop(self):
        self.shutdown()
        self.server_close()


def start(port=8798):
    srv = Server(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
