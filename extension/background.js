// Citrello browser extension: background.
// - The toolbar button: on a paper's page, opens Citrello's "Add to Citrello" pop-up with the page's details, and
//   hands the pop-up the paper's text (the article on the page, or its PDF), read with the person's own access.
// - "Fetch with my access" from Citrello: opens the extension's fetch page, which visits each paper in turn.
/* global grabPaper, pageInfo */
if (typeof importScripts === "function" && typeof grabPaper === "undefined") importScripts("grab.js");

const WEB = "https://citrello.app/app/clip.html";
const DESKTOP = "http://localhost:8585/clip";
const pendingForClip = new Map(); // pop-up tab id -> promise of the paper's text

async function clipUrl() {
  const { target, testClipUrl } = await chrome.storage.local.get(["target", "testClipUrl"]);
  return testClipUrl || (target === "desktop" ? DESKTOP : WEB);
}

// the toolbar button (also called directly by the tests, which can't click a toolbar)
self.clipTab = async (tab) => {
  if (!tab || !tab.id || !/^https?:/.test(tab.url || "https:")) return;
  let info;
  try {
    [{ result: info }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: pageInfo });
  } catch (e) {
    return; // a page extensions can't read (the browser's own pages, the web store…)
  }
  const grabbing = chrome.scripting.executeScript({ target: { tabId: tab.id }, func: grabPaper, args: [""] })
    .then((r) => (r && r[0] && r[0].result) || {}).catch(() => ({}));
  const url = (await clipUrl()) + "#" + encodeURIComponent(JSON.stringify(info));
  const win = await chrome.windows.create({ url, type: "popup", width: 560, height: 720 });
  const popTab = win.tabs && win.tabs[0];
  if (popTab) pendingForClip.set(popTab.id, grabbing);
};
chrome.action.onClicked.addListener((tab) => self.clipTab(tab));

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!msg || !sender.tab) return;
  if (msg.action === "clip-paper") {  // the pop-up asks for the paper's text
    const p = pendingForClip.get(sender.tab.id);
    if (!p) { reply({ status: "not-mine" }); return; }  // opened by the bookmarklet, which sends the text itself
    pendingForClip.delete(sender.tab.id);
    p.then((g) => reply(g.status === "ok" ? g : { status: "none" }));
    return true;
  }
  if (msg.action === "fetch" && Array.isArray(msg.papers)) {  // Citrello: fetch these papers' texts with my access
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

chrome.tabs.onRemoved.addListener((id) => pendingForClip.delete(id));
