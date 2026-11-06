# Citrello

**References, the sweet part.**

Citrello is a free, open-source reference manager built for writing with AI. Your library lives on
your computer and is shared by the Citrello app, Claude and Word:
- **Claude:** every reference you or Claude adds is there in every Claude chat.
- **Word:** documents get live citations in any of ~10,000 journal styles.
- **Mendeley and Zotero:** import from and export to both, so nobody is locked in.

> *Citron* hides "cit" for citation; *-ello* is the sweet Italian ending of limoncello, the
> drink that closes a good meal. Writing is the hard part. References should be the sweet finish.

## What it does
- **Library**
  - **Adding references:** by DOI, by a link to a paper or web page, or by pasting a reference as
    typed in a paper. Each one is looked up on Crossref (or doi.org for DataCite, Zenodo and other
    registries).
  - **Reliability rating:** each reference gets one:
    - **Verified:** matches the publisher record.
    - **Check:** e.g. no DOI, web page, preprint, year or authors differ.
    - **Problem:** retracted paper, or a DOI that's wrong or doesn't exist.
    - **Complete:** imported, details look complete, not checked online.
  - **Duplicates:** detected and skipped.
  - **Projects:** group the references of each paper.
  - **Verify online:** checks a whole imported library and corrects its details from the
    publisher record.
- **Claude connector:** Claude Desktop (and Claude Code) can search, add, organise, verify and
  format your references, check the citations in a text, and write or restyle Word documents with
  live citations. Install it from Settings → Claude (Citrello makes the `Citrello.mcpb` extension file for you).
- **ChatGPT, Claude on the web and phone:** sign in to a free Citrello account, then add the connector
  `https://mcp.citrello.app/mcp`. Requests go through Citrello's bridge to the app on your computer.
- **Codex:** one click adds Citrello to the Codex app, CLI and IDE extension.
- **Word tab (Windows)**
  - **Insert / Edit citation:** a search dialog over your library, with page, prefix ("see") and
    "year only" for *Smith et al. (2020)*.
  - **Update citations:** renumbers and reformats everything and rebuilds the bibliography, as one
    Undo step.
  - **Citation style:** chooses the style for the document.
  - **Click a citation** to see its reference in the status bar; **double-click** to edit it.
- **Browser button:** a bookmark that adds the paper you're looking at, or a reference you've
  selected on the page, even on sites that block robots.
- **Documents: check and fix any paper.** Drop in a Word document, a PDF or a LaTeX project. It can come from
  Zotero, Mendeley (Desktop or Cite), EndNote or Word's own citations, be typed by hand, or be written with Claude
  using `[@Key]` markers. Citrello:
  - checks every reference online: retracted, wrong or nonexistent DOI, authors or year not matching the publisher;
  - finds the same paper listed twice, citations to references missing from the list, references never cited, and
    reference lists that weren't refreshed;
  - adds the references to your library in one click, and downloads the report as a spreadsheet;
  - **fixes Word documents:** writes a corrected copy where every citation is live (Mendeley Cite compatible),
    details come from the publisher's record, duplicates are merged, and the reference list is rebuilt in any of
    ~10,000 styles, with a list of every change.
- **LaTeX and Overleaf**
  - **Clean BibTeX and biblatex:** stable keys (the same as `[@Key]`), so `\cite{Key}` never breaks.
    Acronyms are brace-protected (`{LiDAR}`), special characters are escaped, and page ranges use `--`.
    Importing a `.bib` keeps its keys.
  - **Check a paper:** a `.tex` file or an Overleaf project `.zip`. Citrello reports:
    - cited keys missing from the `.bib`, which it can add from your library;
    - unused entries;
    - the same paper under two keys;
    - retracted papers, wrong DOIs and missing DOIs.
  - **Convert a hand-typed bibliography:** turns `\begin{thebibliography}` / `\bibitem` into `references.bib`
    plus matching `\cite` keys. Each reference is checked on Crossref and added to your library.
  - **Keep a `.bib` file up to date:** Citrello rewrites it whenever a project changes. Use it with local
    LaTeX, or with Overleaf through Dropbox or Git sync.
  - **Claude and ChatGPT:** they can write `\cite{Key}`, give you the `.bib` for a project, and check a LaTeX
    project.
- **Import from Zotero in one click:** reads the Zotero library on your computer directly (no account or key; Zotero
  can stay open). Zotero collections become Citrello projects.
- **Import / export:** RIS, BibTeX, biblatex and CSL-JSON (Mendeley, Zotero, EndNote and others), plus a
  Mendeley account (import and export) and a Zotero account (import).

## Check a paper without installing anything
[citrello.app/check](https://citrello.app/check) runs the paper checker in your browser: drop in a Word file or PDF,
get the report and a fixed copy. Citrello's own code runs inside the browser (Pyodide), so the paper never leaves
your computer; only the reference lookups go to Crossref.

## Install (Windows)
1. **Download** `Citrello-windows.zip` from the
   [latest release](https://github.com/citrello/citrello/releases/latest). Right-click it → **Extract All** into
   a permanent folder (for example Documents\Citrello). Keep its two files together.
2. **Start** `Citrello.exe`. Your browser opens Citrello's window.
   - *Windows may say "Windows protected your PC" for new downloads: click **More info → Run
     anyway**.*
3. **Settings → Claude → Connect to Claude Desktop.** Citrello shows `Citrello.mcpb` in File Explorer;
   drag it onto **Claude Desktop → Settings → Extensions** and click **Install**.
4. **Settings → Word → Install Word button**, then restart Word.
5. **Settings → Browser button:** drag **＋ Add to Citrello** onto your bookmarks bar.
6. *Optional:* **Settings → Account:** sign in with your email to use ChatGPT or Claude on the web, then follow
   the steps under **ChatGPT and Claude on the web**.

## Install (Mac, beta)
For Macs with Apple silicon (M1 or newer). The Word tab is Windows-only for now.
1. **Download** `Citrello-mac.zip` from the
   [latest release](https://github.com/citrello/citrello/releases/latest). Unzip `Citrello-mac.zip` and drag
   **Citrello** into **Applications**.
2. **Open it the first time.** The app isn't signed with an Apple developer certificate yet, so macOS blocks
   the first launch:
   1. Double-click Citrello and click **Done** in the warning.
   2. Open **System Settings → Privacy & Security**, scroll down, and click **Open Anyway** next to "Citrello
      was blocked". Confirm with your password.
   3. Your browser opens Citrello's window. After this, Citrello opens normally.
3. **Settings → Claude → Connect to Claude Desktop.** Finder shows `Citrello.mcpb`. Drag it onto
   **Claude Desktop → Settings → Extensions** and click **Install**.
4. Citrello has no Dock icon: it runs in your browser at `http://localhost:8585`. Close it with **Quit** in the
   sidebar, and open the page again by starting Citrello.

## Using it with Claude
Once installed, Citrello's tools are available in every Claude Desktop chat. For example:
- "Add 10.1016/j.renene.2016.10.066 to my Citrello library, project *Decommissioning review*."
- "Write the introduction using my *Decommissioning review* references and give me a Word file in
  Elsevier Harvard style."
- "Edit this paragraph but keep every [@…] marker." Then use Word's **Update citations**.
- "Write the related-work section in LaTeX with \citep from my *Thesis* project, and give me the biblatex file."

## Where your data is
- **Library:** `%USERPROFILE%\Citrello\library.json` (or `$CITRELLO_HOME`). Keep the folder in
  OneDrive or Dropbox to share it between computers. Back it up with Library → Export → CSL-JSON.
- **Documents Claude creates:** `%USERPROFILE%\Citrello\Documents`.
- **Extra styles:** `%USERPROFILE%\Citrello\styles`.
- **Backups made by the Word tab before changes:** `%USERPROFILE%\Citrello\backups`.

**Privacy:** your library stays on your computer. Citrello only goes online for these:
- to look up references (Crossref, doi.org, the page you add);
- to download citation styles;
- to reach your Mendeley or Zotero account, if you connect them;
- once a day, an anonymous usage count: a random install ID, the app version and the operating system
  (turn it off in Settings → Account);
- if you sign in, to keep the connection open for ChatGPT and Claude on the web.

The account is optional. It stores your email, your computers and whether you want feature emails. See the
[privacy policy](https://citrello.app/privacy). There is no other tracking.

## Build from source
Windows: requires Python 3.10+. Mac: `bash packaging/build_mac.sh` (builds `dist/Citrello.app`).
```
build_windows.bat
```
The script creates `dist\Citrello.exe` and `dist\CitrelloMCP.exe`; the Claude extension (`Citrello.mcpb`) is
built from the connector the first time you click Connect to Claude Desktop. To run
without building, use `Start Citrello.bat` (needs `pip install python-docx citeproc-py pypdf`).

Each push to this repository is tested, and each version tag (`v1.0.0`, …) builds the Windows files
and attaches them to a GitHub release automatically. See `.github/workflows/build.yml`.

The account and connector service is in [`bridge/`](bridge/README.md). It runs on Cloudflare Workers.

Development setup:
```
pip install python-docx citeproc-py pypdf pytest
pytest
```

## Limits
- **Mac:** the Mac version is a beta for Apple silicon, without the Word tab. Use Documents → Update for Word
  files on a Mac.
- **Web connectors need the app running:** ChatGPT and Claude on the web reach your library through the app,
  so Citrello must be open on a computer that's signed in.
- **Footnote styles** (e.g. Chicago notes) are not supported yet.
- **Formatting engine:** the open-source citeproc-py. A few styles may differ in small details from
  Zotero's or Mendeley's output.

## Contributing and licence
Issues and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md), which includes the
contributor licence agreement.

Copyright © 2026 Armin Hosseinghorbani. Citrello is licensed under the
[GNU Affero General Public License v3.0 or later](LICENSE). Citation styles are from the
[CSL project](https://citationstyles.org/) (CC BY-SA 3.0).
