# Citrello web app: plan (written 6 November 2026, before building started)

**Goal:** everything Citrello offers, at **citrello.app/app**, with nothing to install, because the Windows
SmartScreen / Mac Gatekeeper warnings are now the biggest drop-off. The desktop app stays for people who want it.

**Promise to keep:** the library stays on the person's device unless they choose cloud sync. The web app keeps
it in the browser (IndexedDB, plus a backup file); a server copy is opt-in only.

**Starting point that already exists:** `site/public/check.html` + `check-worker.js` run Citrello's own Python in
the browser with Pyodide 0.27.7 (built by `packaging/build_web_check.py`). The bridge at mcp.citrello.app already
does accounts, OAuth for ChatGPT/Claude, and relays MCP calls to a "device" over a WebSocket
(`bridge/src/relay.js`: the device gets `{type: "rpc", id, msg}` and answers). **The web app can be that device.**

---

## 1. What moves to the web, and how

| Desktop feature | In the web app | How / notes |
|---|---|---|
| Library (add, edit, projects, search, filters, multi-select) | Yes | `library.py` in Pyodide; storage in IndexedDB (or OPFS). Daily automatic backup download + "Export everything". Chrome/Edge: optional File System Access so `library.json` lives in a folder the person picks (e.g. OneDrive). Safari: IndexedDB + reminders to back up. |
| Add by DOI / link / typed reference, verify online, retraction checks | Yes | Crossref, doi.org, Retraction Watch data via Crossref all allow browser requests (CORS). The checker already does this. |
| Check and fix a document (Word, PDF, LaTeX), Mendeley Cite, EndNote… | Yes | Already in the browser checker; add "Fix and format" (python-docx works in Pyodide) and "add to library". |
| 10,000 styles, style search | Yes | `styles/catalog.json.gz` (200 KB) + CSL files from raw.githubusercontent.com (CORS ok). |
| LaTeX: .bib/biblatex export, project check, thebibliography conversion | Yes | Pure Python. |
| arXiv-ready .zip | Yes | `arxiv.py` + pybtex are pure Python; ship `styles/bst/` (1.5 MB) as static files, load on demand. |
| "Keep a .bib file up to date" (writes a file on disk) | Changed | Replace with the **Overleaf live link**: a private URL `mcp.citrello.app/bib/<secret>.bib` that Overleaf's "Add file → From external URL" refreshes. Needs the opt-in cloud copy (section 3) or the page being open. Chrome/Edge can also keep writing a local file via File System Access while the tab is open. |
| Import RIS / BibTeX / CSL-JSON | Yes | File picker. |
| Import Zotero (web API) | Yes | api.zotero.org allows browser requests. |
| Import Zotero "one click" (reads zotero.sqlite) | Changed | Person picks `zotero.sqlite` (or the Zotero folder in Chrome); Pyodide's sqlite3 reads it. Two clicks instead of one. |
| Import Mendeley (API) | Via bridge | Mendeley OAuth redirect to citrello.app; token exchange and API calls through the bridge if Mendeley blocks browser requests (check). |
| Browser button (bookmarklet) | Yes, simpler | Pop-up opens citrello.app/clip, which saves into the same browser storage. No local server needed. |
| AI connector: ChatGPT, Claude web/phone | Yes | See section 2. |
| AI connector: Claude Desktop extension, Codex, Claude Code (stdio) | Changed | They use the same **remote** connector URL instead (Claude Desktop: Settings → Connectors; Claude Code: `claude mcp add --transport http citrello https://mcp.citrello.app/mcp`; Codex supports remote MCP too: check its current syntax). Local-file tools (`check_latex_project path=…`) become "upload in the web app" or accept file contents. |
| Word tab (Windows VBA) | Replace | **Office add-in** (Office.js): works in Word for Windows, **Mac** and **Word on the web**, installs from Microsoft's store (AppSource) with **no macro or SmartScreen warning**. Word add-ins run in their own browser sandbox, so they can't read citrello.app's IndexedDB: the add-in needs the signed-in account (cloud copy, section 3) or talks to an open citrello.app tab through the bridge. |
| Updates | Not needed | The web is always current. Keep a version banner for the cached PWA. |
| Offline use | Mostly | Make it a PWA (service worker caches Pyodide, ~25–35 MB first visit). Lookups need internet anyway. |

**Answer to "only the Word tab?":** no. The hard parts are (1) the AI connector without a program running on the
computer, (2) the Word tab, (3) anything that writes files on disk by itself (.bib auto-export). All three have a
web answer, but (1) and (2) work best with an **optional cloud copy of the library**, which is a product/privacy
decision (section 3).

---

## 2. The AI connector without a download

Today: ChatGPT / claude.ai → `mcp.citrello.app/mcp` → Durable Object → WebSocket → **the desktop app** answers.

**Phase A: the browser tab is the device (no privacy change).**
- The web app signs in (same email-code accounts), opens the device WebSocket to the bridge and answers `rpc`
  messages by running the same tool functions (`mcp_server.py` `TOOLS`) in Pyodide, against the browser library.
- Browsers can't send custom headers on a WebSocket: pass the device token as a query parameter or
  `Sec-WebSocket-Protocol` value; update `relay.js` / `index.js` to accept it.
- Limit: works while a citrello.app tab is open (same as "the app must be running" today). Show a clear status
  ("ChatGPT can reach your library while this tab is open") and keep the cached tool list
  (`tools/list` already answers from cache when no device is online).
- Tools that write Word files: put the file in the tab's downloads, or let the bridge hold it for 1 hour behind a
  one-time link that the AI shows.
- Free Claude plans allow one custom connector (beta; confirm in Claude's help centre). ChatGPT needs a plan with
  Developer mode. Apply for **Claude's connector directory** and **ChatGPT's app directory** so it's one click.

**Phase B: always-on connector (optional cloud copy, "Citrello Sync").**
- A signed-in person can turn on sync: the library is stored in D1 (or R2 as JSON) per account; the bridge answers
  read tools (search, get BibTeX, format) itself, so the AI works with no tab open, on the phone, and in the Word
  add-in. Writes (add reference) go to the cloud copy and sync back.
- Formatting server-side needs citeproc: either run it in the Worker (citeproc-js) or keep formatting in the tab and
  only serve search/BibTeX from the cloud. Decide when building.
- This is also the base for the Overleaf live link, the Word add-in, multi-computer use, and "Citrello Plus".

---

## 3. Decisions for Armin before building
1. **Cloud copy (Phase B):** opt-in only, off by default, deletable, EU/UK storage? Free or part of "Plus"?
   Recommendation: free for one library up to a limit, because it removes the biggest friction; Plus later adds
   shared/group libraries and claim checking at scale.
2. **Word add-in on AppSource:** needs a Microsoft Partner Center account (free for individuals as of 2025; check).
   Recommendation: yes, it replaces both the VBA tab and its macro warnings, and finally covers Mac.
3. **Keep the desktop app?** Yes, as "Citrello for desktop" for offline/privacy-strict users and university IT, but
   lead with the web app on citrello.app. Meanwhile the quickest fix for the Windows warning on the desktop app is
   still **SignPath** (pending) or the **Microsoft Store** (the Store signs the package; it's already built in CI).

---

## 4. "Does the cited paper support the sentence?" (the last row of the AI problems table)

Two different jobs:
- **Find evidence for a claim** (what Consensus does well; it has a hosted MCP server at
  `https://mcp.consensus.app/mcp` with OAuth, and API access by application/quote: confirm with Consensus).
- **Check a citation that is already in the paper**: this sentence cites that reference; does the reference say
  that? Consensus doesn't know which sentence cites what. **Citrello does**: it reads the citation fields in Word
  (Zotero/Mendeley/EndNote/Cite), `\cite` in LaTeX and `[@Key]` markers.

Recommendation:
1. **Build our own free "claim check" first, using the person's own AI**: a tool `check_claim_support` (and a
   button in the web app) that returns, for each citing sentence, the cited paper's abstract (Crossref, OpenAlex,
   Semantic Scholar) and, when open access, the relevant passages (Unpaywall → PDF → pypdf). The AI the person
   already pays for judges "supports / partly / doesn't / can't tell"; Citrello shows the evidence side by side.
   No API costs, works with Claude and ChatGPT, nobody else does it from the document's real citation fields.
2. **Recommend Consensus alongside** ("Find better evidence for this sentence") as a link or by suggesting people
   add the Consensus connector too. Zero cost, no dependency.
3. **Later, in a paid tier:** run the claim check without the person's AI (our own model calls), and possibly the
   Consensus API for "suggest a better source". Only after checking their terms, price per call, and whether
   results can be shown inside another product.

---

## 5. "Claude can already do this": what AI can't do without Citrello

Put this on the home page, ideally as a live demo ("Ask any AI for 10 references on your topic, paste them here,
see which are real").
1. **It can't check every reference against the publisher's record, every time.** AI web search checks a few,
   inconsistently; it still makes up DOIs and pages. Citrello checks all of them deterministically (Crossref,
   retractions), and the result is the same tomorrow.
2. **It doesn't know about retractions after its training date.** Citrello checks live.
3. **It can't keep your library.** Chat memory is fuzzy text that differs per tool. Citrello is one exact library
   shared by Claude, ChatGPT, Codex, Word and Overleaf; switching AI loses nothing.
4. **It can't make live Word citations.** AI writes text that looks like citations. Citrello writes real citation
   fields (Zotero/Mendeley-compatible) that renumber and change style in one click.
5. **It can't read the hidden citation data** in a Zotero/Mendeley/EndNote Word file; it only sees text. Citrello
   reads the fields, so it knows exactly which sentence cites which paper.
6. **It approximates styles.** Citrello renders 10,000 journal styles exactly (CSL, the standard Zotero/Mendeley
   use).
7. **It can't make the files**: an exact `.bbl` for arXiv, a clean `.bib` with stable keys, a fixed `.docx`.
8. **Scale and privacy:** a 1,000-reference import with correct duplicate handling; the library stays on the
   person's device.

Message: **"Citrello makes your AI trustworthy with references,"** not "instead of AI".

---

## 6. Build order (suggested)
1. `/app` shell: library in IndexedDB, add/search/edit/projects, import/export, backup. Reuse `ui.html` layout.
2. Documents in the web app: check + fix + add to library; LaTeX check; arXiv zip; style search.
3. Connector Phase A (tab as device) + sign-in in the web app; directory applications (Claude, ChatGPT).
4. Clip button for the web app; Zotero (web API + sqlite file); Mendeley through the bridge.
5. Claim check (section 4, step 1).
6. Cloud copy (Phase B) → Overleaf live link → Word add-in (Office.js) on AppSource.
7. Site: lead with "Open Citrello" (web), desktop as a secondary download; "What AI can't do" section/demo.

Testing: Playwright against the web app in Chromium **and WebKit** (Safari storage behaves differently); a bridge
e2e where the "device" is a browser tab; keep the existing desktop tests green.
