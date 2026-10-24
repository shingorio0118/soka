// End-to-end check. Run with the dev server up:  python3 -m http.server 8765 --bind 127.0.0.1
//   NODE_PATH=/path/to/node_modules node tools/e2e/e2e.js [outdir]   (needs puppeteer-core + /usr/bin/google-chrome)
const puppeteer = require('puppeteer-core');
const OUT = (process.argv[2] || '/workspace/soka-shots2') + '/';
const URL = 'http://127.0.0.1:8765/index.html';
const fs = require('fs'); fs.mkdirSync(OUT, { recursive: true });
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
  const results = {}; const checks = [];
  const check = (name, ok, info) => { checks.push({ name, ok: !!ok, info }); };

  // in-page helpers: BFS over walkable tiles, finders
  const helpers = () => {
    const S = window.__soka, m = S.map, W = m.w, H = m.h;
    window.__h = {
      bfs(sx, sy, allow) {
        const dist = new Int32Array(W * H).fill(-1), prev = new Int32Array(W * H).fill(-1), q = [sy * W + sx]; dist[q[0]] = 0;
        for (let i = 0; i < q.length; i++) {
          const c = q[i], x = c % W, y = (c / W) | 0;
          for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
            const a = x + dx, b = y + dy; if (a < 0 || b < 0 || a >= W || b >= H || S.isSolid(a, b) || dist[b * W + a] >= 0) continue;
            if (allow && !allow(a, b)) continue;
            dist[b * W + a] = dist[c] + 1; prev[b * W + a] = c; q.push(b * W + a);
          }
        }
        return { dist, prev, order: q };
      },
      path(prev, tx, ty) { const out = []; let c = ty * W + tx; while (c >= 0) { out.push([c % W, (c / W) | 0]); c = prev[c]; } return out.reverse(); }
    };
  };

  // ---------- desktop
  let page = await browser.newPage(); hook(page, 'desktop');
  await page.setViewport({ width: 1280, height: 720 });
  await page.goto(URL + '?debug', { waitUntil: 'networkidle0' });
  await sleep(500); await page.evaluate(helpers);
  results.start = await st(page);
  results.mapSize = { w: results.start.w, h: results.start.h };
  await page.screenshot({ path: OUT + 'spawn-desktop.png' });

  // reachability, independent re-computation in the browser
  results.reach = await page.evaluate(() => {
    const S = window.__soka, m = S.map, sp = m.spawn; let walk = 0;
    for (let y = 0; y < m.h; y++) for (let x = 0; x < m.w; x++) if (!S.isSolid(x, y)) walk++;
    const r = window.__h.bfs(sp.x, sp.y); return { walkable: walk, reachable: r.order.length, pct: +(100 * r.order.length / walk).toFixed(2) };
  });
  check('reachable >= 98%', results.reach.pct >= 98, results.reach.pct);

  // walk right with arrow keys
  const s0 = await st(page);
  await page.keyboard.down('ArrowRight'); await sleep(700); await page.keyboard.up('ArrowRight'); await sleep(350);
  results.afterRight = await st(page);
  check('keyboard walk moves right', results.afterRight.tx > s0.tx, [s0.tx, results.afterRight.tx]);
  await page.keyboard.down('KeyW'); await sleep(200); await page.keyboard.up('KeyW');

  // collisions
  const wall = await page.evaluate(() => { const S = window.__soka, m = S.map; for (let y = 5; y < m.h - 5; y++) for (let x = 5; x < m.w - 5; x++) { if (!S.isSolid(x, y) && S.isSolid(x + 1, y) && /roof|^h\d|^m\d/.test(S.baseName(x + 1, y) || '')) return [x, y]; } });
  await page.evaluate((w) => window.__soka.teleport(w[0], w[1], 'right'), wall);
  await page.keyboard.down('ArrowRight'); await sleep(600); await page.keyboard.up('ArrowRight');
  let a = await st(page); check('building blocks movement', a.tx === wall[0] && a.ty === wall[1], { wall, after: [a.tx, a.ty] });
  const kinds = await page.evaluate(() => {
    const S = window.__soka, m = S.map, out = {};
    for (const [label, pat] of [['rail', /^rail_/], ['water', /^water_/], ['deck', /^deck/], ['lamp-or-flag', /^(lamp_shop|flag_|bikes)/]]) {
      for (let y = 5; y < m.h - 5 && !out[label]; y++) for (let x = 5; x < m.w - 5; x++) {
        const n = S.baseName(x, y), d = S.decoName(x, y);
        if (((n && pat.test(n)) || (d && pat.test(d))) && !S.isSolid(x - 1, y) && S.isSolid(x, y)) { out[label] = [x - 1, y]; break; }
      }
    }
    return out;
  });
  results.kinds = {};
  for (const [k, pos] of Object.entries(kinds)) {
    await page.evaluate((p) => window.__soka.teleport(p[0], p[1], 'right'), pos);
    await page.keyboard.down('ArrowRight'); await sleep(500); await page.keyboard.up('ArrowRight');
    const q = await st(page); results.kinds[k] = { from: pos, after: [q.tx, q.ty], blocked: q.tx === pos[0] && q.ty === pos[1] };
    check('collision: ' + k, results.kinds[k].blocked, results.kinds[k]);
  }
  // map edge: cannot leave the map
  const top = await page.evaluate(() => { const S = window.__soka, m = S.map; for (let y = 0; y < m.h; y++) for (let x = 0; x < m.w; x++) if (!S.isSolid(x, y)) return [x, y]; });
  await page.evaluate((p) => window.__soka.teleport(p[0], p[1], 'up'), top);
  await page.keyboard.down('ArrowUp'); await sleep(500); await page.keyboard.up('ArrowUp');
  a = await st(page); check('cannot walk off the top edge', a.ty >= 0 && a.tx === top[0], { top, after: [a.tx, a.ty] });

  // camera follow
  await page.evaluate(() => window.__soka.teleport(100, 100, 'down')); await sleep(200); const c1 = await st(page);
  await page.evaluate(() => window.__soka.teleport(140, 160, 'down')); await sleep(200); const c2 = await st(page);
  check('camera follows', c1.camX !== c2.camX && c1.camY !== c2.camY, [[c1.camX, c1.camY], [c2.camX, c2.camY]]);

  // station passage: spawn (west) -> walk right through to the east side
  const sp = await page.evaluate(() => window.__soka.map.spawn);
  await page.evaluate((s) => window.__soka.teleport(s.x, s.y, 'right'), sp);
  await page.keyboard.down('ArrowRight'); await sleep(3200); await page.keyboard.up('ArrowRight'); await sleep(300);
  results.passageWalk = { from: [sp.x, sp.y], after: await st(page) };
  check('east-west station passage', results.passageWalk.after.tx > sp.x + 9, results.passageWalk);
  await page.screenshot({ path: OUT + 'passage-desktop.png' });

  // spawn label
  await page.evaluate((s) => window.__soka.teleport(s.x, s.y, 'right'), sp);
  results.spawnLabel = (await st(page)).label;
  check('place label at spawn', !!results.spawnLabel, results.spawnLabel);

  // rail crossing (road/path under the viaduct): walk straight through with the keyboard
  const cross = await page.evaluate(() => {
    const S = window.__soka, m = S.map, runs = [];
    for (let y = 2; y < m.h - 2; y++) {
      let x = 0;
      while (x < m.w) {
        while (x < m.w && S.isSolid(x, y)) x++;
        const s = x; let under = 0, lab = null;
        while (x < m.w && !S.isSolid(x, y)) { const o = S.overName(x, y); if (o && /^under_/.test(o)) { under++; lab = lab || S.labelAt(x, y); } x++; }
        if (under >= 3 && x - s >= 12) runs.push({ y, s, e: x - 1, under, label: lab });
      }
    }
    return runs;
  });
  results.crossingRuns = cross.length;
  const pick = cross.find(r => r.label === '草加神社通り') || cross[0];
  results.crossingTested = pick;
  if (pick) {
    // start 4 tiles before the first under tile and walk right until 4 tiles after the last
    const ux = await page.evaluate((p) => { const S = window.__soka; let f = -1, l = -1; for (let x = p.s; x <= p.e; x++) { const o = S.overName(x, p.y); if (o && /^under_/.test(o)) { if (f < 0) f = x; l = x; } } return [f, l]; }, pick);
    const sx = Math.max(pick.s, ux[0] - 5), ex = Math.min(pick.e, ux[1] + 5);
    await page.evaluate((x, y) => window.__soka.teleport(x, y, 'right'), sx, pick.y);
    await page.keyboard.down('ArrowRight');
    let shot = false, t0 = Date.now(), seenUnder = false;
    while (Date.now() - t0 < (ex - sx) * 175 + 600) {
      const q = await st(page);
      if (!shot && q.tx >= ux[0] + 1) { await page.screenshot({ path: OUT + 'rail-crossing.png' }); shot = true; }
      if (q.tx >= ex) break; await sleep(40);
    }
    await page.keyboard.up('ArrowRight'); await sleep(250);
    a = await st(page); results.crossingWalk = { from: [sx, pick.y], to: [a.tx, a.ty], underX: ux, target: ex };
    check('walk through rail underpass', a.tx >= ux[1] + 1, results.crossingWalk);
  }

  // shopping street: walk along the largest brick-paved component, collect labels
  const shop = await page.evaluate(() => {
    const S = window.__soka, m = S.map, W = m.w, H = m.h, seen = new Uint8Array(W * H); let best = [];
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      if (seen[y * W + x]) continue; const n = S.baseName(x, y); if (!n || !/^shopst_/.test(n) || S.isSolid(x, y)) continue;
      const q = [[x, y]]; seen[y * W + x] = 1;
      for (let i = 0; i < q.length; i++) for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const a = q[i][0] + dx, b = q[i][1] + dy; if (a < 0 || b < 0 || a >= W || b >= H || seen[b * W + a]) continue;
        const nn = S.baseName(a, b); if (nn && /^shopst_/.test(nn) && !S.isSolid(a, b)) { seen[b * W + a] = 1; q.push([a, b]); }
      }
      if (q.length > best.length) best = q;
    }
    best.sort((p, q) => p[1] - q[1] || p[0] - q[0]);
    const a = best[0], r = window.__h.bfs(a[0], a[1], (x, y) => { const nn = S.baseName(x, y); return nn && /^shopst_/.test(nn); });
    let far = a, fd = 0; for (const c of r.order) if (r.dist[c] > fd) { fd = r.dist[c]; far = [c % W, (c / W) | 0]; }
    return { size: best.length, from: a, to: far, path: window.__h.path(r.prev, far[0], far[1]) };
  });
  results.shopStreet = { tiles: shop.size, from: shop.from, to: shop.to, pathLen: shop.path.length };
  await page.evaluate((p) => window.__soka.teleport(p[0], p[1], 'down'), shop.from);
  const labelsSeen = new Set(); let k = 0;
  const dirsOf = (a, b) => b[0] > a[0] ? 'ArrowRight' : b[0] < a[0] ? 'ArrowLeft' : b[1] > a[1] ? 'ArrowDown' : 'ArrowUp';
  let shotShop = false;
  for (let i = 1; i < shop.path.length;) {
    const key = dirsOf(shop.path[i - 1], shop.path[i]); let j = i; while (j + 1 < shop.path.length && dirsOf(shop.path[j], shop.path[j + 1]) === key) j++;
    await page.keyboard.down(key);
    const want = shop.path[j]; const t0 = Date.now();
    while (Date.now() - t0 < (j - i + 1) * 175 + 400) { const q = await st(page); if (q.label) labelsSeen.add(q.label); if (!shotShop && i + 1 > shop.path.length / 2) { await page.screenshot({ path: OUT + 'shopping-street.png' }); shotShop = true; } if (q.tx === want[0] && q.ty === want[1]) break; await sleep(30); }
    await page.keyboard.up(key); await sleep(40); i = j + 1;
  }
  await sleep(300); a = await st(page);
  results.shopWalk = { end: [a.tx, a.ty], target: shop.to, labels: [...labelsSeen] };
  check('walked the shopping street', Math.abs(a.tx - shop.to[0]) + Math.abs(a.ty - shop.to[1]) <= 2, results.shopWalk);
  check('shopping street label shown', labelsSeen.has('商店街') || [...labelsSeen].length > 0, [...labelsSeen]);
  if (!shotShop) await page.screenshot({ path: OUT + 'shopping-street.png' });

  // north / south ends: the farthest reachable tiles
  const ends = await page.evaluate(() => {
    const S = window.__soka, m = S.map, sp = m.spawn, r = window.__h.bfs(sp.x, sp.y); let n = null, s = null;
    for (const c of r.order) { const x = c % m.w, y = (c / m.w) | 0; if (!n || y < n[1] || (y === n[1] && Math.abs(x - sp.x) < Math.abs(n[0] - sp.x))) n = [x, y]; if (!s || y > s[1] || (y === s[1] && Math.abs(x - sp.x) < Math.abs(s[0] - sp.x))) s = [x, y]; }
    return { n, s };
  });
  results.ends = ends;
  await page.evaluate((p) => window.__soka.teleport(p[0], p[1], 'up'), ends.n); await sleep(300);
  await page.screenshot({ path: OUT + 'north-end.png' });
  await page.evaluate((p) => window.__soka.teleport(p[0], p[1], 'down'), ends.s); await sleep(300);
  await page.screenshot({ path: OUT + 'south-end.png' });

  // mini-map toggle
  await page.evaluate((s) => window.__soka.teleport(s.x, s.y, 'right'), sp); await sleep(200);
  await page.click('#mapbtn'); await sleep(300);
  let ms = await st(page); const btnText = await page.$eval('#mapbtn', e => e.textContent);
  await page.keyboard.down('ArrowRight'); await sleep(600); await page.keyboard.up('ArrowRight');
  const ms2 = await st(page);
  await page.screenshot({ path: OUT + 'minimap-desktop.png' });
  await page.click('#mapbtn'); await sleep(200); ms = await st(page);
  check('mini-map opens, freezes movement, closes', btnText === '閉じる' && ms2.tx === sp.x && !ms.mini, { btnText, frozenAt: [ms2.tx, ms2.ty], closed: !ms.mini });

  // frame rate while walking (desktop, software GL headless: a pessimistic number)
  await page.evaluate((s) => window.__soka.teleport(s.x, s.y, 'right'), sp);
  const walkP = page.keyboard.down('ArrowRight');
  const fps = async () => page.evaluate(() => new Promise(res => { const ts = []; const f = t => { ts.push(t); if (ts.length < 180) requestAnimationFrame(f); else { const d = ts.slice(1).map((v, i) => v - ts[i]).sort((a, b) => a - b); res({ frames: ts.length, avgMs: +(d.reduce((a, b) => a + b, 0) / d.length).toFixed(2), p95Ms: +d[Math.floor(d.length * .95)].toFixed(2), maxMs: +d[d.length - 1].toFixed(2), fps: +(1000 / (d.reduce((a, b) => a + b, 0) / d.length)).toFixed(1) }); } }; requestAnimationFrame(f); }));
  await walkP; results.fpsDesktop = await fps(); await page.keyboard.up('ArrowRight');
  check('desktop frame rate >= 55 fps', results.fpsDesktop.fps >= 55, results.fpsDesktop);
  results.desktopScroll = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, bw: document.body.scrollWidth }));
  check('desktop: no horizontal scroll', results.desktopScroll.sw <= results.desktopScroll.cw && results.desktopScroll.bw <= results.desktopScroll.cw, results.desktopScroll);
  await page.close();

  // ---------- phone portrait
  page = await browser.newPage(); hook(page, 'phone');
  await page.setUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1');
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
  await page.goto(URL + '?debug', { waitUntil: 'networkidle0' });
  await sleep(500); await page.evaluate(helpers);
  results.phoneStart = await st(page);
  results.dpadVisible = await page.evaluate(() => getComputedStyle(document.getElementById('dpad')).display);
  check('d-pad visible on phone', results.dpadVisible !== 'none', results.dpadVisible);
  results.phoneScroll = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, bw: document.body.scrollWidth, ww: window.innerWidth }));
  check('phone: no horizontal scroll', results.phoneScroll.sw <= results.phoneScroll.cw && results.phoneScroll.bw <= results.phoneScroll.ww, results.phoneScroll);
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
  check('d-pad moves in all four directions', t1.ty > t0.ty && t2.tx < t1.tx && t3.ty < t2.ty && t4.tx > t3.tx, results.dpad);
  // phone: shopping street, D-pad held up the street
  await page.evaluate((p) => window.__soka.teleport(p[0], p[1], 'down'), shop.from); await sleep(300);
  const bd = await page.$('#dpad button[data-dir=down]'); const bdb = await bd.boundingBox();
  await page.touchscreen.touchStart(bdb.x + 30, bdb.y + 30); await sleep(1000);
  await page.screenshot({ path: OUT + 'phone-shopping-street.png' });
  results.fpsPhone = await page.evaluate(() => new Promise(res => { const ts = []; const f = t => { ts.push(t); if (ts.length < 120) requestAnimationFrame(f); else { const d = ts.slice(1).map((v, i) => v - ts[i]).sort((a, b) => a - b); const avg = d.reduce((a, b) => a + b, 0) / d.length; res({ frames: ts.length, avgMs: +avg.toFixed(2), p95Ms: +d[Math.floor(d.length * .95)].toFixed(2), fps: +(1000 / avg).toFixed(1) }); } }; requestAnimationFrame(f); }));
  await page.touchscreen.touchEnd(); await sleep(200);
  results.phoneLabel = (await st(page)).label;
  // phone: mini-map via touch tap
  const mb = await page.$('#mapbtn'); const mbb = await mb.boundingBox();
  await page.touchscreen.tap(mbb.x + mbb.width / 2, mbb.y + mbb.height / 2); await sleep(400);
  const mst = await st(page); await page.screenshot({ path: OUT + 'phone-minimap.png' });
  check('phone: mini-map opens by tap', mst.mini, mst);
  await page.touchscreen.tap(mbb.x + mbb.width / 2, mbb.y + mbb.height / 2); await sleep(300);
  // phone: station front + label
  await page.evaluate((s) => window.__soka.teleport(s.x + 2, s.y, 'right'), sp); await sleep(300);
  await page.screenshot({ path: OUT + 'phone-spawn.png' });
  await page.close();

  console.log(JSON.stringify(results, null, 1));
  console.log('CHECKS:'); for (const c of checks) console.log((c.ok ? '  PASS ' : '  FAIL ') + c.name);
  console.log('ERRORS:', errors.length ? errors : 'none');
  await browser.close();
  process.exit(checks.every(c => c.ok) && !errors.length ? 0 : 2);
})().catch(e => { console.error('FAIL', e); process.exit(1); });
