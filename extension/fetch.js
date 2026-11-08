// The extension's fetch page: visits each paper Citrello asked for, one at a time, in a background tab with the
// person's own access, takes its text and hands it to the Citrello tab, which keeps only the text.
/* global grabPaper, pdfFrom, clickAndFollow */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let job = null, state = "idle", current = 0, results = [];
let checkTab = null;                // the tab showing an "are you human?" check, waiting for the person
const slowHosts = new Set();        // sites that asked for a check: go slower there
const STATUS = { ok: "Full text ✓", page: "Full text ✓", pdf: "Full text ✓ (PDF)", "no-access": "No access", captcha: "Needs you",
                 "other-page": "Didn't reach the paper", failed: "Couldn't save", error: "Didn't load", work: "Reading…", wait: "" };

async function load() {
  ({ job } = await chrome.storage.local.get("job"));
  if (!job || !job.papers || !job.papers.length) {
    $("msg").innerHTML = `<div class="notice">Nothing to fetch. Start from Citrello: Library → Import → Papers' full text.</div>`;
    $("start").disabled = true;
    return;
  }
  results = job.papers.map(() => "wait");
  render();
}

function render() {
  const n = job.papers.length, done = results.filter((r) => r !== "wait" && r !== "work" && r !== "captcha").length;
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

// One paper: its page in a background tab (or the tab already open for an "are you human?" check), then the text:
// the article on the page, or a PDF it links to (fetched here, with the extension's own access, when the page can't),
// or the PDF its button opens. A check page is left open for the person.
async function fetchOne(p, existingTab = null) {
  let tabId = existingTab;
  if (!tabId) {
    const url = p.doi ? "https://doi.org/" + p.doi : p.url;
    tabId = (await chrome.tabs.create({ url, active: false })).id;
  }
  let keep = false;
  try {
    await waitForLoad(tabId);                 // (after a check, the page usually reloads to the article)
    await sleep(existingTab ? 2000 : 3000);   // pages that build the article with scripts
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId }, func: grabPaper, args: [p.doi] });
    const g = result || { status: "error" };
    if (g.status === "captcha") { keep = true; return Object.assign(g, { tabId }); }
    if (g.status !== "no-access") return g;
    for (const u of g.pdfLinks || []) {  // the page's PDF links, fetched with the extension's access
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

async function run() {
  // access to publishers' sites: asked once, now (not when the extension was installed)
  const origins = ["<all_urls>"];
  if (!job.testFast && !(await chrome.permissions.contains({ origins }))) {  // (a local test copy skips the prompt)
    if (!(await chrome.permissions.request({ origins }))) {
      $("msg").innerHTML = `<div class="notice">The extension needs to read publishers' pages to take the papers' text. Click Start again and allow it.</div>`;
      return;
    }
  }
  state = "running";
  $("start").classList.add("hidden"); $("pause").classList.remove("hidden"); $("stop").classList.remove("hidden");
  $("msg").innerHTML = "";
  const noAccess = {};  // host -> papers in a row without access
  while (current < job.papers.length && state === "running") {
    const p = job.papers[current];
    results[current] = "work"; render();
    const g = await fetchOne(p, checkTab);
    checkTab = null;
    if (g.status === "captcha") {  // a person has to answer it: show them the tab and wait
      checkTab = g.tabId;
      slowHosts.add(g.host);
      results[current] = "captcha"; render();
      state = "check";
      chrome.tabs.update(g.tabId, { active: true }).catch(() => {});
      $("msg").innerHTML = `<div class="notice"><b>${esc(g.host)}</b> wants to check that you're a person (a CAPTCHA). We've opened that page:
        answer the check there, then come back to this tab and click <b>Continue</b>. Citrello never answers these for you,
        and it will go more slowly on that site from now on.</div>`;
      break;
    }
    let r = g.status === "ok" ? g.how : g.status;
    if (g.status === "ok") {
      try {
        const saved = await chrome.tabs.sendMessage(job.citrelloTab, { action: "fulltext", key: p.key, how: g.how, text: g.text, pdf: g.pdf });
        if (!saved || !saved.ok) r = "failed";
      } catch (e) {
        state = "stopped";
        $("msg").innerHTML = `<div class="notice">Your Citrello tab was closed. Open Citrello again and start from there.</div>`;
        results[current] = "wait"; render();
        break;
      }
      noAccess[g.host] = 0;
    } else if (g.status === "no-access" && g.host) {
      noAccess[g.host] = (noAccess[g.host] || 0) + 1;
    }
    results[current] = r; current++; render();
    if (g.host && noAccess[g.host] >= 3 && current < job.papers.length) {  // probably not signed in there
      state = "paused";
      $("msg").innerHTML = `<div class="notice">Three papers on <b>${esc(g.host)}</b> in a row had no access. Sign in to your university or library there
        (open one of those papers in a tab and use "Access through your institution"), then click Resume.</div>`;
      noAccess[g.host] = 0;
      break;
    }
    const slow = slowHosts.has(g.host) ? 3 : 1;  // after a check on that site, three times slower
    if (current < job.papers.length && state === "running") await sleep(job.testFast ? 300 : slow * (10000 + Math.random() * 10000));
  }
  if (current >= job.papers.length) {
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
}

$("start").onclick = () => run();
$("pause").onclick = () => { state = "paused"; $("start").textContent = "Resume"; $("start").classList.remove("hidden"); $("pause").classList.add("hidden");
                              $("msg").innerHTML = `<div class="notice">Paused after this paper.</div>`; };
$("stop").onclick = () => { state = "stopped"; if (checkTab) chrome.tabs.remove(checkTab).catch(() => {}); checkTab = null; chrome.storage.local.remove("job"); $("pause").classList.add("hidden"); $("stop").classList.add("hidden");
                             $("msg").innerHTML = `<div class="notice">Stopped. What was fetched is in Citrello.</div>`; };
load();
