// The extension's fetch page: visits the papers Citrello asked for in background tabs, with the person's own access,
// takes their text and hands it to the Citrello tab, which keeps only the text. Up to three at once, but never two
// from the same publisher, and each publisher gets a pause between its papers (publishers block fast downloading).
/* global grabPaper, pdfFrom, clickAndFollow, isChallenge */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const PARALLEL = 3;
let job = null, state = "idle", results = [], reports = [];
const checkTabs = {};               // paper index -> the tab showing its "are you human?" check
const slowHosts = new Set();        // sites that asked for a check: go slower there
const nextAt = {}, busy = new Set();  // per publisher: when its next paper may start, and whether one is running
const STATUS = { ok: "Full text ✓", page: "Full text ✓", pdf: "Full text ✓ (PDF)", "no-access": "No access", captcha: "Needs you",
                 "not-paper": "Web page: nothing to read", "other-page": "Didn't reach the paper", failed: "Couldn't save",
                 error: "Didn't load", work: "Reading…", wait: "" };
// a publisher: the DOI's prefix (10.1016 = Elsevier, 10.1109 = IEEE…), or the site for a link
const pubOf = (p) => { if (p.doi) return p.doi.split("/")[0]; try { return new URL(p.url).hostname; } catch (e) { return "?"; } };

async function load() {
  ({ job } = await chrome.storage.local.get("job"));
  if (!job || !job.papers || !job.papers.length) {
    $("msg").innerHTML = `<div class="notice">Nothing to fetch. Start from Citrello: Full texts → Fetch the missing ones with my access.</div>`;
    $("start").disabled = true;
    return;
  }
  results = job.papers.map(() => "wait");
  render();
}

function render() {
  const n = job.papers.length, done = results.filter((r) => !["wait", "work", "captcha"].includes(r)).length;
  const ok = results.filter((r) => r === "page" || r === "pdf").length;
  $("count").textContent = `${done} of ${n} done · ${ok} full text${ok === 1 ? "" : "s"} added`;
  $("bar").style.width = `${(100 * done) / n}%`;
  $("list").innerHTML = job.papers.map((p, i) => {
    const r = results[i], cls = r === "page" || r === "pdf" ? "ok" : r === "work" ? "work" : r === "wait" ? "" : "no";
    return `<li class="${cls}"><span class="t">${esc(p.title || p.doi || p.url)}</span><span class="s">${esc(STATUS[r] || "")}</span></li>`;
  }).join("");
}

function waitForLoad(tabId, timeout = 45000) {
  return new Promise((resolve) => {
    const done = (ok) => { chrome.tabs.onUpdated.removeListener(listen); clearTimeout(t); resolve(ok); };
    const listen = (id, info) => { if (id === tabId && info.status === "complete") done(true); };
    const t = setTimeout(() => done(false), timeout);
    chrome.tabs.onUpdated.addListener(listen);
    chrome.tabs.get(tabId).then((tab) => { if (tab.status === "complete" && tab.url && !tab.url.startsWith("about:")) done(true); }).catch(() => done(false));
  });
}

// Loaded and staying put: redirects (doi.org → linkinghub.elsevier.com → ScienceDirect…) finished, and the address
// hasn't changed for a few seconds.
async function settle(tabId) {
  await waitForLoad(tabId);
  let last = null, stableSince = Date.now();
  for (const end = Date.now() + 25000; Date.now() < end;) {
    const t = await chrome.tabs.get(tabId).catch(() => null);
    if (!t) return;
    if (t.url !== last || t.status !== "complete") { last = t.url; stableSince = Date.now(); if (t.status !== "complete") await waitForLoad(tabId, 20000); }
    else if (Date.now() - stableSince > 2500) return;
    await sleep(500);
  }
}

// Security checks (Cloudflare's "Just a moment…") usually pass by themselves after some seconds and then reload the
// page; in a background tab the browser slows pages down, so give them up to a minute, following the reload. Only
// a check still there after that is a real "are you human?" for the person.
async function passCheck(tabId, limit = job && job.testFast ? 20000 : 60000) {
  const check = async () => {
    try { return (await chrome.scripting.executeScript({ target: { tabId }, func: isChallenge }))[0].result; }
    catch (e) { return true; }  // the page is reloading (the check passed): look again
  };
  if (!(await check())) return;
  for (const end = Date.now() + limit; Date.now() < end;) {
    await sleep(1500);
    if (!(await check())) { await settle(tabId); if (!(await check())) return; }
  }
}

// One paper: its page in a background tab (or the tab already open for an "are you human?" check), then the text:
// the article on the page, or a PDF it links to or its platform keeps (fetched here, with the extension's own access,
// when the page can't), or the PDF its button opens. A check page is left open for the person.
// DOIs to try: the DOI, then without a site's own number glued on (AIP's "10.1063/1.4812391/138579") or a
// trailing ".pdf" or "/full" (doi.org says "DOI not found" for those)
function doiCandidates(doi) {
  const out = [doi];
  let d = doi;
  for (let i = 0; i < 2; i++) {
    const t = d.replace(/\/(\d+|full|abstract|pdf|epdf)$/i, "").replace(/\.pdf$/i, "");
    if (t === d || !/^10\.\d{4,9}\/\S+$/.test(t)) break;
    out.push(t);
    d = t;
  }
  return out;
}
self.doiCandidates = doiCandidates;

async function fetchOne(p, existingTab = null) {
  let tabId = existingTab;
  if (!tabId) {
    const url = p.doi ? "https://doi.org/" + p.doi : p.url;
    tabId = (await chrome.tabs.create({ url, active: false })).id;
  }
  let keep = false;
  try {
    await settle(tabId);
    if (p.doi && !existingTab) {  // doi.org didn't know this DOI: try it without a glued-on ending
      for (const d of doiCandidates(p.doi).slice(1)) {
        const t = await chrome.tabs.get(tabId).catch(() => null);
        if (!t || !/(^|\.)doi\.org$/.test(new URL(t.url || "https://x/").hostname)) break;
        await chrome.tabs.update(tabId, { url: "https://doi.org/" + d });
        await settle(tabId);
      }
    }
    await passCheck(tabId);
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId }, func: grabPaper, args: [p.doi, p.type || ""] });
    const g = result || { status: "error" };
    if (g.status === "captcha") { keep = true; return Object.assign(g, { tabId }); }
    if (g.status !== "no-access") return g;
    for (const u of g.pdfLinks || []) {  // the page's PDF links and the platform's PDF address, with the extension's access
      const pdf = await pdfFrom(u);
      if (pdf) return { status: "ok", how: "pdf", pdf, host: g.host };
    }
    if (g.pdfButton) {                     // a PDF button that works with scripts: click it and follow it
      const u = await clickAndFollow(tabId);
      const pdf = u && await pdfFrom(u);
      if (pdf) return { status: "ok", how: "pdf", pdf, host: g.host };
    }
    return g;
  } catch (e) {
    return { status: "error", error: String(e && e.message || e) };
  } finally {
    if (!keep) chrome.tabs.remove(tabId).catch(() => {});
  }
}

const pause = (html, label) => {
  if (state !== "running") return;
  state = label;
  $("msg").innerHTML = `<div class="notice">${html}</div>`;
};

async function worker(noAccess) {
  while (state === "running") {
    const now = Date.now();
    const i = results.findIndex((r, k) => r === "wait" && !busy.has(pubOf(job.papers[k])) && (nextAt[pubOf(job.papers[k])] || 0) <= now);
    if (i < 0) {
      if (!results.includes("wait")) return;
      await sleep(400);
      continue;
    }
    const p = job.papers[i], pub = pubOf(p);
    busy.add(pub);
    results[i] = "work"; render();
    const g = await fetchOne(p, checkTabs[i] || null);
    delete checkTabs[i];
    busy.delete(pub);
    const slow = slowHosts.has(g.host) ? 3 : 1;  // after a check on that site, three times slower
    nextAt[pub] = Date.now() + (job.testFast ? 300 : slow * (10000 + Math.random() * 10000));
    if (g.status === "captcha") {  // a person has to answer it: show them the tab and wait
      checkTabs[i] = g.tabId;
      slowHosts.add(g.host);
      results[i] = "captcha"; render();
      chrome.tabs.update(g.tabId, { active: true }).catch(() => {});
      pause(`<b>${esc(g.host)}</b> wants to check that you're a person (a CAPTCHA). We've opened that page:
        answer the check there, then come back to this tab and click <b>Continue</b>. Citrello never answers these for you,
        and it will go more slowly on that site from now on.`, "check");
      return;
    }
    let r = g.status === "ok" ? g.how : g.status;
    if (g.status === "ok") {
      try {
        const saved = await chrome.tabs.sendMessage(job.citrelloTab, { action: "fulltext", key: p.key, how: g.how, text: g.text, pdf: g.pdf });
        if (!saved || !saved.ok) r = "failed";
      } catch (e) {
        results[i] = "wait"; render();
        state = "running"; pause("Your Citrello tab was closed. Open Citrello again and start from there.", "stopped");
        return;
      }
      noAccess[g.host] = 0;
    } else {
      reports[i] = { title: p.title, id: p.doi || p.url, status: g.status, page: g.page || { error: g.error } };
      if (g.status === "no-access" && g.host) noAccess[g.host] = (noAccess[g.host] || 0) + 1;
    }
    results[i] = r; render();
    if (g.host && noAccess[g.host] >= 3 && results.includes("wait")) {  // probably not signed in there
      noAccess[g.host] = 0;
      pause(`Three papers on <b>${esc(g.host)}</b> in a row had no access. Sign in to your university or library there
        (open one of those papers in a tab and use "Access through your institution"), then click Resume.`, "paused");
      return;
    }
  }
}

async function run() {
  // access to publishers' sites: asked once, now (not when the extension was installed)
  const origins = ["<all_urls>"];
  if (!job.testFast && !(await chrome.permissions.contains({ origins }))) {  // (a local test copy skips the prompt)
    if (!(await chrome.permissions.request({ origins }))) {
      $("msg").innerHTML = `<div class="notice">The extension needs to read publishers' pages to take the papers' text. Click Start again and allow it.</div>`;
      return;
    }
  }
  results = results.map((r) => (r === "captcha" ? "wait" : r));  // after a check: that paper again, in its tab
  state = "running";
  $("start").classList.add("hidden"); $("pause").classList.remove("hidden"); $("stop").classList.remove("hidden");
  $("report").classList.add("hidden");
  $("msg").innerHTML = "";
  const noAccess = {};
  await Promise.all(Array.from({ length: PARALLEL }, () => worker(noAccess)));
  const left = results.filter((r) => r === "wait" || r === "captcha").length;
  if (!left && state === "running") {
    state = "done";
    const ok = results.filter((x) => x === "page" || x === "pdf").length;
    $("msg").innerHTML = `<div class="notice ok">Finished: ${ok} of ${job.papers.length} full texts added to Citrello.` +
      (ok < job.papers.length ? " The others had no access or no text; you can add their PDFs in Citrello." : "") + `</div>`;
    $("pause").classList.add("hidden"); $("stop").classList.add("hidden");
    chrome.storage.local.remove("job");
  } else if (state === "paused" || state === "check") {
    $("start").textContent = state === "check" ? "Continue" : "Resume";
    $("start").classList.remove("hidden"); $("pause").classList.add("hidden");
  }
  if (reports.some(Boolean)) $("report").classList.remove("hidden");
}

// "Copy a report": for the papers that didn't work, how their pages were built (site, title, headings, PDF links
// found), never their text or anything about the person, so Citrello can learn those sites.
function report() {
  const lines = [`Citrello extension ${chrome.runtime.getManifest().version}: papers it couldn't read (${navigator.userAgent.match(/(Firefox|Edg|Chrome)\/[\d.]+/)?.[0] || ""})`];
  reports.forEach((r, i) => {
    if (!r) return;
    const pg = r.page || {};
    lines.push(`${i + 1}. ${r.title} [${r.id}] -> ${STATUS[r.status] || r.status}`,
      `   site: ${pg.host || "?"}${pg.path || ""} | page title: ${pg.title || ""} | text on page: ${pg.chars ?? "?"} chars, article part: ${pg.articleChars ?? "?"}`,
      `   headings: ${(pg.headings || []).join(" / ")}`,
      `   PDF links found: ${(pg.pdfLinks || []).join(" , ") || "none"} | PDF button: ${pg.pdfButton ? "yes" : "no"}${pg.error ? " | error: " + pg.error : ""}`);
  });
  return lines.join("\n");
}

$("report").onclick = async () => {
  try { await navigator.clipboard.writeText(report()); $("report").textContent = "Copied: paste it to Citrello's team"; }
  catch (e) { prompt("Copy this report:", report()); }
};
$("start").onclick = () => run();
$("pause").onclick = () => { if (state !== "running") return; state = "paused"; $("start").textContent = "Resume"; $("start").classList.remove("hidden");
                              $("pause").classList.add("hidden"); $("msg").innerHTML = `<div class="notice">Pausing after the papers being read now.</div>`; };
$("stop").onclick = () => { state = "stopped"; Object.values(checkTabs).forEach((t) => chrome.tabs.remove(t).catch(() => {}));
                             chrome.storage.local.remove("job"); $("pause").classList.add("hidden"); $("stop").classList.add("hidden");
                             $("msg").innerHTML = `<div class="notice">Stopped. What was fetched is in Citrello.</div>`;
                             if (reports.some(Boolean)) $("report").classList.remove("hidden"); };
load();
