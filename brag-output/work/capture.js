const { chromium } = require('playwright');
const path = require('path'), fs = require('fs');
(async () => {
  const [mode, out, ...rest] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await page.goto('file://' + path.resolve(__dirname, 'video.html'));
  await page.evaluate(() => document.fonts.ready);
  fs.mkdirSync(out, { recursive: true });
  const times = mode === 'all' ? Array.from({ length: Math.round(+rest[0] * 30) }, (_, i) => i / 30) : rest.map(Number);
  for (let i = 0; i < times.length; i++) {
    await page.evaluate(t => window.render(t), times[i]);
    const name = mode === 'all' ? String(i).padStart(4, '0') + '.jpg' : 't' + times[i].toFixed(2) + '.jpg';
    await page.screenshot({ path: path.join(out, name), type: 'jpeg', quality: 93 });
  }
  await browser.close();
})();
