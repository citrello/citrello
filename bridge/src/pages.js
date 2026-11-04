// Small HTML pages served by the bridge: home, sign-in for connectors, privacy, unsubscribe.

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const CSS = `
:root{--bg:#fffdf6;--card:#fff;--ink:#2b2a26;--muted:#6f6b60;--line:#e9e4d4;--accent:#e2b007;--accent-ink:#2b2a26;
--err:#b3261e;--ok:#2e7d32}
@media (prefers-color-scheme:dark){:root{--bg:#1c1b18;--card:#262521;--ink:#f3efe3;--muted:#b4ae9c;--line:#3a3832;
--accent:#f2c94c;--accent-ink:#1c1b18;--err:#ff8a80;--ok:#81c784}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:520px;margin:0 auto;padding:40px 16px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:20px;margin-bottom:24px}
.logo{width:32px;height:32px;border-radius:9px;background:var(--accent);color:var(--accent-ink);display:grid;
place-items:center;font-weight:800}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px}
h1{font-size:22px;margin:0 0 8px}p{margin:8px 0}.muted{color:var(--muted)}.small{font-size:14px}
label{display:block;font-weight:600;margin:16px 0 6px}
input[type=email],input[type=text]{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:10px;
background:var(--bg);color:var(--ink);font-size:16px}
input.code{letter-spacing:6px;font-size:22px;text-align:center}
button{margin-top:18px;width:100%;padding:12px;border:0;border-radius:10px;background:var(--accent);
color:var(--accent-ink);font-weight:700;font-size:16px;cursor:pointer}
button.link{background:none;color:var(--muted);width:auto;padding:0;margin-top:12px;font-weight:400;
text-decoration:underline;font-size:14px}
.check{display:flex;gap:8px;align-items:flex-start;font-weight:400;font-size:14px}
.err{color:var(--err)}.ok{color:var(--ok)}code{background:var(--bg);border:1px solid var(--line);border-radius:6px;
padding:1px 6px;word-break:break-all}a{color:inherit}ol{padding-left:20px}`;

export function page(title, body, status = 200) {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(title)}</title>
<style>${CSS}</style></head><body><main><div class="brand"><div class="logo">C</div>Citrello</div>
${body}</main></body></html>`;
  return new Response(html, { status, headers: {
    "Content-Type": "text/html; charset=utf-8",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https: http://localhost:* http://127.0.0.1:*; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
  } });
}

export function home(origin) {
  return page("Citrello connector", `<div class="card"><h1>Citrello connector</h1>
<p>Use your Citrello reference library from ChatGPT or Claude on the web and on your phone.</p>
<ol class="small">
<li>Open the Citrello app on your computer and sign in (Settings → Account).</li>
<li>Add a connector with this address:<br><code>${esc(origin)}/mcp</code></li>
<li>Sign in with the same email when asked.</li></ol>
<p class="small muted">The library stays on your computer. Citrello needs to be running for the connector to reach it.</p>
<p class="small"><a href="https://citrello.app">citrello.app</a> · <a href="/privacy">Privacy</a></p></div>`);
}

// The sign-in page an OAuth client (ChatGPT, claude.ai) sends the user to.
export function authorize({ params, client, step, email = "", error = "", info = "" }) {
  const hidden = Object.entries(params)
    .map(([k, v]) => `<input type="hidden" name="${esc(k)}" value="${esc(v)}">`).join("");
  let host = "";
  try { host = new URL(params.redirect_uri).host; } catch {}
  const who = `<p><b>${esc(client.name || "An app")}</b> <span class="muted small">(${esc(host)})</span>
wants to use your Citrello library: search, add and format your references.</p>`;
  const msg = (error ? `<p class="err small">${esc(error)}</p>` : "") + (info ? `<p class="ok small">${esc(info)}</p>` : "");
  if (step === "code") {
    return page("Sign in to Citrello", `<div class="card"><h1>Check your email</h1>${who}
<p class="small muted">We sent a 6-digit code to <b>${esc(email)}</b>. It expires in 10 minutes.</p>${msg}
<form method="post">${hidden}<input type="hidden" name="email" value="${esc(email)}">
<input type="hidden" name="step" value="verify">
<label for="code">Code</label><input id="code" class="code" name="code" type="text" inputmode="numeric"
autocomplete="one-time-code" pattern="[0-9 ]*" maxlength="8" required autofocus>
<button type="submit">Allow access</button></form>
<form method="post">${hidden}<input type="hidden" name="step" value="start">
<button class="link" type="submit">Use a different email</button></form></div>`);
  }
  return page("Sign in to Citrello", `<div class="card"><h1>Sign in to Citrello</h1>${who}${msg}
<form method="post">${hidden}<input type="hidden" name="step" value="send">
<label for="email">Email</label><input id="email" name="email" type="email" value="${esc(email)}"
autocomplete="email" required autofocus>
<p class="small muted">Use the email you signed in with in the Citrello app.</p>
<button type="submit">Email me a code</button></form>
<p class="small muted">New to Citrello? Get the free app at <a href="https://citrello.app">citrello.app</a>.</p></div>`);
}

export function message(title, text, status = 200) {
  return page(title, `<div class="card"><h1>${esc(title)}</h1><p>${esc(text)}</p></div>`, status);
}

export function unsubscribe(params, done) {
  if (done) return message("Unsubscribed", "You won't get emails about new features any more. " +
    "You can turn them back on in the Citrello app (Settings → Account).");
  return page("Unsubscribe", `<div class="card"><h1>Unsubscribe</h1>
<p>Stop emails about new Citrello features?</p>
<form method="post"><input type="hidden" name="u" value="${esc(params.u)}">
<input type="hidden" name="s" value="${esc(params.s)}"><button type="submit">Unsubscribe</button></form></div>`);
}

export function privacy() {
  return page("Citrello privacy", `<div class="card"><h1>Privacy</h1>
<p class="small muted">Last updated 3 November 2026</p>
<p><b>Your library stays on your computer.</b> References, projects and documents are stored by the
Citrello app on your own computer. They are never uploaded to or stored by Citrello's servers.</p>
<p><b>Account.</b> If you sign in, we store your email address, when you signed up and were last active,
the computers you signed in on (computer name, system and app version), and whether you agreed to emails about
new features. We use this to let you use the same account on several computers and connectors, to count how many
people use Citrello, and, only if you agreed, to email you about new features.</p>
<p><b>Anonymous usage count.</b> Once a day, while it runs, the app sends a random install ID made on your
computer, the app version, your operating system and its version, and whether you're signed in. Nothing else: no
name, email, IP address record, library or documents. It tells us how many people use Citrello. Turn it off in the
app (Settings → Account).</p>
<p><b>Connectors (ChatGPT, Claude on the web).</b> When a connector uses your library, its request is passed through
our server to the Citrello app on your computer and the answer is passed back. We don't keep the contents of these
requests. The connector's own provider (for example OpenAI or Anthropic) handles what you send it under its own
privacy policy.</p>
<p><b>Emails.</b> Sign-in codes are sent with Resend. Emails about new features are sent only if you agreed, and every
one has an unsubscribe link. You can also turn them off in the app.</p>
<p><b>Your choices.</b> You can sign out of any computer, and delete your account and everything we store about you,
in the app (Settings → Account). Citrello works fully without an account; only the connectors need one.</p>
<p><b>Sharing.</b> We don't sell or share your data. It is processed by Cloudflare (hosting) and Resend (email).</p>
<p><b>Contact.</b> Questions: open an issue at <a href="https://github.com/citrello/citrello">github.com/citrello/citrello</a>
or email hello@citrello.app.</p></div>`);
}
