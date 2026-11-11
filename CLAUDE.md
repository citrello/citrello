# Citrello: notes for Claude

Citrello (citrello.app, "References, the sweet part.") is a free, open-source (AGPL-3.0-or-later) reference manager
built for writing with AI. Owner and sole maintainer: **Armin Hosseinghorbani** (GitHub: arminhgh; organisation
github.com/citrello). The library lives on the user's computer and is shared by the desktop app, Word, LaTeX, and
AI assistants (Claude, ChatGPT, Codex) through an MCP connector.

Read **docs/STATUS.md** for where things stand, decisions already made, and the to-do list. The current project is
the web app: **docs/WEB_APP_PLAN.md**. **docs/VALUE_PROPOSITION.html** is the value-proposition report.

## Working with Armin
- He is a researcher, not a developer. Explain in plain language, give exact click-by-click steps for anything he
  does himself (GitHub, Cloudflare, stores), and lead with the answer.
- **Never mention "Refcite"** (the project's earlier name) in anything user-facing. Code still reads old
  `refcite-*` data for migration; that's fine.
- **Never write a bare `@name` in anything posted to GitHub** (release notes, issues, PR text, commit messages):
  GitHub treats it as a mention of a real user. Put citation markers in backticks: `` `[@Key]` ``. (A release-
  notes `[@Key]` once listed a stranger called "key" as a contributor.)
- Don't put model names in commits, PRs or code.
- Ask before outward-facing actions (publishing, deleting, emailing users). Pushing to `main` is the normal workflow
  here; Armin publishes releases on GitHub himself (sessions have been unable to push tags).
- Test what you build, ideally in the real app/browser, and say plainly what was and wasn't tested.

## What's in the repository
Python 3.12, standard library plus python-docx, citeproc-py, pypdf, pybtex (and certifi/Pillow at build time).

| File | What it is |
|---|---|
| `citrello_core.py` | Shared core (imported as `core`): `VERSION` (the one place the version is set), reference parsing, Crossref/doi.org lookup (`crossref_lookup`), reliability rules (`assess_references`), `LibraryIndex` duplicate detection (different DOIs or titles differing only by a number are never duplicates), `find_bibliography` / `body_paragraphs` (reads inside content controls), converting typed citations (`convert_document`), Mendeley API, RIS/BibTeX/CSV writers. |
| `library.py` | The library: `~/Citrello/library.json` (`CITRELLO_HOME`). `editing()` context manager (lock + atomic save; also runs automatic .bib exports), `add_csl` (dedupe, keeps BibTeX keys), `import_items` (bulk, one index per import), imports (RIS, BibTeX, CSL-JSON, Mendeley, Zotero web API with retries), `change_projects`, verify online, export. |
| `citeformat.py` | Citation styles (11 bundled in `styles/`, others downloaded from the CSL repo; `styles/catalog.json.gz` lists all ~10,900 for `search_styles`, rebuilt by `packaging/build_style_catalog.py` at each build; `install_style` downloads + test-renders), `Renderer`, Word fields (`ADDIN CSL_CITATION`, Mendeley-compatible), `update_document` (markers → live citations, restyle, rebuild bibliography). |
| `doccheck.py` | "Check and fix a document": reads citations from Zotero, Mendeley Desktop (fields), **Mendeley Cite** (content controls tagged `MENDELEY_CITATION_v3_<base64 JSON>`), EndNote, Word's own citations (customXml sources), `[@Key]` markers, typed lists, and PDFs (pypdf). `check()` → report (online checks, duplicates, citation mistakes); `fix()` → corrected .docx with live citations in a chosen style; `sample_paper()` (six planted mistakes, for the tours). |
| `sources.py` | "What your sources say": each cited sentence of a Word document and the cited paper's full text (word matches); `ai_items` / `library_candidates` pick the passages Claude reads. Verdicts come from Claude through the bridge (`bridge/src/ai.js`, pro feature; the page calls it in `aiJudgeAll`, cached in IndexedDB). |
| `latex.py` | BibTeX/biblatex writer (stable keys, brace protection), `\cite` parsing, LaTeX project check, thebibliography conversion, automatic .bib export. |
| `arxiv.py` | "Make an arXiv-ready .zip": makes the `.bbl` with pybtex (byte-identical to BibTeX) from the project's `.bst` or the common ones in `styles/bst/` (unmodified TeX Live copies), strips comments, removes build files/.bib, adds `\pdfoutput=1`, and reports what makes arXiv fail (missing/case-mismatched files, biblatex, unknown style, undefined citations, file names, >50 MB). |
| `fulltext.py` | Papers' full text, **text only, never the PDF**: `Citrello/fulltext/<item id>.txt` + the item's `"fulltext"` note. PDF → text (pypdf, cleaned), matching a PDF to its reference (DOI, then title), BM25 `passages()` for the AI. Connector tools `read_paper`, `find_in_papers`. |
| `zotero_local.py` | One-click import from Zotero's own `zotero.sqlite` (copy; falls back to `.bak`); collections → projects. |
| `mcp_server.py` | The MCP connector (stdio JSON-RPC, hand-written): 19 tools (search/add/format references, read papers' full text, find styles, Word documents, verify, import, BibTeX, LaTeX check, arXiv zip, Zotero import...). `remote_call()` limits file access for requests that come from web connectors. |
| `app.py` | The desktop app: local server on 127.0.0.1:8585 serving `ui.html` + JSON API; Claude Desktop / Codex connection, Word tab install, document checks, LaTeX, accounts, updates. Windows: claims the port exclusively so a new copy takes over. |
| `webapp.py` | The web app's runtime (citrello.app/app): answers `ui.html`'s `/api/...` requests with `app.py`'s own handler inside Pyodide; desktop-only endpoints answer "needs Citrello for desktop"; adds backup/restore. Page side: `site/public/app/web.js` (fetch → worker, downloads) and `worker.js` (Pyodide, IDBFS storage). |
| `ui.html` | The app's interface (one file): Home (nine tasks, each a guided tour on the real screen: `TOURS`), Studio layout (sidebar with projects, reference list, detail panel), search box that also adds (DOI/link/reference), multi-select + reliability filters, Documents (one "Check and fix" card + "Keep a .bib up to date"), Settings, welcome screen, update banner. Brand: cobalt `#1d3f8f`, lemon `#f6d646`, leaf `#3e7b4f`, fonts Gloock + Figtree. |
| `clip.html` | Pop-up of the browser bookmarklet ("Add to Citrello"). |
| `word_addin.py` | Windows Word tab (VBA template `Citrello.dotm`, ribbon, dialogs built through PowerShell). `_check_vba` guards VBA syntax. |
| `bridge_client.py` | Sign-in (email code) and the app's WebSocket to the bridge (for ChatGPT / claude.ai web connectors). |
| `usage.py` | Anonymous once-a-day count (random install ID, version, OS); off in Settings or `CITRELLO_NO_USAGE`. |
| `updater.py` | In-app updates: checks `mcp.citrello.app/api/latest` (GitHub fallback), downloads only from citrello GitHub releases, verifies SHA-256, swaps files (Windows renames running .exe) or the .app (Mac), restarts. |
| `codex_config.py` | Adds Citrello to Codex's `config.toml`. |
| `make_mcpb.py` | Builds the Claude Desktop extension (.mcpb). Not shipped: the app builds it from the connector into `~/Citrello` when needed. |
| `brand_icon.py` | Draws the lemon-slice icon (needs Pillow). |
| `bridge/` | Cloudflare Worker at **mcp.citrello.app**: accounts (email codes via Resend), OAuth 2.1 + remote MCP endpoint relaying to the user's app (Durable Object per account), `/api/ping` usage count, `/api/latest`, Claude checks `/api/ai/*` (pro, `src/ai.js`), `/admin/stats`, `/admin/broadcast` and `/admin/pro` (Bearer ADMIN_TOKEN). D1 migrations in `bridge/migrations`. See `bridge/README.md`. |
| `site/` | citrello.app (static, Cloudflare Workers assets): home, `/download`, `/check` (the paper checker running Citrello's Python in the browser via Pyodide), `/app` (the web app), `/privacy`. `/check-py`, `/app/index.html` and `/app/py` are built by `packaging/build_web_check.py` at deploy. In `ui.html`, `class="desktop-only"` / `"web-only"` hides parts in one or the other. |
| `extension/` | The Citrello browser extension (Manifest V3, Chrome/Edge/Firefox): toolbar "Add to Citrello" (replaces the bookmarklet), and bulk "Fetch the missing ones with my access" (`fetch.html`: visits papers in background tabs, `grab.js` takes the article text or PDF, `bridge.js` hands it to the Citrello page, which saves only the text). Site access is optional (asked at first bulk fetch). Build: `packaging/build_extension.py`. |
| `packaging/` | `build_mac.sh` (Citrello.app + zip), `make_msix.py` (Microsoft Store package, ready but not submitted), `store-listing.md`, `build_web_check.py`. |
| `tests/` | pytest unit tests; `tests/e2e/` drives the real app in a browser (Playwright) with screenshots; `test_bridge_e2e.py` (needs local `wrangler dev`), `test_web_check.py` (needs the site served + internet), `update_smoke.py` (real update on Windows/Mac in CI). |

## Commands
```bash
pip install python-docx citeproc-py pypdf pybtex pytest playwright
pytest -q                                   # unit tests (e2e skips without Playwright browsers)
python app.py                               # run the app from source (opens the browser at localhost:8585)
CITRELLO_E2E_SHOTS=/tmp/shots pytest tests/e2e      # browser tests against `python app.py` (CITRELLO_E2E_CHROMIUM=path for a non-Playwright Chromium)
python packaging/build_web_check.py         # build the web checker (site/public/check-py) and the web app (site/public/app)
(cd site/public && python -m http.server 8099) &   # then: CITRELLO_WEB_CHECK=http://localhost:8099 pytest tests/test_web_app.py tests/test_web_check.py
cd bridge && npm install && npx wrangler d1 migrations apply citrello --local && npm run dev   # local bridge
```
Useful environment variables for tests and local runs: `CITRELLO_HOME`, `CITRELLO_APP_CONFIG`,
`CITRELLO_ACCOUNT_FILE`, `CITRELLO_INSTALL_FILE`, `CITRELLO_BIB_EXPORTS`, `CITRELLO_BRIDGE`, `CITRELLO_CROSSREF`,
`CITRELLO_DOIORG`, `CITRELLO_NO_USAGE`, `CITRELLO_UPDATE_URL`, `CITRELLO_ZOTERO_DIR`, `CITRELLO_ZOTERO_API`.
When running a second copy of the app in a sandbox, kill it with `pgrep -f "python3 app.py --no-browser" | xargs -r kill`
in its own command (a `pkill -f` pattern can match and kill your own shell).

## CI and deployment (GitHub Actions)
- **build.yml** (every push to main, tags `v*`, or run by hand with input `release_tag` to attach builds to an
  existing release): unit + offline e2e tests (Linux); Windows build (PyInstaller exes, icon, msix, e2e online,
  real update test, `Citrello-windows.zip` = Citrello.exe + CitrelloMCP.exe); Mac build on `macos-latest`
  (Apple silicon `Citrello.app` → `Citrello-mac.zip`, e2e online, update test). On a release tag it attaches both zips.
- **bridge.yml**: tests the Worker with a local copy, then deploys to Cloudflare (D1 migrations, secrets).
- **site.yml**: builds and tests the web checker in a real browser, then deploys citrello.app.
- Secrets (in GitHub, values never in the repo): `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`,
  `RESEND_API_KEY_CITRELLO` (or `RESEND_API_KEY`), `BRIDGE_ADMIN_TOKEN`. Variables for the Store package (not set
  yet): `MSIX_IDENTITY_NAME`, `MSIX_PUBLISHER`, `MSIX_PUBLISHER_NAME`.
- **Releasing:** bump `VERSION` in `citrello_core.py`, push to main, wait for green builds, then Armin creates the
  GitHub release (tag `vX.Y.Z` on main). Users on 1.0.1+ get it through the in-app update banner.

## Conventions
- Keep the app working offline; anything online fails gracefully (a failed lookup never stops a check).
- Privacy is a product promise: the library and documents never leave the computer; the paper checker runs in the
  browser; feature emails are opt-in (unticked by default); usage count is anonymous.
- Writing style for UI and docs: plain, concrete, sentence case, active voice; no jargon in user-facing text.
- Every new feature gets a unit test and, where it has a screen, an e2e scenario.
