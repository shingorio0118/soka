// End-to-end check. Run with the dev server up:  python3 -m http.server 8765 --bind 127.0.0.1
//   NODE_PATH=/path/to/node_modules node tools/e2e/e2e.js    (needs puppeteer-core + /usr/bin/google-chrome)
const puppeteer = require('puppeteer-core');
const OUT = '/workspace/soka-shots/';
const URL = 'http://127.0.0.1:8765/index.html';
(async () => {
  const browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
  const errors = [];
  const hook = (page, tag) => {
    page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') errors.push(tag + ' console.' + m.type() + ': ' + m.text()); });
    page.on('pageerror', e => errors.push(tag + ' pageerror: ' + e.message));
    page.on('requestfailed', r => errors.push(tag + ' requestfailed: ' + r.url()));
    page.on('response', r => { if (r.status() >= 400) errors.push(tag + ' http ' + r.status() + ' ' + r.url()); });
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const st = p => p.evaluate(() => window.__soka.state());
  const results = {};

  // ---------- desktop
  let page = await browser.newPage(); hook(page, 'desktop');
  await page.setViewport({ width: 1280, height: 720 });
  await page.goto(URL + '?debug', { waitUntil: 'networkidle0' });
  await sleep(500);
  results.start = await st(page);
  await page.screenshot({ path: OUT + 'spawn-desktop.png' });
  // walk right 6 steps
  await page.keyboard.down('ArrowRight'); await sleep(1100); await page.keyboard.up('ArrowRight'); await sleep(400);
  results.afterRight = await st(page);
  await page.keyboard.down('KeyW'); await sleep(200); await page.keyboard.up('KeyW');
  await page.screenshot({ path: OUT + 'midwalk-desktop.png' });
  // blocked by building: teleport next to a solid tile and press into it
  const wall = await page.evaluate(() => { const m = window.__soka.map; for (let y = 5; y < m.h - 5; y++) for (let x = 5; x < m.w - 5; x++) { if (!window.__soka.isSolid(x, y) && window.__soka.isSolid(x + 1, y) && m.layers.base[y][x + 1] >= 0) return [x, y]; } });
  await page.evaluate((w) => window.__soka.teleport(w[0], w[1], 'right'), wall);
  await page.keyboard.down('ArrowRight'); await sleep(700); await page.keyboard.up('ArrowRight');
  results.blockedAt = { wall, after: await st(page) };
  // blocked by rail (find rail tile name index) and water
  const kinds = await page.evaluate(() => {
    const m = window.__soka.map, names = m.names, out = {};
    for (const [label, pat] of [['rail', /^rail_/], ['water', /^water_/], ['building-roof', /^(h|m)\d_roof/], ['deck', /^deck/]]) {
      for (let y = 5; y < m.h - 5 && !out[label]; y++) for (let x = 5; x < m.w - 5; x++) {
        const id = m.layers.base[y][x];
        if (id >= 0 && pat.test(names[id]) && !window.__soka.isSolid(x - 1, y) && window.__soka.isSolid(x, y)) { out[label] = [x - 1, y]; break; }
      }
    }
    return out;
  });
  results.kinds = {};
  for (const [k, pos] of Object.entries(kinds)) {
    await page.evaluate((p) => window.__soka.teleport(p[0], p[1], 'right'), pos);
    await page.keyboard.down('ArrowRight'); await sleep(600); await page.keyboard.up('ArrowRight');
    const a = await st(page); results.kinds[k] = { from: pos, after: [a.tx, a.ty], blocked: a.tx === pos[0] && a.ty === pos[1] };
  }
  // camera follow: far apart positions must give different camera offsets
  await page.evaluate(() => window.__soka.teleport(20, 20, 'down')); await sleep(200); const c1 = await st(page);
  await page.evaluate(() => window.__soka.teleport(50, 50, 'down')); await sleep(200); const c2 = await st(page);
  results.camera = { c1: [c1.camX, c1.camY], c2: [c2.camX, c2.camY], follows: c1.camX !== c2.camX && c1.camY !== c2.camY };
  // passage under station: walk east through passage
  const sp = await page.evaluate(() => window.__soka.map.spawn);
  await page.evaluate((s) => window.__soka.teleport(s.x, s.y, 'right'), sp);
  await page.keyboard.down('ArrowRight'); await sleep(3200); await page.keyboard.up('ArrowRight'); await sleep(300);
  results.passageWalk = { from: [sp.x, sp.y], after: await st(page) };
  await page.screenshot({ path: OUT + 'passage-desktop.png' });
  const hs = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, bw: document.body.scrollWidth }));
  results.desktopScroll = hs;
  await page.close();

  // ---------- phone portrait
  page = await browser.newPage(); hook(page, 'phone');
  await page.setUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1');
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
  await page.goto(URL + '?debug', { waitUntil: 'networkidle0' });
  await sleep(500);
  results.phoneStart = await st(page);
  results.dpadVisible = await page.evaluate(() => getComputedStyle(document.getElementById('dpad')).display);
  results.phoneScroll = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, bw: document.body.scrollWidth, ww: window.innerWidth }));
  await page.screenshot({ path: OUT + 'phone-portrait.png' });
  const btn = async (dir, ms) => {
    const b = await page.$(`#dpad button[data-dir=${dir}]`); const box = await b.boundingBox();
    const x = box.x + box.width / 2, y = box.y + box.height / 2;
    await page.touchscreen.touchStart(x, y); await sleep(ms); await page.touchscreen.touchEnd(); await sleep(250);
  };
  const t0 = await st(page);
  await btn('down', 600); const t1 = await st(page);
  await btn('left', 600); const t2 = await st(page);
  await btn('up', 600); const t3 = await st(page);
  await btn('right', 600); const t4 = await st(page);
  results.dpad = { start: [t0.tx, t0.ty], down: [t1.tx, t1.ty, t1.dir], left: [t2.tx, t2.ty, t2.dir], up: [t3.tx, t3.ty, t3.dir], right: [t4.tx, t4.ty, t4.dir] };
  // hold right for a while
  const b = await page.$('#dpad button[data-dir=right]'); const bb = await b.boundingBox();
  await page.touchscreen.touchStart(bb.x + 30, bb.y + 30); await sleep(1000);
  await page.screenshot({ path: OUT + 'phone-midwalk.png' });
  await page.touchscreen.touchEnd(); await sleep(300);
  results.phoneHold = await st(page);
  // phone, east plaza with the pond, mid-walk
  await page.evaluate(() => window.__soka.teleport(55, 33, 'right')); await sleep(200);
  const b2 = await page.$('#dpad button[data-dir=up]'); const bb2 = await b2.boundingBox();
  await page.touchscreen.touchStart(bb2.x + 30, bb2.y + 30); await sleep(420);
  await page.screenshot({ path: OUT + 'phone-midwalk-east.png' });
  await page.touchscreen.touchEnd(); await sleep(200);
  await page.close();

  console.log(JSON.stringify(results, null, 1));
  console.log('ERRORS:', errors.length ? errors : 'none');
  await browser.close();
})().catch(e => { console.error('FAIL', e); process.exit(1); });
