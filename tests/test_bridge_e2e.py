"""End to end: the app signs in to a local bridge (wrangler dev), and an OAuth client uses the library through it.

Run the bridge first (see bridge/README.md), then:
  CITRELLO_BRIDGE_E2E=1 CITRELLO_BRIDGE=http://localhost:8787 pytest tests/test_bridge_e2e.py
The bridge must run with DEV_RETURN_CODES=1, PUBLIC_URL set to its own address, and ADMIN_TOKEN=testadmin.
"""
import base64
import hashlib
import http.client
import json
import os
import re
import time
import urllib.parse

import pytest

import mcp_server

pytestmark = pytest.mark.skipif(os.environ.get("CITRELLO_BRIDGE_E2E") != "1", reason="needs a running bridge")

import bridge_client as bc  # noqa: E402
import library  # noqa: E402

U = urllib.parse.urlsplit(bc.BRIDGE)
EMAIL = f"e2e{int(time.time())}@example.com"


def call(method, path, body=None, headers=None, form=False):
    c = http.client.HTTPConnection(U.hostname, U.port or 80, timeout=130)
    h = dict(headers or {})
    data = None
    if body is not None:
        data = urllib.parse.urlencode(body) if form else json.dumps(body)
        h["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    c.request(method, path, data, h)
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read().decode()


def rpc(token, method, params=None, id=1):
    msg = {"jsonrpc": "2.0", "method": method, **({"id": id} if id is not None else {}), **({"params": params} if params else {})}
    return call("POST", "/mcp", msg, {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"})


def oauth_sign_in(redirect="https://chatgpt.com/connector_platform_oauth_redirect"):
    st, _, raw = call("POST", "/register", {"client_name": "Test <client>", "redirect_uris": [redirect],
                                            "token_endpoint_auth_method": "none"})
    assert st == 201
    client = json.loads(raw)
    verifier = base64.urlsafe_b64encode(os.urandom(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    p = {"response_type": "code", "client_id": client["client_id"], "redirect_uri": redirect, "state": "s1",
         "code_challenge": challenge, "code_challenge_method": "S256", "scope": "citrello"}
    st, _, html = call("GET", "/authorize?" + urllib.parse.urlencode(p))
    assert st == 200 and "Test &lt;client&gt;" in html  # client names are escaped
    _, _, html = call("POST", "/authorize", {**p, "step": "send", "email": EMAIL}, form=True)
    code = re.search(r"your code is (\d{6})", html).group(1)
    _, _, html = call("POST", "/authorize", {**p, "step": "verify", "email": EMAIL, "code": "000000" if code != "000000" else "111111"}, form=True)
    assert "isn" in html  # wrong code: shown again with an error
    st, h, _ = call("POST", "/authorize", {**p, "step": "verify", "email": EMAIL, "code": code}, form=True)
    assert st == 302
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(h["Location"]).query))
    assert q["state"] == "s1"
    tok = {"grant_type": "authorization_code", "code": q["code"], "redirect_uri": redirect, "client_id": client["client_id"]}
    assert call("POST", "/token", {**tok, "code_verifier": "x" * 43}, form=True)[0] == 400   # PKCE checked
    assert call("POST", "/token", {**tok, "code_verifier": verifier}, form=True)[0] == 400   # and the code is single use
    return client, p, verifier


def test_end_to_end():
    with library.editing() as data:
        library.add_csl(data, {"type": "article-journal", "title": "Sustainable decommissioning of an offshore wind farm",
                               "author": [{"family": "Topham", "given": "Eva"}], "issued": {"date-parts": [[2017]]},
                               "container-title": "Renewable Energy"}, source="test")
    r = bc.start_sign_in(EMAIL)
    bc.finish_sign_in(EMAIL, r["dev_code"], newsletter=True)
    for _ in range(80):
        if bc.CONNECTION.state == "online":
            break
        time.sleep(0.25)
    assert bc.CONNECTION.state == "online", bc.CONNECTION.status()
    me = bc.account_info()
    assert me["user"]["email"] == EMAIL and me["user"]["newsletter"] and me["devices"][0]["online"]

    client, p, verifier = oauth_sign_in()
    # a fresh sign-in, then the token exchange and a refresh
    _, _, html = call("POST", "/authorize", {**p, "step": "send", "email": EMAIL}, form=True)
    code = re.search(r"your code is (\d{6})", html).group(1)
    _, h, _ = call("POST", "/authorize", {**p, "step": "verify", "email": EMAIL, "code": code}, form=True)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(h["Location"]).query))
    st, _, raw = call("POST", "/token", {"grant_type": "authorization_code", "code": q["code"], "redirect_uri": p["redirect_uri"],
                                         "client_id": client["client_id"], "code_verifier": verifier}, form=True)
    assert st == 200
    tok = json.loads(raw)
    st, _, raw = call("POST", "/token", {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                         "client_id": client["client_id"]}, form=True)
    assert st == 200
    old_refresh, tok = tok["refresh_token"], json.loads(raw)
    assert call("POST", "/token", {"grant_type": "refresh_token", "refresh_token": old_refresh,
                                   "client_id": client["client_id"]}, form=True)[0] == 400  # refresh tokens rotate
    access = tok["access_token"]

    st, h, _ = call("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert st == 401 and "resource_metadata" in h["WWW-Authenticate"]
    st, _, raw = rpc(access, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "t", "version": "1"}})
    assert st == 200 and json.loads(raw)["result"]["serverInfo"]["name"] == "citrello"
    assert rpc(access, "notifications/initialized", id=None)[0] == 202
    tools = json.loads(rpc(access, "tools/list")[2])["result"]["tools"]
    assert len(tools) == len(mcp_server.TOOLS) and "prepare_arxiv" in {t["name"] for t in tools}
    res = json.loads(rpc(access, "tools/call", {"name": "search_references", "arguments": {"query": "offshore"}})[2])["result"]
    assert not res["isError"] and "Topham2017" in res["content"][0]["text"]
    res = json.loads(rpc(access, "tools/call", {"name": "create_word_document",
                                                "arguments": {"text": "x", "output_path": "/tmp/x.docx"}})[2])["result"]
    assert res["isError"]  # web requests can't write outside Citrello's Documents folder

    iid = os.urandom(16).hex()
    for _ in range(2):
        assert call("POST", "/api/ping", {"install_id": iid, "version": "1.0.0", "os": "mac", "os_version": "15",
                                          "store": False, "signed_in": True})[0] == 200
    assert call("POST", "/api/ping", {"install_id": "../../etc"})[0] == 400
    st, _, raw = call("GET", "/admin/stats", headers={"Authorization": "Bearer testadmin"})
    stats = json.loads(raw)
    assert st == 200 and stats["users"] >= 1
    assert stats["installs"]["active_1d"] >= 1 and any(r["os"] == "mac" for r in stats["installs"]["by_os_30d"])
    assert call("GET", "/admin/stats", headers={"Authorization": "Bearer nope"})[0] == 401
    assert call("POST", "/admin/broadcast", {"subject": "s", "text": "t"}, {"Authorization": "Bearer testadmin"})[0] == 400

    bc.sign_out()
    time.sleep(1)
    assert len(json.loads(rpc(access, "tools/list")[2])["result"]["tools"]) == len(mcp_server.TOOLS)  # remembered while offline
    res = json.loads(rpc(access, "tools/call", {"name": "search_references", "arguments": {}})[2])["result"]
    assert res["isError"] and "isn't running" in res["content"][0]["text"]
