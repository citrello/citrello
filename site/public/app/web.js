// Citrello web app: the desktop app's interface (ui.html), with its requests answered in this browser.
// Every fetch("/api/...") the interface makes goes to worker.js (Citrello's Python, running here) instead of a
// local server, and links to /api/... files (exports, fixed documents) become downloads made in the browser.
(function () {
  "use strict";
  document.documentElement.classList.add("web");
  // a locally served copy can be pointed at a local test bridge: /app/?bridge=http://localhost:8787 (read now:
  // the page tidies the address bar when it starts)
  const params = new URLSearchParams(location.search);
  const LOCAL = ["localhost", "127.0.0.1"].includes(location.hostname);
  const bridge = params.get("bridge");
  // tests only (a locally served copy): a stand-in for Mendeley's sign-in page and API
  const mendeleyAuth = (LOCAL && params.get("mendeley_auth")) || "https://api.mendeley.com/oauth/authorize";
  const mendeleyApi = LOCAL && params.get("mendeley_api");

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
    const p = cover.querySelector("#webCoverText");  // works before the loading screen is on the page too
    if (p) p.textContent = t;
    cover.classList.toggle("err", !!err);
  };
  let coverGone = false;
  const hideCover = () => { coverGone = true; cover.remove(); };
  document.addEventListener("DOMContentLoaded", () => { if (!coverGone) document.body.appendChild(cover); });

  // ---------- the worker ----------
  let worker = null, readyResolve, readyReject;
  const ready = new Promise((res, rej) => { readyResolve = res; readyReject = rej; });
  ready.catch(() => {});
  const pending = new Map();
  const progress = new Map(); // path of a progress poll -> latest answer, while a job keeps Python busy
  let nextId = 0;

  function startWorker() {
    // this tab holds the library (one tab at a time): the Citrello extension uses this one, never a second
    document.documentElement.dataset.citrelloMain = "1";
    const q = new URLSearchParams();
    if (bridge) q.set("bridge", bridge);
    if (mendeleyApi) q.set("mendeley_api", mendeleyApi);
    worker = new Worker("worker.js" + (q.toString() ? "?" + q : ""));
    worker.onmessage = (ev) => {
      const m = ev.data;
      if (m.type === "status") coverText(m.text);
      else if (m.type === "ready") { hideCover(); readyResolve(); }
      else if (m.type === "error") { coverText(m.text + " Try reloading the page, or use another browser.", true); readyReject(new Error(m.text)); }
      else if (m.type === "progress") { if (m.body) progress.set(m.path, m.body); else progress.delete(m.path); }
      else if (m.type === "library-changed") {
        if (typeof loadLibrary === "function") loadLibrary();
        webDocuments().catch(() => {});
      }
      else if (m.type === "connection") window.dispatchEvent(new CustomEvent("citrello-connection", { detail: m.state }));
      else if (m.type === "storage-error") console.warn("Citrello: couldn't save to the browser's storage:", m.text);
      else if (m.type === "response" && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    };
    worker.onerror = (e) => { coverText("Citrello couldn't start: " + (e.message || "unknown error"), true); readyReject(e); };
  }

  // Ask for the browser to keep this site's storage (fewer automatic clean-ups, e.g. Safari after 7 days)
  if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});

  // The browser button's pop-up (clip.html) uses the open Citrello tab when there is one: it asks over a
  // BroadcastChannel (same site only), and that tab answers its /api/clip/ requests. With no tab open, the pop-up
  // opens Citrello itself.
  const CLIP = !!window.CITRELLO_CLIP;
  const channel = "BroadcastChannel" in window ? new BroadcastChannel("citrello-web-app") : null;
  const me = Math.random().toString(36).slice(2);
  let relay = false; // this pop-up's requests go to the open Citrello tab

  function askOpenTab() {
    if (channel && !worker && !relay) channel.postMessage({ type: "ping", from: me });
  }

  // One tab at a time: two tabs would each keep their own copy of the library and overwrite each other.
  function startHere() {
    if (!navigator.locks) return startWorker();
    navigator.locks.request("citrello-web-app", { ifAvailable: true }, (lock) => {
      if (relay) return null;
      if (!lock) {
        if (CLIP) {  // the open tab is still starting: keep asking it
          coverText("Waiting for your open Citrello tab…");
          setTimeout(() => { askOpenTab(); if (!relay) setTimeout(startHere, 1000); }, 500);
          return null;
        }
        coverText("Citrello is already open in another tab or window. Use that one, or close it and reload this page.", true);
        readyReject(new Error("Citrello is open in another tab"));
        return null;
      }
      startWorker();
      return new Promise(() => {}); // keep the lock while this tab is open
    });
  }

  if (channel) channel.addEventListener("message", async (ev) => {
    const m = ev.data || {};
    if (m.type === "pong" && m.to === me && CLIP && !worker && !relay) {
      relay = true;
      hideCover();
      readyResolve();
    } else if (m.type === "response" && m.to === me && pending.has(m.id)) {
      pending.get(m.id)(m);
      pending.delete(m.id);
    } else if (m.type === "ping" && worker && !relay) {
      try { await ready; } catch (e) { return; }
      channel.postMessage({ type: "pong", to: m.from });
    } else if (m.type === "request" && m.to === "open-tab" && worker && !relay) {
      const url = new URL(m.path, location.origin);
      let res;
      if (!url.pathname.startsWith("/api/clip/")) {  // the pop-up only adds what it found
        res = { status: 403, ctype: "application/json", body: new TextEncoder().encode('{"error":"not allowed"}') };
      } else {
        const r = await apiFetch(url, { method: m.method, headers: m.headers, body: m.body });
        res = { status: r.status, ctype: r.headers.get("Content-Type"), body: new Uint8Array(await r.arrayBuffer()) };
        if ((url.pathname === "/api/clip/add" || url.pathname === "/api/clip/fulltext") && r.ok && typeof loadLibrary === "function") loadLibrary();
      }
      channel.postMessage({ type: "response", to: m.from, id: m.id, headers: {}, ...res });
    }
  });

  if (CLIP && channel) {
    askOpenTab();
    setTimeout(() => { if (!relay) startHere(); }, 600);
  } else {
    startHere();
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
      if (relay) channel.postMessage({ type: "request", to: "open-tab", from: me, id, method,
                                       path: url.pathname + url.search, headers, body });
      else worker.postMessage({ type: "request", id, method,
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
  // Word files the connector saved (Settings → ChatGPT and Claude)
  async function webDocuments() {
    const box = document.getElementById("webDocs");
    if (!box) return;
    const { documents } = await (await fetch("/api/web/documents")).json();
    box.innerHTML = "";
    const wrap = document.getElementById("webDocsBox");
    if (wrap) wrap.classList.toggle("hidden", !documents.length);  // only when there's something to download
    if (!documents.length) return;
    for (const d of documents) {
      const row = document.createElement("div");
      row.className = "row";
      row.style.margin = "4px 0";
      const a = document.createElement("a");
      a.href = "/api/web/documents/" + encodeURIComponent(d.name);
      a.textContent = d.name;
      const when = document.createElement("span");
      when.className = "muted";
      when.textContent = " · " + new Date(d.time * 1000).toLocaleString();
      row.append(a, when);
      box.appendChild(row);
    }
  }
  // when the connection changes, the sidebar shows it straight away
  window.addEventListener("citrello-connection", () => {
    if (typeof renderSideConn === "function") fetch("/api/account").then((r) => r.json()).then(renderSideConn).catch(() => {});
  });
  window.addEventListener("citrello-downloaded", (e) => { if (e.detail === "/api/web/backup") backupState(); });
  document.addEventListener("DOMContentLoaded", () => {
    if (CLIP) return;
    ready.then(backupState).catch(() => {});
    document.addEventListener("click", (e) => {
      if (e.target.closest && e.target.closest('.side button.navb')) setTimeout(() => {
        backupState().catch(() => {});
        webDocuments().catch(() => {});
      }, 300);
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

  // ---------- settings made at deploy (config.json): e.g. Citrello's Mendeley app ----------
  let configPromise = null;
  const config = () => configPromise || (configPromise = realFetch("config.json", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : {})).catch(() => ({})));

  // ---------- "Import my Mendeley library": sign in to Mendeley in a pop-up ----------
  // Mendeley sends the person back to /app/mendeley.html with a short-lived token, which hands it to this tab.
  // No Citrello server is involved; the library is then read straight from Mendeley by this browser.
  async function mendeleyToken() {
    // open the window straight away: Safari only allows pop-ups opened during the click itself
    const w = window.open("", "citrello-mendeley", "width=520,height=720");
    if (!w) throw new Error("Your browser blocked the Mendeley window. Allow pop-ups for this site and click again.");
    const cfg = await config();
    if (!cfg.mendeley_client_id) {
      w.close();
      throw new Error("Mendeley import isn't set up yet. Export from Mendeley instead (see below).");
    }
    const state = Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
    const url = mendeleyAuth + "?" + new URLSearchParams({ client_id: cfg.mendeley_client_id, response_type: "token",
      redirect_uri: location.origin + "/app/mendeley.html", scope: "all", state });
    const ch = new BroadcastChannel("citrello-mendeley");
    const answer = new Promise((resolve, reject) => {
      ch.onmessage = (ev) => {
        const m = ev.data || {};
        if (m.state !== state) return;
        ch.close();
        if (m.access_token) resolve(m.access_token);
        else reject(new Error("Mendeley didn't sign you in" + (m.error ? ": " + m.error : ".")));
      };
    });
    w.location.href = url;
    return answer;
  }
  config().then((cfg) => {
    const show = () => document.querySelectorAll(".mendeley-web").forEach((el) => el.classList.toggle("hidden", !cfg.mendeley_client_id));
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", show); else show();
  });

  window.citrelloWeb = { ready, config, mendeleyToken };
})();
