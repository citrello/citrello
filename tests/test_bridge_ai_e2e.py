"""Claude checks through the bridge (local wrangler dev), with a pretend Claude API (tests/anthropic_mock.py).

The bridge must run with ANTHROPIC_BASE_URL=http://127.0.0.1:8799, ANTHROPIC_API_KEY=test-key, AI_FREE_MONTHLY=3,
DEV_RETURN_CODES=1 and ADMIN_TOKEN=testadmin (see .github/workflows/bridge.yml), then:
  CITRELLO_BRIDGE_E2E=1 CITRELLO_BRIDGE=http://localhost:8787 pytest tests/test_bridge_ai_e2e.py
"""
import json
import os
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("CITRELLO_BRIDGE_E2E") != "1", reason="needs a running bridge")

import anthropic_mock  # noqa: E402
from test_bridge_e2e import call  # noqa: E402


@pytest.fixture(scope="module")
def claude():
    srv = anthropic_mock.start()
    yield anthropic_mock.SEEN
    srv.stop()


def sign_in(email):
    st, _, raw = call("POST", "/api/auth/start", {"email": email})
    code = json.loads(raw)["dev_code"]
    st, _, raw = call("POST", "/api/auth/verify", {"email": email, "code": code, "device_name": "test"})
    assert st == 200
    return {"Authorization": "Bearer " + json.loads(raw)["token"]}


ITEM = {"id": "0", "claim": "Leaving the cables buried doubled the cost.",
        "papers": [{"ref": "0", "title": "Sustainable decommissioning", "passages": [
            "Removing the subsea export cables accounted for 42% of the total decommissioning cost. Leaving the cables buried in place halved that cost."]}]}


def test_claude_checks(claude):
    assert call("POST", "/api/ai/judge", {"items": [ITEM]})[0] == 401  # signed-in accounts only
    me = sign_in(f"ai{int(time.time())}@example.com")
    st, _, raw = call("GET", "/api/ai/quota", headers=me)
    q = json.loads(raw)
    assert st == 200 and q == dict(q, pro=False, used=0, limit=3, web_used=0, web_limit=0, ready=True)

    # a verdict with the exact words from the paper
    st, _, raw = call("POST", "/api/ai/judge", {"items": [ITEM]}, headers=me)
    assert st == 200, raw
    r = json.loads(raw)
    assert r["results"] == [dict(r["results"][0], id="0", paper="0", verdict="contradicts", passage=1)]
    assert "halved that cost" in r["results"][0]["quote"] and r["quota"]["used"] == 1
    sent = claude[-1]
    assert sent["key"] == "test-key" and sent["body"]["model"] == "claude-sonnet-5-5"
    assert sent["body"]["output_config"]["format"]["type"] == "json_schema"  # a structured answer
    assert sent["body"]["fallbacks"] == "default"

    # finding supporting papers (library or web) is for Pro: refused plainly, nothing sent to Claude
    n = len(claude)
    st, _, raw = call("POST", "/api/ai/find-web", {"claim": "Cable recovery is the costliest step."}, headers=me)
    assert st == 402 and json.loads(raw)["error"] == "pro_only"
    st, _, raw = call("POST", "/api/ai/judge", {"kind": "library", "items": [ITEM]}, headers=me)
    assert st == 402 and json.loads(raw)["error"] == "pro_only" and len(claude) == n

    # the free verdicts: 3 for this test's free accounts (10 by default), then a plain message
    assert call("POST", "/api/ai/judge", {"items": [ITEM]}, headers=me)[0] == 200
    assert call("POST", "/api/ai/judge", {"items": [ITEM]}, headers=me)[0] == 200
    st, _, raw = call("POST", "/api/ai/judge", {"items": [ITEM]}, headers=me)
    assert st == 402 and "3 Claude checks a month" in json.loads(raw)["error_description"] and len(claude) == n + 2

    # Pro (switched on by the admin until payments exist): a bigger allowance
    email = json.loads(call("GET", "/api/me", headers=me)[2])["user"]["email"]
    assert call("POST", "/admin/pro", {"email": email, "pro": True})[0] == 401
    assert call("POST", "/admin/pro", {"email": email, "pro": True}, headers={"Authorization": "Bearer testadmin"})[0] == 200
    q = json.loads(call("GET", "/api/ai/quota", headers=me)[2])
    assert q["pro"] and q["limit"] > 3 and q["web_limit"] > 0
    assert call("POST", "/api/ai/judge", {"items": [ITEM]}, headers=me)[0] == 200
    # with Pro: supporting papers on the web (Claude searches) and in the library (counts as one check)
    st, _, raw = call("POST", "/api/ai/find-web", {"claim": "Cable recovery is the costliest step."}, headers=me)
    assert st == 200, raw
    r = json.loads(raw)
    assert r["papers"][0]["doi"] == "10.1016/j.example.2021.0042" and r["quota"]["web_used"] == 1
    assert claude[-1]["body"]["tools"][0]["type"] == "web_search_20260209"
    used = r["quota"]["used"]
    two = [dict(ITEM["papers"][0], ref=str(i)) for i in range(2)]
    st, _, raw = call("POST", "/api/ai/judge", {"kind": "library", "items": [dict(ITEM, papers=two)]}, headers=me)
    assert st == 200 and json.loads(raw)["quota"]["used"] == used + 1
    assert call("POST", "/api/ai/judge", {"items": []}, headers=me)[0] == 400


def test_cloud_copy_endpoints():
    """The cloud copy of a web-app library: signed-in accounts, gzip files only, one per account, deleted with it."""
    import gzip
    assert call("GET", "/api/cloud")[0] == 401
    me = sign_in(f"cloud{int(time.time())}@example.com")
    st, _, raw = call("GET", "/api/cloud", headers=me)
    assert st == 200 and json.loads(raw) == dict(json.loads(raw), ready=True, exists=False)
    import http.client
    from test_bridge_e2e import U

    def put(body, extra=None):
        c = http.client.HTTPConnection(U.hostname, U.port or 80, timeout=30)
        c.request("PUT", "/api/cloud", body, {**me, "X-Items": "2", "X-Texts": "1", "X-Device": "browser-test", **(extra or {})})
        r = c.getresponse()
        return r.status, json.loads(r.read())
    assert put(b"not gzip")[0] == 400
    data = gzip.compress(json.dumps({"citrello_cloud": 1, "library": {"items": []}, "fulltext": {}}).encode())
    st, info = put(data)
    assert st == 200 and info["exists"] and info["items"] == 2 and info["texts"] == 1 and info["device"] == "browser-test"
    c = http.client.HTTPConnection(U.hostname, U.port or 80, timeout=30)
    c.request("GET", "/api/cloud/data", headers=me)
    r = c.getresponse()
    assert r.status == 200 and r.read() == data
    assert call("DELETE", "/api/cloud", headers=me)[0] == 200
    assert json.loads(call("GET", "/api/cloud", headers=me)[2])["exists"] is False
