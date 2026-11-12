// Citrello web app (citrello.app/app): runs the desktop app's own Python in the browser with Pyodide.
// The page sends each API request here; webapp.py answers it with the desktop app's own code. The library lives in
// /home/web, which is kept in the browser's own storage (IndexedDB), so it never leaves this computer.
// Only reference lookups go out (Crossref, doi.org, the CSL style repository), as in the desktop app.
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
importScripts(PYODIDE + "pyodide.js");

// The Word add-in (citrello.app/word) runs this same engine with its own storage, and isn't the connector's device
const WORD = new URLSearchParams(self.location.search).get("mode") === "word";
const DATA = WORD ? "/home/word" : "/home/web";
// The bridge ChatGPT and Claude reach this tab through. A local test bridge only for a locally served copy.
const LOCAL = ["localhost", "127.0.0.1"].includes(self.location.hostname);
const BRIDGE = (LOCAL && new URLSearchParams(self.location.search).get("bridge")) || "https://mcp.citrello.app";
const MENDELEY_API = (LOCAL && new URLSearchParams(self.location.search).get("mendeley_api")) || "";

function browserName() {
  const ua = navigator.userAgent;
  const b = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome"
    : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /iPhone|iPad/.test(ua) ? "iPhone or iPad" : /Android/.test(ua) ? "Android" : /Mac OS X/.test(ua) ? "Mac"
    : /Windows/.test(ua) ? "Windows" : /Linux|CrOS/.test(ua) ? "Linux" : "";
  return WORD ? `Word add-in${os ? " on " + os : ""}` : `${b}${os ? " on " + os : ""} (web app)`;
}
let py = null, handle = null, hasPending = null, runPending = null, webapp = null;
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
os.environ["CITRELLO_BRIDGE"] = ${JSON.stringify(BRIDGE)}
if ${JSON.stringify(MENDELEY_API)}:
    os.environ["CITRELLO_API"] = ${JSON.stringify(MENDELEY_API)}   # app.py points Mendeley calls there (tests)
sys.path.insert(0, "/citrello")
import pyodide_http
pyodide_http.patch_all()          # urllib -> the browser's own requests
import webapp
webapp.start(${JSON.stringify(browserName())})
`);
  webapp = py.globals.get("webapp");
  handle = webapp.handle;
  hasPending = webapp.has_pending;
  runPending = webapp.run_pending;
  // a running job sends its progress to the page, which answers its own progress polls with it
  webapp.PUBLISH = (path, body) => post({ type: "progress", path, body });
  await sync(false);
  post({ type: "ready", version: man.version });
  planConnection();
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
  planConnection(); // signing in or out changes what the connection should do
  // a job the request started (checking a document, verifying references…) runs now that the page has its id
  if (hasPending && hasPending()) {
    try {
      runPending();
    } catch (e) {
      console.error(e);
    }
    try { await sync(false); } catch (e) { post({ type: "storage-error", text: String(e && e.message || e) }); }
  }
}

// ---------- the connector: ChatGPT and Claude reach the library through the bridge, while this tab is open ----------
// The same protocol as the desktop app (bridge_client.py): {type: "hello"} first, then {type: "rpc", id, msg} in and
// {type: "reply", id, msg} out. The sign-in token goes as a WebSocket subprotocol (browsers can't send headers).
let ws = null, wsToken = null, wsGeneration = -1, wsOpened = false, pingTimer = null, retryTimer = null, retryDelay = 2000;

function setState(state, error) {
  webapp.CONNECTION.set_state(state, error || "");
  post({ type: "connection", state });
}

function planConnection() {
  if (!webapp || WORD) return;
  const plan = JSON.parse(webapp.CONNECTION.plan());
  if (plan.generation === wsGeneration && plan.token === wsToken && (ws || retryTimer || !plan.token)) return;
  const fresh = plan.generation !== wsGeneration || plan.token !== wsToken;
  wsGeneration = plan.generation;
  wsToken = plan.token;
  if (fresh) {
    clearTimeout(retryTimer);
    retryTimer = null;
    retryDelay = 2000;
    if (ws) { const old = ws; ws = null; old.onclose = null; old.close(1000, "signed out"); clearInterval(pingTimer); }
  }
  if (!plan.token) return setState("signed_out");
  if (!ws) openSocket(plan.url, plan.token);
}

function openSocket(url, token) {
  setState("connecting");
  wsOpened = false;
  let sock;
  try {
    sock = new WebSocket(url, ["citrello", token]);
  } catch (e) {
    return retryLater(String(e && e.message || e));
  }
  ws = sock;
  sock.onopen = () => {
    if (ws !== sock) return;
    wsOpened = true;
    retryDelay = 2000;
    sock.send(webapp.hello());
    setState("online");
    clearInterval(pingTimer);
    pingTimer = setInterval(() => { try { sock.send("ping"); } catch (e) {} }, 30000);
  };
  sock.onmessage = (ev) => {
    if (typeof ev.data !== "string" || ev.data === "pong") return;
    let frame;
    try { frame = JSON.parse(ev.data); } catch (e) { return; }
    if (frame.type !== "rpc") return;
    // in the same queue as the page's requests: one thing at a time touches the library
    queue = queue.then(() => answerRpc(sock, ev.data, frame)).catch(() => {});
  };
  sock.onclose = (ev) => {
    if (ws !== sock) return;
    ws = null;
    clearInterval(pingTimer);
    if (!wsOpened) webapp.check_account(); // refused: maybe this browser was signed out elsewhere
    if (ev.code === 4001) webapp.check_account();
    retryLater(wsOpened ? "Connection lost" : "Can't reach the Citrello server");
    planConnection();
  };
}

function retryLater(error) {
  ws = null;
  setState("offline", error);
  clearTimeout(retryTimer);
  retryTimer = setTimeout(() => { retryTimer = null; planConnection(); }, retryDelay);
  retryDelay = Math.min(retryDelay * 2, 60000);
}

async function answerRpc(sock, raw, frame) {
  await ready;
  let reply;
  try {
    reply = webapp.answer_rpc(raw);
  } catch (e) {
    reply = JSON.stringify({ type: "reply", id: frame.id, msg: { jsonrpc: "2.0", id: (frame.msg || {}).id,
      error: { code: -32603, message: "Citrello error: " + (e && e.message || e) } } });
  }
  try { sock.send(reply); } catch (e) {}
  try { await sync(false); } catch (e) { post({ type: "storage-error", text: String(e && e.message || e) }); }
  if ((frame.msg || {}).method === "tools/call") post({ type: "library-changed" });
}
