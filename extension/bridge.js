// Runs on Citrello's own pages (citrello.app/app and the desktop app at localhost:8585). Passes messages between
// the page and the extension: "the extension is here", "fetch these papers", each paper's text as it arrives, and
// the toolbar panel's requests (look up, add, keep the text).
(function () {
  const VERSION = chrome.runtime.getManifest().version;
  const toPage = (m) => window.postMessage(Object.assign({ citrelloExt: "" }, m), location.origin);
  const hello = () => toPage({ citrelloExt: "hello", version: VERSION });

  window.addEventListener("message", (e) => {
    if (e.source !== window || e.origin !== location.origin || !e.data || typeof e.data.citrelloExt !== "string") return;
    const m = e.data;
    if (m.citrelloExt === "ping") hello();
    if (m.citrelloExt === "fetch") {
      chrome.runtime.sendMessage({ action: "fetch", papers: m.papers, origin: location.origin, fast: !!m.fast })
        .then((r) => toPage({ citrelloExt: "fetch-started", n: r && r.n }))
        .catch((err) => toPage({ citrelloExt: "fetch-failed", error: String(err && err.message || err) }));
    }
  });

  // each paper's text from the fetch page -> the Citrello page (which saves it) -> the answer back
  const waiting = new Map();
  window.addEventListener("message", (e) => {
    if (e.source !== window || !e.data || e.data.citrelloExt !== "saved") return;
    const done = waiting.get(e.data.id);
    if (done) { waiting.delete(e.data.id); done({ ok: !!e.data.ok, error: e.data.error || "" }); }
  });
  chrome.runtime.onMessage.addListener((msg, sender, reply) => {
    if (!msg || msg.action !== "fulltext") return;
    const id = Math.random().toString(36).slice(2);
    waiting.set(id, reply);
    toPage({ citrelloExt: "fulltext", id, key: msg.key, how: msg.how, text: msg.text || "", pdf: msg.pdf || "" });
    setTimeout(() => { if (waiting.has(id)) { waiting.delete(id); reply({ ok: false, error: "Citrello didn't answer" }); } }, 120000);
    return true;
  });

  // the toolbar panel's requests (look a paper up, add it, keep its text), answered by this Citrello page
  const apiWaiting = new Map();
  window.addEventListener("message", (e) => {
    if (e.source !== window || !e.data || e.data.citrelloExt !== "api-reply") return;
    const done = apiWaiting.get(e.data.id);
    if (done) { apiWaiting.delete(e.data.id); done({ status: e.data.status, data: e.data.data }); }
  });
  chrome.runtime.onMessage.addListener((msg, sender, reply) => {
    if (!msg || msg.action !== "api") return;
    const id = Math.random().toString(36).slice(2);
    apiWaiting.set(id, reply);
    toPage({ citrelloExt: "api", id, path: msg.path, body: msg.body, pdf: msg.pdf, headers: msg.headers });
    setTimeout(() => { if (apiWaiting.has(id)) { apiWaiting.delete(id); reply({ error: "Citrello didn't answer in time." }); } }, 120000);
    return true;
  });
  if (!/\/(clip|mendeley)(\.html)?$/.test(location.pathname)) chrome.runtime.sendMessage({ action: "bridge-here" }).catch(() => {});
  hello();
})();
