// The extension's fetch page: visits each paper Citrello asked for, one at a time, in a background tab with the
// person's own access, takes its text and hands it to the Citrello tab, which keeps only the text.
/* global grabPaper */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let job = null, state = "idle", current = 0, results = [];
const STATUS = { ok: "Full text ✓", page: "Full text ✓", pdf: "Full text ✓ (PDF)", "no-access": "No access",
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
  const n = job.papers.length, done = results.filter((r) => r !== "wait" && r !== "work").length;
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

async function fetchOne(p) {
  const url = p.doi ? "https://doi.org/" + p.doi : p.url;
  const tab = await chrome.tabs.create({ url, active: false });
  try {
    await waitForLoad(tab.id);
    await sleep(3000);  // pages that build the article with scripts
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: grabPaper, args: [p.doi] });
    return result || { status: "error" };
  } catch (e) {
    return { status: "error", error: String(e && e.message || e) };
  } finally {
    chrome.tabs.remove(tab.id).catch(() => {});
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
    const g = await fetchOne(p);
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
    if (current < job.papers.length && state === "running") await sleep(job.testFast ? 300 : 10000 + Math.random() * 10000);
  }
  if (current >= job.papers.length) {
    state = "done";
    const ok = results.filter((x) => x === "page" || x === "pdf").length;
    $("msg").innerHTML = `<div class="notice ok">Finished: ${ok} of ${job.papers.length} full texts added to Citrello.` +
      (ok < job.papers.length ? " The others had no access or no text; you can add their PDFs in Citrello." : "") + `</div>`;
    $("pause").classList.add("hidden"); $("stop").classList.add("hidden");
    chrome.storage.local.remove("job");
  } else if (state === "paused") {
    $("start").textContent = "Resume"; $("start").classList.remove("hidden"); $("pause").classList.add("hidden");
  }
}

$("start").onclick = () => run();
$("pause").onclick = () => { state = "paused"; $("start").textContent = "Resume"; $("start").classList.remove("hidden"); $("pause").classList.add("hidden");
                              $("msg").innerHTML = `<div class="notice">Paused after this paper.</div>`; };
$("stop").onclick = () => { state = "stopped"; chrome.storage.local.remove("job"); $("pause").classList.add("hidden"); $("stop").classList.add("hidden");
                             $("msg").innerHTML = `<div class="notice">Stopped. What was fetched is in Citrello.</div>`; };
load();
