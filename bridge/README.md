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

## Claude checks (`/api/ai/*`) and credits
"What your sources say" and the Word add-in's "Check the claim" ask Claude, through here, whether each cited paper
supports the sentence (`/api/ai/judge`: the sentence plus a few passages of the paper's text picked by the app;
structured JSON answer); `kind: "library"` judges candidate papers from the person's own library ("Find another
reference"). The web search (`/api/ai/find-web`) is switched off (410 unless `AI_WEB_FIND = "1"`). Signed-in accounts
only; nothing of the text is stored.
- Secret: `ANTHROPIC_API_KEY` (GitHub secret of the same name; the deploy workflow passes it on). Without it the
  endpoints answer "Claude checks aren't switched on yet" and the app keeps showing word matches.
- Variables (optional, in `wrangler.toml` `[vars]`): `AI_MODEL`, `AI_WEB_MODEL`; `ANTHROPIC_BASE_URL` points the tests
  at a pretend Claude.
- **Credits** (`src/credits.js`): a claim check costs 3 credits per paper, a library find 8 (`COST`). Every account
  gets 100 free credits once (`users.bonus_credits`). Plans add monthly credits (Student 1,000, Researcher 4,000;
  yearly plans also month by month, from `plan_anchor`); a plan's credits are spent first and don't carry over, then
  the bonus. Credits are checked before Claude is asked (402 `no_credits`) and taken only after it answers.
  `credit_log` records each spend/gift. `/api/ai/quota` returns `{credits, bonus, checks_left, costs, plan, ...}`.
- Credits by hand (support, refunds, testers; negative to take away): `curl -X POST -H "Authorization: Bearer
  ADMIN_TOKEN" -H "Content-Type: application/json" -d '{"email":"someone@example.com","credits":500,"why":"refund"}'
  https://mcp.citrello.app/admin/credits`

## Plans and payments (`/api/billing`, Stripe Managed Payments)
`src/billing.js`. Stripe sells the plans as seller of record (Managed Payments: tax, receipts, disputes; customers see
"Sold through Link"). `GET /api/billing` (plans with prices + balance), `POST /api/billing/checkout {plan, interval}`
(a Checkout Session, `mode=subscription`, `managed_payments[enabled]=true`; success/cancel go to
citrello.app/thanks), `POST /api/billing/portal` (Stripe's customer portal: switch, card, cancel), `POST
/api/stripe/webhook`. The webhook isn't signed-checked: it only says *something* changed; the bridge reads the
subscription back from Stripe with its own key, so a made-up event can't give credits.
- Secret: `STRIPE_SECRET_KEY` (GitHub secret; a restricted or secret test key in the sandbox, a live key for real
  payments). Without it the Plans windows say "Plans open soon".
- `scripts/stripe-setup.mjs` (run by bridge.yml after each deploy, safe to repeat) creates the two products and four
  prices (GBP, tax inclusive, tax code `txcd_10105001`, lookup keys `citrello_{student,researcher}_{monthly,yearly}`),
  the webhook endpoint and the default customer portal. Prices: Student £6/month or £50/year, Researcher £18/month or
  £150/year (change in the script and on site/public/pricing.html; Stripe prices can't be edited, so a new price
  needs the old lookup key moved, `transfer_lookup_key`).
- Tests: `tests/stripe_mock.py` (pretend Stripe on 127.0.0.1:8798, `STRIPE_API_BASE`), `tests/test_bridge_billing_e2e.py`.

## The cloud copy of web-app libraries (`/api/cloud`)
Signed in, citrello.app/app saves its library (references, projects, papers' text; one gzip file) here and brings
it back in another browser (`src/cloud.js`). Stored in the R2 bucket `citrello-cloud` with EU jurisdiction (binding
`CLOUD`), one object per account, replaced on each save, deleted with the account or from Settings. Limits:
`CLOUD_FREE_MB` (100) and `CLOUD_PRO_MB` (1000). The deploy workflow creates the bucket; that needs R2 enabled in the
Cloudflare dashboard and the API token's "Workers R2 Storage: Edit" permission (otherwise it deploys without the
cloud copy and says so).
