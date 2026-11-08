// Runs inside a paper's page (injected by the extension), with the person's own access to it.
// grabPaper: the paper's text, from the article shown on the page, the page itself when it's a PDF, or a PDF the page
// links to; otherwise it reports what it found (PDF links and buttons to try, a "are you human?" check, no access).
// Only text is kept in the end: Citrello keeps the text and never the PDF.
// pageInfo: what the page says about the paper (citation <meta> tags), for the "Add to Citrello" panel.
// clickPdfButton: clicks the page's PDF button (one that works with scripts, not a plain link).

/* exported grabPaper, pageInfo, clickPdfButton */
async function grabPaper(expectDoi) {
  const meta = (name) => {
    const el = document.querySelector(`meta[name="${name}" i], meta[property="${name}" i]`);
    return el ? (el.content || "").trim() : "";
  };
  const cleanDoi = (d) => (d || "").toLowerCase().replace(/^https?:\/\/(dx\.)?doi\.org\//, "").replace(/^doi:\s*/, "").trim();
  const host = location.hostname;
  const b64 = (buf) => {
    const bytes = new Uint8Array(buf);
    let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  };
  const isPdf = (buf) => buf.byteLength < 40e6 && new TextDecoder().decode(new Uint8Array(buf.slice(0, 5))) === "%PDF-";
  async function tryPdf(url) {
    try {
      const r = await fetch(url, { credentials: "include" });
      if (!r.ok) return null;
      const buf = await r.arrayBuffer();
      return isPdf(buf) ? b64(buf) : null;
    } catch (e) { return null; }  // elsewhere, and that site doesn't let this page fetch it: the extension tries
  }

  // the page itself is a PDF (the browser's PDF viewer)
  if (document.contentType === "application/pdf") {
    const pdf = await tryPdf(location.href);
    return pdf ? { status: "ok", how: "pdf", pdf, host, isPdfPage: true } : { status: "no-access", host, isPdfPage: true };
  }
  // a "please confirm you're human" page (CAPTCHA, bot check): the person has to answer it, never Citrello
  const top = ((document.title || "") + " " + (document.body ? document.body.innerText.slice(0, 1500) : "")).toLowerCase();
  if (/just a moment|verify (that )?you are (a )?human|are you a robot|unusual traffic|attention required|press (and|&) hold|checking your browser|confirm you are human|i'?m not a robot|human verification/.test(top) ||
      document.querySelector('iframe[src*="captcha"], iframe[src*="challenges.cloudflare.com"], #challenge-form, .g-recaptcha, .h-captcha, #px-captcha')) {
    return { status: "captcha", host };
  }
  const doi = cleanDoi(meta("citation_doi") || meta("dc.identifier") || meta("prism.doi"));
  if (expectDoi && doi && doi.startsWith("10.") && doi !== cleanDoi(expectDoi)) return { status: "other-page", doi, host };

  // the article as the page shows it (publishers show the full text when you have access)
  let best = "";
  for (const q of ["#body", ".Body", ".article__body", ".c-article-body", "#article-body", ".article-body",
                   ".fulltext-view", "[itemprop=articleBody]", "article", "main", "[role=main]"]) {
    for (const el of document.querySelectorAll(q)) {
      const t = el.innerText || "";
      if (t.length > best.length) best = t;
    }
  }
  if (best.length > 6000 && /introduction|methods?|results|discussion|conclusions?/i.test(best)) {
    return { status: "ok", how: "page", text: best.slice(0, 600000), doi, host };
  }

  // PDFs the page offers: its citation_pdf_url, and links or buttons that say PDF
  const links = [];
  const add = (u) => {
    try {
      const x = new URL(u, location.href);
      if (/^https?:$/.test(x.protocol) && !links.includes(x.href)) links.push(x.href);
    } catch (e) { /* not a link */ }
  };
  if (meta("citation_pdf_url")) add(meta("citation_pdf_url"));
  const PDF_HREF = /\.pdf(\?|#|$)|\/pdf(ft|direct|plus)?(\/|\?|$)|\/epdf\/|\/doi\/(pdf|epdf|pdfdirect)\/|\/content\/pdf\/|\/articlepdf\/|download.{0,20}pdf|pdf.{0,20}download/i;
  const PDF_LABEL = /^(download|view|get|open|full[- ]text|article)?\s*(the )?pdf\b|\bpdf\s*(download|\(\d|$)/i;
  let button = false;
  for (const el of document.querySelectorAll("a, button, [role=button]")) {
    const label = (el.innerText || el.getAttribute("aria-label") || el.title || "").replace(/\s+/g, " ").trim();
    const href = el.getAttribute("href") || "";
    const looksPdf = PDF_HREF.test(href) || (label.length < 60 && PDF_LABEL.test(label));
    if (!looksPdf) continue;
    if (href && !/^(javascript:|#)/i.test(href)) add(href);
    else if (!button) { el.setAttribute("data-citrello-pdf", "1"); button = true; }
  }
  for (const u of links.slice(0, 6)) {
    const pdf = await tryPdf(u);
    if (pdf) return { status: "ok", how: "pdf", pdf, doi, host };
  }
  return { status: "no-access", doi, host, pdfLinks: links.slice(0, 6), pdfButton: button };
}

// Runs in the page's own scripts' world. Clicks the PDF button, and catches where it wanted to go: a click made by the
// extension isn't a person's, so the browser would block the new window the button opens. The address is kept in
// data-citrello-open for the extension to fetch.
function clickPdfButton() {
  const el = document.querySelector("[data-citrello-pdf]");
  if (!el) return false;
  const root = document.documentElement;
  const keep = (u) => { try { root.dataset.citrelloOpen = new URL(String(u), location.href).href; } catch (e) { /* not a URL */ } };
  const realOpen = window.open;
  window.open = function (u) {
    if (u) keep(u);
    // a stand-in window, for buttons that open an empty window and then set its address
    const loc = {};
    Object.defineProperty(loc, "href", { set: keep, get: () => root.dataset.citrelloOpen || "" });
    return { location: new Proxy(loc, { set: (t, k, v) => { keep(v); return true; } }), close() {}, focus() {},
             document: { write() {}, close() {} } };
  };
  document.addEventListener("click", (e) => {  // links the button makes and clicks (target=_blank)
    const a = e.target && e.target.closest && e.target.closest("a[href]");
    if (a && a.target === "_blank") { keep(a.href); e.preventDefault(); }
  }, true);
  el.click();
  setTimeout(() => { window.open = realOpen; }, 5000);
  return true;
}

function pageInfo() {
  const m = {};
  let n = 0;
  document.querySelectorAll("meta[name],meta[property]").forEach((e) => {
    const k = (e.getAttribute("name") || e.getAttribute("property") || "").toLowerCase();
    if (/^(citation_|dc\.|prism\.|og:|bepress_|article:published_time$)/.test(k) && k !== "citation_reference") {
      m[k] = m[k] || [];
      if (m[k].length < 30 && n < 400) { m[k].push((e.content || "").slice(0, 400)); n++; }
    }
  });
  return { url: location.href, title: document.title.slice(0, 300), meta: m, sel: String(window.getSelection()).slice(0, 2000),
           isPdf: document.contentType === "application/pdf" };
}
