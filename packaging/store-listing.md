# Microsoft Store listing: Citrello

Copy these into Partner Center. Fields are in the order Partner Center asks for them.

## Product name
Citrello

## Category
- Primary: **Productivity**
- Secondary: **Education**

## Pricing
Free. Available in all markets.

## Privacy policy URL
https://citrello.app/privacy

## Website
https://citrello.app

## Support contact
https://github.com/citrello/citrello/issues

## Short description (up to 100 characters for some layouts; keep this first sentence strong)
The free reference manager for Claude, ChatGPT, Word and Overleaf. Real, checked citations.

## Description
References used to be the bitter end of every paper. Citrello makes them the sweet part.

Citrello is a free, open-source reference manager built for writing with AI. Your library stays on your computer and is shared with Claude, ChatGPT, Word and Overleaf.

AI that cites only real papers
• Claude and ChatGPT search your own library and cite only papers in it. They don't make references up.
• Every new reference is looked up on Crossref before it's added.

Live citations in Word
• Insert citations from a search dialog in Word's Citrello tab.
• Switch between 10,000 journal styles (APA, IEEE, Harvard, Nature, Vancouver…) and update every citation and the bibliography at once, as one Undo step.
• Convert old drafts with hand-typed citations like [1] or (Smith et al., 2020) into live ones.

Reliability checks
• Each reference is rated verified, check or problem.
• Retracted papers, DOIs that don't exist and authors that don't match are flagged.

LaTeX and Overleaf
• Clean BibTeX and biblatex with stable keys.
• Check a project for missing, unused and duplicate entries, and keep a .bib file up to date automatically.

Your library, your choice
• Import and export Mendeley, Zotero, EndNote, RIS, BibTeX and CSL-JSON.
• Add papers from any website with the browser button, even behind your university login.
• No ads, no tracking. An account is optional.

## What's new in this version
First release.

## Product features (up to 20 short lines)
- Claude and ChatGPT cite only papers in your library
- Live citations in Microsoft Word
- 10,000 citation styles
- Reliability rating for every reference
- Retraction and wrong-DOI detection
- Convert hand-typed citations into live ones
- Clean BibTeX and biblatex for LaTeX and Overleaf
- Import and export Mendeley and Zotero
- Add papers from any website with one click
- Your library stays on your computer

## Search terms (up to 7)
reference manager; citations; bibliography; Zotero; Mendeley; LaTeX; Claude

## Screenshots (1366×768 or larger, PNG)
1. Library with the detail panel (a reference selected).
2. Pasting a DOI in the search box ("Press Enter to look up this DOI").
3. Word with the Citrello tab and the Insert citation dialog.
4. Documents → LaTeX and Overleaf, showing a check report.
5. Settings → ChatGPT and Claude on the web.

## Age rating questionnaire
No user-generated content shared with others, no violence, no purchases. Expected rating: 3+ (everyone).

## Notes for certification (Submission options → Notes for certification)
Citrello is a desktop reference manager. When started, it runs a small local server on 127.0.0.1:8585 and opens its window in the default web browser (this is the app's interface; nothing is loaded from the internet). Quit it with the Quit button in the sidebar.

To test: start Citrello, paste 10.1016/j.renene.2016.10.066 into the search box and press Enter. The reference is looked up on Crossref and added to the library.

Restricted capabilities:
- runFullTrust: Citrello is a desktop (Win32) app.
- unvirtualizedResources: Citrello installs its Word tab as a template in Word's own STARTUP folder (%APPDATA%\Microsoft\Word\STARTUP), and, when the user clicks Connect, adds itself to the settings files of Claude Desktop (%APPDATA%\Claude) and Codex (%USERPROFILE%\.codex). These files must be the real ones that Word, Claude and Codex read, not a private copy for the package. Citrello only writes these files when the user asks it to, and removes the Word template on request (Settings → Word → Remove).

No account is needed to test. Signing in is optional and only used for the ChatGPT connector.
