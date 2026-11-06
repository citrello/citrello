# Citrello status (updated 6 November 2026)

## Released
- **Citrello 1.0.1** is the latest GitHub release (Windows `Citrello-windows.zip`, Mac beta `Citrello-mac.zip`,
  Apple silicon). Downloads: citrello.app/download. In-app updates work from 1.0.1 on; 1.0.0 users must download once.
- After 1.0.1, `main` also has: Citrello™ in the app sidebar, TRADEMARKS.md. Next release would be 1.0.2.

## Live services
- **citrello.app** (Cloudflare): home, /download, /check (in-browser paper checker), /privacy.
- **mcp.citrello.app** (Cloudflare Worker + D1 + Durable Objects): sign-in by email code (Resend), ChatGPT /
  claude.ai web connector (`https://mcp.citrello.app/mcp`), usage count, latest-release endpoint, admin stats:
  `curl -H "Authorization: Bearer <BRIDGE_ADMIN_TOKEN>" https://mcp.citrello.app/admin/stats` (Armin has the token).
- Cloudflare account and the citrello.app DNS belong to Armin; Resend sends from hello@citrello.app.

## Business and legal decisions
- **Licence:** stays AGPL-3.0-or-later (open source helps trust, university IT approval, grants, SignPath).
  CONTRIBUTING.md has contributor terms that allow relicensing. Money later from hosted services (sync,
  "Citrello Plus", university licences, possibly job ads), never from user data.
- **Trademark:** UK IPO application for the word mark **CITRELLO** filed **6 November 2026**, Standard examination,
  classes 9 and 42 (broad research/academic software wording incl. AI, document checking, job-search and networking
  software). Application number: (ask Armin). Next: examiner report in ~2–4 weeks; publication + 2-month opposition;
  registration ~4 months. **Deadline to extend to US/EU with the same date: 6 May 2027** (Madrid via UK IPO).
  Class 35 (job ads/recruitment) deliberately not filed yet. Use ™ until registered, then ®.
  Earlier search: no conflicting marks (similar ones are lab instruments/aerials/appliances; a US "CITRELLO" for
  biscuits was cancelled in 1981).
- **Refmee** (refmee.com, Armin's main product: structured, verified references replacing reference letters for
  applicants, referees and institutions) stays a **separate brand**, endorsed as "by the team behind Citrello".
  Citrello is the free tool that reaches researchers and professors. Rules: separate consent before any Refmee
  marketing email to Citrello users; links/mentions in context are fine. Refmee needs its own trademark (check
  for the old "RefME" citation tool first).
- **Sign-up is optional** (welcome screen with Skip). Mandatory sign-up was considered and rejected.
- **Microsoft Store:** package builds in CI; submission postponed (needs Partner Center account + the three
  MSIX_* repository variables). **SignPath** (free code signing) application submitted; when approved, add the
  signing step (CODE_SIGNING.md is the required policy page). Mac signing/notarisation needs Apple's $99/year
  Developer Program, not done.

## Things Armin still needs to do (remind him)
- Edit the 1.0.1 release notes on GitHub: put `[@Key]` in backticks (removes the stray contributor "key").
- Send a real **Mendeley Cite** document to confirm that format works on real files (built from documentation,
  tested on generated files only).
- Try citrello.app/check with a real paper.

## Ideas and next steps (not started unless noted)
1. **Launch**: quiet test with 5–10 real users and their own papers (Zotero, Mendeley Cite, EndNote, typed); fix
   what breaks; then launch posts (r/PhD, Hacker News...) led by the free checker ("find fake and retracted
   references in your paper"); a 30–60 s demo video. Armin wants to launch "very soon".
2. **Overleaf live .bib link** through the bridge (private URL Overleaf can refresh), caching the last copy.
3. **Word tab for Mac** as an Office add-in (would also work in Word on the web).
4. **One program instead of two** (app + connector) to halve the download size.
5. **Research job positions** in Citrello (software side is covered by the trademark; job ads = class 35 later),
   linking to Refmee for applications.
6. Store submission, SignPath signing step, Mac notarisation (see above).
7. Web version of the full app with cloud sync ("Citrello Plus") later.

## Known limits
- Windows SmartScreen and Mac Gatekeeper warnings until signing.
- PDF checks can't be fixed (report only); scanned PDFs can't be read; unusual two-column layouts may split refs.
- ChatGPT connector needs a paid ChatGPT plan with Developer mode; web connectors need the app running.
- The Claude Desktop extension doesn't update itself (users re-drag it after connector changes).
