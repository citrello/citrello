// Runs Citrello's own Python code in the browser (Pyodide), so the paper never leaves the visitor's computer.
// Only reference lookups go out (to Crossref and doi.org), as in the app.
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
importScripts(PYODIDE + "pyodide.js");

let py = null;
const post = (m, transfer) => self.postMessage(m, transfer || []);
self.postProgress = (i, n, step) => post({ type: "progress", i, n, step });

const SETUP = `
import os, sys, json
os.environ["CITRELLO_HOME"] = "/tmp/citrello"
os.environ["HOME"] = "/tmp"
sys.path.insert(0, "/citrello")
import pyodide_http
pyodide_http.patch_all()          # urllib -> browser requests
import doccheck, citeformat, citrello_core as core

REPORT = None

def _public(rep):
    refs = [{"number": r["number"], "title": r["csl"].get("title", ""),
             "authors": ", ".join((a.get("family") or a.get("literal") or "") for a in r["csl"].get("author", [])[:3])
                        + (" et al." if len(r["csl"].get("author", [])) > 3 else ""),
             "year": core.year_of(r["csl"]), "container": r["csl"].get("container-title", ""),
             "doi": r["csl"].get("DOI", ""), "reliability": r.get("reliability"), "checks": r.get("checks", []),
             "cited": r.get("cited")} for r in rep["refs"]]
    out = {k: rep[k] for k in ("source", "manager", "references", "counts", "citation_issues", "online")}
    out["duplicates"] = len(rep["duplicates"])
    out["refs"] = refs
    return out

def run_check(path):
    global REPORT
    from js import postProgress
    REPORT = doccheck.check(path, False, None, lambda i, n, step: postProgress(i, n, step))
    return json.dumps(_public(REPORT))

def run_fix(path, style):
    res = doccheck.fix(path, REPORT, "/tmp/fixed.docx", style)
    return json.dumps(res)

def report_csv():
    core.write_check_csv(REPORT["refs"], "/tmp/report.csv")

def styles():
    return json.dumps([{"id": s["id"], "title": s["title"]} for s in citeformat.list_styles()])
`;

async function setup() {
  post({ type: "status", text: "Loading the checker into your browser… (the first time takes 10–30 seconds)" });
  py = await loadPyodide({ indexURL: PYODIDE });
  await py.loadPackage(["lxml", "micropip", "typing-extensions"]);
  const man = await (await fetch("check-py/manifest.json")).json();
  const micropip = py.pyimport("micropip");
  await micropip.install(man.wheels.map((w) => new URL("check-py/wheels/" + w, self.location).href));
  py.FS.mkdirTree("/citrello/styles");
  for (const m of man.modules) py.FS.writeFile("/citrello/" + m, await (await fetch("check-py/" + m)).text());
  for (const s of man.styles) py.FS.writeFile("/citrello/styles/" + s, await (await fetch("check-py/styles/" + s)).text());
  py.runPython(SETUP);
  post({ type: "ready", styles: JSON.parse(py.runPython("styles()")), version: man.version });
}
const ready = setup().catch((e) => post({ type: "error", text: "The checker couldn't load: " + e.message }));

function pyError(e) {
  const msg = String(e && e.message || e);
  const last = msg.trim().split("\n").filter(Boolean).pop() || msg;
  return last.replace(/^\w+(Error|Exception): /, "");
}

self.onmessage = async (ev) => {
  const m = ev.data;
  await ready;
  if (!py) return;
  try {
    if (m.type === "check") {
      const path = "/tmp/" + (m.name.toLowerCase().endsWith(".pdf") ? "paper.pdf" : "paper.docx");
      py.FS.writeFile(path, new Uint8Array(m.data));
      self.path = path;
      const report = JSON.parse(py.globals.get("run_check")(path));
      post({ type: "report", report });
    } else if (m.type === "fix") {
      const res = JSON.parse(py.globals.get("run_fix")(self.path, m.style));
      const bytes = py.FS.readFile("/tmp/fixed.docx");
      post({ type: "fixed", result: res, bytes }, [bytes.buffer]);
    } else if (m.type === "csv") {
      py.globals.get("report_csv")();
      const bytes = py.FS.readFile("/tmp/report.csv");
      post({ type: "csv", bytes }, [bytes.buffer]);
    }
  } catch (e) {
    post({ type: "error", text: pyError(e) });
  }
};
