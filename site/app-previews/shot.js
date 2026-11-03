const { chromium } = require('playwright');
(async () => { const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1360, height: 860 } });
  for (const f of ['A-classic','B-studio','C-search']) { await p.goto('file://' + __dirname + '/' + f + '.html'); await p.waitForTimeout(1500);
    await p.screenshot({ path: __dirname + '/shots/' + f + '.png' }); }
  await b.close(); })();
