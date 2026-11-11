// Citrello bridge: accounts, sign-in, and a remote MCP endpoint for ChatGPT / claude.ai that relays
// to the Citrello app on the user's computer.
//
//   /mcp                       remote MCP server (Streamable HTTP, OAuth 2.1 bearer tokens)
//   /.well-known/...           OAuth metadata (RFC 9728, RFC 8414)
//   /register /authorize /token  OAuth: dynamic client registration, email-code sign-in, PKCE
//   /api/auth/start|verify     the Citrello app signs in with an emailed code
//   /api/me ...                account settings for the app
//   /device/connect            the app's WebSocket
//   /admin/stats|broadcast     user counts and feature emails (ADMIN_TOKEN)
import { Relay } from "./relay.js";
import * as pages from "./pages.js";
import * as ai from "./ai.js";

export { Relay };

const ACCESS_TTL = 3600;               // 1 hour
const REFRESH_TTL = 180 * 86400;       // 180 days
const LOGIN_CODE_TTL = 600;            // 10 minutes
const AUTH_CODE_TTL = 300;             // 5 minutes
const MAX_CODES_PER_HOUR = 5;
const MAX_CODE_ATTEMPTS = 5;
const SCOPE = "citrello";

class HttpError extends Error {
  constructor(status, code, message) { super(message || code); this.status = status; this.code = code; }
}

const now = () => Math.floor(Date.now() / 1000);

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type, Mcp-Session-Id, Mcp-Protocol-Version",
  "Access-Control-Expose-Headers": "WWW-Authenticate, Mcp-Session-Id",
};

function json(obj, status = 200, headers = {}) {
  return new Response(JSON.stringify(obj), { status, headers: {
    "Content-Type": "application/json", "Cache-Control": "no-store", ...CORS, ...headers } });
}

function b64url(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

const randomToken = (n = 32) => b64url(crypto.getRandomValues(new Uint8Array(n)));

async function sha256(text) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
}

const hashHex = async (text) => [...(await sha256(text))].map((b) => b.toString(16).padStart(2, "0")).join("");

async function hmac(key, text) {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" },
    false, ["sign"]);
  return b64url(new Uint8Array(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(text))));
}

function safeEqual(a, b) {
  a = String(a); b = String(b);
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

async function readBody(request) {
  const type = request.headers.get("Content-Type") || "";
  if (type.includes("application/json")) {
    try { return await request.json(); } catch { throw new HttpError(400, "invalid_request", "Body is not valid JSON"); }
  }
  if (type.includes("application/x-www-form-urlencoded") || type.includes("multipart/form-data")) {
    return Object.fromEntries(await request.formData());
  }
  const text = await request.text();
  if (!text) return {};
  try { return JSON.parse(text); } catch { return Object.fromEntries(new URLSearchParams(text)); }
}

function bearer(request) {
  const h = request.headers.get("Authorization") || "";
  return h.toLowerCase().startsWith("bearer ") ? h.slice(7).trim() : "";
}

// Browsers can't add headers to a WebSocket, so the web app (citrello.app/app) sends its device token as a
// WebSocket subprotocol instead: new WebSocket(url, ["citrello", token]).
const WS_PROTOCOL = "citrello";
function wsProtocolToken(request) {
  const offered = (request.headers.get("Sec-WebSocket-Protocol") || "").split(",").map((s) => s.trim());
  return offered.includes(WS_PROTOCOL) ? offered.find((p) => p.startsWith("cdt_")) || "" : "";
}

// ---------------------------------------------------------------------------
// Email
// ---------------------------------------------------------------------------

async function sendEmails(env, messages) {
  if (!env.RESEND_API_KEY) {
    for (const m of messages) console.log(`[email not sent: no RESEND_API_KEY] to=${m.to} subject=${m.subject}`);
    return;
  }
  const from = env.MAIL_FROM || "Citrello <hello@citrello.app>";
  for (let i = 0; i < messages.length; i += 100) {
    const batch = messages.slice(i, i + 100).map((m) => ({ from, to: [m.to], subject: m.subject, text: m.text,
      ...(m.html ? { html: m.html } : {}), ...(m.headers ? { headers: m.headers } : {}) }));
    const single = batch.length === 1;
    const r = await fetch(single ? "https://api.resend.com/emails" : "https://api.resend.com/emails/batch", {
      method: "POST",
      headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify(single ? batch[0] : batch),
    });
    if (!r.ok) {
      console.error("Resend error", r.status, await r.text());
      throw new HttpError(502, "email_failed", "The email couldn't be sent. Please try again in a minute.");
    }
  }
}

// ---------------------------------------------------------------------------
// Users and sign-in codes
// ---------------------------------------------------------------------------

function normEmail(email) {
  const e = String(email || "").trim().toLowerCase();
  if (e.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e)) {
    throw new HttpError(400, "invalid_email", "Please enter a valid email address.");
  }
  return e;
}

async function sendLoginCode(env, rawEmail) {
  const email = normEmail(rawEmail);
  const t = now();
  const row = await env.DB.prepare("SELECT sent_count, window_start FROM login_codes WHERE email = ?").bind(email).first();
  let sent = 0, windowStart = t;
  if (row && row.window_start > t - 3600) { sent = row.sent_count; windowStart = row.window_start; }
  if (sent >= MAX_CODES_PER_HOUR) {
    throw new HttpError(429, "too_many_codes", "Too many codes requested. Please wait an hour and try again.");
  }
  const n = crypto.getRandomValues(new Uint32Array(1))[0] % 1_000_000;
  const code = String(n).padStart(6, "0");
  await env.DB.prepare(`INSERT INTO login_codes (email, code_hash, expires_at, attempts, sent_count, window_start)
      VALUES (?1, ?2, ?3, 0, ?4, ?5)
      ON CONFLICT(email) DO UPDATE SET code_hash = ?2, expires_at = ?3, attempts = 0, sent_count = ?4, window_start = ?5`)
    .bind(email, await hashHex(`${email}:${code}`), t + LOGIN_CODE_TTL, sent + 1, windowStart).run();
  await sendEmails(env, [{
    to: email,
    subject: `${code} is your Citrello code`,
    text: `Your Citrello sign-in code is ${code}\n\nIt expires in 10 minutes. If you didn't ask for it, ignore this email.\n\n` +
          `Citrello - references, the sweet part.\nhttps://citrello.app`,
    html: `<div style="font-family:system-ui,sans-serif;font-size:16px;color:#2b2a26">
<p>Your Citrello sign-in code is</p><p style="font-size:30px;font-weight:700;letter-spacing:6px">${code}</p>
<p style="color:#6f6b60">It expires in 10 minutes. If you didn't ask for it, ignore this email.</p>
<p style="color:#6f6b60">Citrello · references, the sweet part · <a href="https://citrello.app">citrello.app</a></p></div>`,
  }]);
  return { email, code: env.DEV_RETURN_CODES === "1" ? code : undefined };
}

// Checks the code; on success returns the user (created on first sign-in).
async function checkLoginCode(env, rawEmail, rawCode) {
  const email = normEmail(rawEmail);
  const code = String(rawCode || "").replace(/\D/g, "");
  const t = now();
  const row = await env.DB.prepare("SELECT code_hash, expires_at, attempts FROM login_codes WHERE email = ?")
    .bind(email).first();
  if (!row || !row.code_hash || row.expires_at < t) {
    throw new HttpError(400, "code_expired", "This code has expired. Please ask for a new one.");
  }
  if (row.attempts >= MAX_CODE_ATTEMPTS) {
    throw new HttpError(400, "too_many_attempts", "Too many wrong codes. Please ask for a new one.");
  }
  if (!safeEqual(row.code_hash, await hashHex(`${email}:${code}`))) {
    await env.DB.prepare("UPDATE login_codes SET attempts = attempts + 1 WHERE email = ?").bind(email).run();
    throw new HttpError(400, "wrong_code", "That code isn't right. Check the latest email from Citrello.");
  }
  await env.DB.prepare("UPDATE login_codes SET code_hash = '', expires_at = 0 WHERE email = ?").bind(email).run();
  await env.DB.prepare("INSERT INTO users (id, email, created_at, last_seen) VALUES (?, ?, ?, ?) ON CONFLICT(email) DO NOTHING")
    .bind("u_" + randomToken(12), email, t, t).run();
  await env.DB.prepare("UPDATE users SET last_seen = ? WHERE email = ?").bind(t, email).run();
  return env.DB.prepare("SELECT * FROM users WHERE email = ?").bind(email).first();
}

async function setNewsletter(env, userId, on) {
  await env.DB.prepare("UPDATE users SET newsletter = ?, newsletter_at = ? WHERE id = ?")
    .bind(on ? 1 : 0, on ? now() : null, userId).run();
}

async function deviceFromRequest(env, request, allowWsProtocol = false) {
  const token = bearer(request) || (allowWsProtocol ? wsProtocolToken(request) : "");
  if (!token.startsWith("cdt_")) throw new HttpError(401, "unauthorized", "Not signed in");
  const row = await env.DB.prepare(`SELECT d.id AS device_id, d.name AS device_name, d.last_seen AS device_seen,
      u.id AS user_id, u.email, u.newsletter, u.last_seen
      FROM devices d JOIN users u ON u.id = d.user_id WHERE d.token_hash = ?`).bind(await hashHex(token)).first();
  if (!row) throw new HttpError(401, "unauthorized", "This computer has been signed out. Please sign in again.");
  const t = now();
  if (row.device_seen < t - 600) {
    await env.DB.batch([
      env.DB.prepare("UPDATE devices SET last_seen = ? WHERE id = ?").bind(t, row.device_id),
      env.DB.prepare("UPDATE users SET last_seen = ? WHERE id = ?").bind(t, row.user_id),
    ]);
  }
  return row;
}

const relay = (env, userId) => env.RELAY.get(env.RELAY.idFromName(userId));

async function relayCall(env, userId, path, body) {
  const r = await relay(env, userId).fetch(`https://relay${path}`, body === undefined ? {} :
    { method: "POST", body: JSON.stringify(body) });
  return r.json();
}

function userPublic(u) {
  return { id: u.user_id || u.id, email: u.email, newsletter: !!u.newsletter };
}

// ---------------------------------------------------------------------------
// App API
// ---------------------------------------------------------------------------

async function apiAuthStart(request, env) {
  const b = await readBody(request);
  const res = await sendLoginCode(env, b.email);
  return json({ ok: true, email: res.email, ...(res.code ? { dev_code: res.code } : {}) });
}

async function apiAuthVerify(request, env) {
  const b = await readBody(request);
  const user = await checkLoginCode(env, b.email, b.code);
  if (b.newsletter === true || b.newsletter === "true" || b.newsletter === "1") {
    await setNewsletter(env, user.id, true);
    user.newsletter = 1;
  }
  const token = "cdt_" + randomToken(32);
  const deviceId = "d_" + randomToken(12);
  const t = now();
  const clip = (s, n) => String(s || "").slice(0, n);
  await env.DB.prepare(`INSERT INTO devices (id, user_id, name, platform, app_version, token_hash, created_at, last_seen)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)`)
    .bind(deviceId, user.id, clip(b.device_name, 80), clip(b.platform, 80), clip(b.app_version, 40),
          await hashHex(token), t, t).run();
  return json({ token, device_id: deviceId, user: userPublic(user) });
}

async function apiMe(request, env) {
  const d = await deviceFromRequest(env, request);
  if (request.method === "POST") {
    const b = await readBody(request);
    if ("newsletter" in b) await setNewsletter(env, d.user_id, !!b.newsletter);
  }
  const user = await env.DB.prepare("SELECT * FROM users WHERE id = ?").bind(d.user_id).first();
  const { results } = await env.DB.prepare(
    "SELECT id, name, platform, app_version, created_at, last_seen FROM devices WHERE user_id = ? ORDER BY last_seen DESC")
    .bind(d.user_id).all();
  const status = await relayCall(env, d.user_id, "/status");
  const online = new Set(status.devices.map((x) => x.deviceId));
  const conn = await env.DB.prepare(
    "SELECT COUNT(DISTINCT client_id) AS n FROM oauth_tokens WHERE user_id = ? AND kind = 'refresh' AND expires_at > ?")
    .bind(d.user_id, now()).first();
  const origin = new URL(request.url).origin;
  return json({
    user: userPublic(user),
    device_id: d.device_id,
    devices: results.map((x) => ({ ...x, this_device: x.id === d.device_id, online: online.has(x.id) })),
    connector_url: `${origin}/mcp`,
    connectors: conn ? conn.n : 0,
  });
}

async function apiSignOut(request, env) {
  const d = await deviceFromRequest(env, request);
  const b = await readBody(request).catch(() => ({}));
  const target = b.device_id || d.device_id;
  await env.DB.prepare("DELETE FROM devices WHERE id = ? AND user_id = ?").bind(target, d.user_id).run();
  await relayCall(env, d.user_id, "/disconnect", { deviceId: target });
  return json({ ok: true });
}

async function apiDeleteAccount(request, env) {
  const d = await deviceFromRequest(env, request);
  const user = await env.DB.prepare("SELECT email FROM users WHERE id = ?").bind(d.user_id).first();
  await env.DB.batch([
    env.DB.prepare("DELETE FROM oauth_tokens WHERE user_id = ?").bind(d.user_id),
    env.DB.prepare("DELETE FROM oauth_codes WHERE user_id = ?").bind(d.user_id),
    env.DB.prepare("DELETE FROM devices WHERE user_id = ?").bind(d.user_id),
    env.DB.prepare("DELETE FROM login_codes WHERE email = ?").bind(user ? user.email : ""),
    env.DB.prepare("DELETE FROM users WHERE id = ?").bind(d.user_id),
  ]);
  await relayCall(env, d.user_id, "/disconnect", {});
  return json({ ok: true });
}

async function deviceConnect(request, env) {
  if (request.headers.get("Upgrade") !== "websocket") return new Response("expected websocket", { status: 426 });
  const d = await deviceFromRequest(env, request, true);
  await env.DB.batch([
    env.DB.prepare("UPDATE devices SET last_seen = ? WHERE id = ?").bind(now(), d.device_id),
    env.DB.prepare("UPDATE users SET last_seen = ? WHERE id = ?").bind(now(), d.user_id),
  ]);
  const headers = new Headers(request.headers);
  headers.set("X-Device-Id", d.device_id);
  headers.set("X-Device-Name", d.device_name || "");
  headers.set("X-Device-Protocol", bearer(request) ? "" : WS_PROTOCOL);
  headers.delete("Sec-WebSocket-Protocol"); // the token must not travel further than this
  return relay(env, d.user_id).fetch(new Request("https://relay/connect", { headers }));
}

// ---------------------------------------------------------------------------
// OAuth 2.1 for connectors
// ---------------------------------------------------------------------------

function authServerMetadata(origin) {
  return {
    issuer: origin,
    authorization_endpoint: `${origin}/authorize`,
    token_endpoint: `${origin}/token`,
    registration_endpoint: `${origin}/register`,
    revocation_endpoint: `${origin}/revoke`,
    response_types_supported: ["code"],
    grant_types_supported: ["authorization_code", "refresh_token"],
    code_challenge_methods_supported: ["S256"],
    token_endpoint_auth_methods_supported: ["none", "client_secret_post", "client_secret_basic"],
    scopes_supported: [SCOPE],
    service_documentation: "https://citrello.app",
  };
}

function okRedirectUri(u) {
  try {
    const url = new URL(u);
    if (url.hash) return false;
    if (url.protocol === "https:") return true;
    return url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  } catch { return false; }
}

async function register(request, env) {
  const b = await readBody(request);
  const uris = Array.isArray(b.redirect_uris) ? b.redirect_uris.map(String) : [];
  if (!uris.length || uris.length > 10 || !uris.every(okRedirectUri)) {
    return json({ error: "invalid_redirect_uri", error_description: "redirect_uris must be https (or http://localhost)" }, 400);
  }
  const method = b.token_endpoint_auth_method || "client_secret_basic";
  if (!["none", "client_secret_post", "client_secret_basic"].includes(method)) {
    return json({ error: "invalid_client_metadata", error_description: "Unsupported token_endpoint_auth_method" }, 400);
  }
  const id = "c_" + randomToken(16);
  const secret = method === "none" ? null : "cs_" + randomToken(32);
  const name = String(b.client_name || "").replace(/[\u0000-\u001f]/g, "").slice(0, 100);
  await env.DB.prepare("INSERT INTO oauth_clients (id, name, redirect_uris, secret_hash, created_at) VALUES (?, ?, ?, ?, ?)")
    .bind(id, name, JSON.stringify(uris), secret ? await hashHex(secret) : null, now()).run();
  return json({
    client_id: id, client_id_issued_at: now(), ...(secret ? { client_secret: secret, client_secret_expires_at: 0 } : {}),
    client_name: name, redirect_uris: uris, token_endpoint_auth_method: method,
    grant_types: ["authorization_code", "refresh_token"], response_types: ["code"], scope: SCOPE,
  }, 201);
}

async function getClient(env, id) {
  if (!id) return null;
  const c = await env.DB.prepare("SELECT * FROM oauth_clients WHERE id = ?").bind(String(id)).first();
  if (c) c.redirect_uris = JSON.parse(c.redirect_uris);
  return c;
}

const AUTH_PARAMS = ["response_type", "client_id", "redirect_uri", "state", "code_challenge",
                     "code_challenge_method", "scope", "resource"];

async function authorize(request, env) {
  const input = request.method === "POST" ? await readBody(request)
    : Object.fromEntries(new URL(request.url).searchParams);
  const params = {};
  for (const k of AUTH_PARAMS) if (input[k] !== undefined && input[k] !== "") params[k] = String(input[k]);

  const client = await getClient(env, params.client_id);
  if (!client) return pages.message("Can't sign in", "This connector isn't registered. Remove it and add it again.", 400);
  if (!params.redirect_uri && client.redirect_uris.length === 1) params.redirect_uri = client.redirect_uris[0];
  if (!client.redirect_uris.includes(params.redirect_uri)) {
    return pages.message("Can't sign in", "The connector sent an unexpected return address.", 400);
  }
  const back = (extra) => {
    const u = new URL(params.redirect_uri);
    for (const [k, v] of Object.entries(extra)) u.searchParams.set(k, v);
    if (params.state) u.searchParams.set("state", params.state);
    return Response.redirect(u.toString(), 302);
  };
  if (params.response_type !== "code") return back({ error: "unsupported_response_type" });
  if (!params.code_challenge || (params.code_challenge_method || "plain") !== "S256") {
    return back({ error: "invalid_request", error_description: "PKCE with S256 is required" });
  }

  const step = request.method === "POST" ? input.step : "start";
  if (step === "send") {
    try {
      const res = await sendLoginCode(env, input.email);
      return pages.authorize({ params, client, step: "code", email: res.email,
        info: res.code ? `Development mode: your code is ${res.code}` : "" });
    } catch (e) {
      if (!(e instanceof HttpError)) throw e;
      return pages.authorize({ params, client, step: "start", email: input.email, error: e.message });
    }
  }
  if (step === "verify") {
    let user;
    try {
      user = await checkLoginCode(env, input.email, input.code);
    } catch (e) {
      if (!(e instanceof HttpError)) throw e;
      return pages.authorize({ params, client, step: "code", email: input.email, error: e.message });
    }
    const code = "cac_" + randomToken(32);
    await env.DB.prepare(`INSERT INTO oauth_codes (code_hash, client_id, user_id, redirect_uri, challenge, scope, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)`)
      .bind(await hashHex(code), client.id, user.id, params.redirect_uri, params.code_challenge,
            params.scope || SCOPE, now() + AUTH_CODE_TTL).run();
    return back({ code, iss: new URL(request.url).origin });
  }
  return pages.authorize({ params, client, step: "start" });
}

async function clientFromTokenRequest(request, env, b) {
  let id = b.client_id, secret = b.client_secret;
  const h = request.headers.get("Authorization") || "";
  if (h.toLowerCase().startsWith("basic ")) {
    try {
      const [u, p] = atob(h.slice(6).trim()).split(":");
      id = decodeURIComponent(u); secret = decodeURIComponent(p || "");
    } catch { throw new HttpError(401, "invalid_client"); }
  }
  const client = await getClient(env, id);
  if (!client) throw new HttpError(401, "invalid_client", "Unknown client");
  if (client.secret_hash && !safeEqual(client.secret_hash, await hashHex(String(secret || "")))) {
    throw new HttpError(401, "invalid_client", "Wrong client secret");
  }
  return client;
}

async function issueTokens(env, clientId, userId, scope) {
  const access = "cat_" + randomToken(32), refresh = "crt_" + randomToken(32), t = now();
  const ins = "INSERT INTO oauth_tokens (token_hash, kind, client_id, user_id, scope, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)";
  await env.DB.batch([
    env.DB.prepare(ins).bind(await hashHex(access), "access", clientId, userId, scope, t, t + ACCESS_TTL),
    env.DB.prepare(ins).bind(await hashHex(refresh), "refresh", clientId, userId, scope, t, t + REFRESH_TTL),
  ]);
  return json({ access_token: access, token_type: "Bearer", expires_in: ACCESS_TTL, refresh_token: refresh, scope });
}

async function token(request, env) {
  const b = await readBody(request);
  const client = await clientFromTokenRequest(request, env, b);
  if (b.grant_type === "authorization_code") {
    const h = await hashHex(String(b.code || ""));
    const row = await env.DB.prepare("DELETE FROM oauth_codes WHERE code_hash = ? RETURNING *").bind(h).first();
    if (!row || row.expires_at < now() || row.client_id !== client.id) throw new HttpError(400, "invalid_grant", "Invalid or expired code");
    if (b.redirect_uri && b.redirect_uri !== row.redirect_uri) throw new HttpError(400, "invalid_grant", "redirect_uri mismatch");
    const verifier = String(b.code_verifier || "");
    if (verifier.length < 43 || b64url(await sha256(verifier)) !== row.challenge) {
      throw new HttpError(400, "invalid_grant", "PKCE verification failed");
    }
    return issueTokens(env, client.id, row.user_id, row.scope);
  }
  if (b.grant_type === "refresh_token") {
    const h = await hashHex(String(b.refresh_token || ""));
    const row = await env.DB.prepare("DELETE FROM oauth_tokens WHERE token_hash = ? AND kind = 'refresh' RETURNING *").bind(h).first();
    if (!row || row.expires_at < now() || row.client_id !== client.id) throw new HttpError(400, "invalid_grant", "Invalid or expired refresh token");
    return issueTokens(env, client.id, row.user_id, row.scope);
  }
  throw new HttpError(400, "unsupported_grant_type");
}

async function revoke(request, env) {
  const b = await readBody(request);
  await clientFromTokenRequest(request, env, b);
  await env.DB.prepare("DELETE FROM oauth_tokens WHERE token_hash = ?").bind(await hashHex(String(b.token || ""))).run();
  return json({});
}

// ---------------------------------------------------------------------------
// MCP endpoint
// ---------------------------------------------------------------------------

async function mcp(request, env) {
  const origin = new URL(request.url).origin;
  const unauthorized = () => json({ jsonrpc: "2.0", id: null, error: { code: -32001, message: "Sign-in required" } }, 401, {
    "WWW-Authenticate": `Bearer resource_metadata="${origin}/.well-known/oauth-protected-resource", scope="${SCOPE}"` });
  const tok = bearer(request);
  if (!tok.startsWith("cat_")) return unauthorized();
  const row = await env.DB.prepare(`SELECT t.user_id, u.email, u.last_seen FROM oauth_tokens t JOIN users u ON u.id = t.user_id
      WHERE t.token_hash = ? AND t.kind = 'access' AND t.expires_at > ?`).bind(await hashHex(tok), now()).first();
  if (!row) return unauthorized();

  if (request.method === "GET") return new Response("This server doesn't stream events", { status: 405, headers: { ...CORS, Allow: "POST" } });
  if (request.method === "DELETE") return new Response(null, { status: 204, headers: CORS });
  if (row.last_seen < now() - 600) {
    await env.DB.prepare("UPDATE users SET last_seen = ? WHERE id = ?").bind(now(), row.user_id).run();
  }

  let body;
  try { body = await request.json(); } catch {
    return json({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } }, 400);
  }
  const msgs = Array.isArray(body) ? body : [body];
  const replies = [];
  for (const msg of msgs) {
    const { reply } = await relayCall(env, row.user_id, "/rpc", { msg, email: row.email });
    if (reply) replies.push(reply);
  }
  if (!replies.length) return new Response(null, { status: 202, headers: CORS });
  return json(Array.isArray(body) ? replies : replies[0]);
}

// ---------------------------------------------------------------------------
// Anonymous usage count (the app sends one ping a day; no personal data)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Free (open-access) PDFs for the web app's "Find free full texts"
// ---------------------------------------------------------------------------
// Most repositories don't let web pages download their files, so the web app asks here with a DOI. Only copies
// Unpaywall lists as open access are fetched; the PDF goes straight back to the browser (which keeps only its
// text) and nothing is stored here. Only the DOI is sent.
const OA_UA = "Citrello/1.0 (hello@citrello.app)";
async function oaPdf(request) {
  const doi = (new URL(request.url).searchParams.get("doi") || "").trim();
  if (!/^10\.\d{4,9}\/[^\s?#]{1,300}$/.test(doi)) throw new HttpError(400, "bad_doi", "That isn't a DOI.");
  const up = await fetch(`https://api.unpaywall.org/v2/${encodeURI(doi)}?email=hello@citrello.app`,
                         { headers: { "User-Agent": OA_UA, Accept: "application/json" } });
  if (!up.ok) throw new HttpError(404, "not_found", "No free copy found.");
  const d = await up.json();
  const urls = [...new Set([d.best_oa_location, ...(d.oa_locations || [])]
    .filter((l) => l && l.url_for_pdf).map((l) => l.url_for_pdf))].slice(0, 4);
  for (const u of urls) {
    try {
      if (!/^https?:\/\//.test(u)) continue;
      const r = await fetch(u, { headers: { "User-Agent": OA_UA, Accept: "application/pdf" }, redirect: "follow" });
      if (!r.ok) continue;
      const buf = await r.arrayBuffer();
      if (buf.byteLength > 40e6 || new TextDecoder().decode(new Uint8Array(buf.slice(0, 5))) !== "%PDF-") continue;
      return new Response(buf, { headers: { "Content-Type": "application/pdf", "Cache-Control": "no-store", ...CORS } });
    } catch {}
  }
  throw new HttpError(404, "not_found", "No free copy could be downloaded.");
}

async function usagePing(request, env) {
  const b = await readBody(request);
  const id = String(b.install_id || "");
  if (!/^[0-9a-f]{32}$/.test(id)) throw new HttpError(400, "invalid_request", "Bad install_id");
  const clip = (v, n) => String(v ?? "").replace(/[^\w.\- ]/g, "").slice(0, n);
  const os = ["windows", "mac", "linux"].includes(b.os) ? b.os : "other";
  const t = now(), dayStart = t - (t % 86400);
  await env.DB.prepare(`INSERT INTO installs (id, first_seen, last_seen, days, version, os, os_version, store, signed_in)
      VALUES (?1, ?2, ?2, 1, ?3, ?4, ?5, ?6, ?7)
      ON CONFLICT(id) DO UPDATE SET days = days + (CASE WHEN last_seen < ?8 THEN 1 ELSE 0 END), last_seen = ?2,
        version = ?3, os = ?4, os_version = ?5, store = ?6, signed_in = ?7`)
    .bind(id, t, clip(b.version, 20), os, clip(b.os_version, 20), b.store ? 1 : 0, b.signed_in ? 1 : 0, dayStart).run();
  return json({ ok: true });
}

// ---------------------------------------------------------------------------
// Latest release, for the app's update check (GitHub's answer, cached for everyone)
// ---------------------------------------------------------------------------

async function latestRelease(request, env, ctx) {
  const cache = caches.default;
  const key = new Request("https://cache.citrello.internal/latest-release");
  const hit = await cache.match(key);
  if (hit) return new Response(hit.body, hit);
  const r = await fetch("https://api.github.com/repos/citrello/citrello/releases/latest", {
    headers: { "User-Agent": "citrello-bridge", Accept: "application/vnd.github+json" } });
  if (!r.ok) return json({ error: "unavailable" }, 502);
  const rel = await r.json();
  const body = {
    tag_name: rel.tag_name, html_url: rel.html_url, published_at: rel.published_at,
    assets: (rel.assets || []).map((a) => ({ name: a.name, size: a.size, digest: a.digest || "",
                                             browser_download_url: a.browser_download_url })),
  };
  const res = json(body, 200, { "Cache-Control": "public, max-age=600" });
  if (ctx) ctx.waitUntil(cache.put(key, res.clone()));
  return res;
}

// ---------------------------------------------------------------------------
// Unsubscribe and admin
// ---------------------------------------------------------------------------

const unsubscribeKey = (env) => `unsubscribe:${env.ADMIN_TOKEN || "dev"}`;

async function unsubscribeUrl(env, origin, userId) {
  return `${origin}/unsubscribe?u=${encodeURIComponent(userId)}&s=${await hmac(unsubscribeKey(env), userId)}`;
}

async function unsubscribe(request, env) {
  const url = new URL(request.url);
  const p = request.method === "POST" ? { ...Object.fromEntries(url.searchParams), ...(await readBody(request)) }
    : Object.fromEntries(url.searchParams);
  if (!p.u || !p.s || !safeEqual(p.s, await hmac(unsubscribeKey(env), p.u))) {
    return pages.message("Link not valid", "This unsubscribe link isn't valid. You can turn emails off in the Citrello app.", 400);
  }
  if (request.method !== "POST") return pages.unsubscribe(p, false);
  await setNewsletter(env, p.u, false);
  return pages.unsubscribe(p, true);
}

function requireAdmin(request, env) {
  if (!env.ADMIN_TOKEN || !safeEqual(bearer(request), env.ADMIN_TOKEN)) throw new HttpError(401, "unauthorized");
}

// Claude checks (src/ai.js): signed-in accounts only; errors are explained in plain words
async function aiCall(request, env, fn) {
  const d = await deviceFromRequest(env, request);
  try {
    return json(await fn(d));
  } catch (e) {
    if (e instanceof ai.AiError) throw new HttpError(e.status, e.code, e.message);
    if (ai.isApiError(e)) {  // the Claude API said no (busy, overloaded, bad key...)
      console.error("Claude API", e.status, e.message);
      throw new HttpError(e.status === 429 || e.status === 529 ? 503 : 502, "ai_unavailable",
                          "Claude is busy or unavailable right now. Please try again in a minute.");
    }
    throw e;
  }
}

// Turn Citrello Pro on or off for an account (until payments exist): {"email": ..., "pro": true}
async function adminPro(request, env) {
  requireAdmin(request, env);
  const b = await readBody(request);
  const r = await env.DB.prepare("UPDATE users SET pro = ? WHERE email = ?").bind(b.pro ? 1 : 0, String(b.email || "").trim().toLowerCase()).run();
  if (!r.meta.changes) throw new HttpError(404, "not_found", "No account with that email.");
  return json({ ok: true, email: b.email, pro: !!b.pro });
}

async function adminStats(request, env) {
  requireAdmin(request, env);
  const t = now(), d7 = t - 7 * 86400, d30 = t - 30 * 86400;
  const q = (sql, ...a) => env.DB.prepare(sql).bind(...a).first().then((r) => r.n);
  return json({
    users: await q("SELECT COUNT(*) AS n FROM users"),
    new_users_7d: await q("SELECT COUNT(*) AS n FROM users WHERE created_at > ?", d7),
    new_users_30d: await q("SELECT COUNT(*) AS n FROM users WHERE created_at > ?", d30),
    active_users_7d: await q("SELECT COUNT(*) AS n FROM users WHERE last_seen > ?", d7),
    active_users_30d: await q("SELECT COUNT(*) AS n FROM users WHERE last_seen > ?", d30),
    newsletter_subscribers: await q("SELECT COUNT(*) AS n FROM users WHERE newsletter = 1"),
    devices: await q("SELECT COUNT(*) AS n FROM devices"),
    users_with_connector: await q("SELECT COUNT(DISTINCT user_id) AS n FROM oauth_tokens WHERE kind = 'refresh' AND expires_at > ?", t),
    installs: {
      total: await q("SELECT COUNT(*) AS n FROM installs WHERE id != ?", ""),
      active_1d: await q("SELECT COUNT(*) AS n FROM installs WHERE last_seen > ?", t - 86400),
      active_7d: await q("SELECT COUNT(*) AS n FROM installs WHERE last_seen > ?", d7),
      active_30d: await q("SELECT COUNT(*) AS n FROM installs WHERE last_seen > ?", d30),
      new_7d: await q("SELECT COUNT(*) AS n FROM installs WHERE first_seen > ?", d7),
      returning_30d: await q("SELECT COUNT(*) AS n FROM installs WHERE last_seen > ? AND days > 1", d30),
      signed_in_30d: await q("SELECT COUNT(*) AS n FROM installs WHERE last_seen > ? AND signed_in = 1", d30),
      by_os_30d: (await env.DB.prepare("SELECT os, COUNT(*) AS n FROM installs WHERE last_seen > ? GROUP BY os ORDER BY n DESC").bind(d30).all()).results,
      by_version_30d: (await env.DB.prepare("SELECT version, COUNT(*) AS n FROM installs WHERE last_seen > ? GROUP BY version ORDER BY n DESC LIMIT 10").bind(d30).all()).results,
    },
  });
}

async function adminBroadcast(request, env) {
  requireAdmin(request, env);
  const b = await readBody(request);
  if (!b.subject || !b.text) throw new HttpError(400, "invalid_request", "subject and text are required");
  const origin = new URL(request.url).origin;
  let users;
  if (b.test_to) {
    users = [await env.DB.prepare("SELECT id, email FROM users WHERE email = ?").bind(normEmail(b.test_to)).first()
             || { id: "test", email: normEmail(b.test_to) }];
  } else {
    if (b.confirm !== true) throw new HttpError(400, "confirm_required", "Send a test first, then repeat with confirm: true");
    users = (await env.DB.prepare("SELECT id, email FROM users WHERE newsletter = 1").all()).results;
  }
  const messages = [];
  for (const u of users) {
    const link = await unsubscribeUrl(env, origin, u.id);
    messages.push({
      to: u.email, subject: String(b.subject),
      text: `${b.text}\n\n--\nYou get this because you asked for news about Citrello features.\nUnsubscribe: ${link}`,
      headers: { "List-Unsubscribe": `<${link}>`, "List-Unsubscribe-Post": "List-Unsubscribe=One-Click" },
    });
  }
  await sendEmails(env, messages);
  return json({ sent: messages.length });
}

async function adminSubscribers(request, env) {
  requireAdmin(request, env);
  const { results } = await env.DB.prepare("SELECT email, newsletter_at FROM users WHERE newsletter = 1 ORDER BY newsletter_at").all();
  const csv = "email,agreed_at\n" + results.map((r) => `${r.email},${new Date(r.newsletter_at * 1000).toISOString()}`).join("\n");
  return new Response(csv, { headers: { "Content-Type": "text/csv; charset=utf-8", "Cache-Control": "no-store" } });
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

async function route(request, env, ctx) {
  // A fixed public address keeps OAuth issuer and resource URLs stable (wrangler dev rewrites the host).
  if (env.PUBLIC_URL) {
    const u = new URL(request.url);
    request = new Request(env.PUBLIC_URL.replace(/\/+$/, "") + u.pathname + u.search, request);
  }
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const m = request.method;
  if (m === "OPTIONS") return new Response(null, { status: 204, headers: { ...CORS, "Access-Control-Max-Age": "86400" } });

  if (path === "/mcp") return mcp(request, env);
  if (path === "/.well-known/oauth-protected-resource" || path === "/.well-known/oauth-protected-resource/mcp") {
    return json({ resource: `${url.origin}/mcp`, authorization_servers: [url.origin], scopes_supported: [SCOPE],
                  bearer_methods_supported: ["header"], resource_name: "Citrello" });
  }
  if (path === "/.well-known/oauth-authorization-server" || path === "/.well-known/openid-configuration"
      || path === "/.well-known/oauth-authorization-server/mcp") {
    return json(authServerMetadata(url.origin));
  }
  if (path === "/register" && m === "POST") return register(request, env);
  if (path === "/authorize" && (m === "GET" || m === "POST")) return authorize(request, env);
  if (path === "/token" && m === "POST") return token(request, env);
  if (path === "/revoke" && m === "POST") return revoke(request, env);

  if (path === "/api/ping" && m === "POST") return usagePing(request, env);
  if (path === "/api/latest" && m === "GET") return latestRelease(request, env, ctx);
  if (path === "/api/oa-pdf" && m === "GET") return oaPdf(request);
  if (path === "/api/auth/start" && m === "POST") return apiAuthStart(request, env);
  if (path === "/api/auth/verify" && m === "POST") return apiAuthVerify(request, env);
  if (path === "/api/me" && (m === "GET" || m === "POST")) return apiMe(request, env);
  if (path === "/api/signout" && m === "POST") return apiSignOut(request, env);
  if (path === "/api/account/delete" && m === "POST") return apiDeleteAccount(request, env);
  if (path === "/device/connect") return deviceConnect(request, env);
  if (path === "/api/ai/quota" && m === "GET") return aiCall(request, env, (d) => ai.quota(env, d.user_id));
  if (path === "/api/ai/judge" && m === "POST") return aiCall(request, env, async (d) => ai.judge(env, d.user_id, await readBody(request)));
  if (path === "/api/ai/find-web" && m === "POST") return aiCall(request, env, async (d) => ai.findOnWeb(env, d.user_id, await readBody(request)));
  if (path === "/admin/pro" && m === "POST") return adminPro(request, env);

  if (path === "/admin/stats") return adminStats(request, env);
  if (path === "/admin/broadcast" && m === "POST") return adminBroadcast(request, env);
  if (path === "/admin/subscribers.csv") return adminSubscribers(request, env);

  if (path === "/unsubscribe") return unsubscribe(request, env);
  if (path === "/privacy") return pages.privacy();
  if (path === "/") return pages.home(url.origin);
  return json({ error: "not_found" }, 404);
}

export default {
  async fetch(request, env, ctx) {
    try {
      return await route(request, env, ctx);
    } catch (e) {
      if (e instanceof HttpError) return json({ error: e.code, error_description: e.message }, e.status);
      console.error(e && e.stack || e);
      return json({ error: "server_error", error_description: "Something went wrong on the Citrello server." }, 500);
    }
  },

  // Daily clean-up of expired codes and tokens.
  async scheduled(event, env) {
    const t = now();
    await env.DB.batch([
      env.DB.prepare("DELETE FROM oauth_codes WHERE expires_at < ?").bind(t),
      env.DB.prepare("DELETE FROM oauth_tokens WHERE expires_at < ?").bind(t),
      env.DB.prepare("DELETE FROM login_codes WHERE window_start < ? AND expires_at < ?").bind(t - 86400, t),
      // installs not seen for two years are forgotten
      env.DB.prepare("DELETE FROM installs WHERE last_seen < ?").bind(t - 2 * 365 * 86400),
    ]);
  },
};
