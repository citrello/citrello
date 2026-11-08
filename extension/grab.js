// Runs inside a paper's page (injected by the extension), with the person's own access to it.
// grabPaper: the paper's text, from the article shown on the page, the page itself when it's a PDF, or a PDF the page
// links to (or that the publisher's platform always has at a known address); otherwise it reports what it found:
// PDF links and buttons to try, an "are you human?" check that didn't clear by itself, a web page with nothing to
// read, or no access. It also returns a short description of the page (no text) for "Copy a report".
// Only text is kept in the end: Citrello keeps the text and never the PDF.
// pageInfo: what the page says about the paper (citation <meta> tags), for the "Add to Citrello" panel.
// clickPdfButton: clicks the page's PDF button (one that works with scripts, not a plain link).

/* exported grabPaper, pageInfo, clickPdfButton */

async function grabPaper(expectDoi, kind) {
  // Where the big publishing platforms keep a paper's PDF, from the page's address and the DOI. This covers most
  // fields without knowing every journal: Elsevier, IEEE, Wiley, Springer, Nature, the "/doi/pdf/" platform that
  // Taylor & Francis, SAGE, ACS, Science, PNAS, ACM, Annual Reviews and many societies use, IOP, MDPI, arXiv...
  function publisherPdfUrls(href, doi) {
    const out = [];
    let u;
    try { u = new URL(href); } catch (e) { return out; }
    const o = u.origin, h = u.hostname, p = u.pathname;
    const d = doi || ((p.match(/\/doi\/(?:abs\/|full\/|epdf\/|epub\/|pdf\/|book\/)?(10\.\d{4,9}\/[^?#]+)/) || [])[1] || "");
    const D = d ? encodeURI(decodeURIComponent(d)) : "";
    if (/(^|\.)ieeexplore\.ieee\.org$/.test(h)) {
      const n = (p.match(/\/(?:document|abstract\/document)\/(\d+)/) || [])[1];
      if (n) out.push(`${o}/stampPDF/getPDF.jsp?tp=&arnumber=${n}`, `${o}/stamp/stamp.jsp?tp=&arnumber=${n}`);
    }
    if (/(^|\.)sciencedirect\.com$/.test(h)) {
      const pii = (p.match(/\/pii\/([A-Z0-9]+)/i) || [])[1];
      if (pii) out.push(`${o}/science/article/pii/${pii}/pdfft?isDTMRedir=true&download=true`);
    }
    if (/onlinelibrary\.wiley\.com$|\.wiley\.com$/.test(h) && D) out.push(`${o}/doi/pdfdirect/${D}?download=true`, `${o}/doi/pdf/${D}`);
    if (/(^|\.)link\.springer\.com$/.test(h) && D) out.push(`${o}/content/pdf/${D}.pdf`);
    if (/(^|\.)nature\.com$/.test(h) && /\/articles\/[^/.]+$/.test(p)) out.push(`${o}${p}.pdf`);
    if (/(^|\.)iopscience\.iop\.org$/.test(h) && D) out.push(`${o}/article/${D}/pdf`);
    if (/(^|\.)mdpi\.com$/.test(h) && /^\/\d{4}-\d{3,4}/.test(p)) out.push(`${o}${p.replace(/\/htm$/, "")}/pdf`);
    if (/(^|\.)arxiv\.org$/.test(h) && /^\/abs\//.test(p)) out.push(`${o}${p.replace(/^\/abs\//, "/pdf/")}`);
    if (/(^|\.)journals\.aps\.org$/.test(h)) out.push(`${o}${p.replace(/\/abstract\//, "/pdf/")}`);
    if (/\/doi\//.test(p) && D) out.push(`${o}/doi/pdf/${D}`, `${o}/doi/pdf/${D}?download=true`);  // the "/doi/" platforms
    return out.filter((x, i) => out.indexOf(x) === i);
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const meta = (name) => {
    const el = document.querySelector(`meta[name="${name}" i], meta[property="${name}" i]`);
    return el ? (el.content || "").trim() : "";
  };
  const cleanDoi = (d) => (d || "").toLowerCase().replace(/^https?:\/\/(dx\.)?doi\.org\//, "").replace(/^doi:\s*/, "").trim();
  const host = location.hostname;
  const describe = (extra) => Object.assign({ host, path: location.pathname.slice(0, 120), title: (document.title || "").slice(0, 120),
    headings: [...document.querySelectorAll("h1,h2,h3")].map((x) => (x.innerText || "").trim()).filter((t) => t && t.length < 70).slice(0, 10),
    chars: document.body ? document.body.innerText.length : 0 }, extra || {});
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
  const challenge = () => {
    const top = ((document.title || "") + " " + (document.body ? document.body.innerText.slice(0, 1500) : "")).toLowerCase();
    return /just a moment|verify (that )?you are (a )?human|are you a robot|unusual traffic|attention required|press (and|&) hold|checking your browser|confirm you are human|i'?m not a robot|human verification|performing security verification|security check to access/.test(top) ||
      !!document.querySelector('iframe[src*="captcha"], iframe[src*="challenges.cloudflare.com"], #challenge-form, .g-recaptcha, .h-captcha, #px-captcha');
  };

  // the page itself is a PDF (the browser's PDF viewer)
  if (document.contentType === "application/pdf") {
    const pdf = await tryPdf(location.href);
    return pdf ? { status: "ok", how: "pdf", pdf, host, isPdfPage: true } : { status: "no-access", host, isPdfPage: true, page: describe() };
  }
  // a security check: many clear by themselves in a few seconds (wait for that); the rest need the person
  for (let i = 0; i < 20 && challenge(); i++) await sleep(1000);
  if (challenge()) return { status: "captcha", host, page: describe() };

  const doi = cleanDoi(meta("citation_doi") || meta("dc.identifier") || meta("prism.doi") ||
                       ((location.pathname.match(/\/doi\/(?:\w+\/)?(10\.\d{4,9}\/[^?#]+)/) || [])[1] || ""));
  if (expectDoi && doi && doi.startsWith("10.") && doi !== cleanDoi(expectDoi)) return { status: "other-page", doi, host, page: describe() };

  // the article as the page shows it (publishers show the full text when you have access); pages built by scripts
  // may still be filling in, so look a few times
  const CONTAINERS = ["#body", ".Body", "#bodymatter", ".article__body", ".article-section__content", ".c-article-body",
    "#article-body", ".article-body", ".hlFld-Fulltext", ".fulltext-view", ".article-full-text", "#maincontent .article",
    "[itemprop=articleBody]", ".section-paragraph", "#article-content", ".article-content", "article", "main", "[role=main]", "#content"];
  const SECTION = /^\s*(\d+(\.\d+)*\.?\s*)?(introduction|background|related work|methods?|methodology|materials and methods|experimental( section)?|theory|model|results|results and discussion|discussion|conclusions?|summary|literature review)\b/i;
  let best = "", bestHeads = 0;
  for (let tries = 0; tries < 4; tries++) {
    for (const q of CONTAINERS) {
      for (const el of document.querySelectorAll(q)) {
        const t = el.innerText || "";
        if (t.length <= best.length) continue;
        best = t;
        bestHeads = [...el.querySelectorAll("h2,h3,h4,.section-title,.c-article-section__title")]
          .filter((x) => SECTION.test(x.innerText || "")).length;
      }
    }
    if (best.length > 6000 && (bestHeads >= 2 || best.length > 25000)) {
      return { status: "ok", how: "page", text: best.slice(0, 600000), doi, host };
    }
    if (tries < 3) await sleep(1500);
  }
  // a web page (not a paper): its readable text is what there is to keep
  if (kind === "webpage" && best.length > 1500) return { status: "ok", how: "page", text: best.slice(0, 600000), host };

  // PDFs: the page's citation_pdf_url, its links or buttons that say PDF, and where the platform keeps it
  const links = [];
  const add = (u) => {
    try {
      const x = new URL(u, location.href);
      if (/^https?:$/.test(x.protocol) && !links.includes(x.href)) links.push(x.href);
    } catch (e) { /* not a link */ }
  };
  if (meta("citation_pdf_url")) add(meta("citation_pdf_url"));
  const PDF_HREF = /\.pdf(\?|#|$)|\/pdf(ft|direct|plus)?(\/|\?|$)|\/epdf\/|\/doi\/(pdf|epdf|pdfdirect)\/|\/content\/pdf\/|\/articlepdf\/|stamp\.jsp|getpdf|download.{0,20}pdf|pdf.{0,20}download/i;
  const PDF_LABEL = /^(download|view|get|open|full[- ]text|article|read)?\s*(the )?(article )?pdf\b|\bpdf\s*(download|\(\d|$)/i;
  let button = false;
  for (const el of document.querySelectorAll("a, button, [role=button]")) {
    const label = (el.innerText || el.getAttribute("aria-label") || el.title || "").replace(/\s+/g, " ").trim();
    const href = el.getAttribute("href") || "";
    if (!(PDF_HREF.test(href) || (label.length < 60 && PDF_LABEL.test(label)))) continue;
    if (href && !/^(javascript:|#)/i.test(href)) add(href);
    else if (!button) { el.setAttribute("data-citrello-pdf", "1"); button = true; }
  }
  for (const u of publisherPdfUrls(location.href, doi)) add(u);
  for (const u of links.slice(0, 8)) {
    const pdf = await tryPdf(u);
    if (pdf) return { status: "ok", how: "pdf", pdf, doi, host };
  }
  const notPaper = kind === "webpage" || (!doi && !meta("citation_title") && links.length === 0 && !button);
  return { status: notPaper ? "not-paper" : "no-access", doi, host, pdfLinks: links.slice(0, 8), pdfButton: button,
           page: describe({ pdfLinks: links.slice(0, 8).map((x) => x.replace(/[?#].*$/, "")), pdfButton: button, articleChars: best.length }) };
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
