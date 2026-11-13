// Citrello for Word: the add-in's panel (citrello.app/word/taskpane).
// Citrello's own Python runs here (the web app's engine, /app/worker.js?mode=word, with its own storage), so
// formatting citations in any of ~10,900 styles works exactly as in the app. The library comes from the person's
// Citrello cloud copy (signed in); the add-in only reads it.
//
// In the document, each citation is a content control tagged CITRELLO_CITATION_v1_<base64 JSON> holding the papers'
// details (like Mendeley Cite), so the document keeps working on any computer and Citrello's "Check and fix" reads
// it. The reference list is a content control tagged CITRELLO_BIBLIOGRAPHY. The style is a document setting.
(function () {
  "use strict";
  const TAG = "CITRELLO_CITATION_v1_";
  const BIB = "CITRELLO_BIBLIOGRAPHY";
  const params = new URLSearchParams(location.search);
  const LOCAL = ["localhost", "127.0.0.1"].includes(location.hostname);
  const bridgeParam = LOCAL ? params.get("bridge") : null;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const show = (id, on) => $(id).classList.toggle("hidden", !on);
  const say = (text) => { $("status").textContent = text || ""; };

  // ---------- the engine ----------
  let worker, nextId = 0;
  const pending = new Map();
  let readyResolve, readyReject;
  const ready = new Promise((res, rej) => { readyResolve = res; readyReject = rej; });
  ready.catch(() => {});
  function startEngine() {
    const q = new URLSearchParams({ mode: "word" });
    if (bridgeParam) q.set("bridge", bridgeParam);
    worker = new Worker("/app/worker.js?" + q);
    worker.onmessage = (ev) => {
      const m = ev.data;
      if (m.type === "status") $("loadingText").textContent = m.text;
      else if (m.type === "ready") readyResolve();
      else if (m.type === "error") { $("loadingText").textContent = m.text; readyReject(new Error(m.text)); }
      else if (m.type === "response" && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
      else if (m.type === "progress" && m.body) onProgress(m.path, m.body);
    };
    worker.onerror = (e) => { $("loadingText").textContent = "Citrello couldn't start: " + (e.message || "unknown error"); readyReject(e); };
  }
  async function api(method, path, body, extraHeaders) {
    await ready;
    const raw = body === undefined ? null : body instanceof ArrayBuffer ? new Uint8Array(body)
      : new TextEncoder().encode(JSON.stringify(body));
    const m = await new Promise((resolve) => {
      const id = ++nextId;
      pending.set(id, resolve);
      const headers = raw && !(body instanceof ArrayBuffer) ? { "Content-Type": "application/json" } : {};
      worker.postMessage({ type: "request", id, method, path, headers: { ...headers, ...(extraHeaders || {}) }, body: raw });
    });
    const text = new TextDecoder().decode(m.body);
    let data = text;
    if ((m.ctype || "").includes("json")) { try { data = JSON.parse(text); } catch { data = {}; } }
    if (m.status >= 400) throw new Error((data && data.error) || `Citrello couldn't do that (${m.status}).`);
    return data;
  }
  async function apiRaw(method, path) {  // the answer's bytes and headers (the cloud copy)
    await ready;
    return new Promise((resolve) => {
      const id = ++nextId;
      pending.set(id, resolve);
      worker.postMessage({ type: "request", id, method, path, headers: {}, body: null });
    });
  }
  const get = (path) => api("GET", path);
  const post = (path, body) => api("POST", path, body ?? {});

  // ---------- signing in, and the library from the cloud copy ----------
  let session = null;
  async function bridgeCall(path) {
    return fetch(session.bridge + path, { headers: { Authorization: "Bearer " + session.token } });
  }
  async function pullLibrary(quiet) {
    session = await get("/api/ai/session");
    if (!session.signed_in) return false;
    try {
      const r = await bridgeCall("/api/cloud");
      const info = await r.json().catch(() => ({}));
      const st = await get("/api/web/state");
      if (!r.ok || !info.ready) libInfo(st.items, "Your library couldn't be fetched from Citrello right now.");
      else if (!info.exists) libInfo(st.items, "No cloud copy yet: open Citrello signed in (citrello.app/app) and your library comes here.");
      else if (!st.items || info.saved_at > (st.cloud_at || 0)) {
        if (!quiet) say("Getting your library…");
        const d = await bridgeCall("/api/cloud/data");
        if (!d.ok) throw new Error("Your library couldn't be downloaded.");
        const res = await post("/api/web/cloud-restore", await d.arrayBuffer());
        libInfo(res.items);
        if (!quiet) say("");
        search();
      } else libInfo(st.items);
    } catch (e) {
      say(e.message);
    }
    return true;
  }
  function libInfo(n, note) {
    $("libInfo").innerHTML = `${n || 0} references in your library · ${esc(session && session.email || "")} ` +
      `<button class="link" id="signout">Sign out</button>` + (note ? `<br>${esc(note)}` : "");
    $("signout").onclick = signOut;
  }
  async function signOut() {
    try { await post("/api/account/signout"); } catch {}
    show("main", false); show("stylePick", false); show("signin", true);
  }
  function siMsg(text, kind) { $("siMsg").innerHTML = text ? `<div class="notice ${kind || "err"}">${esc(text)}</div>` : ""; }
  $("siSend").onclick = async () => {
    const email = $("siEmail").value.trim();
    if (!/^\S+@\S+\.\S+$/.test(email)) return siMsg("Type your email address.");
    $("siSend").disabled = true; siMsg("");
    try {
      await post("/api/account/start", { email });
      $("siSent").textContent = `We emailed a code to ${email}. Type it here (check spam if it doesn't arrive).`;
      show("siStep1", false); show("siStep2", true); $("siCode").focus();
    } catch (e) { siMsg(e.message); } finally { $("siSend").disabled = false; }
  };
  $("siBack").onclick = () => { show("siStep2", false); show("siStep1", true); siMsg(""); };
  $("siVerify").onclick = async () => {
    $("siVerify").disabled = true; siMsg("");
    try {
      await post("/api/account/verify", { email: $("siEmail").value.trim(), code: $("siCode").value.trim() });
      await openMain();
    } catch (e) { siMsg(e.message); } finally { $("siVerify").disabled = false; }
  };
  $("siCode").addEventListener("keydown", (e) => { if (e.key === "Enter") $("siVerify").click(); });
  $("siEmail").addEventListener("keydown", (e) => { if (e.key === "Enter") $("siSend").click(); });

  async function openMain() {
    show("signin", false); show("main", true); show("stylePick", true);
    await loadStyles();
    await pullLibrary();
    search();
    checkDocument().catch((e) => docSay(e.message, "err"));
    loadQuota();
    setInterval(() => pullLibrary(true), 5 * 60 * 1000);  // new references added in Citrello arrive by themselves
  }

  // ---------- style (stored in the document) ----------
  const settings = () => Office.context.document.settings;
  let style = "apa";
  async function loadStyles() {
    const d = await get("/api/styles");
    style = settings().get("citrelloStyle") || d.default || "apa";
    const sel = $("style");
    const opts = d.styles.map((s) => [s.id, s.title]);
    if (!opts.some(([id]) => id === style)) opts.unshift([style, style]);
    sel.innerHTML = opts.map(([id, t]) => `<option value="${esc(id)}">${esc(t)}</option>`).join("") +
      '<option value="__other">Another style…</option>';
    sel.value = style;
    sel.onchange = async () => {
      if (sel.value === "__other") {
        const q = prompt("Which style? Type a journal or style name (for example: Nature, Chicago, Harvard).");
        const found = q ? (await get("/api/styles/search?q=" + encodeURIComponent(q))).styles || [] : [];
        if (!found.length) { if (q) say(`No style found for “${q}”.`); sel.value = style; return; }
        const pick = found[0];
        if (![...sel.options].some((o) => o.value === pick.id)) sel.insertAdjacentHTML("afterbegin", `<option value="${esc(pick.id)}">${esc(pick.title)}</option>`);
        sel.value = pick.id;
      }
      style = sel.value;
      settings().set("citrelloStyle", style);
      settings().saveAsync(() => {});
      await refresh(true);
    };
  }

  // ---------- searching the library ----------
  let lastResults = [];
  let searchTimer = null;
  $("q").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(search, 200); });
  async function search() {
    if (!session || !session.signed_in) return;
    const q = $("q").value.trim();
    const d = await get("/api/library?project=*&q=" + encodeURIComponent(q));
    lastResults = d.items.slice(0, 40);
    renderResults($("results"), lastResults);
    $("empty").textContent = !d.total ? "Your library is empty. Add references in Citrello (citrello.app/app) and they appear here."
      : !d.items.length ? `Nothing in your library matches “${q}”.` : d.items.length > 40 ? `Showing 40 of ${d.items.length}. Type more to narrow it down.` : "";
  }
  function who(it) { return `${esc(it.authors || "No author")}${it.year ? " (" + esc(it.year) + ")" : ""}`; }
  function renderResults(ul, items, opts = {}) {
    const verb = current ? "Add" : "Cite";
    ul.innerHTML = items.map((it) => `<li><div><span class="who">${who(it)}</span><span class="t">${esc(it.title)}</span></div>` +
      `<button data-key="${esc(it.key)}" title="${verb === "Add" ? "Add this paper to the citation you're in" : "Insert a citation at the cursor"}">${verb}</button></li>`).join("");
    ul.querySelectorAll("button[data-key]").forEach((b) => { b.onclick = () => cite([b.dataset.key], opts); });
  }

  // ---------- citations in the document ----------
  const b64 = (obj) => { const bytes = new TextEncoder().encode(JSON.stringify(obj)); let s = ""; bytes.forEach((x) => { s += String.fromCharCode(x); }); return btoa(s); };
  const unb64 = (s) => { try { return JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(s), (c) => c.charCodeAt(0)))); } catch { return null; } };
  const newId = () => Math.random().toString(36).slice(2, 10);
  const payloadOf = (tag) => (tag && tag.startsWith(TAG) ? unb64(tag.slice(TAG.length)) : null);

  async function itemsFor(keys) { return (await post("/api/word-addin/items", { keys })).items; }

  // Insert a citation at the cursor, or add the papers to the citation the cursor is in
  async function cite(keys, opts = {}) {
    say("");
    const items = await itemsFor(keys);
    if (!items.length) return say("That reference isn't in your library any more.");
    try {
      await Word.run(async (ctx) => {
        const sel = ctx.document.getSelection();
        const inside = sel.parentContentControlOrNullObject;
        inside.load("tag");
        await ctx.sync();
        const p = !inside.isNullObject && payloadOf(inside.tag);
        if (p) {  // add to this citation (no duplicates)
          const have = new Set(p.citationItems.map((ci) => ci.id));
          items.filter((it) => !have.has(it.key)).forEach((it) => p.citationItems.push({ id: it.key, itemData: it.csl }));
          inside.tag = TAG + b64(p);
        } else {
          let at = sel;
          if (opts.replaceTail) {  // "Wang et al. (" typed: the citation takes the "(" and what follows
            const hits = opts.replaceTail.range.search("(", { matchCase: true });
            hits.load("items");
            await ctx.sync();
            if (hits.items.length) at = hits.items[hits.items.length - 1].expandTo(sel.getRange("Start"));
          }
          const payload = { citationID: newId(), citationItems: items.map((it) => ({ id: it.key, itemData: it.csl,
            ...(opts.narrative ? { "suppress-author": true } : {}) })), properties: { noteIndex: 0 } };
          if (opts.space) at = at.insertText(" ", opts.replaceTail ? "Replace" : "End");
          const target = opts.space ? at.getRange("End") : at;
          const cc = target.insertContentControl();
          cc.tag = TAG + b64(payload);
          cc.title = "Citrello citation";
          cc.appearance = "BoundingBox";
          cc.insertText("[citation]", "Replace");
          cc.getRange("After").select();  // keep writing after it
        }
        await ctx.sync();
      });
      await refresh();
      await updateContext();
    } catch (e) { say("Couldn't insert the citation: " + e.message); }
  }

  // Every Citrello citation in the document, in order
  async function readCitations(ctx) {
    const ccs = ctx.document.contentControls;
    ccs.load("items/tag,items/text,items/id");
    await ctx.sync();
    const cits = [], bibs = [];
    for (const cc of ccs.items) {
      if (cc.tag === BIB) bibs.push(cc);
      else { const p = payloadOf(cc.tag); if (p) cits.push({ cc, p }); }
    }
    return { cits, bibs };
  }

  // HTML from Citrello's formatter -> runs of text with italic/bold/superscript, written into the control so the
  // text keeps the document's own font and size
  function runsOf(html) {
    const doc = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html");
    const out = [];
    (function walk(node, f) {
      for (const n of node.childNodes) {
        if (n.nodeType === 3) { if (n.nodeValue) out.push({ t: n.nodeValue, ...f }); continue; }
        if (n.nodeType !== 1) continue;
        const tag = n.tagName.toLowerCase(), st = (n.getAttribute("style") || "").toLowerCase();
        walk(n, { i: f.i || tag === "i" || tag === "em" || /font-style:\s*italic/.test(st),
                  b: f.b || tag === "b" || tag === "strong" || /font-weight:\s*bold/.test(st),
                  sup: f.sup || tag === "sup", sub: f.sub || tag === "sub",
                  sc: f.sc || /small-caps/.test(st) });
      }
    })(doc.body.firstChild, {});
    return out;
  }
  const plain = (runs) => runs.map((r) => r.t).join("");
  const sameText = (a, b) => a.replace(/\r/g, "\n").trim() === b.replace(/\r/g, "\n").trim();
  function writeRuns(cc, runs) {
    cc.clear();
    for (const r of runs) {
      const rg = cc.insertText(r.t, "End");
      rg.font.italic = !!r.i; rg.font.bold = !!r.b; rg.font.superscript = !!r.sup; rg.font.subscript = !!r.sub;
      if (r.sc) rg.font.smallCaps = true;
    }
  }

  // Re-format every citation (numbers change when citations move) and the reference list
  let refreshing = null;
  async function refresh(all) {
    if (refreshing) return refreshing;
    refreshing = (async () => {
      try {
        await Word.run(async (ctx) => {
          const { cits, bibs } = await readCitations(ctx);
          if (!cits.length && !bibs.length) return;
          const citations = cits.map(({ p }) => ({ items: p.citationItems.map((ci) => ({ key: ci.id, csl: ci.itemData,
            suppress: !!ci["suppress-author"], locator: ci.locator || "", label: ci.label || "page" })) }));
          const out = await post("/api/word-addin/render", { style, citations });
          let changed = 0;
          cits.forEach(({ cc }, i) => {
            const runs = runsOf(out.citations[i] || "");
            if (all || !sameText(cc.text, plain(runs))) { writeRuns(cc, runs.length ? runs : [{ t: "[no reference]" }]); changed++; }
          });
          const bibRuns = [];
          out.bibliography.forEach((h, i) => { if (i) bibRuns.push({ t: "\n" }); bibRuns.push(...runsOf(h)); });
          for (const cc of bibs) {
            if (all || !sameText(cc.text, plain(bibRuns))) writeRuns(cc, bibRuns.length ? bibRuns : [{ t: "(No citations yet: the reference list fills in as you cite.)" }]);
          }
          await ctx.sync();
          if (all) say(`${cits.length} citation${cits.length === 1 ? "" : "s"} in ${out.style && out.style.title || style}.`);
        });
      } catch (e) { say("Couldn't update the citations: " + e.message); }
      finally { refreshing = null; }
    })();
    return refreshing;
  }
  $("refresh").onclick = () => refresh(true);

  $("bib").onclick = async () => {
    try {
      await Word.run(async (ctx) => {
        const { bibs } = await readCitations(ctx);
        if (bibs.length) { bibs[0].select(); await ctx.sync(); say("The reference list is already in the document: it's updated."); return; }
        const cc = ctx.document.getSelection().insertContentControl();
        cc.tag = BIB;
        cc.title = "Citrello reference list";
        cc.insertText("[reference list]", "Replace");
        await ctx.sync();
        say("Reference list inserted. It updates as you cite.");
      });
      await refresh();
    } catch (e) { say("Couldn't insert the reference list: " + e.message); }
  };

  // ---------- what's at the cursor: the citation you're in, or the author you just typed ----------
  let current = null;  // { tag, payload } of the citation the cursor is in
  const AUTHOR_RE = /(?:^|[^\p{L}])(\p{Lu}[\p{L}'’-]+)(?:(\s+et\s+al\.?)|\s+(?:and|&)\s+(\p{Lu}[\p{L}'’-]+))?(\s*\(\s*(\d{4})?\s*)?$/u;
  function typedAuthor(textBefore) {
    const t = textBefore.slice(-90).replace(/\s+$/, (sp) => (sp.length ? " " : ""));
    const m = AUTHOR_RE.exec(t.replace(/\s+$/, ""));
    if (!m) return null;
    const [whole, first, etal, second, paren, year] = m;
    const before = t.slice(0, t.length - whole.length + (whole.startsWith(first) ? 0 : 1)).replace(/\s+$/, "");
    // a lone capitalised word only counts mid-sentence ("as shown by Wang"), not "The" at a sentence's start
    if (!etal && !second && !paren && (!before || /[.!?:]$/.test(before))) return null;
    if (/^(The|This|These|That|Those|In|On|At|We|It|Our|Table|Figure|Fig|Section|Chapter|Equation)$/.test(first)) return null;
    return { first, second: second || "", year: year || "", paren: !!paren, label: whole.trim(), endsWithSpace: /\s$/.test(textBefore) };
  }

  async function updateContext() {
    let ctxInfo = null;
    try {
      await Word.run(async (ctx) => {
        const sel = ctx.document.getSelection();
        const inside = sel.parentContentControlOrNullObject;
        inside.load("tag");
        const para = sel.paragraphs.getFirst();
        const before = para.getRange("Start").expandTo(sel.getRange("Start"));
        before.load("text"); para.load("text");
        await ctx.sync();
        ctxInfo = { tag: inside.isNullObject ? null : inside.tag, before: before.text || "", range: before };
        const p = ctxInfo.tag && payloadOf(ctxInfo.tag);
        const same = current && p && current.payload.citationID === p.citationID;
        current = p ? { tag: ctxInfo.tag, payload: p, paragraph: para.text || "", at: (before.text || "").length } : null;
        if (!same) claimShown = null;
      });
    } catch { current = null; }
    renderCurrent();
    renderResults($("results"), lastResults);
    if (!current && ctxInfo) await suggest(ctxInfo);
    else show("suggest", false);
  }

  async function renderCurrent() {
    show("current", !!current);
    if (!current) return;
    renderClaim();
    const its = current.payload.citationItems;
    $("curList").innerHTML = its.map((ci, i) => {
      const c = ci.itemData || {};
      const a = (c.author || []).map((x) => x.family || x.literal).filter(Boolean);
      const names = (c.author || []).map((x) => x.literal || [x.given, x.family].filter(Boolean).join(" ")).filter(Boolean);
      const au = a.length > 2 ? a[0] + " et al." : a.join(" & ");
      const y = (((c.issued || {})["date-parts"] || [[]])[0] || [])[0] || "";
      return `<li><div><span class="who">${esc(au || "No author")}${y ? " (" + esc(y) + ")" : ""}</span>` +
        `<span class="t">${esc(c.title || "")}</span>` +
        `<details><summary>Details</summary><div class="small">` +
        `${names.length ? esc(names.join(", ")) + "<br>" : ""}${c["container-title"] ? "<i>" + esc(c["container-title"]) + "</i>" : ""}` +
        `${c.volume ? ", " + esc(c.volume) : ""}${c.issue ? "(" + esc(c.issue) + ")" : ""}${c.page ? ", " + esc(c.page) : ""}` +
        `${c.DOI ? `<br><a href="https://doi.org/${esc(c.DOI)}" target="_blank" rel="noopener">doi.org/${esc(c.DOI)}</a>` : ""}` +
        `${!c.DOI && c.URL ? `<br><a href="${esc(c.URL)}" target="_blank" rel="noopener">${esc(c.URL)}</a>` : ""}` +
        `<label class="chk"><input type="checkbox" data-sup="${i}" ${ci["suppress-author"] ? "checked" : ""}> Leave the author's name out (I wrote it in the sentence)</label>` +
        `<label class="row">Page <input type="text" data-loc="${i}" value="${esc(ci.locator || "")}" placeholder="e.g. 12–14" style="width:110px"></label>` +
        `</div></details></div><button data-rm="${i}" title="Remove this paper from the citation">Remove</button></li>`;
    }).join("");
    $("curList").querySelectorAll("[data-rm]").forEach((b) => { b.onclick = () => editCurrent((p) => p.citationItems.splice(+b.dataset.rm, 1)); });
    $("curList").querySelectorAll("[data-sup]").forEach((b) => { b.onchange = () => editCurrent((p) => { const ci = p.citationItems[+b.dataset.sup]; if (b.checked) ci["suppress-author"] = true; else delete ci["suppress-author"]; }); });
    $("curList").querySelectorAll("[data-loc]").forEach((b) => { b.onchange = () => editCurrent((p) => { const ci = p.citationItems[+b.dataset.loc]; if (b.value.trim()) { ci.locator = b.value.trim(); ci.label = "page"; } else { delete ci.locator; delete ci.label; } }); });
  }

  // Change the citation the cursor is in (no papers left: the citation goes)
  async function editCurrent(change) {
    if (!current) return;
    try {
      await Word.run(async (ctx) => {
        const sel = ctx.document.getSelection();
        const cc = sel.parentContentControlOrNullObject;
        cc.load("tag");
        await ctx.sync();
        const p = !cc.isNullObject && payloadOf(cc.tag);
        if (!p) return;
        change(p);
        if (!p.citationItems.length) { cc.getRange("After").select(); cc.delete(false); current = null; }
        else cc.tag = TAG + b64(p);
        await ctx.sync();
      });
      await refresh();
      await updateContext();
    } catch (e) { say("Couldn't change the citation: " + e.message); }
  }
  $("curDelete").onclick = () => editCurrent((p) => { p.citationItems.length = 0; });

  let lastSuggestFor = "";
  async function suggest(info) {
    const a = typedAuthor(info.before);
    if (!a) { show("suggest", false); lastSuggestFor = ""; return; }
    const sig = [a.first, a.second, a.year].join("|");
    if (sig === lastSuggestFor && !$("suggest").classList.contains("hidden")) return;
    lastSuggestFor = sig;
    const d = await get(`/api/word-addin/suggest?first=${encodeURIComponent(a.first)}&second=${encodeURIComponent(a.second)}&year=${encodeURIComponent(a.year)}`);
    if (!d.items.length) { show("suggest", false); return; }
    $("sugTitle").textContent = `You wrote “${a.label}” – cite ${d.items.length === 1 ? "this paper" : "one of these"}?`;
    // "Wang et al." then Cite gives "Wang et al. (2020)": the name is already in the sentence
    renderResults($("sugList"), d.items.slice(0, 6), { narrative: !a.paren, replaceTail: a.paren ? { range: info.range } : null,
      space: !a.paren && !a.endsWithSpace });
    show("suggest", true);
  }

  let selTimer = null;
  function onSelection() { clearTimeout(selTimer); selTimer = setTimeout(() => updateContext().catch(() => {}), 350); }

  // ---------- this document: other citations become Citrello's, and its references are checked ----------
  // Word gives the panel a copy of the file; Citrello's "Check and fix" reads every kind of citation in it (Zotero,
  // Mendeley, EndNote, Word's own, typed, the web app's) and checks the references online. Converting keeps the
  // text: each citation is wrapped in Citrello's content control (docconvert.py), then the whole document is
  // formatted in the chosen style with Track Changes on, so every change can be accepted or rejected.
  let docCheck = null, docPlan = null, docReport = null, docBusy = false, docFixes = null, docNotes = [];
  const sigOf = (t) => String(t || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^0-9a-z]+/g, "");
  function docSay(html, kind) {
    show("doc", true);
    $("docBody").innerHTML = kind ? `<div class="notice ${kind}">${html}</div>` : html;
  }
  function onProgress(path, body) {
    if (!docBusy || path !== "/api/doccheck/" + docCheck) return;
    try {
      const st = JSON.parse(body);
      if (st.step) docSay(`<p class="small"><span class="spinner"></span>${esc(st.step)}${st.n ? ` (${st.i} of ${st.n})` : ""}…</p>`);
    } catch {}
  }
  function readFile() {  // the document as a .docx, from Word
    return new Promise((resolve, reject) => {
      Office.context.document.getFileAsync(Office.FileType.Compressed, { sliceSize: 4194304 }, (r) => {
        if (r.status !== "succeeded") return reject(new Error("Word didn't give Citrello a copy of the document."));
        const f = r.value, parts = [];
        const next = (i) => f.getSliceAsync(i, (sl) => {
          if (sl.status !== "succeeded") { f.closeAsync(); return reject(new Error("Word didn't give Citrello a copy of the document.")); }
          parts.push(new Uint8Array(sl.value.data));
          if (i + 1 < f.sliceCount) return next(i + 1);
          f.closeAsync();
          const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
          let at = 0;
          parts.forEach((p) => { out.set(p, at); at += p.length; });
          resolve(out.buffer);
        });
        next(0);
      });
    });
  }
  async function checkDocument() {
    if (docBusy) return;
    docBusy = true; docPlan = docReport = null;
    docSay('<p class="small"><span class="spinner"></span>Reading this document…</p>');
    try {
      const file = await readFile();
      docCheck = (await api("POST", "/api/doccheck", file, { "X-Filename": "document.docx" })).id;
      let st = await get("/api/doccheck/" + docCheck);  // answered once the check has run
      while (st.state === "checking") { await new Promise((r) => setTimeout(r, 1000)); st = await get("/api/doccheck/" + docCheck); }
      if (st.state === "error") {
        docBusy = false;
        if (/No references found/.test(st.error || "")) return docSay('<p class="small muted">No citations in this document yet. Cite from your library below.</p>');
        return docSay(esc(st.error), "err");
      }
      docReport = st.report;
      docPlan = await post("/api/word-addin/plan", { id: docCheck });
      docFixes = await post("/api/word-addin/fixes", { id: docCheck });   // for citations that are Citrello's already
      docNotes = (await post("/api/word-addin/notes", { id: docCheck })).notes;  // what only the writer can fix
    } finally { docBusy = false; }
    renderDoc();
  }
  // what converting fixes by itself: duplicates become one reference, the reference list is rebuilt from the citations
  const FIXED_BY_CONVERTING = /listed more than once|reference list has \d+ entries|Refresh the bibliography|No reference list|Listed twice|^Title differs|^Authors differ|^Year differs|was not found; the paper was found by its title/i;
  function problemsHtml() {
    const r = docReport, converted = docPlan && docPlan.converted;
    let fixed = 0;
    const keep = (text) => { if (converted && FIXED_BY_CONVERTING.test(text)) { fixed++; return false; } return true; };
    const issues = r.citation_issues.filter((i) => i.level !== "info" && keep(i.text)).map((i) => ({ level: i.level, text: i.text }));
    for (const ref of r.refs) {
      if (ref.reliability !== "problem" && ref.reliability !== "check") continue;
      const left = (ref.checks || []).filter((c) => keep(c.text));
      if (!left.length && (ref.checks || []).length) continue;
      const what = left.map((c) => c.text).join(" ");
      issues.push({ level: ref.reliability, text: `[${ref.number}] ${ref.authors || ""}${ref.year ? " (" + ref.year + ")" : ""}: ${what || ref.title}` });
    }
    const fixedNote = fixed ? '<p class="small" style="color:var(--ok)">✓ Fixed while converting: duplicate references merged into one, and the reference list rebuilt from the citations.</p>' : "";
    const online = fixedNote + (r.online ? "" : '<p class="small muted">Couldn\'t check the references online (no internet?): only the document itself was checked.</p>');
    if (!issues.length && fixed) return online;
    if (!issues.length) return `<p class="small" style="color:var(--ok)">✓ No problems found in the ${r.references} references${r.online ? " (checked online)" : ""}.</p>` + online;
    const n = issues.length;
    return `<details ${n <= 6 ? "open" : ""}><summary>${n} thing${n === 1 ? "" : "s"} to look at</summary><ul class="issues">` +
      issues.map((i) => `<li class="lvl-${i.level === "problem" ? "problem" : "check"}">${esc(i.text)}</li>`).join("") + "</ul></details>" + online;
  }
  function renderDoc(done) {
    const p = docPlan;
    if (!p) return;
    let html = "";
    if (done) html += `<div class="notice ok">${done}</div>`;
    if (p.citations) {
      const by = Object.entries(p.by).map(([k, v]) => `${esc(k)} (${v})`).join(", ");
      html += `<p class="big"><b>${p.citations} citation${p.citations === 1 ? "" : "s"}</b> made with ${by} can become Citrello citations, so you can edit them here.` +
        `${p.bibliography && p.bibliography.kind !== "done" ? " The reference list becomes Citrello's too." : ""}</p>` +
        `<label class="chk"><input type="checkbox" id="docTrack" checked> Show the changes as tracked changes (accept or reject them in Review)</label>` +
        `<div class="row"><button class="primary" id="docConvert">Make them Citrello citations</button></div>`;
    } else if (p.already) {
      html += `<p class="small">All ${p.already} citation${p.already === 1 ? " is a Citrello citation" : "s are Citrello citations"}.</p>`;
    }
    const fixN = docFixes ? Object.keys(docFixes.changes || {}).length : 0;
    if (!p.citations && fixN) {
      const parts = [docFixes.corrected && `${docFixes.corrected} paper${docFixes.corrected === 1 ? "'s" : "s'"} details corrected from what was found online`,
                     docFixes.merged && `${docFixes.merged} duplicate${docFixes.merged === 1 ? "" : "s"} merged into one reference`].filter(Boolean);
      html += `<p class="big"><b>Citrello can fix ${fixN === 1 ? "one thing" : `${fixN} things`}:</b> ${parts.join(", ")}, and the reference list ${p.bibliography ? "rebuilt" : "added at the end of the document"}.</p>` +
        `<label class="chk"><input type="checkbox" id="docTrack" checked> Show the changes as tracked changes (accept or reject them in Review)</label>` +
        `<div class="row"><button class="primary" id="docFix">Fix them</button></div>`;
    }
    html += problemsHtml();
    if (docNotes.length && !p.citations) html += `<div class="row"><button id="docNotes">Add what needs you as comments</button></div>` +
      `<p class="small muted">${docNotes.length} comment${docNotes.length === 1 ? "" : "s"} in the text, next to each problem, so you can find them (Review → Next).</p>`;
    html += '<div class="row"><button class="link" id="docAgain">Check again</button></div>';
    docSay(html);
    if ($("docConvert")) $("docConvert").onclick = () => convertDoc().catch((e) => { docBusy = false; renderDoc(); say("Couldn't convert: " + e.message); });
    $("docAgain").onclick = () => checkDocument().catch((e) => docSay(esc(e.message), "err"));
    if ($("docFix")) $("docFix").onclick = () => fixDoc().catch((e) => { docBusy = false; renderDoc(); say("Couldn't fix: " + e.message); });
    if ($("docNotes")) $("docNotes").onclick = () => notesDoc().catch((e) => { docBusy = false; renderDoc(); say("Couldn't add the comments: " + e.message); });
  }

  // No reference list in the document: one at the end, under a "References" heading (as a tracked change when on)
  async function ensureBibliography(ctx) {
    const { cits, bibs } = await readCitations(ctx);
    if (bibs.length || !cits.length) return false;
    const body = ctx.document.body;
    const h = body.insertParagraph("References", "End");
    h.styleBuiltIn = "Heading1";
    const cc = body.insertParagraph("", "End").insertContentControl();
    cc.tag = BIB;
    cc.title = "Citrello reference list";
    cc.insertText("[reference list]", "Replace");
    await ctx.sync();
    return true;
  }

  // Fix them (citations that are Citrello's already): each citation's papers get the details checked online, the same
  // paper listed twice becomes one, then everything is restyled (and the list rebuilt) with Track Changes on
  async function fixDoc() {
    if (docBusy || !docFixes) return;
    docBusy = true;
    const track = $("docTrack") && $("docTrack").checked;
    const canTrack = !!(Office.context.requirements && Office.context.requirements.isSetSupported("WordApi", "1.4"));
    const changes = docFixes.changes || {};
    let original = null, citations = 0, addedList = false;
    docSay('<p class="small"><span class="spinner"></span>Fixing…</p>');
    try {
      await Word.run(async (ctx) => {
        const doc = ctx.document;
        if (canTrack) { doc.load("changeTrackingMode"); await ctx.sync(); original = doc.changeTrackingMode; }
        const { cits } = await readCitations(ctx);
        for (const { cc, p } of cits) {
          let changed = false;
          const seen = new Set(), items = [];
          for (const ci of p.citationItems) {
            const ch = changes[ci.id];
            const next = ch ? { ...ci, id: ch.into, itemData: ch.csl } : ci;
            if (ch) changed = true;
            if (seen.has(next.id)) { changed = true; continue; }  // merged into a paper already in this citation
            seen.add(next.id); items.push(next);
          }
          if (changed) { p.citationItems = items; cc.tag = TAG + b64(p); citations++; }
        }
        if (canTrack && track) doc.changeTrackingMode = "TrackAll";
        await ctx.sync();
        addedList = await ensureBibliography(ctx);
      });
      await refresh();  // the visible changes: citations and the reference list, tracked
    } finally {
      if (canTrack && original !== null) await Word.run(async (ctx) => { ctx.document.changeTrackingMode = original; await ctx.sync(); }).catch(() => {});
      docBusy = false;
    }
    docFixes = { changes: {}, corrected: 0, merged: 0 };
    docPlan = { ...docPlan, converted: true };
    renderDoc(`Fixed: ${citations} citation${citations === 1 ? "" : "s"} updated` + (addedList ? ", and a reference list added at the end" : "") +
      (canTrack && track ? ". The changes are tracked: Review → Accept or Reject." : ".") +
      " Press Check again to see what's left.");
  }

  // What only the writer can fix, as Word comments next to each problem (a retracted paper on its citations, a typed
  // [7] with no reference 7, a paper missing from the library...), so they're easy to find in the text
  async function notesDoc() {
    if (docBusy || !docNotes.length) return;
    if (!(Office.context.requirements && Office.context.requirements.isSetSupported("WordApi", "1.4"))) {
      return say("This version of Word can't add comments from add-ins. Update Word, or see the list above.");
    }
    docBusy = true;
    let added = 0, unplaced = 0, already = 0;
    try {
      await Word.run(async (ctx) => {
        const body = ctx.document.body;
        const existing = body.getComments();
        existing.load("items/content");
        const { cits, bibs } = await readCitations(ctx);
        const have = new Set(existing.items.map((c) => (c.content || "").trim()));
        for (const n of docNotes) {
          if (have.has(n.text.trim())) { already++; continue; }
          let ranges = [];
          if (n.at.kind === "citation") ranges = cits.filter(({ p }) => p.citationItems.some((ci) => ci.id === n.at.key)).map(({ cc }) => cc.getRange("Whole"));
          else if (n.at.kind === "bibliography") ranges = bibs.slice(0, 1).map((cc) => cc.getRange("Whole"));
          else if (n.at.kind === "search" && n.at.text) {
            const hits = body.search(n.at.text.slice(0, 250), { matchCase: true });
            hits.load("items");
            await ctx.sync();
            ranges = hits.items.slice(0, 3);
          }
          if (!ranges.length) { unplaced++; continue; }
          ranges.forEach((r) => r.insertComment(n.text));
          added++;
        }
        await ctx.sync();
      });
    } finally { docBusy = false; }
    renderDoc(`${added ? `Added ${added} comment${added === 1 ? "" : "s"} in the text: in Word, Review → Next goes through them.` : "No new comments."}` +
      (already ? ` ${already} ${already === 1 ? "was" : "were"} there already.` : "") +
      (unplaced ? ` ${unplaced} couldn't be placed in the text (see the list).` : ""));
  }

  async function convertDoc() {
    if (docBusy || !docPlan) return;
    docBusy = true;
    const track = $("docTrack") && $("docTrack").checked;
    const canTrack = !!(Office.context.requirements && Office.context.requirements.isSetSupported("WordApi", "1.4"));
    let original = null, done = 0, missing = 0, addedList = false;
    docSay('<p class="small"><span class="spinner"></span>Converting the citations…</p>');
    try {
      await Word.run(async (ctx) => {
        const doc = ctx.document;
        if (canTrack) {  // the conversion itself changes no text: not tracked
          doc.load("changeTrackingMode"); await ctx.sync();
          original = doc.changeTrackingMode;
          doc.changeTrackingMode = "Off"; await ctx.sync();
        }
        const paras = doc.body.paragraphs;
        paras.load("items/text"); await ctx.sync();
        const bySig = new Map();
        paras.items.forEach((p) => { const k = sigOf(p.text); if (!bySig.has(k)) bySig.set(k, []); bySig.get(k).push(p); });
        const find = (t) => t && (bySig.get(t.sig) || [])[t.nth];
        const bib = docPlan.bibliography;  // the reference list: wrapped as it is, rewritten in the style below
        if (bib && bib.kind === "paragraphs") {
          const a = find(bib.first), b = find(bib.last);
          if (a && b) { const cc = a.getRange("Whole").expandTo(b.getRange("Whole")).insertContentControl(); cc.tag = BIB; cc.title = "Citrello reference list"; }
        } else if (bib && bib.kind === "cc") {
          const ccs = doc.contentControls; ccs.load("items/tag"); await ctx.sync();
          ccs.items.filter((c) => (c.tag || "").toUpperCase().startsWith(bib.tag)).forEach((c) => { c.tag = BIB; c.title = "Citrello reference list"; });
        }
        await ctx.sync();
        for (const t of docPlan.paragraphs) {
          const p = find(t);
          if (!p) { missing += t.count; continue; }
          const ox = p.getOoxml(); await ctx.sync();
          const r = await post("/api/word-addin/convert", { id: docCheck, ooxml: ox.value });
          if (r.count) { p.getRange("Content").insertOoxml(r.ooxml, "Replace"); await ctx.sync(); done += r.count; }
          docSay(`<p class="small"><span class="spinner"></span>Converting the citations… ${done} of ${docPlan.citations}</p>`);
        }
        if (canTrack && track) { doc.changeTrackingMode = "TrackAll"; await ctx.sync(); }
        if (done) addedList = await ensureBibliography(ctx);
      });
      await refresh();  // every citation and the list in the chosen style: these are the visible (tracked) changes
    } finally {
      if (canTrack && original !== null) await Word.run(async (ctx) => { ctx.document.changeTrackingMode = original; await ctx.sync(); }).catch(() => {});
      docBusy = false;
    }
    const notes = (docPlan.notes || []).length ? `<br>Left as they were: ${docPlan.notes.map(esc).join("; ")}` : "";
    const lost = missing ? `<br>${missing} couldn't be found again in the document (was it edited meanwhile?): press Check again.` : "";
    docPlan = { ...docPlan, citations: 0, already: (docPlan.already || 0) + done, paragraphs: [], converted: true };
    renderDoc(`Done: ${done} citation${done === 1 ? " is" : "s are"} now Citrello citations` + (addedList ? ", with a reference list added at the end" : "") +
      (canTrack && track ? ". The changes are tracked: Review → Accept or Reject." : ".") + notes + lost);
  }

  // ---------- Claude checks the citation clicked (free: 10 a month), and finds another (Pro) ----------
  // The papers' text comes from the library; when a paper has none, Citrello looks for a free copy online (it's kept
  // in the library, and the library goes back to the cloud copy so the web app has it too). Without a free copy the
  // writer can add the PDF. Claude reads the passages of each paper that matter for the sentence.
  let quota = null, claimShown = null, claimBusy = false, libChanged = false;
  const VERDICT = { supports: "✓ Supports it", partly: "≈ Partly supports it", contradicts: "✗ Says something different",
                    not_found: "? Doesn't seem to say it" };
  const cacheKey = (claim, keys) => "citrelloClaim:" + claim + "|" + keys.join(",");
  const cacheGet = (k) => { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch { return null; } };
  const cacheSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} };
  async function bridgeAi(method, path, body) {
    if (!session || !session.signed_in) session = await get("/api/ai/session");
    const r = await fetch(session.bridge + path, { method, headers: { Authorization: "Bearer " + session.token,
      ...(body ? { "Content-Type": "application/json" } : {}) }, body: body ? JSON.stringify(body) : undefined });
    const d = await r.json().catch(() => ({}));
    if (d.quota) { quota = d.quota; showQuota(); }
    if (!r.ok) { const e = new Error(d.error_description || d.error || "Claude couldn't check this right now."); e.code = d.error; throw e; }
    return d;
  }
  async function loadQuota() { try { quota = await bridgeAi("GET", "/api/ai/quota"); showQuota(); } catch {} }
  function showQuota() {
    if (!quota) return;
    const left = Math.max(0, quota.limit - quota.used);
    $("quotaLine").innerHTML = quota.pro ? `Citrello Pro · ${left.toLocaleString()} claim checks left this month`
      : `<b>${left} of ${quota.limit}</b> free claim checks left this month · <button class="link" id="quotaPro">Get Pro</button>`;
    if ($("quotaPro")) $("quotaPro").onclick = () => showPro();
  }
  function showPro(lead) {
    $("proLead").textContent = lead || "Make sure every source says what you wrote.";
    show("proCard", true);
    $("proCard").scrollIntoView({ block: "nearest" });
  }
  $("proLater").onclick = () => show("proCard", false);
  const claimSay = (html) => { $("claimOut").innerHTML = html; };
  const spin = (t) => `<p class="small"><span class="spinner"></span>${esc(t)}</p>`;
  const labelOf = (c) => {
    const a = (c.author || []).map((x) => x.family || x.literal).filter(Boolean);
    const y = (((c.issued || {})["date-parts"] || [[]])[0] || [])[0] || "";
    return `${a.length > 2 ? a[0] + " et al." : a.join(" & ") || (c.title || "").slice(0, 40)}${y ? " (" + y + ")" : ""}`;
  };

  let claimFor = null;  // the citation whose results are on screen (they stay while the cursor stays in it)
  function renderClaim() {
    showQuota();
    if (claimBusy) return;
    if (claimFor === current.payload.citationID) return;
    claimFor = current.payload.citationID;
    if (!claimShown) {  // checked before? (kept in this browser)
      const keys = current.payload.citationItems.map((ci) => ci.id);
      const hit = cacheGet(cacheKey(current.paragraph.slice(0, 400) + "@" + current.at, keys));
      claimShown = hit || null;
    }
    claimSay(claimShown ? claimHtml(claimShown) : "");
    wireClaim();
  }
  function claimHtml(r) {
    const missing = r.papers.filter((p) => p.status !== "text" && (p.doi || p.status === "abstract"));
    const access = missing.length ? `<div class="notice">${missing.length === 1 ? "One paper has" : `${missing.length} papers have`} no full text here.` +
      ` Citrello can open ${missing.length === 1 ? "it" : "them"} in your browser with your university or library access, then carry on here by itself.` +
      `<div class="row"><button class="primary" id="getAccess">Get ${missing.length === 1 ? "it" : "them"} with my access</button></div></div>` : "";
    return (r.claim ? `<p class="small muted">Checked: “${esc(r.claim)}”</p>` : "") + access + r.papers.map((p) => {
      const v = p.result;
      if (!v) {
        const why = p.status === "none" ? "No full text found online, so Claude can't check it." : "Not checked.";
        return `<div class="verdict none"><b>${esc(p.label)}: ${why}</b>` +
          `<div class="row"><button data-pdf="${esc(p.key)}">Add its PDF</button>${p.doi ? `<a class="small" href="https://doi.org/${esc(p.doi)}" target="_blank" rel="noopener">Open the paper</a>` : ""}</div></div>`;
      }
      return `<div class="verdict ${esc(v.verdict)}"><b>${esc(p.label)}: ${VERDICT[v.verdict] || esc(v.verdict)}</b>${esc(v.reason)}` +
        `${v.quote ? `<details><summary>More details</summary><q>${esc(v.quote)}</q><span class="small muted">From the paper's ${p.status === "abstract" ? "abstract" : "full text"}.</span></details>` : ""}</div>`;
    }).join("");
  }
  function wireClaim() {
    if ($("getAccess")) $("getAccess").onclick = () => getWithAccess().catch(claimFail);
    $("claimOut").querySelectorAll("[data-pdf]").forEach((b) => { b.onclick = () => pickPdf(b.dataset.pdf); });
    $("claimOut").querySelectorAll("[data-add]").forEach((b) => { b.onclick = () => useFound(b.dataset.add, b.dataset.how === "replace"); });
  }

  $("claimCheck").onclick = () => checkClaim().catch((e) => { claimBusy = false; claimFail(e); });
  function claimFail(e) {
    if (e.code === "ai_limit" || e.code === "pro_only") { claimSay(`<div class="notice warn">${esc(e.message)}</div>`); showPro(e.message); }
    else claimSay(`<div class="notice err">${esc(e.message)}</div>`);
  }
  async function checkClaim() {
    if (!current || claimBusy) return;
    claimBusy = true;
    const cit = current, items = cit.payload.citationItems;
    if (quota && !quota.pro && quota.used + items.length > quota.limit) {
      claimBusy = false;
      return showPro(`Your ${quota.limit} free claim checks this month are used up. Citrello Pro checks every citation.`);
    }
    await pullLibrary(true);  // the newest library first (papers' text added elsewhere)
    const papers = [];
    for (let i = 0; i < items.length; i++) {  // one paper at a time: its text, found online if needed
      const ci = items[i], label = labelOf(ci.itemData || {});
      claimSay(spin(`Getting the text of ${label}${items.length > 1 ? ` (${i + 1} of ${items.length})` : ""}…`));
      const t = await post("/api/word-addin/paper-text", { key: ci.id, csl: ci.itemData });
      if (t.changed) libChanged = true;
      papers.push({ key: t.key, label, status: t.status, doi: t.doi });
    }
    const readable = papers.filter((p) => p.status !== "none");
    let claim = "";
    if (readable.length) {
      claimSay(spin("Claude is reading the papers…"));
      const ci = await post("/api/word-addin/claim-item", { paragraph: cit.paragraph, at: cit.at, keys: readable.map((p) => p.key) });
      claim = ci.claim;
      if (ci.item) {
        const res = await bridgeAi("POST", "/api/ai/judge", { items: [ci.item] });
        for (const r of res.results) { const p = papers.find((x) => x.key === r.paper); if (p) p.result = r; }
      }
    }
    claimShown = { claim, papers };
    cacheSet(cacheKey(cit.paragraph.slice(0, 400) + "@" + cit.at, items.map((x) => x.id)), claimShown);
    claimBusy = false;
    if (current && current.payload.citationID === cit.payload.citationID) { claimSay(claimHtml(claimShown)); wireClaim(); }
    if (libChanged) pushLibrary().catch(() => {});
  }

  // papers behind a paywall: the web app, in the person's own browser, opens them with the Citrello extension and
  // their access; the texts come back through the cloud copy, and the check carries on here by itself
  let waiting = null;
  async function getWithAccess() {  // the papers of the citation clicked
    const cit = current, r = claimShown;
    if (!cit || !r) return;
    const keys = r.papers.filter((p) => p.status !== "text").map((p) => p.key);
    await viaBrowser(keys, cit, () => { claimShown = null; return checkClaim(); });
  }
  // Open the web app in the person's browser for `keys`; when all their texts have come back through the cloud copy,
  // run `then` (only if the writer is still in citation `cit`). Shows the waiting message meanwhile.
  async function viaBrowser(keys, cit, then) {
    claimSay(spin("Saving the papers to your cloud copy…"));
    libChanged = true;
    await pushLibrary();  // the web app finds the papers in the cloud copy
    const url = `${location.origin}/app/?word_fetch=${encodeURIComponent(keys.join(","))}` + (bridgeParam ? `&bridge=${encodeURIComponent(bridgeParam)}` : "");
    try {
      if (Office.context.ui && Office.context.ui.openBrowserWindow) Office.context.ui.openBrowserWindow(url);
      else window.open(url, "_blank", "noopener");
    } catch { window.open(url, "_blank", "noopener"); }
    const started = Date.now(), here = () => current && current.payload.citationID === cit.payload.citationID;
    const me = waiting = { id: cit.payload.citationID, keys: [...keys] };
    const tick = async () => {
      if (waiting !== me) return;
      const left = me.keys.length;
      if (here()) claimSay(spin(`Waiting for ${left === 1 ? "1 paper" : `${left} papers`} from your browser…`) +
        `<p class="small muted">Citrello opened the web app in your browser: follow it there (sign in, the extension). This carries on by itself.` +
        ` <a href="${esc(url)}" target="_blank" rel="noopener">Open it again</a> · <button class="link" id="stopWait">Stop waiting</button></p>`);
      if ($("stopWait")) $("stopWait").onclick = () => { waiting = null; claimFor = null; renderClaim(); };
      await pullLibrary(true);
      const got = [];
      for (const key of me.keys) {
        const t = await post("/api/word-addin/paper-text", { key, csl: {}, search: false });
        if (t.status === "text") got.push(key);
      }
      me.keys = me.keys.filter((k) => !got.includes(k));
      if (!me.keys.length) { waiting = null; if (here()) await then(); return; }
      if (Date.now() - started > 20 * 60 * 1000) { waiting = null; claimFor = null; renderClaim(); return; }
      setTimeout(() => tick().catch(() => {}), 8000);
    };
    setTimeout(() => tick().catch(() => {}), 4000);
  }

  // a PDF from the writer's computer, for a paper Citrello couldn't find a free copy of
  let pdfFor = null;
  function pickPdf(key) { pdfFor = key; $("pdfPick").value = ""; $("pdfPick").click(); }
  $("pdfPick").onchange = async () => {
    const f = $("pdfPick").files[0];
    if (!f || !pdfFor) return;
    claimSay(spin("Reading the PDF…"));
    try {
      await api("POST", "/api/fulltext/attach", await f.arrayBuffer(), { "X-Key": encodeURIComponent(pdfFor) });
      libChanged = true;
      pushLibrary().catch(() => {});
      claimShown = null;
      await checkClaim();
    } catch (e) { claimBusy = false; claimFail(e); }
  };

  // ----- find another reference (Pro): in the library, or on the web -----
  let found = { library: [], web: [] };
  $("findAnother").onclick = async () => {
    if (!quota || !quota.pro) await loadQuota();  // just switched to Pro?
    if (!quota || !quota.pro) return showPro("Finding another reference is part of Citrello Pro.");
    show("proCard", false);
    claimSay('<p class="small">Find another reference that says this:</p><div class="row"><button id="findLib">In my library</button><button id="findWeb">On the web</button></div>');
    $("findLib").onclick = () => findInLibrary().catch(claimFail);
    $("findWeb").onclick = () => findOnWeb().catch(claimFail);
  };
  async function claimOfCurrent() {
    return (await post("/api/word-addin/claim-item", { paragraph: current.paragraph, at: current.at, keys: [] })).claim;
  }
  async function findInLibrary() {
    claimSay(spin("Looking through your library…"));
    const claim = await claimOfCurrent();
    const cands = (await post("/api/sources/library-candidates", { claim, exclude: current.payload.citationItems.map((c) => c.id) })).papers;
    if (!cands.length) return claimSay('<div class="notice">Nothing in your library seems to say this. Try <button class="link" id="findWeb2">the web</button>.</div>'), ($("findWeb2").onclick = () => findOnWeb().catch(claimFail));
    claimSay(spin(`Claude is reading ${cands.length} papers from your library…`));
    const res = await bridgeAi("POST", "/api/ai/judge", { kind: "library", items: [{ id: "lib", claim, papers: cands }] });
    found.library = res.results.filter((r) => r.verdict === "supports" || r.verdict === "partly")
      .map((r) => ({ ...r, cand: cands.find((c) => c.ref === r.paper) })).filter((r) => r.cand);
    if (!found.library.length) return claimSay('<div class="notice">None of the papers in your library clearly says this.</div>');
    claimSay(found.library.map((r) => `<div class="verdict ${r.verdict}"><b>${esc(r.cand.label)}: ${VERDICT[r.verdict]}</b>${esc(r.cand.title)}<br>${esc(r.reason)}` +
      `${r.quote ? `<q>${esc(r.quote)}</q>` : ""}<div class="row"><button data-add="${esc(r.cand.key)}">Add to this citation</button><button data-add="${esc(r.cand.key)}" data-how="replace">Use instead</button></div></div>`).join(""));
    wireClaim();
  }
  // On the web: Claude picks up to 5 papers from their abstracts; they go to the library ("Found by Claude") and are
  // shown at once; then each one's full text is fetched (free copies by themselves, the rest with the person's access)
  // and Claude reads it, so each verdict ends up resting on a sentence from the body of the paper.
  let webRun = null;  // {cit, claim, papers: [{...claude's, key, confirmed, stage, status}]}
  async function findOnWeb() {
    const cit = current;
    claimSay(spin("Claude is searching published papers (about a minute)…"));
    const claim = await claimOfCurrent();
    const res = await bridgeAi("POST", "/api/ai/find-web", { claim, context: cit.paragraph.slice(0, 2000),
      exclude: cit.payload.citationItems.map((c) => (c.itemData || {}).DOI).filter(Boolean) });
    if (!(res.papers || []).length) return claimSay('<div class="notice">Claude didn\'t find a published paper that clearly says this.</div>');
    claimSay(spin(`Claude found ${res.papers.length} paper${res.papers.length === 1 ? "" : "s"}. Adding them to your library…`));
    const added = (await post("/api/word-addin/add-found", { papers: res.papers })).added;
    const inCitation = new Set(cit.payload.citationItems.map((c) => c.id));
    const papers = res.papers.map((p, i) => added[i] && { ...p, key: added[i].key, confirmed: added[i].confirmed, stage: "abstract", status: "looking" })
      .filter((p) => p && !inCitation.has(p.key));
    if (!papers.length) return claimSay('<div class="notice">Claude only found papers that are already in this citation.</div>');
    libChanged = true;
    const run = webRun = { cit, claim, papers };
    found.web = papers;
    showWeb(run, `Getting the full text of each paper, to find the exact sentence…`);
    for (let i = 0; i < papers.length; i++) {  // free copies, one paper at a time
      if (webRun !== run) return;
      const t = await post("/api/word-addin/paper-text", { key: papers[i].key, csl: {}, search: true });
      papers[i].status = t.status === "text" ? "text" : "needs_access";
      papers[i].hasLink = !!(t.doi || papers[i].url);
      showWeb(run, `Getting the full text of each paper (${i + 1} of ${papers.length})…`);
    }
    await verifyWeb(run);
    pushLibrary().catch(() => {});
  }
  async function verifyWeb(run) {  // Claude reads the full text of the papers that have it
    const ready = run.papers.filter((p) => p.status === "text" && p.stage !== "fulltext");
    if (ready.length) {
      showWeb(run, `Claude is reading the full text of ${ready.length} paper${ready.length === 1 ? "" : "s"}…`);
      const ci = await post("/api/word-addin/claim-item", { paragraph: run.cit.paragraph, at: run.cit.at, keys: ready.map((p) => p.key) });
      if (ci.item) {
        const res = await bridgeAi("POST", "/api/ai/judge", { kind: "library", items: [ci.item] });
        for (const r of res.results) {
          const p = run.papers.find((x) => x.key === r.paper);
          if (p) Object.assign(p, { stage: "fulltext", verdict: r.verdict, quote: r.quote, reason: r.reason });
        }
      }
    }
    showWeb(run);
  }
  const FT_VERDICT = { supports: "✓ The full text supports it", partly: "≈ The full text partly supports it",
                       contradicts: "✗ The full text says something different", not_found: "? The full text doesn't seem to say it" };
  function showWeb(run, busy) {
    if (webRun !== run || !current || current.payload.citationID !== run.cit.payload.citationID) return;
    const rank = (p) => (p.stage === "fulltext" ? 0 : 1) * 10 + ({ supports: 0, partly: 1, not_found: 2, contradicts: 3 }[p.verdict] ?? 2);
    const order = run.papers.map((p, i) => [p, i]).sort((a, b) => rank(a[0]) - rank(b[0]));
    const needAccess = run.papers.filter((p) => p.status === "needs_access" && p.hasLink);
    const head = busy ? spin(busy) : needAccess.length
      ? `<div class="notice">${needAccess.length === 1 ? "One paper has" : `${needAccess.length} papers have`} no free copy, so ${needAccess.length === 1 ? "its verdict rests" : "their verdicts rest"} on the abstract only.` +
        `<div class="row"><button class="primary" id="webAccess">Get ${needAccess.length === 1 ? "it" : "them"} with my access</button></div></div>`
      : `<p class="small muted">Every verdict below comes from the paper's full text.</p>`;
    claimSay(head + order.map(([p, i]) => {
      const ft = p.stage === "fulltext";
      const where = ft ? "From the full text." : p.status === "looking" ? "From the abstract · getting the full text…"
        : p.status === "needs_access" ? "From the abstract · no free copy of the full text." : "From the abstract.";
      return `<div class="verdict ${esc(p.verdict)}"><b>${esc(p.authors)}${p.year ? " (" + esc(p.year) + ")" : ""}: ${ft ? FT_VERDICT[p.verdict] : VERDICT[p.verdict]}</b>${esc(p.title)}<br>${esc(p.reason)}` +
        `${p.quote ? `<q>${esc(p.quote)}</q>` : ""}<span class="small muted">${where}${p.confirmed ? "" : " Its details couldn't be confirmed on Crossref: check them."}</span>` +
        `${p.doi ? ` <a class="small" href="https://doi.org/${esc(p.doi)}" target="_blank" rel="noopener">doi.org/${esc(p.doi)}</a>` : ""}` +
        `<div class="row"><button data-add="${esc(p.key)}">Add to this citation</button><button data-add="${esc(p.key)}" data-how="replace">Use instead</button></div></div>`;
    }).join("") + '<p class="small muted">They\'re in your library, in the project “Found by Claude”.</p>');
    wireClaim();
    if ($("webAccess")) $("webAccess").onclick = () => viaBrowser(needAccess.map((p) => p.key), run.cit, async () => {
      needAccess.forEach((p) => { p.status = "text"; });
      webRun = run;
      await verifyWeb(run);
    }).catch(claimFail);
  }
  async function useFound(key, replace) {
    if (replace) await editCurrent((p) => { p.citationItems.length = 0; });
    await citeHere([key], replace);
  }
  // add papers to the citation the cursor is in (or, when it was emptied to replace, a new one in its place)
  async function citeHere(keys) {
    await cite(keys);
    claimShown = null;
    claimSay('<div class="notice ok">Done. Press Check the claim to check the citation now.</div>');
  }

  // the library goes back to the cloud copy after the add-in changed it (papers' text, papers added)
  async function pushLibrary() {
    if (!libChanged || !session || !session.signed_in) return;
    libChanged = false;
    const b = await apiRaw("GET", "/api/web/cloud-bundle");
    const st = await get("/api/web/state");
    const r = await fetch(session.bridge + "/api/cloud", { method: "PUT", body: b.body, headers: {
      Authorization: "Bearer " + session.token, "Content-Type": "application/gzip", "X-Items": b.headers["X-Items"] || "0",
      "X-Texts": b.headers["X-Texts"] || "0", "X-Device": st.cloud_device } });
    if (!r.ok) { libChanged = true; return; }
    await post("/api/web/cloud-saved", { stamp: b.headers["X-Stamp"] });
  }

  // ---------- start ----------
  Office.onReady(async (info) => {
    if (info.host && info.host !== Office.HostType.Word) {
      $("loadingText").textContent = "Citrello's add-in works in Word."; return;
    }
    startEngine();
    try { await ready; } catch { return; }
    show("loading", false);
    Office.context.document.addHandlerAsync(Office.EventType.DocumentSelectionChanged, onSelection);
    session = await get("/api/ai/session");
    if (session.signed_in) await openMain();
    else show("signin", true);
  });
})();
