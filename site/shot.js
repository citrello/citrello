const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  p.on('pageerror', (e) => console.log('PAGEERROR', e.message));
  await p.goto('file://' + __dirname + '/public/index.html'); await p.waitForTimeout(9000);
  await p.screenshot({ path: __dirname + '/shots/desktop-hero.png' });
  await p.screenshot({ path: __dirname + '/shots/desktop-full.png', fullPage: true });
  const m = await b.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
  await m.goto('file://' + __dirname + '/public/index.html'); await m.waitForTimeout(9000);
  await m.screenshot({ path: __dirname + '/shots/mobile-full.png', fullPage: true });
  const w = await m.evaluate(() => document.documentElement.scrollWidth); console.log('mobile scrollWidth', w);
  await b.close();
})();
