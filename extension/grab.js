// Runs inside a paper's page (injected by the extension), with the person's own access to it.
// grabPaper: the paper's text, from the article shown on the page, or else the PDF the page links to. Only text
// leaves the page in the end: Citrello keeps the text and never the PDF.
// pageInfo: what the page says about the paper (citation <meta> tags), for the "Add to Citrello" pop-up.

/* exported grabPaper, pageInfo */
async function grabPaper(expectDoi) {
  const meta = (name) => {
    const el = document.querySelector(`meta[name="${name}" i], meta[property="${name}" i]`);
    return el ? (el.content || "").trim() : "";
  };
  const cleanDoi = (d) => (d || "").toLowerCase().replace(/^https?:\/\/(dx\.)?doi\.org\//, "").replace(/^doi:\s*/, "").trim();
  const doi = cleanDoi(meta("citation_doi") || meta("dc.identifier") || meta("prism.doi"));
  if (expectDoi && doi && doi.startsWith("10.") && doi !== cleanDoi(expectDoi)) {
    return { status: "other-page", doi, host: location.hostname };
  }
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
    return { status: "ok", how: "page", text: best.slice(0, 600000), doi, host: location.hostname };
  }
  // or the PDF the page links to, fetched the way the page's own download link would be
  const pdf = meta("citation_pdf_url");
  if (pdf) {
    try {
      const r = await fetch(pdf, { credentials: "include" });
      if (r.ok) {
        const buf = await r.arrayBuffer();
        const head = new TextDecoder().decode(new Uint8Array(buf.slice(0, 5)));
        if (head === "%PDF-" && buf.byteLength < 40e6) {
          const bytes = new Uint8Array(buf);
          let bin = "";
          for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
          return { status: "ok", how: "pdf", pdf: btoa(bin), doi, host: location.hostname };
        }
      }
    } catch (e) { /* the PDF is elsewhere and can't be fetched from here */ }
  }
  return { status: "no-access", doi, host: location.hostname };
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
  return { url: location.href, title: document.title.slice(0, 300), meta: m, sel: String(window.getSelection()).slice(0, 2000) };
}
