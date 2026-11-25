# Citrello in Claude's and ChatGPT's directories

The connector: `https://mcp.citrello.app/mcp` (OAuth 2.1 with dynamic client registration, PKCE S256).
Documentation page: https://citrello.app/connect. Privacy: https://citrello.app/privacy. Terms: https://citrello.app/terms.
Support: hello@citrello.app.

## Reviewer account (both directories, and Microsoft's)

- Email **reviewer@citrello.app**, fixed 8-digit code (the code boxes take 8) in the GitHub secret `REVIEWER_CODE` (deployed to the bridge by
  "Deploy bridge"). No email is sent for this address; wrong codes are limited like everyone's (5, then ask again).
- Its web-app library fills with the six sample papers (`site/public/word/samples.json`) when it's empty.
- Instructions to give reviewers (never put the code in a package or the repository):

  > 1. Open https://citrello.app/app in a browser tab. Click **Settings**, then under **Account** enter
  >    reviewer@citrello.app and click **Email me a code** (no email is sent for this address), then enter the code `<REVIEWER_CODE>`.
  > 2. Keep that tab open (it can be in the background): it is the user's Citrello, holding six sample papers.
  > 3. Connect Citrello in ChatGPT / Claude and sign in with the same email and code.
  > 4. Try the test prompts. Tools that need files on a computer (LaTeX project check, arXiv .zip, updating an
  >    existing Word file, Zotero import) need the desktop app; in the browser they explain that.

## Claude: Connectors Directory (done: published 25 Nov 2026)

Listed at https://claude.ai/directory/connectors/citrello as a community connector (automated review).
Managed at claude.ai/directory/manage. "Verified" is decided by Anthropic after a human test; they ask for
test access by email (use the reviewer account above).

## ChatGPT: plugin directory

Package: `python packaging/build_chatgpt_plugin.py --demo-url <video link>` → `citrello-chatgpt-plugin.zip`
(plugin.json + mcp.json from `packaging/chatgpt-plugin/`, logo drawn by brand_icon.py). The script checks
OpenAI's limits; `tests/test_chatgpt_plugin.py` too. The listing texts and the 5 + 3 test cases are in
`packaging/chatgpt-plugin/plugin.json`.

Steps (Armin):
1. **Verify the organisation**: platform.openai.com → Settings → Organization → General → verification
   (individual or business). The directory shows the verified name.
2. **Record the demo video** (script below), upload it unlisted (YouTube "Unlisted", or a Google Drive link set to
   "Anyone with the link"), and send the link: the package is rebuilt with it.
3. platform.openai.com/plugins → **Upload new or existing plugin** → choose the verified developer identity →
   **Upload plugin** → the .zip. Fix anything "Metadata & Skills" lists (Copy issues → send them).
4. **MCPs → Connect MCP server**: URL and OAuth come from the package. The **domain verification** step shows a
   token: send it; it goes in `OPENAI_APPS_CHALLENGE` in bridge/wrangler.toml and is served at
   https://mcp.citrello.app/.well-known/openai-apps-challenge after "Deploy bridge". Then **Connect**, sign in
   with the reviewer account, and wait for the tool scan.
5. **Review details**: reviewer credentials (email reviewer@citrello.app, code, login URL https://citrello.app/app,
   the instructions above); check the imported test cases; **Save details**.
6. **Submit for review**, complete the attestations. Feedback comes by email. After approval: **Publish plugin**.
7. Category: if "Productivity" isn't offered, choose the closest (Education / Research).

Rules worth remembering: the name is just "Citrello" (no "MCP"/"Plugin"); the plugin can't sell anything; tool
changes after approval are scanned daily (no new package), listing changes need a new package.

## Demo video script (1–2 minutes, screen recording, no voice needed)

Before: sign in as the reviewer account in a citrello.app/app tab (sample papers), and connect Citrello in ChatGPT
(Developer mode connector with https://mcp.citrello.app/mcp until the plugin is listed).

1. (5 s) The citrello.app/app tab with the six sample papers. Caption: "Citrello: your reference library".
2. (15 s) ChatGPT, new chat with Citrello on: "Find my papers on offshore wind decommissioning and use them to
   write a short cited paragraph about end-of-life costs." Show the tool call and the paragraph citing Topham 2017.
3. (15 s) "Add 10.1038/nature14539 to my Citrello library and tell me whether it's reliable." Show "verified".
4. (15 s) "Format all the references in my library in APA 7th edition."
5. (10 s) "Which citation style does the Journal of Cleaner Production use? Find it in Citrello."
6. (10 s) "Give me BibTeX entries for the deep learning papers in my Citrello library."
7. (10 s) A question that shouldn't use Citrello ("What is the capital of Australia?") answered without it.
8. (5 s) End card: citrello.app/connect.

Record with Windows' Snipping Tool (Record), or Mac's Screenshot app (Cmd+Shift+5 → Record).
