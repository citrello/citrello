// Used by the extension's own pages (fetch.html, popup.html) when a paper's page didn't give its text directly:
// - pdfFrom: fetch a PDF link the page offered, with the extension's own access (a publisher's PDF server is often
//   another site, which the page itself can't fetch from). A viewer page with the PDF inside it is looked into.
// - clickAndFollow: click the page's PDF button (one that works with scripts) and follow where it goes: a new tab,
//   or the same tab moving to the PDF. Extra tabs are closed afterwards.
/* global clickPdfButton */
/* exported pdfFrom, clickAndFollow, toBase64 */

function toBase64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

async function pdfFrom(url, depth = 0) {
  let r, buf;
  try {
    r = await fetch(url, { credentials: "include" });
    if (!r.ok) return null;
    buf = await r.arrayBuffer();
  } catch (e) {
    return null;
  }
  if (buf.byteLength < 40e6 && new TextDecoder().decode(new Uint8Array(buf.slice(0, 5))) === "%PDF-") return toBase64(buf);
  if (depth > 0 || !/html/i.test(r.headers.get("content-type") || "")) return null;
  // a viewer page: the PDF is in an <iframe>, <embed>, <object> or a link on it
  const html = new TextDecoder().decode(buf.slice(0, 3e6));
  const found = [];
  for (const m of html.matchAll(/(?:src|href|data|content)\s*=\s*["']([^"']+)["']/gi)) {
    const u = m[1].replace(/&amp;/g, "&");
    if (/\.pdf(\?|#|$)|\/pdf(ft|direct)?(\/|\?|$)|pdf\.sciencedirectassets|\/content\/pdf\/|\/doi\/pdf/i.test(u)) {
      try { const abs = new URL(u, r.url).href; if (!found.includes(abs) && abs !== url) found.push(abs); } catch (e) { /* skip */ }
    }
  }
  for (const u of found.slice(0, 4)) {
    const pdf = await pdfFrom(u, depth + 1);
    if (pdf) return pdf;
  }
  return null;
}

function clickAndFollow(tabId, timeout = 20000) {
  return new Promise((resolve) => {
    const opened = new Set();
    let startUrl = null, done = false;
    const finish = (url) => {
      if (done) return;
      done = true;
      chrome.tabs.onCreated.removeListener(onCreated);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      clearTimeout(timer);
      for (const id of opened) chrome.tabs.remove(id).catch(() => {});
      resolve(url);
    };
    const onCreated = (t) => { if (t.openerTabId === tabId) opened.add(t.id); };
    const onUpdated = (id, info, t) => {
      if (info.status !== "complete" || !t.url || !/^https?:/.test(t.url)) return;
      if (opened.has(id)) finish(t.url);                         // the PDF opened in a new tab
      else if (id === tabId && startUrl && t.url !== startUrl) finish(t.url);  // the same tab moved to it
    };
    const timer = setTimeout(() => finish(null), timeout);
    chrome.tabs.onCreated.addListener(onCreated);
    chrome.tabs.onUpdated.addListener(onUpdated);
    chrome.tabs.get(tabId).then((t) => { startUrl = t.url; })
      .then(() => chrome.scripting.executeScript({ target: { tabId }, func: clickPdfButton, world: "MAIN" }))
      .then(async (r) => {
        if (!(r && r[0] && r[0].result)) return finish(null);
        // where the button wanted to open the PDF (a window the browser blocked, or a new-tab link)
        for (let i = 0; i < 10 && !done; i++) {
          await new Promise((res) => setTimeout(res, 500));
          const [{ result }] = await chrome.scripting.executeScript({ target: { tabId }, func: () => document.documentElement.dataset.citrelloOpen || "" });
          if (result) return finish(result);
        }
      })
      .catch(() => finish(null));
  });
}
