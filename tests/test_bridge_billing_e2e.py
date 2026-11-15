"""Plans and payments through the bridge (local wrangler dev) with a pretend Stripe (tests/stripe_mock.py).

The bridge must run with STRIPE_SECRET_KEY=rk_test_mock, STRIPE_API_BASE=http://127.0.0.1:8798, DEV_RETURN_CODES=1
and ADMIN_TOKEN=testadmin (see .github/workflows/bridge.yml), then:
  CITRELLO_BRIDGE_E2E=1 CITRELLO_BRIDGE=http://localhost:8787 pytest tests/test_bridge_billing_e2e.py
"""
import json
import os
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("CITRELLO_BRIDGE_E2E") != "1", reason="needs a running bridge")

import anthropic_mock  # noqa: E402
import stripe_mock  # noqa: E402
from test_bridge_ai_e2e import ITEM, sign_in  # noqa: E402
from test_bridge_e2e import call  # noqa: E402


@pytest.fixture(scope="module")
def mocks():
    s, c = stripe_mock.start(), anthropic_mock.start()
    yield stripe_mock.STATE
    s.stop()
    c.stop()


def webhook(kind, obj_id):
    return call("POST", "/api/stripe/webhook", {"type": kind, "data": {"object": {"id": obj_id}}})


def test_plans_and_credits(mocks):
    assert call("GET", "/api/billing")[0] == 401
    me = sign_in(f"bill{int(time.time())}@example.com")
    q = lambda: json.loads(call("GET", "/api/ai/quota", headers=me)[2])  # noqa: E731

    # the plans, with Stripe's prices
    st, _, raw = call("GET", "/api/billing", headers=me)
    b = json.loads(raw)
    assert st == 200 and b["credits"] == 100 and b["plan"] is None and b["billing"] is True
    plans = {p["id"]: p for p in b["plans"]}
    assert plans["student"]["credits_per_month"] == 1000 and plans["researcher"]["credits_per_month"] == 4000
    assert plans["student"]["prices"]["month"] == {"id": "price_sm", "amount": 600, "currency": "gbp"}
    assert plans["researcher"]["prices"]["year"]["amount"] == 15000
    assert mocks["seen"][-1][3] == "Bearer rk_test_mock" and mocks["seen"][-1][4] == "2025-03-31.basil"

    # Stripe's payment page (Managed Payments: Stripe is the seller of record)
    st, _, raw = call("POST", "/api/billing/checkout", {"plan": "student", "interval": "month"}, headers=me)
    assert st == 200, raw
    url = json.loads(raw)["url"]
    sid = url.rsplit("/", 1)[1]
    p = mocks["sessions"][sid]["params"]
    assert p["mode"] == "subscription" and p["managed_payments[enabled]"] == "true" and p["line_items[0][price]"] == "price_sm"
    assert p["customer_email"].startswith("bill") and p["client_reference_id"] and p["success_url"].endswith("/thanks?plan=student")
    assert call("POST", "/api/billing/checkout", {"plan": "gold"}, headers=me)[0] == 400

    # nothing changes until Stripe says the payment went through; a made-up session changes nothing
    assert q()["plan"] is None
    assert webhook("checkout.session.completed", "cs_made_up")[0] == 502
    assert q()["plan"] is None
    st, _, raw = webhook("checkout.session.completed", sid)
    assert st == 200 and json.loads(raw)["plan"] == "student", raw
    b = q()
    assert b["plan"]["id"] == "student" and b["plan"]["left"] == 1000 and b["credits"] == 1100  # the month's + the free 100

    # the plan's credits are spent first
    two = [dict(ITEM["papers"][0], ref=str(i)) for i in range(2)]
    assert call("POST", "/api/ai/judge", {"items": [dict(ITEM, papers=two)]}, headers=me)[0] == 200
    b = q()
    assert b["plan"]["left"] == 994 and b["bonus"] == 100 and b["credits"] == 1094

    # one plan at a time: switching and cancelling happen on Stripe's page
    assert call("POST", "/api/billing/checkout", {"plan": "researcher"}, headers=me)[0] == 409
    st, _, raw = call("POST", "/api/billing/portal", headers=me)
    assert st == 200 and json.loads(raw)["url"].startswith("https://billing.stripe.test/portal?c=cus_")

    # switched to Researcher: Stripe says so, the bridge reads it back
    sub = mocks["sessions"][sid]["subscription"]
    stripe_mock.set_plan(sub, key="citrello_researcher_monthly")
    assert webhook("customer.subscription.updated", sub)[0] == 200
    b = q()
    assert b["plan"]["id"] == "researcher" and b["plan"]["left"] == 3994

    # cancelled and ended: back to the free credits that are left
    stripe_mock.set_plan(sub, status="canceled")
    assert webhook("customer.subscription.deleted", sub)[0] == 200
    b = q()
    assert b["plan"] is None and b["credits"] == 100

    # events Citrello doesn't use are acknowledged and ignored
    st, _, raw = call("POST", "/api/stripe/webhook", {"type": "charge.refunded", "data": {"object": {"id": "ch_1"}}})
    assert st == 200 and json.loads(raw)["ignored"] == "charge.refunded"
