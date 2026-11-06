// Citrello web app (citrello.app/app): runs the desktop app's own Python in the browser with Pyodide.
// The page sends each API request here; webapp.py answers it with the desktop app's own code. The library lives in
// /home/web, which is kept in the browser's own storage (IndexedDB), so it never leaves this computer.
// Only reference lookups go out (Crossref, doi.org, the CSL style repository), as in the desktop app.
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
importScripts(PYODIDE + "pyodide.js");

const DATA = "/home/web";
let py = null, handle = null;
const post = (m, transfer) => self.postMessage(m, transfer || []);

const sync = (populate) => new Promise((resolve, reject) =>
  py.FS.syncfs(populate, (err) => (err ? reject(err) : resolve())));

async function text(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r;
}

async function setup() {
  post({ type: "status", text: "Opening Citrello… (the first time takes 10–30 seconds)" });
  py = await loadPyodide({ indexURL: PYODIDE });
  const man = await (await text("py/manifest.json")).json();
  await py.loadPackage(man.pyodide_packages);
  const micropip = py.pyimport("micropip");
  await micropip.install(man.wheels.map((w) => new URL("py/wheels/" + w, self.location).href));

  // the person's data: restored from the browser's storage, saved back after every request
  py.FS.mkdirTree(DATA);
  py.FS.mount(py.FS.filesystems.IDBFS, {}, DATA);
  await sync(true);

  py.FS.mkdirTree("/citrello/styles");
  const files = [...man.modules.map((m) => [m, "/citrello/" + m]),
                 ...man.styles.map((s) => ["styles/" + s, "/citrello/styles/" + s])];
  await Promise.all(files.map(async ([src, dst]) => {
    const r = await text("py/" + src);
    py.FS.mkdirTree(dst.slice(0, dst.lastIndexOf("/")));
    py.FS.writeFile(dst, new Uint8Array(await r.arrayBuffer()));
  }));
  py.runPython(`
import os, sys
os.environ["HOME"] = "${DATA}"
os.environ["CITRELLO_NO_USAGE"] = "1"
sys.path.insert(0, "/citrello")
import pyodide_http
pyodide_http.patch_all()          # urllib -> the browser's own requests
import webapp
webapp.start()
`);
  handle = py.globals.get("webapp").handle;
  await sync(false);
  post({ type: "ready", version: man.version });
}

const ready = setup().catch((e) => {
  const why = e && (e.message || (e.errno !== undefined && "file system error " + e.errno)) || String(e);
  post({ type: "error", text: "Citrello couldn't load in this browser: " + why });
  throw e;
});

// one request at a time, like the desktop app's library lock
let queue = Promise.resolve();
self.onmessage = (ev) => {
  const m = ev.data;
  if (m.type !== "request") return;
  queue = queue.then(() => answer(m)).catch(() => {});
};

async function answer(m) {
  try {
    await ready;
  } catch (e) {
    return post({ type: "response", id: m.id, status: 503, ctype: "application/json", headers: {},
                  body: new TextEncoder().encode(JSON.stringify({ error: "Citrello didn't load." })) });
  }
  let res;
  try {
    const out = handle(m.method, m.path, py.toPy(m.headers || {}), m.body || null);
    const [status, ctype, headers, body] = out.toJs({ dict_converter: Object.fromEntries });
    out.destroy();
    res = { status, ctype, headers, body: new Uint8Array(body) };
  } catch (e) {
    const msg = String(e && e.message || e).trim().split("\n").filter(Boolean).pop();
    res = { status: 500, ctype: "application/json", headers: {},
            body: new TextEncoder().encode(JSON.stringify({ error: msg })) };
  }
  try {
    await sync(false);
  } catch (e) {
    post({ type: "storage-error", text: String(e && e.message || e) });
  }
  post({ type: "response", id: m.id, ...res }, [res.body.buffer]);
}
