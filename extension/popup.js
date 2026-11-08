// The toolbar panel ("Add to Citrello"): what's on this page, the paper's text if the page shows it (with the
// person's own access), a project, and Add. Citrello itself looks the paper up and keeps it: the open Citrello tab
// (opened in the background when there isn't one), or the desktop app. See background.js (citrello-api).
/* global grabPaper, pageInfo, pdfFrom, clickPdfButton */
const $ = (id) => document.getElementById(id);
const show = (id, on = true) => $(id).classList.toggle("hidden", !on);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const params = new URLSearchParams(location.search);  // ?tab=<id>: the tests open this page in a tab of its own
let tabId = null, rec = null, key = null, paper = null, grabbing = null, grabbed = null;

function fail(msg) { show("loading", false); $("error").textContent = msg; show("error"); }

async function call(path, body, headers = {}, pdf = "") {
  const r = await chrome.runtime.sendMessage({ action: "citrello-api", path, body, headers, pdf });
  if (!r) throw new Error("Citrello didn't answer. Open Citrello and try again.");
  if (r.error) throw new Error(r.error);
  if (r.status >= 400) throw new Error((r.data && r.data.error) || `Citrello couldn't do that (${r.status}).`);
  return r.data || {};
}

function showText() {
  const ft = $("ft");
  ft.className = "ft " + (paper ? "ok" : "");
  if (paper) { ft.textContent = "Full text found: ChatGPT and Claude will be able to read this paper."; return; }
  if (!grabbed) { ft.textContent = "Full text: looking on the page…"; return; }
  if (grabbed.status === "captcha") {
    ft.textContent = "This page wants to check that you're a person. Answer the check on the page, then click the Citrello button again.";
  } else if ((grabbed.pdfLinks && grabbed.pdfLinks.length) || grabbed.pdfButton) {
    ft.innerHTML = 'No full text on the page itself. <a href="#" id="openPdf">Open the PDF</a>, then click the Citrello button on it.';
    $("openPdf").onclick = async (e) => {
      e.preventDefault();
      if (grabbed.pdfLinks && grabbed.pdfLinks.length) await chrome.tabs.create({ url: grabbed.pdfLinks[0] });
      else {  // the page's PDF button: click it, then open where it wanted to go (the browser blocks it otherwise)
        await chrome.scripting.executeScript({ target: { tabId }, func: clickPdfButton, world: "MAIN" });
        await new Promise((res) => setTimeout(res, 1500));
        const [{ result }] = await chrome.scripting.executeScript({ target: { tabId }, func: () => document.documentElement.dataset.citrelloOpen || "" });
        if (result) await chrome.tabs.create({ url: result });
      }
      window.close();
    };
  } else {
    ft.textContent = "No full text on this page. You can add the PDF later in Citrello.";
  }
}

// the paper's text: what the page gave, or a PDF it links to, fetched here with the extension's access
async function getPaper() {
  let g = {};
  try { g = (await chrome.scripting.executeScript({ target: { tabId }, func: grabPaper, args: [""] }))[0].result || {}; } catch (e) { /* off-limits */ }
  if (g.status === "no-access") {
    for (const u of g.pdfLinks || []) {
      const pdf = await pdfFrom(u);
      if (pdf) { g = { status: "ok", how: "pdf", pdf, host: g.host }; break; }
    }
  }
  return g;
}

// on a PDF itself: its text goes to the matching reference (found by its DOI or title; added if it's new)
async function pdfMode() {
  show("loading", false); show("found");
  $("title").textContent = "This is a PDF";
  $("meta").textContent = "Citrello reads it, finds its reference in your library (or adds it), and keeps only its text.";
  $("tags").innerHTML = ""; $("ft").textContent = "";
  $("add").textContent = "Add its text to my library";
  $("add").onclick = async () => {
    $("add").disabled = true; $("add").textContent = "Reading the PDF…";
    try {
      const g = await grabbing;
      if (!g || !g.pdf) throw new Error("Citrello couldn't read this PDF here. Download it and drop it in Citrello (Full texts).");
      const project = $("project").value.trim();
      const r = await call("/api/clip/pdf", "", { "X-Project": encodeURIComponent(project) }, g.pdf);
      if (r.unmatched) throw new Error("Couldn't tell which paper this is (no DOI or matching title in it). Add it in Citrello: open the reference → Full text.");
      key = r.key;
      show("found", false); show("done");
      $("doneMsg").innerHTML = `Full text added to <code>[@${esc(key)}]</code>` + (r.how === "added" ? " (new in your library)" : "") +
        ".<br>ChatGPT and Claude can read it.";
      chrome.runtime.sendMessage({ action: "badge", tabId });
    } catch (e) {
      $("add").disabled = false; $("add").textContent = "Add its text to my library";
      fail(e.message);
    }
  };
}

async function start() {
  tabId = params.get("tab") ? Number(params.get("tab")) : ((await chrome.tabs.query({ active: true, currentWindow: true }))[0] || {}).id;
  let info;
  try {
    [{ result: info }] = await chrome.scripting.executeScript({ target: { tabId }, func: pageInfo });
  } catch (e) {
    return fail("Citrello can't read this page (the browser's own pages and the web store are off-limits). Open a paper's page and click again.");
  }
  grabbing = getPaper();
  grabbing.then((g) => { grabbed = g; paper = g.status === "ok" ? g : null; showText(); });
  if (info.isPdf) return pdfMode();
  const slow = setTimeout(async () => {
    const { target } = await chrome.storage.local.get("target");
    $("loadingText").textContent = target === "desktop" ? "Asking Citrello on this computer…"
      : "Opening Citrello in a background tab… It stays open, so your library is ready next time.";
  }, 2500);
  let r;
  try {
    r = await call("/api/clip/preview", JSON.stringify(info), { "Content-Type": "application/json" });
  } catch (e) {
    clearTimeout(slow);
    return fail(e.message);
  }
  clearTimeout(slow);
  rec = r.rec;
  const c = rec.csl;
  show("loading", false); show("found");
  $("title").textContent = c.title || "(no title)";
  const names = (c.author || []).map((a) => a.family || a.literal).filter(Boolean);
  const au = names.length > 2 ? names[0] + " et al." : names.join(" & ");
  const year = c.issued && c.issued["date-parts"] ? c.issued["date-parts"][0][0] : "n.d.";
  $("meta").textContent = [au, year, c["container-title"] || c.publisher, c.DOI].filter(Boolean).join(" · ");
  const rel = { verified: ["ok", "✓ Verified"], check: ["warn", "Check"], problem: ["bad", "Problem"], complete: ["", "Complete"] }[rec.reliability] || ["", ""];
  $("tags").innerHTML = (rel[1] ? `<span class="tag ${rel[0]}">${rel[1]}</span>` : "") +
    `<span class="tag">${esc((c.type || "").replace("article-journal", "journal article").replace("paper-conference", "conference paper"))}</span>`;
  $("projects").innerHTML = (r.projects || []).map((p) => `<option value="${esc(p)}">`).join("");
  const { lastProject } = await chrome.storage.local.get("lastProject");
  $("project").value = lastProject || "";
  if (r.existing) {
    $("existing").innerHTML = `Already in your library as <code>[@${esc(r.existing)}]</code>.` + (paper ? "" : " Adding again puts it into the project.");
    show("existing");
    $("add").textContent = "Add to project";
  }
  showText();
  $("add").focus();
}

$("add").onclick = async () => {
  $("add").disabled = true;
  $("add").textContent = "Adding…";
  try {
    const project = $("project").value.trim();
    chrome.storage.local.set({ lastProject: project });
    const r = await call("/api/clip/add", JSON.stringify({ rec, project }), { "Content-Type": "application/json" });
    key = r.key;
    // the paper's text, if the page showed it (wait a little for it if it's still coming)
    const g = await Promise.race([grabbing, new Promise((res) => setTimeout(() => res({}), 20000))]);
    let withText = false;
    if (g && g.status === "ok") {
      try {
        await call("/api/clip/fulltext", g.text ? JSON.stringify({ text: g.text }) : "", { "X-Key": encodeURIComponent(key) }, g.pdf || "");
        withText = true;
      } catch (e) { /* the reference is added; the text can be added later */ }
    }
    show("found", false); show("done");
    $("doneMsg").innerHTML = `${r.created ? "Added" : "Already in your library"}: <code>[@${esc(key)}]</code>` +
      (project ? ` · ${esc(project)}` : "") + (withText ? "<br>With its full text: ChatGPT and Claude can read it." : "");
    chrome.runtime.sendMessage({ action: "badge", tabId });
    $("copy").focus();
  } catch (e) {
    $("add").disabled = false;
    $("add").textContent = "Add to library";
    fail(e.message);
  }
};
$("copy").onclick = async () => {
  try { await navigator.clipboard.writeText(`[@${key}]`); $("copy").textContent = "Copied ✓"; } catch (e) { /* not allowed here */ }
};
$("openApp").onclick = (e) => { e.preventDefault(); chrome.runtime.sendMessage({ action: "open-citrello" }); window.close(); };
chrome.commands && chrome.commands.getAll && chrome.commands.getAll().then((cmds) => {
  const c = cmds.find((x) => x.name === "_execute_action");
  if (c && c.shortcut) $("shortcut").textContent = c.shortcut; else $("hint").classList.add("hidden");
}).catch(() => {});
start();
