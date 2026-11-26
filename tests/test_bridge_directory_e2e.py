"""The connector directories' needs, against a local bridge (wrangler dev): the reviewer account's fixed code,
the tool list for an account whose app never connected, and OpenAI's domain check.

The bridge must run as for test_bridge_e2e.py, plus --var REVIEWER_CODE:24681357 --var OPENAI_APPS_CHALLENGE:tok123.
"""
import re
import urllib.parse

import pytest

from test_bridge_e2e import call, rpc, pytestmark  # noqa: F401  (same skip rule)
import test_bridge_e2e as e2e

REVIEWER = "reviewer@citrello.app"


def test_openai_domain_check():
    st, h, body = call("GET", "/.well-known/openai-apps-challenge")
    assert st == 200 and body == "tok123" and h["Content-Type"].startswith("text/plain")


def test_reviewer_signs_in_with_the_fixed_code_and_no_email_is_sent():
    st, _, raw = call("POST", "/api/auth/start", {"email": REVIEWER})
    assert st == 200 and "dev_code" not in raw  # nothing generated or sent
    assert call("POST", "/api/auth/verify", {"email": REVIEWER, "code": "11111111"})[0] == 400
    st, _, raw = call("POST", "/api/auth/verify", {"email": REVIEWER, "code": "24681357"})
    assert st == 200
    import json
    token = json.loads(raw)["token"]
    st, _, raw = call("GET", "/api/cloud", headers={"Authorization": "Bearer " + token})
    assert st == 200 and json.loads(raw).get("reviewer") is True
    # an ordinary account isn't flagged, and can't use the fixed code
    assert call("POST", "/api/auth/verify", {"email": "someone@example.com", "code": "24681357"})[0] == 400


def test_wrong_reviewer_codes_are_limited():
    call("POST", "/api/auth/start", {"email": REVIEWER})
    for _ in range(5):
        assert call("POST", "/api/auth/verify", {"email": REVIEWER, "code": "99999999"})[0] == 400
    st, _, raw = call("POST", "/api/auth/verify", {"email": REVIEWER, "code": "24681357"})
    assert st == 400 and "too_many" in raw  # locked until a new start
    call("POST", "/api/auth/start", {"email": REVIEWER})


def test_a_new_account_sees_every_tool(monkeypatch):
    # a ChatGPT/Claude sign-in for an account whose Citrello app has never connected
    monkeypatch.setattr(e2e, "EMAIL", f"fresh{__import__('time').time_ns()}@example.com")
    client, p, verifier = e2e.oauth_sign_in()
    _, _, html = call("POST", "/authorize", {**p, "step": "send", "email": e2e.EMAIL}, form=True)
    code = re.search(r"your code is (\d{6})", html).group(1)
    st, h, _ = call("POST", "/authorize", {**p, "step": "verify", "email": e2e.EMAIL, "code": code}, form=True)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(h["Location"]).query))
    import json
    _, _, raw = call("POST", "/token", {"grant_type": "authorization_code", "code": q["code"], "redirect_uri": p["redirect_uri"],
                                        "client_id": client["client_id"], "code_verifier": verifier}, form=True)
    access = json.loads(raw)["access_token"]
    _, _, raw = rpc(access, "tools/list")
    tools = json.loads(raw)["result"]["tools"]
    names = {t["name"] for t in tools}
    assert len(tools) >= 19 and {"search_references", "add_reference", "format_references"} <= names
    assert all("readOnlyHint" in t["annotations"] for t in tools)
    _, _, raw = rpc(access, "tools/call", {"name": "search_references", "arguments": {"query": "x"}})
    res = json.loads(raw)["result"]
    assert res["isError"] and "open Citrello" in res["content"][0]["text"]  # a clear message, not a bare error


def test_reviewer_never_runs_out_of_credits():
    """Microsoft's testers must be able to try what the paid plans give (credits for Claude's checks) without paying."""
    import json
    call("POST", "/api/auth/start", {"email": REVIEWER})
    token = json.loads(call("POST", "/api/auth/verify", {"email": REVIEWER, "code": "24681357"})[2])["token"]
    st, _, raw = call("GET", "/api/ai/quota", headers={"Authorization": "Bearer " + token})
    assert st == 200 and json.loads(raw)["credits"] >= 1000
    # an ordinary account keeps the usual 100 free credits
    email = f"normal{__import__('time').time_ns()}@example.com"
    code = json.loads(call("POST", "/api/auth/start", {"email": email})[2])["dev_code"]
    t2 = json.loads(call("POST", "/api/auth/verify", {"email": email, "code": code})[2])["token"]
    assert json.loads(call("GET", "/api/ai/quota", headers={"Authorization": "Bearer " + t2})[2])["credits"] == 100
