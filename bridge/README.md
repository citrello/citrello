# Citrello bridge (`mcp.citrello.app`)

This is a Cloudflare Worker. It does three things:
- **Accounts:** users sign in with their email and a 6-digit code. It records how many users there are, which
  computers they use, and whether they agreed to emails about new features.
- **Remote connector:** an MCP server at `https://mcp.citrello.app/mcp` that ChatGPT, claude.ai and the
  Claude phone apps can add. They sign in with OAuth.
- **Relay:** passes each request to the Citrello app on the user's computer over a WebSocket that the app
  keeps open, then returns the answer. The library never leaves the computer.

```
ChatGPT / claude.ai ──HTTPS──▶ Worker ──▶ Relay (one Durable Object per account) ◀──WebSocket── Citrello app
                                 │
                                 └── D1 database: users, devices, sign-in codes, OAuth clients and tokens
```

Free Cloudflare plan limits are far above what Citrello needs: 100,000 requests a day, plus D1 and
Durable Objects. Idle WebSockets hibernate and cost nothing.

## One-time setup
1. **Cloudflare:** the `citrello.app` domain must be a site in your Cloudflare account. To do this,
   go to Dashboard → *Add a domain*, then change the nameservers where you bought the domain.
2. **Cloudflare API token:** go to *My Profile → API Tokens → Create Token*.
   - Choose the **Edit Cloudflare Workers** template.
   - Add the permission **Account → D1 → Edit**.
   - Under *Zone Resources*, choose `citrello.app`.
   - Create the token and copy it.
   - Copy your **Account ID**. It's shown on the right of the Workers & Pages overview.
3. **Resend (sends the emails, free for 3,000 a month):**
   - Sign up at resend.com.
   - Add the domain `citrello.app`. Resend can add its DNS records to Cloudflare for you.
   - Create an API key.
4. **GitHub:** in the repository, go to *Settings → Secrets and variables → Actions → New repository secret*
   and add:
   - `CLOUDFLARE_API_TOKEN`
   - `CLOUDFLARE_ACCOUNT_ID`
   - `RESEND_API_KEY`
   - `BRIDGE_ADMIN_TOKEN`: a long random password you choose. It protects the admin pages.
5. **Deploy:** go to *Actions → Deploy bridge → Run workflow*. After that, every change to `bridge/` deploys
   by itself. The workflow creates the database the first time.

Check that it works: <https://mcp.citrello.app/> should show the connector page.

## User numbers and feature emails
These commands work from any terminal (PowerShell: use `curl.exe`). Replace `TOKEN` with your
`BRIDGE_ADMIN_TOKEN`.

**Counts:** users, new and active users in the last 7 and 30 days, subscribers, computers, and users with a
connector:
```bash
curl -H "Authorization: Bearer TOKEN" https://mcp.citrello.app/admin/stats
```

**Email about a new feature.** It goes only to people who agreed, and it includes an unsubscribe link.
- First send a test to yourself:
  ```bash
  curl -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" -d '{"subject":"Citrello 1.1: footnote styles","text":"Hi! ...","test_to":"you@example.com"}' https://mcp.citrello.app/admin/broadcast
  ```
- Then send it to everyone by replacing `"test_to":"you@example.com"` with `"confirm":true`.

Resend's free plan sends 100 emails a day. For a bigger list, upgrade Resend, or download the list and use
Resend Broadcasts:
```bash
curl -H "Authorization: Bearer TOKEN" https://mcp.citrello.app/admin/subscribers.csv
```

## Development
```bash
cd bridge
npm install
npx wrangler d1 migrations apply citrello --local
npm run dev
```
`npm run dev` serves the bridge at `http://localhost:8787`. In this mode emails aren't sent: the code is
shown on the sign-in page and printed in the terminal.

Point the app at it by setting `CITRELLO_BRIDGE=http://localhost:8787` before starting Citrello.

## Security notes
- Codes and tokens are stored only as SHA-256 hashes.
- Sign-in codes expire after 10 minutes. A code allows 5 attempts, and an address can get 5 codes an hour.
- OAuth requires PKCE (S256). Return addresses must match the ones the client registered.
- Access tokens last 1 hour. Refresh tokens last 180 days and change every time they're used.
- Requests that arrive from a web connector can save Word files only in the user's Citrello Documents folder,
  and can read only Word and reference files.

## The web app as a device

citrello.app/app connects like the desktop app, but browsers can't put headers on a WebSocket, so it sends its
device token as a subprotocol: `new WebSocket("wss://mcp.citrello.app/device/connect", ["citrello", token])`.
`/device/connect` accepts that (only there), and the Relay answers with `Sec-WebSocket-Protocol: citrello`.
`tests/test_web_bridge_e2e.py` tests it in Chromium and WebKit against a local bridge.

## Free PDFs for the web app (`/api/oa-pdf?doi=…`)

"Find free full texts" in the web app asks here for papers Unpaywall lists as open access, because most
repositories don't let web pages download their files. The Worker looks the DOI up on Unpaywall, fetches the free
PDF (up to four locations, 40 MB max, must start with `%PDF-`) and returns it; nothing is stored. Tested by
`tests/test_web_bridge_e2e.py::test_find_free_full_texts_in_the_browser` against the real services.
