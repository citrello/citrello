import json

import pytest
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packaging"))
import build_chatgpt_plugin as b  # noqa: E402


def test_package_meets_openais_limits(tmp_path):
    plugin, mcp = b.load()
    assert b.check(plugin, mcp) == []
    pytest.importorskip("PIL")  # the logo is drawn with Pillow (a build-time dependency)
    out = b.build(str(tmp_path / "p.zip"), demo_url="https://example.com/demo")
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["assets/logo.png", "mcp.json", "plugin.json"]
        p = json.loads(z.read("plugin.json"))
        assert p["extensions"]["com.openai"]["review"]["demo_recording_url"] == "https://example.com/demo"
        assert z.read("assets/logo.png")[:8] == b"\x89PNG\r\n\x1a\n"


def test_test_cases_name_real_tools():
    import mcp_server
    plugin, _ = b.load()
    for c in plugin["extensions"]["com.openai"]["review"]["test_cases"]["positive"]:
        for name in c["tools_triggered"].split(","):
            assert name.strip() in mcp_server.TOOLS, name


def test_check_catches_mistakes():
    plugin, mcp = b.load()
    plugin["extensions"]["com.openai"]["interface"]["shortDescription"] = "x" * 31
    plugin["extensions"]["com.openai"]["review"]["test_cases"]["negative"].pop()
    plugin["extensions"]["com.openai"]["review"]["demo_recording_url"] = ""
    problems = b.check(plugin, mcp, need_demo=True)
    assert any("shortDescription" in p for p in problems) and any("exactly 5" in p for p in problems)
    assert any("demo_recording_url" in p for p in problems)
