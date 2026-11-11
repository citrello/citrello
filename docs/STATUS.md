# Citrello status (updated 6 November 2026)

## Released
- **Citrello 1.0.1** is the latest GitHub release (Windows `Citrello-windows.zip`, Mac beta `Citrello-mac.zip`,
  Apple silicon). Downloads: citrello.app/download. In-app updates work from 1.0.1 on; 1.0.0 users must download once.
- After 1.0.1, `main` also has: Citrello™ in the app sidebar, TRADEMARKS.md; **style search** (all ~10,150 usable CSL
  styles searchable by journal name in Settings and in "Check and fix"; unusable ones refused with a clear message);
  **"Make an arXiv-ready .zip"** for LaTeX projects (makes the .bbl arXiv needs, strips comments, checks for arXiv
  failures; also the connector tool `prepare_arxiv`). New dependency: pybtex. Next release would be 1.0.2.
- Style numbers (checked Nov 2026): CSL repository 10,869 styles, 720 footnote (not supported), so ~10,150 usable;
  a 300-style sample rendered fine except ~2% that crash in citeproc-py (refused when added). "10,000 styles" is fair.

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
- **Publish the browser extension** (steps given 8 Nov): Chrome Web Store developer account ($5 once), Microsoft
  Partner Center for Edge Add-ons (free), Firefox Add-ons account (free); upload the zips from the "Build the
  browser extension" workflow's artifacts; then put the store links on site/public/extension.html.
- (Done 8 Nov) **Register Citrello's Mendeley app** so "Import my Mendeley library" appears in the web app: dev.mendeley.com →
  My Apps; Armin already has an app and its ID/secret in GitHub (7 Nov). The app's redirect URL must be
  `https://citrello.app/app/mendeley.html`, and the ID must be in GitHub as `MENDELEY_CLIENT_ID` (a variable or
  a secret; site.yml reads both). Re-run "Deploy website" after changing it. Until then the button stays
  hidden and the export instructions show. The web app never uses the Mendeley secret.
- **Apply to the directories** (steps were given in the session of 6 Nov 2026): Claude at claude.ai/directory/manage
  (MCP connector, URL https://mcp.citrello.app/mcp); ChatGPT at platform.openai.com/apps-manage. Both want a privacy
  policy (citrello.app/privacy), a test account for reviewers and example prompts.
- **Edit docs/VALUE_PROPOSITION.html** (the value-proposition report, written 6 Nov 2026 before the web app plan).
  Remind him at the start of the first session in the new account. Update it once the web app exists (no-install,
  "what AI can't do without Citrello", claim checking).
- Edit the 1.0.1 release notes on GitHub: put `[@Key]` in backticks (removes the stray contributor "key").
- Send a real **Mendeley Cite** document to confirm that format works on real files (built from documentation,
  tested on generated files only).
- Try citrello.app/check with a real paper.

## Current focus: papers' full text (decided 8 Nov 2026)
Armin's direction: claim checking against abstracts is what Claude already does well; the real value is the
**full text people have access to but never download**. Keep **only the text** (not PDFs), tied to each reference,
so the AI can read it (`read_paper`, `find_in_papers`). Done (8 Nov): (1) PDFs dropped one by one or in bulk,
matched to references, marks and filters in the library; (2) the browser button takes the text from the paper's
page (the article on the page, or its PDF) with the person's access; (3) "Find free full texts" for imported
libraries: Europe PMC (read by the browser), Unpaywall's open-access PDFs (desktop: direct; web: through the
bridge's `/api/oa-pdf`, DOI only, nothing stored); (4) the **browser extension** (`extension/`, 8 Nov): a toolbar
button replacing the bookmarklet, and "Fetch the missing ones with my access" (bulk, one paper every 10–20 s, in
background tabs with the person's login; asks for site access on first use). Built by `packaging/build_extension.py`
(Chrome/Edge zip, Firefox zip; artifacts of the "Build the browser extension" workflow); tested in Chromium with
the web app (`tests/test_web_extension.py`). **Not yet in the stores** (Armin: store accounts and submissions).
The toolbar button opens a **panel** under the icon (like Mendeley/Zotero; Alt+Shift+C), not a window: the web app
answers through its open tab (the extension opens one in the background if needed and leaves it), the desktop app
directly (it accepts extension origins for /api/clip/ only).
People must re-drag the bookmarklet to get the version that takes the text.
- **Mendeley one-click import works** in the web app (Armin registered a second Mendeley app, 8 Nov).
- **Full texts tab** (sidebar, under Library; 8 Nov): summary, the three ways (extension with university access,
  free copies, PDFs you have), and the list of references still without text (Open paper / Add PDF). Moved out of
  Import because it's a main feature. Extension: when the page doesn't show the article it tries the page's PDF
  links (fetched with the extension's access when the page can't), PDF viewer pages, and script PDF buttons (it
  catches where the button wanted to open); on a PDF itself, the panel adds its text to the matching reference
  (/api/clip/pdf). "Are you human?" checks are **never answered automatically**: the bulk fetch pauses, shows the
  tab, and continues on "Continue", three times slower on that site afterwards. Not handled yet: PDF buttons that
  start a file download (would need the "downloads" permission).
- Extension, 8 Nov (Armin's failures: Elsevier, AIP, Wiley, IEEE, web pages): waits for redirects to settle
  (doi.org → linkinghub → ScienceDirect), for script-built pages, and for security checks that clear by themselves;
  a rule book of platform PDF addresses (`publisherPdfUrls` in grab.js: IEEE stampPDF, ScienceDirect pdfft, Wiley
  pdfdirect, Springer, Nature, the "/doi/pdf/" platforms, IOP, MDPI, arXiv, APS); web pages keep their readable text
  or say "Web page: nothing to read"; up to 3 papers at once, never two from one publisher (DOI prefix), 10–20 s
  between a publisher's papers; "Copy a report" on the fetch page (how failing pages were built, no text) so Armin
  can send it. Armin offered his browser access: declined (credentials); the report is the way to learn paywalled sites.
- Before fetching full texts (extension or free copies), the references not yet verified are checked online first
  ("Check the references online first", ticked by default), so wrong DOIs are corrected and missing ones found.
  Fixed on the way: in the web app a Crossref 404 came back as an ordinary answer, so wrong DOIs were never
  corrected (verify, add by DOI); `core.http_json` now raises HTTPError for 4xx/5xx answers.
- Security checks that pass by themselves (Cloudflare "Just a moment…", e.g. science.org): the extension watches
  from outside the page (`isChallenge`, `passCheck` in fetch.js) for up to a minute, following the reload when it
  passes; only a check still there after that pauses for the person.
- From Armin's first "Copy a report" (9 Nov): only this paper's own PDF links are used (never "Recommended"/
  "Cited by" ones), citation_pdf_url and platform rules first; a refused PDF is opened in the tab like a click and
  the address it lands on (ScienceDirect's signed link) is fetched; the bulk fetch's PDFs must contain most of the
  reference's title words (`fulltext.same_paper`, header X-Check-Title) or they aren't kept; DOIs with # < > ; are
  encoded for doi.org; doi.org "not found" → "DOI not found: check it"; a different DOI on the page is accepted when
  the title matches (Zenodo versions); books → "Book: add the chapters you cite"; nothing with a DOI is called a web
  page; pages that start blank (SPIE) get time; PDFs from the DOI alone (DTIC, arXiv) when the page won't load.
  Extension tests run with a screen in CI (xvfb-run) so PDFs open in Chrome's viewer as for people.

## Current focus: Home and guided tours (decided 10 Nov 2026)
Armin: the app looked like an ordinary reference manager, so people never saw what it solves. Now a **Home** screen
(first in the sidebar; where a new, empty library opens) shows nine tasks in three groups, worded as the person's
goal: Check a paper (catch mistakes in my references; every citation matches the list; my sources say what I wrote),
Write (typed references → live citations; cite while I write; write with Claude/ChatGPT from my library; find
references), My library (tidy up; LaTeX paper ready). Each starts a **tour on the real screen** (`TOURS` in ui.html):
a spotlight on what to click and a bubble with Back/Next, "See all steps" (ticks itself), moving on by itself when
the step is done (file chosen, check finished, button clicked); a tour left halfway is offered again on Home. The
check tours offer a **sample paper** (`doccheck.sample_paper()`, `/api/sample-paper`) with six planted mistakes:
retracted paper, DOI that doesn't exist, duplicate, wrong year, citation with no reference, reference never cited.
Found on the way: in "Check and fix", typed reference lists were looked up online but the result's source wasn't
recorded, so every typed reference said "Not found online" and DOIs/years were never compared; fixed (10 Nov).
AI decision (10 Nov): Claude/ChatGPT subscriptions can't be used by other apps, so the "sources" and "find
references" tours send people to their Claude/ChatGPT (prompt to copy, links that open a new chat with it; the
`?q=` links are not verified to fill in the prompt). **"What your sources say"** (10 Nov, `sources.py`, in the
"Check and fix" results, tour 3): every sentence that cites a paper next to the passages of that paper's full text
that match it best (BM25 + the best sentences, shared words highlighted), no AI, on the computer; reads [@Key]
markers, Zotero/Mendeley/EndNote/Word fields, Mendeley Cite controls and typed [1] / (Author, year) citations; Word
documents only for now (not PDFs). 11 Nov (Armin): Full texts has two cards, "Every paper you can open" (with the
extension: it reads open-access and subscribed papers; without: free copies now + get the extension; tip to sign in to
the university library first) and "PDFs you already have". "Ask Claude / ChatGPT" opens the connector guide the
first time with the question waiting at its end (remembered per browser once done). Regular users: "Show
step-by-step guides" on Home (off → tiles go straight to the screen, the button flashes; "Don't show guides" in the
bubble), finished tours say "Show me again". 11 Nov, the journey without dead ends (Armin: "103 papers without
text and no button"): the sources panel adds the document's references to the library in one click (project named
after the document) and shows them; papers without text get "Get their full texts with my access" (extension, just
those papers), or "Get the free ones now" + get the extension, or "I have the PDFs" / "add its PDF" per paper; the
sentences refresh by themselves as texts arrive; long papers get filters (passages found / no clear match / without
text / not in library) and 20 sentences at a time; PDFs say it works on Word for now. Also: an unreadable document
offers the sample and "tell us"; "See them" after adding references; imports end with "Next: check them online ·
get the papers' full texts"; Verify's problems have "Show only these"; "Still needs you" says what to do.
**Matching by meaning** (11 Nov, Armin: word matching "doesn't work well"): in the page (desktop and web), a
module worker loads transformers.js 3.0.2 from jsDelivr and two models from Hugging Face, cached by the browser
(~220 MB once): all-MiniLM-L6-v2 (q8) fingerprints each sentence of the cited paper (`sources.paper_sentences`,
`/api/sources/windows`, paper's own reference list left out, max 800 sentences) and the claim; the 4 closest + the
best word match are judged by nli-deberta-v3-small (q8): supports (entailment >= 0.5) / seems to contradict
(contradiction >= 0.6, same topic: similarity >= 0.45, and still contradicting when read with its neighbours) /
doesn't clearly say (closest passage shown). Fingerprints and verdicts cached in IndexedDB. Tried and rejected:
MiniLM/bge/e5/gte/arctic/mxbai embeddings alone (2/4 on hard cases), ms-marco and bge rerankers, nli xsmall and
judging 3-sentence passages (missed paraphrases); a stray space before the full stop changed verdicts (fixed in
claimOf). Speed on 4 cores, single-threaded (no cross-origin isolation): ~47 sentences/s fingerprinting, ~11
verdicts/s, so a 103-paper document takes ~10–15 min the first time, instant after. Possible speed-ups: WebGPU,
threads (needs COOP/COEP, which would break the Mendeley/clip pop-ups), fingerprinting papers when their text
arrives. Falls back to word matches if the models can't load (offline). Privacy page updated.
Next: an optional AI verdict with a cheap
model paid by Citrello within a free monthly allowance for signed-in users (about 1–2 cents per paper); "find
references" (OpenAlex + stronger model, ~5–10 cents per paragraph) later, possibly paid credits.

## The web app
**Progress:** steps 1 and 2 are live at citrello.app/app and tested in Chromium and WebKit: library in the browser,
import/export, backup/restore, check and fix Word/PDF/LaTeX, arXiv .zip, style search, progress bars. Step 3 too:
sign in in the web app and the open citrello.app tab answers ChatGPT/Claude (the bridge accepts the browser's token).
Armin still has to apply to Claude's connector directory and ChatGPT's app directory. Step 4 too: the browser button
and Zotero (online, or its zotero.sqlite file) in the web app; Mendeley through an exported file. Next: step 5
(claim check). Armin's section 3 decisions are recorded in the plan: free
cloud copy with a limit, EU storage; Word add-in later; desktop app kept, web app leads.

**docs/WEB_APP_PLAN.md** is the plan: everything in the browser at citrello.app/app so nobody hits install
warnings; the browser tab acts as the connector's device; optional cloud copy for an always-on connector, Word
add-in (Office.js, also Mac/web) and Overleaf live link; claim checking ("does the cited paper support this
sentence?") with the person's own AI, Consensus recommended alongside; "what AI can't do without Citrello".
Decisions for Armin are in its section 3.

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
8. Footnote styles (Chicago notes, OSCOLA: 720 styles, humanities and law). biblatex projects for arXiv (needs
   Biber's .bbl format; today Citrello points to Overleaf's Submit → arXiv). Style search inside the Word tab.

## Known limits
- Windows SmartScreen and Mac Gatekeeper warnings until signing.
- PDF checks can't be fixed (report only); scanned PDFs can't be read; unusual two-column layouts may split refs.
- ChatGPT connector needs a paid ChatGPT plan with Developer mode; web connectors need the app running.
- The Claude Desktop extension doesn't update itself (users re-drag it after connector changes).
