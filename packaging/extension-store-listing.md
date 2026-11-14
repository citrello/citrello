# Citrello browser extension: store listing (Chrome Web Store, Edge Add-ons, Firefox Add-ons)

Copy these into the store forms. Packages: GitHub → Actions → "Build the browser extension" → latest run →
Artifacts → `citrello-extension` (contains `citrello-extension-chrome.zip`, also for Edge, and
`citrello-extension-firefox.zip`).

## Name
Citrello

## Short description (132 characters max)
Add papers to Citrello with one click, and let ChatGPT and Claude read the full text of the papers you have access to.

## Description
Citrello is a free, open-source reference manager built for writing with AI (citrello.app).

This extension adds two things:

• One click to add a paper. On any paper's page, click the Citrello button (or press Alt+Shift+C). A small panel
  shows the paper Citrello found, checked against the publisher's record, whether its full text was found (with your
  own access), and a project to put it in. Click Add.

• Full texts for your whole library. In Citrello (Library → Import → Papers' full text), choose "Fetch the missing
  ones with my access". The extension opens each paper in a background tab with your university or library login,
  takes its text, and closes the tab. ChatGPT and Claude can then check what your sources really say. It goes
  slowly on purpose, one paper every 10–20 seconds, because publishers don't allow fast automatic downloading.

• Never miss a paper's text. Open a paper that's in your Citrello library without its full text, and the extension
  says so on the page, with one button: "Add its text". (Optional: it's turned on when you allow the extension to
  read pages, and the check happens on your computer.)

Only the text is kept, in your Citrello library on your own computer (or in your browser). Nothing is sent to
Citrello's servers. Source code: github.com/citrello/citrello

## Category
Chrome: Productivity (or Education). Edge: Productivity. Firefox: Other / Search Tools → choose "Other".

## Privacy policy URL
https://citrello.app/privacy

## Homepage / support URL
https://citrello.app/extension (support: hello@citrello.app)

## Single purpose (Chrome asks)
Help researchers add papers to their Citrello reference library and keep the text of papers they have access to,
so their AI assistant can read them.

## Why each permission is needed (Chrome asks for each)
- activeTab: when the person opens the toolbar panel (or presses Alt+Shift+C), read that one page's citation details
  and text to add the paper.
- scripting: run the small script that reads a paper's details and text, on the page the person clicked on or on a
  paper they asked the extension to fetch.
- storage: remember the list of papers the person asked to fetch (until finished), whether they use the web or
  desktop version of Citrello, and the list of papers in their library that have no full text yet (DOI, title, key).
- Host permissions (optional, "<all_urls>"): asked only when the person first fetches papers in bulk, or when they
  turn on "tell me when I open a paper whose full text Citrello doesn't have" (from the toolbar panel or the options),
  because papers are on many different publishers' websites. Used to open the papers the person asked for, and to
  compare the citation details of a page the person opens with their own list of papers without text, on the
  device: on a match the extension shows a badge and a small box offering to add the text. Nothing about the pages
  visited is sent anywhere; this can be turned off in the options.
- Host permissions for citrello.app and localhost:8585 (required): to find the person's open Citrello tab (so the
  panel uses it instead of opening another), and to add papers to Citrello for desktop, which runs on the person's
  own computer at localhost:8585. Nothing else is reached.
- Content script on citrello.app and localhost:8585: lets the Citrello app talk to the extension (start a fetch,
  receive each paper's text, answer the toolbar panel).

## Data usage (Chrome's "Privacy practices" tab)
- Collects: "Website content" (the text of papers the person asks for), used only on the person's device for the
  extension's purpose. Not sold, not transferred to third parties, not used for creditworthiness or advertising.
- No personally identifiable information, health, financial, authentication, personal communications, location,
  web history or user activity data is collected. (The notice compares the page open in a tab with the person's list
  of papers on the device; pages visited are neither stored nor sent.)
- Remote code: no (all code is in the package).

## Firefox: source code
The code is not minified or generated: answer "No" when asked whether the add-on contains minified or machine-generated code.

## Screenshots and promo image
1280 × 800, in packaging/extension-shots/, in this order: store-panel.png (the toolbar panel on a real paper's
page), store-notice.png (the notice on a paper in the library without its text), extension-fetch.png (the fetch page,
"Full texts with your access"). A 440 × 280 promo tile is promo-440x280.png. For the toolbar panel,
take a screenshot of a real paper's page with the panel open (Windows: Win+Shift+S; Mac: Cmd+Shift+4), at 1280 × 800.
