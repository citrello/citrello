# Citrello for Word: Microsoft add-in store (AppSource) listing

Partner Center → Marketplace offers → + New offer → Office add-in. Package: upload `site/public/word/manifest.xml`
(also at https://citrello.app/word/manifest.xml).

## Name
Citrello

## Summary (100 characters max)
Cite from your library in any style, fix every reference, and check each claim against the paper.

## Description
Citrello is a free reference manager built for writing with AI (citrello.app). The Word add-in does it all inside
Word:

• Cite as you write. Search your Citrello library and click Cite. Type "Wang et al." and Citrello suggests Wang's
  papers. Over 10,000 citation styles, and a reference list that keeps itself up to date.

• Edit any citation. Click a citation to see its papers, add or remove one, add a page number, or leave the author's
  name out when it's already in your sentence.

• Bring any document. Citations made with Zotero, Mendeley, EndNote or Word, or typed by hand, become Citrello
  citations in one click. The text is kept, and every change is a tracked change you can accept or reject.

• Fix the references. Citrello checks every reference online: wrong years, authors or DOIs, retracted papers, the
  same paper listed twice, citations with no reference behind them. It fixes what it can and adds a comment where
  you need to decide.

• Check what your sources say (with a free Citrello account). Click a citation and press "Check the claim": Claude
  reads the cited paper's full text and shows the exact sentence that supports what you wrote, or says it doesn't.
  Every free account gets 100 credits (about 33 claim checks); the Student and Researcher plans give 1,000 or
  4,000 credits a month, and "Find another reference" looks for a better source in your library.

Try it without an account: open the add-in and choose "Try it with sample papers".

Your library and documents stay on your computer and in your own Citrello account. Citrello is open source:
github.com/citrello/citrello

## Additional purchase
Tick "My add-in requires an additional purchase" / "Additional purchase may be required": the Student and Researcher plans (more
credits for Claude's checks) are sold through Stripe (Link) from inside the add-in and on citrello.app/pricing.

## Category
Productivity (secondary: Education)

## Search keywords
citations, references, bibliography, reference manager, Zotero, Mendeley, EndNote, APA, citation style, research

## Links
- Privacy policy: https://citrello.app/privacy
- Support: https://citrello.app/word/ (email: hello@citrello.app)
- Help / learn more: https://citrello.app/word/
- End user licence: use the standard Microsoft contract, or the AGPL licence:
  https://github.com/citrello/citrello/blob/main/LICENSE

## Platforms
Word on Windows, Word on Mac, Word on the web. Not iPad (for now).

## Screenshots (1366 × 768)
Take them in real Word with the Citrello panel open (Windows: Win+Shift+S; Mac: Cmd+Shift+4), and Claude resizes
them to 1366 × 768:
1. The panel citing a paper, with the "Wang et al." suggestions.
2. A citation clicked: its papers, and "Check the claim" with Claude's verdict and the quote.
3. "This document": a Zotero/Mendeley paper's citations counted and the problems listed, with Track Changes shown.
4. The reference list in a chosen style.

## Notes for certification (paste into "Notes for certification")
Thank you for reviewing Citrello for Word.

No account is needed to test the main features:
1. Open the add-in (Home tab → Citrello). The first start takes 10-30 seconds while Citrello's engine loads.
2. Click "Try it with sample papers". Six real papers appear.
3. Type a sentence in the document, put the cursor at its end, search "nucleic" in the panel and click Cite. A
   citation appears in APA style.
4. Type "LeCun et al." at the end of a sentence: the panel suggests that paper; click Cite.
5. Change the style at the top (for example IEEE): every citation and the reference list change.
6. Click "Insert the reference list": the list appears and updates as you cite.
7. Click inside a citation: its papers are listed; you can add one from the search, remove one, or add a page.
8. To test a document from another reference manager, open any Word file with Zotero, Mendeley or typed citations:
   the "This document" card counts them and lists problems; "Make them Citrello citations" converts them with
   tracked changes.

"Check the claim" and "Find another reference" use Claude and need a free Citrello account (sign-in with a code
emailed to you); they are explained in the panel when not signed in.

Support: hello@citrello.app
