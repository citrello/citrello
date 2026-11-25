"""Writes bridge/src/tools.json: the connector's tools and instructions, so the bridge can list them for an
account whose Citrello app has never connected (the directories' reviewers scan the tool list with a new account).
Run after changing a tool in mcp_server.py; tests/test_bridge_tools.py fails while the copy is out of date."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "bridge", "src", "tools.json")


def current():
    import mcp_server
    return {"instructions": mcp_server.INSTRUCTIONS, "tools": mcp_server.tool_list()}


def main():
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(current(), f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
