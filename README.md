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
  live citations. Install it with one click (`Citrello.mcpb`).
- **Word tab (Windows)**
  - **Insert / Edit citation:** a search dialog over your library, with page, prefix ("see") and
    "year only" for *Smith et al. (2020)*.
  - **Update citations:** renumbers and reformats everything and rebuilds the bibliography, as one
    Undo step.
  - **Citation style:** chooses the style for the document.
  - **Click a citation** to see its reference in the status bar; **double-click** to edit it.
- **Browser button:** a bookmark that adds the paper you're looking at, or a reference you've
  selected on the page, even on sites that block robots.
- **Documents**
  - **Update:** turns `[@Key]` markers (what Claude writes) into live citations.
  - **Convert:** converts papers whose citations were typed by hand, numbered `[1]`, `[2–5]` or
    author-year `(Smith et al., 2020)`, into live citations.
  - **Mendeley Cite compatible:** citations use the format of Mendeley's Word plugin, so Mendeley
    Cite can take a document over.
- **Import / export:** RIS, BibTeX and CSL-JSON (Mendeley, Zotero, EndNote and others), plus a
  Mendeley account (import and export) and a Zotero account (import).

## Install (Windows)
1. **Download** `Citrello.exe`, `CitrelloMCP.exe` and `Citrello.mcpb` from the
   [latest release](https://github.com/citrello/citrello/releases/latest). Keep them in one folder.
2. **Start** `Citrello.exe`. Your browser opens Citrello's window.
   - *Windows may say "Windows protected your PC" for new downloads: click **More info → Run
     anyway**.*
3. **Settings → Claude → Install in Claude Desktop**, then click **Install** in Claude.
4. **Settings → Word → Install Word button**, then restart Word.
5. **Settings → Browser button:** drag **＋ Add to Citrello** onto your bookmarks bar.

Coming from Refcite (Citrello's earlier name)? Your library, settings and Mendeley login are copied
over automatically on first start. Remove the old **Refcite** extension in Claude Desktop under
**Settings → Extensions**.

## Using it with Claude
Once installed, Citrello's tools are available in every Claude Desktop chat. For example:
- "Add 10.1016/j.renene.2016.10.066 to my Citrello library, project *Decommissioning review*."
- "Write the introduction using my *Decommissioning review* references and give me a Word file in
  Elsevier Harvard style."
- "Edit this paragraph but keep every [@…] marker." Then use Word's **Update citations**.

## Where your data is
- **Library:** `%USERPROFILE%\Citrello\library.json` (or `$CITRELLO_HOME`). Keep the folder in
  OneDrive or Dropbox to share it between computers. Back it up with Library → Export → CSL-JSON.
- **Documents Claude creates:** `%USERPROFILE%\Citrello\Documents`.
- **Extra styles:** `%USERPROFILE%\Citrello\styles`.
- **Backups made by the Word tab before changes:** `%USERPROFILE%\Citrello\backups`.

**Privacy:** everything runs on your computer. Citrello only goes online to look up references
(Crossref, doi.org, the page you add), to download citation styles, and, if you connect them, to
reach your Mendeley or Zotero account. There is no tracking.

## Build from source
Requires Windows and Python 3.10+.
```
build_windows.bat
```
The script creates `dist\Citrello.exe`, `dist\CitrelloMCP.exe` and `dist\Citrello.mcpb`. To run
without building, use `Start Citrello.bat` (needs `pip install python-docx citeproc-py`).

Each push to this repository is tested, and each version tag (`v1.0.0`, …) builds the Windows files
and attaches them to a GitHub release automatically. See `.github/workflows/build.yml`.

Development setup:
```
pip install python-docx citeproc-py pytest
pytest
```

## Limits
- **Windows only:** the Word tab and the packaged app are Windows-only for now.
- **Claude access:** the connector works with Claude Desktop and Claude Code. Claude in the browser
  can't reach your computer.
- **Footnote styles** (e.g. Chicago notes) are not supported yet.
- **Formatting engine:** the open-source citeproc-py. A few styles may differ in small details from
  Zotero's or Mendeley's output.

## Contributing and licence
Issues and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md), which includes the
contributor licence agreement.

Copyright © 2026 Armin Hosseinghorbani. Citrello is licensed under the
[GNU Affero General Public License v3.0 or later](LICENSE). Citation styles are from the
[CSL project](https://citationstyles.org/) (CC BY-SA 3.0).
