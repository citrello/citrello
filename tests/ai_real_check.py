"""A real test of Claude checks (costs a few cents): a local copy of the bridge with the real ANTHROPIC_API_KEY,
real cases, and what Claude answered, how long it took and what it cost. Run by the "Real test of Claude checks"
workflow (manual), or locally with a bridge started with the key:
  CITRELLO_BRIDGE=http://localhost:8787 python tests/ai_real_check.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_bridge_e2e import call  # noqa: E402
from test_sources import PAPER  # noqa: E402

import fulltext  # noqa: E402

PRICE = {"claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0), "claude-haiku-5-5": (0.10, 0.50)}  # $ per million tokens
SEARCH = 0.01  # $ per web search


def cost(u):
    pin, pout = PRICE.get(u["model"].split("-2")[0] if u["model"] not in PRICE else u["model"], (2.0, 10.0))
    return u["input_tokens"] * pin / 1e6 + u["output_tokens"] * pout / 1e6 + u.get("web_searches", 0) * SEARCH


CLAIMS = [  # (sentence, what a careful reader would say)
    ("Removing the export cables was the largest share of the decommissioning cost.", "supports"),
    ("Taking the power lines out of the seabed was the costliest operation.", "supports"),
    ("Leaving the cables buried doubled the cost.", "contradicts"),
    ("Recovering the cables was cheaper than removing the turbines.", "contradicts"),
    ("Cable removal is a major part of decommissioning costs.", "supports"),
    ("Most turbine blades end up in landfill.", "not_found"),
]
LEE = ("Export cable recovery costs in North Sea decommissioning.\n\nWe surveyed 12 offshore wind farms. In every farm "
       "studied, recovering the export cables was the single most expensive decommissioning operation, ahead of turbine "
       "and foundation removal. Leaving cables buried in place cut decommissioning costs by about a third.")
BLADES = ("Wind turbine blade waste.\n\nAround 80% of decommissioned glass fibre rotor blades in Europe are currently "
          "landfilled, because recycling routes remain immature and costly.")


def main():
    st, _, raw = call("POST", "/api/auth/start", {"email": f"real{int(time.time())}@example.com"})
    code = json.loads(raw)["dev_code"]
    st, _, raw = call("POST", "/api/auth/verify", {"email": json.loads(raw)["email"], "code": code, "device_name": "real test"})
    me = {"Authorization": "Bearer " + json.loads(raw)["token"]}
    q = json.loads(call("GET", "/api/ai/quota", headers=me)[2])
    if not q["ready"]:
        print("ANTHROPIC_API_KEY isn't set: add it as a GitHub secret.")
        return 1
    email = json.loads(call("GET", "/api/me", headers=me)[2])["user"]["email"]
    call("POST", "/admin/pro", {"email": email, "pro": True}, headers={"Authorization": "Bearer testadmin"})
    total, right = 0.0, 0

    # 1. verdicts: six sentences against one paper, as the app sends them (the paper's best passages)
    items = [{"id": str(i), "claim": c, "papers": [{"ref": "0", "title": "Sustainable decommissioning of an offshore wind farm",
              "passages": [p["text"] for p in fulltext.passages(PAPER, c, n=6)] or [PAPER[:1200]]}]} for i, (c, _) in enumerate(CLAIMS)]
    t = time.time()
    st, _, raw = call("POST", "/api/ai/judge", {"items": items}, headers=me)
    took = time.time() - t
    if st != 200:
        print("verdicts failed:", st, raw)
        return 1
    r = json.loads(raw)
    print(f"\n== Verdicts: 6 sentences x 1 paper in one request, {took:.1f} s, {r['usage']}")
    for res in sorted(r["results"], key=lambda x: int(x["id"])):
        claim, want = CLAIMS[int(res["id"])]
        ok = res["verdict"] == want or (want == "supports" and res["verdict"] == "partly")
        right += ok
        print(f"  {'OK ' if ok else 'XX '} {res['verdict']:<11} (expected {want:<11}) {claim}\n       quote: {res['quote']!r}\n       reason: {res['reason']}")
    c = cost(r["usage"])
    total += c
    print(f"  {right}/6 as expected; cost ${c:.4f} (${c / 6:.4f} per verdict)")

    # 2. a library check: one sentence, three candidate papers
    lib = [{"ref": "topham", "title": "Sustainable decommissioning of an offshore wind farm", "passages": [p["text"] for p in fulltext.passages(PAPER, CLAIMS[3][0], n=3)]},
           {"ref": "lee", "title": "Export cable recovery costs in North Sea decommissioning", "passages": [LEE]},
           {"ref": "blades", "title": "Wind turbine blade waste", "passages": [BLADES]}]
    t = time.time()
    st, _, raw = call("POST", "/api/ai/judge", {"kind": "library", "items": [{"id": "lib", "claim": "Cable recovery is the most expensive decommissioning step.", "papers": lib}]}, headers=me)
    took = time.time() - t
    r = json.loads(raw)
    print(f"\n== Library check: 1 sentence x 3 candidate papers, {took:.1f} s, {r.get('usage')}")
    for res in r.get("results", []):
        print(f"   {res['paper']:<7} {res['verdict']:<11} {res['quote']!r}")
    c = cost(r["usage"])
    total += c
    print(f"  cost ${c:.4f}")

    print(f"\n== Total for this test: ${total:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
