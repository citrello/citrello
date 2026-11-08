// The toolbar panel ("Add to Citrello"): what's on this page, the paper's text if the page shows it (with the
// person's own access), a project, and Add. Citrello itself looks the paper up and keeps it: the open Citrello tab
// (opened in the background when there isn't one), or the desktop app. See background.js (citrello-api).
/* global grabPaper, pageInfo */
const $ = (id) => document.getElementById(id);
const show = (id, on = true) => $(id).classList.toggle("hidden", !on);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const params = new URLSearchParams(location.search);  // ?tab=<id>: the tests open this page in a tab of its own
let tabId = null, rec = null, key = null, paper = null, grabbing = null;

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
  ft.textContent = paper ? "Full text found: ChatGPT and Claude will be able to read this paper."
    : grabbing === null ? "" : "No full text on this page. You can add the PDF later in Citrello.";
}

async function start() {
  tabId = params.get("tab") ? Number(params.get("tab")) : ((await chrome.tabs.query({ active: true, currentWindow: true }))[0] || {}).id;
  let info;
  try {
    [{ result: info }] = await chrome.scripting.executeScript({ target: { tabId }, func: pageInfo });
  } catch (e) {
    return fail("Citrello can't read this page (the browser's own pages and the web store are off-limits). Open a paper's page and click again.");
  }
  grabbing = chrome.scripting.executeScript({ target: { tabId }, func: grabPaper, args: [""] })
    .then((r) => (r && r[0] && r[0].result) || {}).catch(() => ({}));
  grabbing.then((g) => { paper = g.status === "ok" ? g : null; showText(); });
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
