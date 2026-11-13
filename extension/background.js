// Citrello browser extension: background.
// - The toolbar panel (popup.html) asks Citrello to look up and keep papers ("citrello-api"): the desktop app
//   directly, or the web app through its open tab (opened in the background when there isn't one: the web app's
//   library lives in that tab, which also keeps ChatGPT and Claude connected).
// - "Fetch with my access" from Citrello: opens the extension's fetch page, which visits each paper in turn.

// grabPaper, pageInfo, pdfFrom: for the notice on a paper's page (Firefox loads them before this file instead)
if (typeof importScripts === "function" && typeof grabPaper === "undefined") importScripts("grab.js", "follow.js");

const WEB_APP = "https://citrello.app";
const DESKTOP = "http://localhost:8585";
const ready = new Map();  // tab id -> resolve(), when that Citrello tab's bridge says it's there

async function settings() {
  const { target, testAppBase } = await chrome.storage.local.get(["target", "testAppBase"]);
  return { desktop: target === "desktop", base: testAppBase || WEB_APP };
}

const isAppTab = (t, base) => t.url && t.url.startsWith(base + "/app/") && !/\/app\/(clip|mendeley)/.test(t.url);

// Citrello tabs that said "this is the one with the library" (kept across restarts of this background script)
async function knownTabs() {
  const { citrelloTabs } = await chrome.storage.session.get("citrelloTabs");
  return citrelloTabs || {};
}
async function rememberTab(id, url) {
  const tabs = await knownTabs();
  tabs[id] = url || "";
  await chrome.storage.session.set({ citrelloTabs: tabs });
}
async function forgetTab(id) {
  const tabs = await knownTabs();
  if (id in tabs) { delete tabs[id]; await chrome.storage.session.set({ citrelloTabs: tabs }); }
}

function waitReady(tabId, ms) {
  return new Promise((resolve) => {
    const t = setTimeout(() => { if (ready.delete(tabId)) resolve(false); }, ms);
    ready.set(tabId, () => { clearTimeout(t); resolve(true); });
  });
}

// The Citrello web app's tab: the one that holds the library; else one opened before the extension (it gets the
// extension's page script now); else a new one in the background, left open afterwards. Never a second one.
async function citrelloTab() {
  const { base } = await settings();
  for (const [id, url] of Object.entries(await knownTabs())) {
    if (!url.startsWith(base + "/app/")) continue;
    try { return await chrome.tabs.get(Number(id)); } catch (e) { await forgetTab(id); }
  }
  const open = (await chrome.tabs.query({ url: base + "/app/*" })).filter((t) => isAppTab(t, base));
  for (const t of open) {
    const wait = waitReady(t.id, 8000);
    await chrome.scripting.executeScript({ target: { tabId: t.id }, files: ["bridge.js"] }).catch(() => {});
    if (await wait) return t;  // a tab showing "already open in another tab" never says it's ready
  }
  if (open.length) throw new Error("Citrello is open in another window. Switch to it and try again.");
  const tab = await chrome.tabs.create({ url: base + "/app/", active: false });
  if (!(await waitReady(tab.id, 90000))) throw new Error("Citrello took too long to open. Try again.");
  return tab;
}

function bytes(b64) {
  const bin = atob(b64), out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// The Citrello tab if one is open already (never opens one): for the notice, which mustn't open tabs by itself
async function openCitrelloTab() {
  const { base } = await settings();
  for (const [id, url] of Object.entries(await knownTabs())) {
    if (!url.startsWith(base + "/app/")) continue;
    try { return await chrome.tabs.get(Number(id)); } catch (e) { await forgetTab(id); }
  }
  return null;
}

async function citrelloApi(msg) {
  if (!String(msg.path || "").startsWith("/api/clip/")) return { error: "Not allowed" };
  const { desktop } = await settings();
  if (desktop) {  // Citrello for desktop answers on this computer
    try {
      const r = await fetch(DESKTOP + msg.path, { method: "POST", headers: msg.headers || {}, body: msg.pdf ? bytes(msg.pdf) : msg.body });
      return { status: r.status, data: await r.json().catch(() => ({})) };
    } catch (e) {
      return { error: "Citrello for desktop isn't running. Start it, or choose the web version in the extension's options." };
    }
  }
  const tab = msg.noOpen ? await openCitrelloTab() : await citrelloTab();
  if (!tab) return { error: "Citrello isn't open." };
  return chrome.tabs.sendMessage(tab.id, { action: "api", path: msg.path, body: msg.body || "", pdf: msg.pdf || "", headers: msg.headers || {} });
}

// ---------- the notice: a paper you open that's in your library without its text ----------
// With the extension allowed to read pages (asked the first time it fetches papers, or from its button), each page
// that finishes loading is compared with the list of library papers that have no text (by DOI, then title). That
// list comes from Citrello and stays in the extension; nothing about the pages you visit leaves this computer. On a
// match: a badge on the icon and a small box on the page, "Add its text".
const normDoi = (d) => String(d || "").trim().toLowerCase().replace(/^https?:\/\/(dx\.)?doi\.org\//, "").replace(/^doi:\s*/, "");
const normTitle = (t) => String(t || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^0-9a-z]+/g, " ").trim().slice(0, 120);
async function watchList(force) {  // the list, refreshed from Citrello at most every 5 minutes (when it's open)
  const { watch } = await chrome.storage.local.get("watch");
  if (!force && watch && Date.now() - watch.at < 5 * 60 * 1000) return watch;
  const r = await citrelloApi({ path: "/api/clip/missing", body: "{}", headers: { "Content-Type": "application/json" }, noOpen: true }).catch(() => null);
  if (!r || r.error || !r.data || !Array.isArray(r.data.missing)) return watch || { at: 0, missing: [] };
  const fresh = { at: Date.now(), missing: r.data.missing };
  await chrome.storage.local.set({ watch: fresh });
  return fresh;
}
async function noticeOn(url) {  // on, unless turned off in the options, wherever the extension may read pages
  const { notice } = await chrome.storage.local.get("notice");
  return notice !== false && (await chrome.permissions.contains({ origins: [new URL(url).origin + "/*"] }).catch(() => false));
}
function pageDois(info) {  // the DOIs a page names: its citation tags, else its address
  const out = [];
  for (const k of ["citation_doi", "dc.identifier", "prism.doi", "bepress_citation_doi", "dc.identifier.doi"]) {
    for (const v of (info.meta && info.meta[k]) || []) if (/10\.\d{4,9}\//.test(v)) out.push(normDoi(v.slice(v.search(/10\.\d{4,9}\//))));
  }
  const m = /(10\.\d{4,9}\/[^?#\s]+)/.exec(decodeURIComponent(info.url || ""));
  if (m) out.push(normDoi(m[1].replace(/\/(full|abstract|pdf|epdf)$/i, "")));
  return out;
}
async function checkPage(tabId, url) {
  const { base } = await settings();
  if (!/^https?:/.test(url || "") || url.startsWith(base) || url.startsWith(WEB_APP) || url.startsWith(DESKTOP)) return;
  if (!(await noticeOn(url))) return;
  const watch = await watchList(false);
  if (!watch.missing.length) return;
  let info;
  try { [{ result: info }] = await chrome.scripting.executeScript({ target: { tabId }, func: pageInfo }); } catch (e) { return; }
  if (!info || info.isPdf) return;
  const dois = pageDois(info);
  const titles = [...((info.meta && info.meta.citation_title) || []), ...((info.meta && info.meta["dc.title"]) || [])].map(normTitle).filter((t) => t.length > 15);
  const hit = watch.missing.find((p) => (p.doi && dois.includes(p.doi)) || (p.title && titles.includes(p.title)));
  if (!hit) return;
  chrome.action.setBadgeBackgroundColor({ color: "#b26b00", tabId }).catch(() => {});
  chrome.action.setBadgeText({ text: "+", tabId }).catch(() => {});
  chrome.action.setTitle({ title: "This paper is in your Citrello library without its full text: click to add it", tabId }).catch(() => {});
  chrome.scripting.executeScript({ target: { tabId }, func: showNotice, args: [hit.key] }).catch(() => {});
}
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (change.status === "complete") checkPage(tabId, tab.url).catch(() => {});
});

// the box on the paper's page (in its own shadow root, so the page's styles don't touch it)
function showNotice(key) {
  if (document.getElementById("citrello-notice")) return;
  const host = document.createElement("div");
  host.id = "citrello-notice";
  host.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>
    .box{font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;background:#fff;color:#14213d;border:2px solid #1d3f8f;
      border-radius:14px;box-shadow:6px 6px 0 #f6d646;padding:12px 14px;max-width:320px}
    b{color:#1d3f8f} .row{display:flex;gap:8px;align-items:center;margin-top:8px}
    button{font:600 13px system-ui,sans-serif;border-radius:99px;padding:6px 14px;cursor:pointer;border:1px solid #d5def0;background:#fff;color:#14213d}
    button.go{background:#1d3f8f;border-color:#1d3f8f;color:#fff} .x{border:0;background:none;padding:2px 6px;font-size:16px;margin-left:auto}
    .ok{color:#2f6b40} .err{color:#b3261e}</style>
    <div class="box" role="status"><div class="row" style="margin:0"><b>Citrello</b><button class="x" title="Close">✕</button></div>
    <div id="msg">This paper is in your library, but Citrello doesn't have its full text yet. Add it, and Citrello can check every sentence where you cite it.</div>
    <div class="row"><button class="go" id="add">Add its text</button></div></div>`;
  root.querySelector(".x").onclick = () => host.remove();
  root.getElementById("add").onclick = async () => {
    const b = root.getElementById("add"), msg = root.getElementById("msg");
    b.disabled = true; b.textContent = "Reading the paper…";
    const r = await chrome.runtime.sendMessage({ action: "add-text-here", key }).catch((e) => ({ error: String(e) }));
    if (r && r.ok) { msg.className = "ok"; msg.textContent = "Full text added ✓ Citrello can now check your citations of this paper."; b.remove(); }
    else { msg.className = "err"; msg.textContent = (r && r.error) || "It couldn't be added."; b.disabled = false; b.textContent = "Try again"; }
  };
  document.documentElement.appendChild(host);
}

// "Add its text" on the notice: the paper's text from this page (or a PDF it links to, with the person's access)
async function addTextHere(tabId, key) {
  let g = {};
  try { [{ result: g }] = await chrome.scripting.executeScript({ target: { tabId }, func: grabPaper, args: [""] }); } catch (e) { g = {}; }
  g = g || {};
  if (g.status === "no-access" || (g.status !== "ok" && (g.pdfLinks || []).length)) {
    for (const u of g.pdfLinks || []) { const pdf = await pdfFrom(u); if (pdf) { g = { status: "ok", pdf }; break; } }
  }
  if (g.status === "captcha") return { error: "The page wants to check that you're a person. Answer the check, then press Try again." };
  if (g.status !== "ok") return { error: "The full text isn't on this page. Open the paper's PDF, then click the Citrello button on it." };
  const r = await citrelloApi({ path: "/api/clip/fulltext", body: g.text ? JSON.stringify({ text: g.text }) : "", pdf: g.pdf || "",
                                headers: { "X-Key": encodeURIComponent(key), "X-Check-Title": "1" } });
  if (r.error) return { error: r.error };
  if (r.status >= 400) return { error: (r.data && r.data.error) || "Citrello couldn't keep the text." };
  const { watch } = await chrome.storage.local.get("watch");  // it has its text now
  if (watch) await chrome.storage.local.set({ watch: { ...watch, missing: watch.missing.filter((p) => p.key !== key) } });
  chrome.action.setBadgeBackgroundColor({ color: "#3e7b4f", tabId }).catch(() => {});
  chrome.action.setBadgeText({ text: "✓", tabId }).catch(() => {});
  return { ok: true };
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!msg) return;
  if (msg.action === "citrello-api") {  // from the toolbar panel
    citrelloApi(msg).then(reply, (e) => reply({ error: String(e && e.message || e) }));
    return true;
  }
  if (msg.action === "bridge-here" && sender.tab) {  // the Citrello tab with the library (or the desktop app) has loaded
    rememberTab(sender.tab.id, sender.url || sender.tab.url).then(() => {
      const done = ready.get(sender.tab.id);
      if (done) { ready.delete(sender.tab.id); done(); }
    });
    return;
  }
  if (msg.action === "add-text-here" && sender.tab && msg.key) {  // the notice's button, on a paper's page
    addTextHere(sender.tab.id, String(msg.key)).then(reply, (e) => reply({ error: String(e && e.message || e) }));
    return true;
  }
  if (msg.action === "notice-on") {  // the toolbar panel turned the notice on
    watchList(true).then(() => reply({ ok: true }), () => reply({ ok: false }));
    return true;
  }
  if (msg.action === "badge" && msg.tabId) {  // a ✓ on the icon for that page, for a few seconds
    chrome.action.setBadgeBackgroundColor({ color: "#3e7b4f", tabId: msg.tabId }).catch(() => {});
    chrome.action.setBadgeText({ text: "✓", tabId: msg.tabId }).catch(() => {});
    setTimeout(() => chrome.action.setBadgeText({ text: "", tabId: msg.tabId }).catch(() => {}), 5000);
    return;
  }
  if (msg.action === "open-citrello") {
    settings().then(async ({ desktop, base }) => {
      if (desktop) return chrome.tabs.create({ url: DESKTOP + "/" });
      try {
        const open = await citrelloTab();
        chrome.tabs.update(open.id, { active: true });
        chrome.windows.update(open.windowId, { focused: true });
      } catch (e) {
        chrome.tabs.create({ url: base + "/app/" });
      }
    });
    return;
  }
  if (msg.action === "fetch" && Array.isArray(msg.papers) && sender.tab) {  // Citrello: fetch these papers' texts with my access
    const papers = msg.papers.filter((p) => p && p.key && (p.doi || p.url)).slice(0, 2000).map((p) => ({
      key: String(p.key), doi: String(p.doi || ""), url: String(p.url || ""), title: String(p.title || "").slice(0, 300),
      type: String(p.type || "") }));
    // only a local test copy of Citrello may ask to skip the pause between papers
    const testFast = !!msg.fast && /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(msg.origin || "");
    chrome.storage.local.set({ job: { papers, citrelloTab: sender.tab.id, origin: msg.origin || "", testFast, at: Date.now() } })
      .then(() => chrome.tabs.create({ url: chrome.runtime.getURL("fetch.html"), index: sender.tab.index + 1 }))
      .then(() => reply({ ok: true, n: papers.length }));
    return true;
  }
});

chrome.tabs.onRemoved.addListener((id) => { ready.delete(id); forgetTab(String(id)); });
