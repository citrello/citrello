// Citrello browser extension: background.
// - The toolbar panel (popup.html) asks Citrello to look up and keep papers ("citrello-api"): the desktop app
//   directly, or the web app through its open tab (opened in the background when there isn't one: the web app's
//   library lives in that tab, which also keeps ChatGPT and Claude connected).
// - "Fetch with my access" from Citrello: opens the extension's fetch page, which visits each paper in turn.

const WEB_APP = "https://citrello.app";
const DESKTOP = "http://localhost:8585";
const ready = new Map();  // tab id -> resolve(), when that Citrello tab's bridge says it's there

async function settings() {
  const { target, testAppBase } = await chrome.storage.local.get(["target", "testAppBase"]);
  return { desktop: target === "desktop", base: testAppBase || WEB_APP };
}

const isAppTab = (t, base) => t.url && t.url.startsWith(base + "/app/") && !/\/app\/(clip|mendeley)/.test(t.url);

// The Citrello web app's tab: the one that's open, or a new one in the background (left open afterwards).
async function citrelloTab() {
  const { base } = await settings();
  const open = (await chrome.tabs.query({})).find((t) => isAppTab(t, base));
  if (open) return open;
  const tab = await chrome.tabs.create({ url: base + "/app/", active: false, pinned: false });
  await new Promise((resolve, reject) => {
    ready.set(tab.id, resolve);
    setTimeout(() => { if (ready.delete(tab.id)) reject(new Error("Citrello took too long to open. Try again.")); }, 90000);
  });
  return tab;
}

function bytes(b64) {
  const bin = atob(b64), out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
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
  const tab = await citrelloTab();
  return chrome.tabs.sendMessage(tab.id, { action: "api", path: msg.path, body: msg.body || "", pdf: msg.pdf || "", headers: msg.headers || {} });
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!msg) return;
  if (msg.action === "citrello-api") {  // from the toolbar panel
    citrelloApi(msg).then(reply, (e) => reply({ error: String(e && e.message || e) }));
    return true;
  }
  if (msg.action === "bridge-here" && sender.tab) {  // a Citrello tab has loaded
    const done = ready.get(sender.tab.id);
    if (done) { ready.delete(sender.tab.id); done(); }
    return;
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
      const open = (await chrome.tabs.query({})).find((t) => isAppTab(t, base));
      if (open) { chrome.tabs.update(open.id, { active: true }); chrome.windows.update(open.windowId, { focused: true }); }
      else chrome.tabs.create({ url: base + "/app/" });
    });
    return;
  }
  if (msg.action === "fetch" && Array.isArray(msg.papers) && sender.tab) {  // Citrello: fetch these papers' texts with my access
    const papers = msg.papers.filter((p) => p && p.key && (p.doi || p.url)).slice(0, 2000).map((p) => ({
      key: String(p.key), doi: String(p.doi || ""), url: String(p.url || ""), title: String(p.title || "").slice(0, 300) }));
    // only a local test copy of Citrello may ask to skip the pause between papers
    const testFast = !!msg.fast && /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(msg.origin || "");
    chrome.storage.local.set({ job: { papers, citrelloTab: sender.tab.id, origin: msg.origin || "", testFast, at: Date.now() } })
      .then(() => chrome.tabs.create({ url: chrome.runtime.getURL("fetch.html"), index: sender.tab.index + 1 }))
      .then(() => reply({ ok: true, n: papers.length }));
    return true;
  }
});

chrome.tabs.onRemoved.addListener((id) => ready.delete(id));
