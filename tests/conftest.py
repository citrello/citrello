"""Tests run against a throw-away library and never use the network."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_tmp = tempfile.mkdtemp(prefix="citrello_test_")
os.environ["CITRELLO_HOME"] = os.path.join(_tmp, "library")      # must be set before `import library`
os.environ["CITRELLO_LEGACY_HOME"] = os.path.join(_tmp, "no-legacy-data")
os.environ["CITRELLO_CROSSREF"] = "http://127.0.0.1:9"            # unreachable: tests stay offline
os.environ["CITRELLO_DOIORG"] = "http://127.0.0.1:9"
os.environ["CITRELLO_ACCOUNT_FILE"] = os.path.join(_tmp, "account.json")
os.environ.setdefault("CITRELLO_BRIDGE", "http://127.0.0.1:9")  # the end-to-end test sets a real one
