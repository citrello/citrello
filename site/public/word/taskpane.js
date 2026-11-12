// Citrello for Word: the add-in's panel (citrello.app/word/taskpane.html).
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
    };
    worker.onerror = (e) => { $("loadingText").textContent = "Citrello couldn't start: " + (e.message || "unknown error"); readyReject(e); };
  }
  async function api(method, path, body) {
    await ready;
    const raw = body === undefined ? null : body instanceof ArrayBuffer ? new Uint8Array(body)
      : new TextEncoder().encode(JSON.stringify(body));
    const m = await new Promise((resolve) => {
      const id = ++nextId;
      pending.set(id, resolve);
      worker.postMessage({ type: "request", id, method, path, headers: raw && !(body instanceof ArrayBuffer) ? { "Content-Type": "application/json" } : {}, body: raw });
    });
    const text = new TextDecoder().decode(m.body);
    let data = text;
    if ((m.ctype || "").includes("json")) { try { data = JSON.parse(text); } catch { data = {}; } }
    if (m.status >= 400) throw new Error((data && data.error) || `Citrello couldn't do that (${m.status}).`);
    return data;
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
        before.load("text");
        await ctx.sync();
        ctxInfo = { tag: inside.isNullObject ? null : inside.tag, before: before.text || "", range: before };
        const p = ctxInfo.tag && payloadOf(ctxInfo.tag);
        current = p ? { tag: ctxInfo.tag, payload: p } : null;
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
