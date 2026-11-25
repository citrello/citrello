import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packaging"))
import build_bridge_tools  # noqa: E402


def test_bridge_tool_list_is_up_to_date():
    with open(build_bridge_tools.OUT, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved == json.loads(json.dumps(build_bridge_tools.current())), \
        "Run: python packaging/build_bridge_tools.py"


def test_every_tool_has_the_directories_labels():
    for t in build_bridge_tools.current()["tools"]:
        a = t["annotations"]
        assert t["title"] and a["title"] and len(t["name"]) <= 64, t["name"]
        for k in ("readOnlyHint", "destructiveHint", "openWorldHint"):
            assert isinstance(a[k], bool), (t["name"], k)
        assert not (a["readOnlyHint"] and a["destructiveHint"]), t["name"]
