// Citrello web app: the desktop app's interface (ui.html), with its requests answered in this browser.
// Every fetch("/api/...") the interface makes goes to worker.js (Citrello's Python, running here) instead of a
// local server, and links to /api/... files (exports, fixed documents) become downloads made in the browser.
(function () {
  "use strict";
  document.documentElement.classList.add("web");

  // ---------- loading screen ----------
  const cover = document.createElement("div");
  cover.id = "webCover";
  cover.setAttribute("role", "status");
  cover.innerHTML = '<div class="wc-box"><div class="logo">citr<span>ello</span></div>' +
    '<p id="webCoverText">Opening Citrello…</p><div class="wc-bar"><div></div></div>' +
    '<p class="wc-fine">Everything runs in this browser. Your library stays on this computer.</p></div>';
  const style = document.createElement("style");
  style.textContent = `
    #webCover { position: fixed; inset: 0; z-index: 1000; display: grid; place-items: center; background: var(--bg, #f6f8fd);
      color: var(--ink, #14213d); font: 15px/1.5 Figtree, system-ui, sans-serif; text-align: center; padding: 16px; }
    #webCover .logo { font: 40px Gloock, Georgia, serif; color: var(--cobalt, #1d3f8f); }
    #webCover .logo span { color: var(--leaf, #3e7b4f); }
    #webCover .wc-bar { width: min(320px, 80vw); height: 6px; margin: 14px auto; border-radius: 3px; overflow: hidden;
      background: var(--line, #d5def0); }
    #webCover .wc-bar div { width: 30%; height: 100%; background: var(--lemon, #f6d646); animation: wcslide 1.4s ease-in-out infinite; }
    #webCover .wc-fine { color: var(--muted, #4a5774); font-size: 13px; }
    #webCover.err .wc-bar { display: none; }
    @keyframes wcslide { 0% { transform: translateX(-100%); } 100% { transform: translateX(340%); } }
    @media (prefers-reduced-motion: reduce) { #webCover .wc-bar div { animation: none; width: 100%; } }`;
  document.head.appendChild(style);
  const coverText = (t, err) => {
    const p = document.getElementById("webCoverText");
    if (p) p.textContent = t;
    cover.classList.toggle("err", !!err);
  };
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(cover));

  // ---------- the worker ----------
  let worker = null, readyResolve, readyReject;
  const ready = new Promise((res, rej) => { readyResolve = res; readyReject = rej; });
  ready.catch(() => {});
  const pending = new Map();
  const progress = new Map(); // path of a progress poll -> latest answer, while a job keeps Python busy
  let nextId = 0;

  function startWorker() {
    worker = new Worker("worker.js");
    worker.onmessage = (ev) => {
      const m = ev.data;
      if (m.type === "status") coverText(m.text);
      else if (m.type === "ready") { cover.remove(); readyResolve(); }
      else if (m.type === "error") { coverText(m.text + " Try reloading the page, or use another browser.", true); readyReject(new Error(m.text)); }
      else if (m.type === "progress") { if (m.body) progress.set(m.path, m.body); else progress.delete(m.path); }
      else if (m.type === "storage-error") console.warn("Citrello: couldn't save to the browser's storage:", m.text);
      else if (m.type === "response" && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    };
    worker.onerror = (e) => { coverText("Citrello couldn't start: " + (e.message || "unknown error"), true); readyReject(e); };
  }

  // Ask for the browser to keep this site's storage (fewer automatic clean-ups, e.g. Safari after 7 days)
  if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});

  // One tab at a time: two tabs would each keep their own copy of the library and overwrite each other.
  if (navigator.locks) {
    navigator.locks.request("citrello-web-app", { ifAvailable: true }, (lock) => {
      if (!lock) {
        coverText("Citrello is already open in another tab or window. Use that one, or close it and reload this page.", true);
        readyReject(new Error("Citrello is open in another tab"));
        return null;
      }
      startWorker();
      return new Promise(() => {}); // keep the lock while this tab is open
    });
  } else {
    startWorker();
  }

  // ---------- fetch("/api/...") -> worker ----------
  const realFetch = window.fetch.bind(window);
  const isApi = (url) => url.origin === location.origin && url.pathname.startsWith("/api/");

  async function bodyBytes(b) {
    if (b == null) return null;
    if (typeof b === "string") return new TextEncoder().encode(b);
    if (b instanceof ArrayBuffer) return new Uint8Array(b);
    if (ArrayBuffer.isView(b)) return new Uint8Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
    if (b instanceof Blob) return new Uint8Array(await b.arrayBuffer());
    if (b instanceof URLSearchParams) return new TextEncoder().encode(b.toString());
    throw new Error("Unsupported request body");
  }

  async function apiFetch(url, opts) {
    const method = (opts.method || "GET").toUpperCase();
    const live = method === "GET" && progress.get(url.pathname + url.search);
    if (live) return new Response(live, { status: 200, headers: { "Content-Type": "application/json" } });
    await ready;
    const headers = {};
    new Headers(opts.headers || {}).forEach((v, k) => {
      // the desktop server's handler reads headers by their usual spelling (X-Filename, Content-Type…)
      headers[k.replace(/(^|-)([a-z])/g, (_, d, c) => d + c.toUpperCase())] = v;
    });
    const body = await bodyBytes(opts.body);
    const id = ++nextId;
    const m = await new Promise((resolve) => {
      pending.set(id, resolve);
      worker.postMessage({ type: "request", id, method,
                           path: url.pathname + url.search, headers, body }, body ? [body.buffer] : []);
    });
    return new Response(m.body, { status: m.status, headers: { "Content-Type": m.ctype, ...m.headers } });
  }

  window.fetch = function (input, opts) {
    const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url, location.href);
    if (!isApi(url)) return realFetch(input, opts);
    return apiFetch(url, opts || {});
  };

  // ---------- links to /api/... files -> downloads made here ----------
  function fileName(disposition, fallback) {
    const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition || "");
    if (star) return decodeURIComponent(star[1]);
    const plain = /filename="?([^";]+)"?/i.exec(disposition || "");
    return plain ? plain[1] : fallback;
  }
  async function download(url) {
    const r = await apiFetch(url, {});
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.error || r.statusText);
    }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = fileName(r.headers.get("Content-Disposition"), url.pathname.split("/").pop());
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  }
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a) return;
    const url = new URL(a.getAttribute("href"), location.href);
    if (!isApi(url)) return;
    e.preventDefault();
    e.stopPropagation();
    download(url).then(() => window.dispatchEvent(new CustomEvent("citrello-downloaded", { detail: url.pathname })))
      .catch((err) => (typeof toast === "function" ? toast(err.message) : alert(err.message)));
  }, true);

  // ---------- backup and restore (Settings → Library), and the reminder in the Library ----------
  const DAY = 86400;
  async function backupState() {
    const r = await fetch("/api/web/state");
    const s = await r.json();
    const info = document.getElementById("webBackupInfo");
    if (info) info.textContent = s.last_backup
      ? `Last backup: ${new Date(s.last_backup * 1000).toLocaleDateString()} (${s.backup_count} references). The library now has ${s.items}.`
      : `No backup yet. The library has ${s.items} references.`;
    const nag = document.getElementById("webBackupNag");
    if (nag) {
      const old = !s.last_backup || Date.now() / 1000 - s.last_backup > 7 * DAY;
      const grown = s.items - (s.backup_count || 0) >= 10;
      const show = s.items > 0 && (old || grown);
      nag.classList.toggle("hidden", !show);
      document.getElementById("webBackupNagText").textContent = s.last_backup
        ? `Your library is kept in this browser only. Your last backup has ${s.backup_count} of ${s.items} references.`
        : "Your library is kept in this browser only. Save a backup file in case the browser's data is cleared.";
    }
  }
  window.addEventListener("citrello-downloaded", (e) => { if (e.detail === "/api/web/backup") backupState(); });
  document.addEventListener("DOMContentLoaded", () => {
    ready.then(backupState).catch(() => {});
    document.addEventListener("click", (e) => {
      if (e.target.closest && e.target.closest('.side button.navb')) setTimeout(() => backupState().catch(() => {}), 300);
    });
    const file = document.getElementById("webRestoreFile");
    if (file) file.addEventListener("change", async () => {
      const f = file.files[0];
      file.value = "";
      if (!f) return;
      const msg = document.getElementById("webRestoreMsg");
      if (!confirm(`Replace the library in this browser with the backup “${f.name}”?`)) return;
      const r = await fetch("/api/web/restore", { method: "POST", body: await f.arrayBuffer() });
      const data = await r.json().catch(() => ({}));
      msg.innerHTML = "";
      const div = document.createElement("div");
      div.className = "notice " + (r.ok ? "ok" : "err");
      div.textContent = r.ok ? `Restored: ${data.items} references.` : (data.error || "Couldn't restore.");
      msg.appendChild(div);
      if (r.ok) { if (typeof loadLibrary === "function") loadLibrary(); backupState(); }
    });
  });

  window.citrelloWeb = { ready };
})();
