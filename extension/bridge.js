// Runs on Citrello's own pages (citrello.app/app and the desktop app at localhost:8585). Passes messages between
// the page and the extension: "the extension is here", "fetch these papers", each paper's text as it arrives, and
// the text for the "Add to Citrello" pop-up opened by the toolbar button.
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

  // the "Add to Citrello" pop-up opened by the toolbar button: ask for the paper's text and pass it on, in the same
  // form the bookmarklet uses ({citrello: "fulltext", text | pdf})
  if (/\/clip(\.html)?$/.test(location.pathname)) {
    chrome.runtime.sendMessage({ action: "clip-paper" }).then((g) => {
      if (!g || g.status === "not-mine") return;
      const m = { citrello: "fulltext" };
      if (g && g.text) m.text = g.text;
      if (g && g.pdf) {
        const bin = atob(g.pdf), bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        m.pdf = bytes.buffer;
      }
      window.postMessage(m, location.origin);
    }).catch(() => {});
  }
  hello();
})();
