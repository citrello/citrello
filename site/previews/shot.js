const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 1 });
  for (const f of (process.argv[2] ? [process.argv[2]] : ['1-amalfi', '2-manuscript', '3-candied', '4-botanical', '5-night'])) {
    await p.goto('file://' + __dirname + '/' + f + '.html'); await p.waitForTimeout(1500);
    await p.screenshot({ path: __dirname + '/shots/' + f + '.png' });
  }
  await b.close();
})();
