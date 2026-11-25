"""Builds Citrello's ChatGPT plugin package (the .zip uploaded at platform.openai.com/plugins).

  python packaging/build_chatgpt_plugin.py [--demo-url URL] [--out citrello-chatgpt-plugin.zip]

The package is packaging/chatgpt-plugin/ (plugin.json, mcp.json) plus assets/logo.png drawn by brand_icon.py.
check() enforces OpenAI's listed limits, so a bad edit fails here instead of at upload. No credentials go in the
package: the reviewer login is entered in OpenAI's dashboard. Listing texts and the reviewer notes:
packaging/connector-listing.md."""
import argparse
import io
import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "packaging", "chatgpt-plugin")
sys.path.insert(0, ROOT)


def load():
    with open(os.path.join(SRC, "plugin.json"), encoding="utf-8") as f:
        plugin = json.load(f)
    with open(os.path.join(SRC, "mcp.json"), encoding="utf-8") as f:
        mcp = json.load(f)
    return plugin, mcp


def check(plugin, mcp, need_demo=False):
    """OpenAI's limits (developers.openai.com/plugins/build/plugins and /deploy/submission, Nov 2026)."""
    problems = []
    def need(cond, msg):
        if not cond:
            problems.append(msg)
    need(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", plugin["name"]) and len(plugin["name"]) <= 64, "name: lowercase-hyphens, <= 64")
    need(re.fullmatch(r"\d+\.\d+\.\d+", plugin.get("version", "")), "version: x.y.z")
    need(len(plugin.get("description", "")) <= 4000, "description <= 4000")
    need(len(plugin.get("keywords", [])) <= 25, "keywords <= 25")
    oa = plugin["extensions"]["com.openai"]
    ui, review = oa["interface"], oa["review"]
    for k, n in (("displayName", 30), ("shortDescription", 30), ("longDescription", 4000), ("developerName", 80)):
        need(ui.get(k) and len(ui[k]) <= n, f"interface.{k}: required, <= {n}")
    need(ui.get("category"), "interface.category: required")
    for k in ("websiteURL", "supportURL", "privacyPolicyURL", "termsOfServiceURL"):
        need(str(ui.get(k, "")).startswith("https://") and len(ui[k]) <= 1024 and "@" not in ui[k], f"interface.{k}: https URL")
    prompts = ui.get("defaultPrompt", [])
    prompts = [prompts] if isinstance(prompts, str) else prompts
    need(len(prompts) <= 3 and all(len(p) <= 128 and "@" not in p for p in prompts) and len(set(prompts)) == len(prompts),
         "defaultPrompt: up to 3 unique, <= 128, no @mentions")
    caps = ui.get("capabilities", [])
    need(len(caps) <= 20 and all(len(c) <= 120 for c in caps), "capabilities: <= 20 of <= 120")
    for k in ("brandColor", "brandColorDark"):
        need(re.fullmatch(r"#[0-9a-fA-F]{6}", ui.get(k, "#000000")), f"interface.{k}: #RRGGBB")
    need(contrast(ui["brandColor"], "#ffffff") >= 2, "brandColor: 2:1 against white")
    need(contrast(ui["brandColorDark"], "#212121") >= 2, "brandColorDark: 2:1 against #212121")
    need(ui.get("logo", "").startswith("./"), "interface.logo: ./ path")
    tc = review["test_cases"]
    need(len(tc["positive"]) == 5 and len(tc["negative"]) == 3, "test cases: exactly 5 positive and 3 negative")
    for c in tc["positive"]:
        need(all(isinstance(c.get(k), str) and c[k] for k in ("description", "prompt", "tools_triggered", "expected_behavior")),
             "positive case: description, prompt, tools_triggered (string), expected_behavior")
    for c in tc["negative"]:
        need(c.get("description") and c.get("prompt"), "negative case: description and prompt")
    for bad in ("test_credentials", "reviewer_instructions"):
        need(bad not in review, f"review.{bad} isn't allowed in the package")
    if need_demo:
        need(str(review.get("demo_recording_url", "")).startswith("https://"), "review.demo_recording_url: needed for review")
    servers = mcp["mcpServers"]
    need(len(servers) == 1, "one MCP server")
    for s in servers.values():
        need(s["type"] == "streamable-http" and s["url"].startswith("https://") and not s.get("headers"), "server: streamable-http, https, no headers")
        auth = s["extensions"]["com.openai"]["auth"]
        need(auth["type"] == "oauth" and auth["client"] == {"mode": "dcr"}, "auth: oauth with DCR, no client credentials")
    text = json.dumps([plugin, mcp])
    need(not re.search(r"(secret|password|token)\"\s*:", text, re.I), "no credentials in the package")
    return problems


def contrast(a, b):
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def build(out, demo_url=None):
    plugin, mcp = load()
    if demo_url:
        plugin["extensions"]["com.openai"]["review"]["demo_recording_url"] = demo_url
    problems = check(plugin, mcp, need_demo=bool(demo_url))
    if problems:
        raise SystemExit("Not built:\n- " + "\n- ".join(problems))
    import brand_icon
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("plugin.json", json.dumps(plugin, ensure_ascii=False, indent=2) + "\n")
        z.writestr("mcp.json", json.dumps(mcp, ensure_ascii=False, indent=2) + "\n")
        z.writestr("assets/logo.png", brand_icon.png_bytes(512))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo-url", help="the demo video's link (needed before submitting for review)")
    ap.add_argument("--out", default=os.path.join(ROOT, "citrello-chatgpt-plugin.zip"))
    a = ap.parse_args()
    print("wrote", build(a.out, a.demo_url))
    if not a.demo_url:
        print("No --demo-url: fine for uploading and checking; add the video's link before submitting for review.")


if __name__ == "__main__":
    main()
