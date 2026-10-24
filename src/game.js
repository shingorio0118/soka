/* 草加駅前 - tiny canvas engine: grid movement, camera follow, collision, D-pad. No network at runtime except loading local assets. */
(function () {
  'use strict';
  var TILE = 16;
  var STEP_SEC = 0.17;          // seconds per tile
  var params = new URLSearchParams(location.search);
  var DEBUG = params.has('debug');
  if (params.has('dpad')) document.body.classList.add('dpad-on');

  var canvas = document.getElementById('game');
  var ctx = canvas.getContext('2d');
  var dpadEl = document.getElementById('dpad');
  var placeEl = document.getElementById('place'), place1 = document.getElementById('place1'), place2 = document.getElementById('place2');
  var hintEl = document.getElementById('hint');

  function loadImage(src) {
    return new Promise(function (res, rej) {
      var im = new Image(); im.onload = function () { res(im); }; im.onerror = function () { rej(new Error('image ' + src)); }; im.src = src;
    });
  }
  function fixedCanvas(w, h) { var c = document.createElement('canvas'); c.width = w; c.height = h; var x = c.getContext('2d'); x.imageSmoothingEnabled = false; return c; }

  Promise.all([
    fetch('assets/map.json').then(function (r) { return r.json(); }),
    loadImage('assets/tiles.png'), loadImage('assets/player.png'), loadImage('assets/sign_soka.png'), loadImage('assets/signs.png')
  ]).then(function (a) { start(a[0], a[1], a[2], a[3], a[4]); }).catch(function (e) {
    console.error(e); document.getElementById('title').textContent = '読み込みに失敗しました';
  });

  function unrle(a, Type, n) {
    var out = new Type(n), k = 0;
    for (var i = 0; i < a.length; i += 2) { var v = a[i], c = a[i + 1]; out.fill(v, k, k + c); k += c; }
    return out;
  }

  function start(map, tiles, playerImg, signImg, signsImg) {
    var W = map.w, H = map.h, cols = Math.floor(tiles.width / TILE), N = W * H;
    // map data is run-length coded; decode into flat typed arrays once (270x320 = 86k cells)
    var baseL = unrle(map.layers.base, Int16Array, N), decoL = unrle(map.layers.deco, Int16Array, N), overL = unrle(map.layers.over, Int16Array, N);
    var solid = unrle(map.solid, Uint8Array, N), labelGrid = unrle(map.labelGrid, Uint8Array, N);
    var objects = map.objects;

    // only the tiles on screen are drawn each frame (no giant pre-rendered canvas: phones run out of memory on those)
    function drawTiles(layer, x0, y0, x1, y1) {
      for (var ty = y0; ty < y1; ty++) {
        var row = ty * W;
        for (var tx = x0; tx < x1; tx++) {
          var id = layer[row + tx]; if (id < 0) continue;
          ctx.drawImage(tiles, (id % cols) * TILE, Math.floor(id / cols) * TILE, TILE, TILE, tx * TILE, ty * TILE, TILE, TILE);
        }
      }
    }
    function drawObjects(x0, y0, x1, y1, rowFrom, rowTo) {      // objects whose bottom row is in [rowFrom, rowTo)
      for (var i = 0; i < objects.length; i++) {
        var o = objects[i], bottom = o.y + o.h - 1;
        if (bottom < rowFrom || bottom >= rowTo || o.x + o.w < x0 || o.x > x1 || o.y + o.h < y0 || o.y > y1) continue;
        if (o.sprite === 'sign_soka') ctx.drawImage(signImg, o.x * TILE, o.y * TILE);
        else ctx.drawImage(signsImg, o.sx, o.sy, o.w * TILE, o.h * TILE, o.x * TILE, o.y * TILE, o.w * TILE, o.h * TILE);
      }
    }

    // ---- state
    if (window.matchMedia && window.matchMedia('(pointer: coarse)').matches && hintEl) hintEl.textContent = '十字ボタンで歩けます';
    var DIRS = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] };
    var ROW = { down: 0, left: 1, right: 2, up: 3 };
    var P = { tx: map.spawn.x, ty: map.spawn.y, dir: map.spawn.dir || 'down', moving: false, fx: 0, fy: 0, t: 0, parity: 0, bump: 0, turnLock: 0 };
    var held = [];            // most recent last
    var now = 0, moved = false;

    function isSolid(x, y) { return x < 0 || y < 0 || x >= W || y >= H || solid[y * W + x] === 1; }
    function press(d) { var i = held.indexOf(d); if (i >= 0) held.splice(i, 1); held.push(d); }
    function release(d) { var i = held.indexOf(d); if (i >= 0) held.splice(i, 1); }
    function wanted() { return held.length ? held[held.length - 1] : null; }

    // ---- keyboard
    var KEYMAP = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right', w: 'up', a: 'left', s: 'down', d: 'right', W: 'up', A: 'left', S: 'down', D: 'right' };
    window.addEventListener('keydown', function (e) {
      var d = KEYMAP[e.key]; if (!d || miniOpen) return; e.preventDefault(); if (!e.repeat) press(d);
    });
    window.addEventListener('keyup', function (e) { var d = KEYMAP[e.key]; if (d) { e.preventDefault(); release(d); } });
    window.addEventListener('blur', function () { held.length = 0; });

    // ---- D-pad (pointer events cover touch, pen and mouse)
    Array.prototype.forEach.call(dpadEl.querySelectorAll('button'), function (b) {
      var d = b.dataset.dir;
      function down(e) { e.preventDefault(); try { b.setPointerCapture(e.pointerId); } catch (_) {} press(d); b.classList.add('on'); }
      function up(e) { e.preventDefault(); release(d); b.classList.remove('on'); }
      b.addEventListener('pointerdown', down); b.addEventListener('pointerup', up);
      b.addEventListener('pointercancel', up); b.addEventListener('lostpointercapture', up);
      b.addEventListener('contextmenu', function (e) { e.preventDefault(); });
    });
    ['gesturestart', 'gesturechange', 'dblclick'].forEach(function (n) { document.addEventListener(n, function (e) { e.preventDefault(); }, { passive: false }); });
    document.addEventListener('touchmove', function (e) { if (e.touches.length > 1) e.preventDefault(); }, { passive: false });

    // ---- view
    var view = { scale: 3, ox: 0, oy: 0, cw: 0, ch: 0, dpr: 1 };
    function resize() {
      var dpr = window.devicePixelRatio || 1;
      var w = window.innerWidth, h = window.innerHeight;
      canvas.width = Math.floor(w * dpr); canvas.height = Math.floor(h * dpr);
      var m = Math.min(w, h);
      var css = m < 900 ? 3 : 4;                       // integer CSS scale: 3x on phones, 3-4x on desktop
      view.scale = Math.max(1, Math.round(css * dpr)); // integer device-pixel scale
      view.cw = canvas.width; view.ch = canvas.height; view.dpr = dpr;
      var coarse = dpadEl.offsetParent !== null || getComputedStyle(dpadEl).display !== 'none';
      view.bottomUI = coarse ? Math.round((dpadEl.getBoundingClientRect().height + 40) * dpr) : Math.round(22 * dpr);
    }
    resize();
    window.addEventListener('resize', function () { resize(); tagCache = {}; onArrive(); });
    window.addEventListener('orientationchange', function () { resize(); tagCache = {}; onArrive(); });

    // ---- place labels
    var lastLabel = null;
    function labelAt(tx, ty) {
      var id = labelGrid[ty * W + tx];
      return id ? map.labelNames[id - 1] : null;
    }

    // ---- named POIs (shops, eateries, clinics, public facilities ...; OSM names, privacy-filtered at build time)
    var CATS = map.poiCats || [];
    var POIS = (map.pois || []).map(function (r, i) {
      return { id: i, x: r[0], y: r[1], name: r[2], cat: r[3], pri: r[4], x0: r[5], y0: r[6], x1: r[7], y1: r[8], area: r[7] > r[5] || r[8] > r[6] };
    });
    var FONT = '"Hiragino Kaku Gothic ProN","Noto Sans CJK JP","Noto Sans JP","Yu Gothic",Meiryo,sans-serif';
    var NEAR_R = 2.2;           // tiles: "next to" a POI (distance to its building outline)
    var TAG_R = 7;              // tiles: name tags are shown for POIs within this distance
    var tagCache = {}, tags = [], nearPoi = null, arrX = 0, arrY = 0;
    function poiDist(p, tx, ty) {
      var dx = Math.max(p.x0 - tx, 0, tx - p.x1), dy = Math.max(p.y0 - ty, 0, ty - p.y1);
      return Math.sqrt(dx * dx + dy * dy);
    }
    function tagFont() { return Math.round(13 * view.dpr); }
    function measureTag(name, F) {
      var k = name + '|' + F; if (tagCache[k]) return tagCache[k];
      ctx.font = 'bold ' + F + 'px ' + FONT; return (tagCache[k] = ctx.measureText(name).width);
    }
    function overlaps(a, b, m) { return a.x < b.x + b.w + m && a.x + a.w + m > b.x && a.y < b.y + b.h + m && a.y + a.h + m > b.y; }
    // Choose which tags to show around tile (tx,ty): nearest first, collision-avoiding, capped.  Positions are kept relative to
    // the layout tile in device pixels, so they stay valid while the camera scrolls; recomputed on every tile arrival / resize.
    function layoutTags(tx, ty) {
      var s = view.scale, F = tagFont(), phone = view.cw / view.dpr < 700, cap = phone ? 7 : 12;
      var halfW = view.cw / (2 * TILE * s) + 1, halfH = (view.ch - view.bottomUI) / (2 * TILE * s) + 1;
      var cand = [];
      for (var i = 0; i < POIS.length; i++) {
        var p = POIS[i];
        if (Math.abs(p.x - tx) > halfW + 40 && !p.area) continue;
        var d = poiDist(p, tx, ty); if (d > TAG_R) continue;
        var ddx = Math.max(p.x0 - tx, 0, tx - p.x1), ddy = Math.max(p.y0 - ty, 0, ty - p.y1);
        if (ddx > halfW || ddy > halfH) continue;
        cand.push({ p: p, d: d, score: d - p.pri * 0.7 - (p === nearPoi ? 50 : 0) });
      }
      cand.sort(function (a, b) { return a.score - b.score || a.p.id - b.p.id; });
      var padX = Math.round(F * 0.45), stub = Math.round(F * 0.55), th = Math.round(F * 1.45), gap = Math.max(2, Math.round(view.dpr * 2));
      var placed = [], out = [];
      var pl = { x: -TILE * s * 0.7, y: -TILE * s * 1.1, w: TILE * s * 1.4, h: TILE * s * 1.6 };      // keep the player sprite free
      var tries = [0, -1, 1, -2, 2, -3, 3], shifts = [0, -0.55, 0.55];
      for (var c = 0; c < cand.length && out.length < cap; c++) {
        var q = cand[c].p, tw = Math.ceil(measureTag(q.name, F)) + padX * 2 + stub;
        var axw = ((q.area ? (q.x0 + q.x1 + 1) / 2 : q.x + 0.5) - (tx + 0.5)) * TILE * s;       // anchor, relative to the layout tile centre
        var ayw = ((q.area ? (q.y0 + q.y1 + 1) / 2 : q.y + 0.5) - (ty + 0.5)) * TILE * s;
        var baseY = q.area ? ayw - th / 2 : ayw - th - TILE * s * 0.35, got = null;
        for (var k = 0; k < tries.length && !got; k++) {
          for (var sx = 0; sx < shifts.length && !got; sx++) {
            var box = { x: axw - tw / 2 + shifts[sx] * tw, y: baseY + tries[k] * (th + gap), w: tw, h: th };
            if (overlaps(box, pl, 0) || box.x < -view.cw / 2 + 4 || box.x + box.w > view.cw / 2 - 4) continue;
            var bad = false;
            for (var j = 0; j < placed.length && !bad; j++) bad = overlaps(box, placed[j], gap);
            if (!bad) got = box;
          }
        }
        if (!got) continue;
        placed.push(got);
        out.push({ p: q, d: cand[c].d, ax: axw, ay: ayw, x: got.x, y: got.y, w: tw, h: th, F: F, padX: padX, stub: stub });
      }
      tags = out; arrX = tx; arrY = ty;
    }
    function nearestPoi(tx, ty) {
      var best = null, bd = NEAR_R + 1e-6;
      for (var i = 0; i < POIS.length; i++) {
        var p = POIS[i], d = poiDist(p, tx, ty) - p.pri * 0.05;
        if (d < bd) { bd = d; best = p; }
      }
      return best;
    }
    function drawTags(cx, cy) {     // cx, cy: camera offset (device px); tags are positioned relative to the tile they were laid out on
      if (!tags.length || miniOpen) return;
      var s = view.scale, dpr = view.dpr, bw = Math.floor(1.2 * dpr) + 1;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.textBaseline = 'middle'; ctx.lineJoin = 'round';
      var ox = cx + (arrX + 0.5) * TILE * s, oy = cy + (arrY + 0.5) * TILE * s;
      for (var i = 0; i < tags.length; i++) {
        var t = tags[i], col = CATS[t.p.cat] ? CATS[t.p.cat].color : '#555';
        var x = Math.round(ox + t.x), y = Math.round(oy + t.y), ax = Math.round(ox + t.ax), ay = Math.round(oy + t.ay);
        if (x > view.cw || x + t.w < 0 || y > view.ch || y + t.h < 0) continue;
        ctx.globalAlpha = t.d > 5 ? 0.8 : 1;
        var q = Math.max(2, Math.round(dpr * 2));                       // anchor dot: a small square on the shop / building
        ctx.fillStyle = '#ffffff'; ctx.fillRect(ax - q - 1, ay - q - 1, q * 2 + 2, q * 2 + 2); ctx.fillStyle = col; ctx.fillRect(ax - q, ay - q, q * 2, q * 2);
        ctx.fillStyle = t.p === nearPoi ? '#e8c640' : '#1d2330'; ctx.fillRect(x - bw, y - bw, t.w + bw * 2, t.h + bw * 2);   // dark frame (gold when next to it)
        ctx.fillStyle = '#fffdf2'; ctx.fillRect(x, y, t.w, t.h);                                                              // cream plate
        ctx.fillStyle = col; ctx.fillRect(x, y, t.stub, t.h);                                                                 // category colour stub
        ctx.font = 'bold ' + t.F + 'px ' + FONT;
        ctx.lineWidth = Math.max(2, Math.round(dpr * 1.5)); ctx.strokeStyle = '#fffdf2'; ctx.strokeText(t.p.name, x + t.stub + t.padX, y + t.h / 2 + 1);
        ctx.fillStyle = '#1d2330'; ctx.fillText(t.p.name, x + t.stub + t.padX, y + t.h / 2 + 1);
      }
      ctx.globalAlpha = 1;
    }

    // ---- mini-map (button "地図"): one pixel per tile, coloured from the tile art
    var mapBtn = document.getElementById('mapbtn'), miniEl = document.getElementById('mini'), miniC = document.getElementById('minicv');
    var miniBase = null, miniOpen = false;
    function buildMini() {
      var avg = {};
      try {
        var tc = fixedCanvas(tiles.width, tiles.height), tx = tc.getContext('2d'); tx.drawImage(tiles, 0, 0);
        var data = tx.getImageData(0, 0, tiles.width, tiles.height).data, nt = cols * Math.floor(tiles.height / TILE);
        for (var id = 0; id < nt; id++) {
          var r = 0, g = 0, b = 0, n = 0, ox = (id % cols) * TILE, oy = Math.floor(id / cols) * TILE;
          for (var y = 0; y < TILE; y++) for (var x = 0; x < TILE; x++) {
            var i = ((oy + y) * tiles.width + ox + x) * 4; if (data[i + 3] < 128) continue;
            r += data[i]; g += data[i + 1]; b += data[i + 2]; n++;
          }
          avg[id] = n ? [r / n | 0, g / n | 0, b / n | 0] : [200, 196, 180];
        }
      } catch (e) { avg = null; }
      miniBase = fixedCanvas(W, H);
      var mx = miniBase.getContext('2d'), im = mx.createImageData(W, H);
      for (var k = 0; k < N; k++) {
        var c = avg && avg[baseL[k]] || [200, 196, 180];
        im.data[k * 4] = c[0]; im.data[k * 4 + 1] = c[1]; im.data[k * 4 + 2] = c[2]; im.data[k * 4 + 3] = 255;
      }
      mx.putImageData(im, 0, 0);
    }
    // mini-map side panel: category chips (colour key + count); a chip lists its places; tapping a place marks it on the map
    var catsEl = document.getElementById('minicats'), listEl = document.getElementById('minilist'), selEl = document.getElementById('minisel'), panelEl = document.getElementById('minipanel');
    var miniCat = -1, miniSel = null, miniDots = [];
    function buildPanel() {
      catsEl.textContent = '';
      var counts = CATS.map(function () { return 0; }); POIS.forEach(function (p) { counts[p.cat]++; });
      CATS.forEach(function (c, i) {
        if (!counts[i]) return;
        var b = document.createElement('button'); b.type = 'button'; b.className = 'chip'; b.dataset.cat = i;
        var sw = document.createElement('i'); sw.className = 'sw'; sw.style.background = c.color;
        b.appendChild(sw); b.appendChild(document.createTextNode(c.name + ' ' + counts[i]));
        b.addEventListener('click', function () { miniCat = miniCat === i ? -1 : i; miniSel = null; refreshPanel(); drawMini(); });
        catsEl.appendChild(b);
      });
    }
    function refreshPanel() {
      Array.prototype.forEach.call(catsEl.children, function (b) { b.classList.toggle('on', +b.dataset.cat === miniCat); });
      listEl.textContent = '';
      if (miniCat < 0) { listEl.textContent = '分類を押すと、お店・施設の一覧が出ます。地図の四角い印を押しても選べます。'; listEl.className = 'hintline'; }
      else {
        listEl.className = '';
        POIS.filter(function (p) { return p.cat === miniCat; }).sort(function (a, b) { return a.name < b.name ? -1 : a.name > b.name ? 1 : a.id - b.id; }).forEach(function (p) {
          var b = document.createElement('button'); b.type = 'button'; b.className = 'item' + (miniSel === p ? ' on' : ''); b.textContent = p.name;
          b.addEventListener('click', function () { miniSel = miniSel === p ? null : p; refreshPanel(); drawMini(); });
          listEl.appendChild(b);
        });
      }
      selEl.textContent = miniSel ? miniSel.name + ' (' + (CATS[miniSel.cat] ? CATS[miniSel.cat].name : '') + ')' : '';
      selEl.hidden = !miniSel;
    }
    function drawMini() {
      if (!miniBase) { buildMini(); buildPanel(); refreshPanel(); }
      var dpr = window.devicePixelRatio || 1, wide = window.innerWidth >= window.innerHeight * 1.1;
      var aw, ah;
      if (wide) { aw = window.innerWidth - 20 - 280; ah = window.innerHeight - 110; panelEl.style.maxHeight = ah + 'px'; panelEl.style.height = ''; }
      else { var ph = Math.max(120, Math.min(250, Math.round(window.innerHeight * 0.3))); aw = window.innerWidth - 20; ah = window.innerHeight - 110 - ph - 8; panelEl.style.maxHeight = ph + 'px'; }
      var k = Math.min(aw / W, ah / H), cssW = Math.floor(W * k), cssH = Math.floor(H * k);
      miniC.style.width = cssW + 'px'; miniC.style.height = cssH + 'px';
      miniC.width = Math.floor(cssW * dpr); miniC.height = Math.floor(cssH * dpr);
      var x = miniC.getContext('2d'); x.imageSmoothingEnabled = false;
      x.drawImage(miniBase, 0, 0, miniC.width, miniC.height);
      var sx = miniC.width / W, sy = miniC.height / H, q = Math.max(2, Math.round(3 * dpr));
      function mark(tx, ty, col) { x.fillStyle = '#ffffff'; x.fillRect(tx * sx - q, ty * sy - q, q * 2 + sx, q * 2 + sy); x.fillStyle = col; x.fillRect(tx * sx - q + 2, ty * sy - q + 2, q * 2 + sx - 4, q * 2 + sy - 4); }
      // POI dots (a category filter dims the others)
      var d = Math.max(3, Math.round(2.6 * dpr)); miniDots = [];
      for (var pass = 0; pass < 2; pass++) POIS.forEach(function (p) {
        var on = miniCat < 0 || p.cat === miniCat; if ((pass === 0) === on) return;
        var cxp = (p.x + 0.5) * sx, cyp = (p.y + 0.5) * sy;
        if (!on) { x.globalAlpha = 0.35; }
        x.fillStyle = '#ffffff'; x.fillRect(Math.round(cxp - d / 2 - 1), Math.round(cyp - d / 2 - 1), d + 2, d + 2);
        x.fillStyle = CATS[p.cat] ? CATS[p.cat].color : '#555'; x.fillRect(Math.round(cxp - d / 2), Math.round(cyp - d / 2), d, d);
        x.globalAlpha = 1; if (on) miniDots.push({ p: p, x: cxp / dpr, y: cyp / dpr });
      });
      mark(map.station.x, map.station.y, '#2a5db0');
      function outlined(txt, tx, ty, size) {
        x.font = 'bold ' + Math.round(size * dpr) + 'px ' + FONT; x.textBaseline = 'middle'; x.lineJoin = 'round';
        var w = x.measureText(txt).width, px = Math.min(Math.max(tx, 3), miniC.width - w - 3);
        x.lineWidth = Math.round(3 * dpr); x.strokeStyle = '#fffdf2'; x.strokeText(txt, px, ty); x.fillStyle = '#10151f'; x.fillText(txt, px, ty);
      }
      outlined('草加駅', map.station.x * sx + q * 2 + 4, map.station.y * sy, 12);
      if (miniSel) {
        var mx = (miniSel.x + 0.5) * sx, my = (miniSel.y + 0.5) * sy, r = Math.round(7 * dpr);
        x.lineWidth = Math.max(2, Math.round(2 * dpr)); x.strokeStyle = '#d4322a'; x.strokeRect(Math.round(mx - r), Math.round(my - r), r * 2, r * 2);
        outlined(miniSel.name, mx + r + 3, my - r - 4 * dpr, 12);
      }
      var pp = playerPos(); mark(Math.round(pp[0] / TILE), Math.round(pp[1] / TILE), '#d4322a');
    }
    miniC.addEventListener('click', function (e) {      // tap a dot to select it (nearest within ~14 px)
      var r = miniC.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top, best = null, bd = 14;
      miniDots.forEach(function (o) { var d = Math.hypot(o.x - px, o.y - py); if (d < bd) { bd = d; best = o.p; } });
      if (best) { miniCat = best.cat; miniSel = best; } else miniSel = null;
      refreshPanel(); drawMini();
      var it = listEl.querySelector('.item.on'); if (it && it.scrollIntoView) it.scrollIntoView({ block: 'nearest' });
    });
    function toggleMini(on) {
      miniOpen = on === undefined ? !miniOpen : on;
      miniEl.hidden = !miniOpen; mapBtn.textContent = miniOpen ? '閉じる' : '地図';
      mapBtn.setAttribute('aria-pressed', miniOpen ? 'true' : 'false');
      held.length = 0; Array.prototype.forEach.call(dpadEl.querySelectorAll('button'), function (b) { b.classList.remove('on'); });
      if (miniOpen) drawMini();
    }
    mapBtn.addEventListener('click', function () { toggleMini(); });
    mapBtn.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
    window.addEventListener('keydown', function (e) { if (e.key === 'm' || e.key === 'M') toggleMini(); else if (e.key === 'Escape' && miniOpen) toggleMini(false); });
    window.addEventListener('resize', function () { if (miniOpen) drawMini(); });

    // ---- update
    function tryStart(d) {
      var v = DIRS[d], nx = P.tx + v[0], ny = P.ty + v[1];
      if (isSolid(nx, ny)) { P.bump = 0.18; return false; }
      P.moving = true; P.fx = P.tx; P.fy = P.ty; P.tx = nx; P.ty = ny; P.t = 0; P.parity ^= 1; return true;
    }
    function update(dt) {
      now += dt;
      if (miniOpen) return;
      var d = wanted();
      if (P.bump > 0) P.bump -= dt;
      var carry = dt;
      if (P.moving) {
        P.t += dt / STEP_SEC;
        if (P.t >= 1) {
          carry = (P.t - 1) * STEP_SEC; P.moving = false; P.t = 0;
          onArrive();
        } else return;
      }
      if (!P.moving && d) {
        if (P.dir !== d) { P.dir = d; P.turnLock = now + 0.09; }
        if (now >= P.turnLock) { if (tryStart(d)) { moved = true; P.t = Math.min(0.9, carry / STEP_SEC); } }
      }
    }
    var placeKey = null;
    function setPlace(poi, area) {
      var key = (poi ? poi.id : '-') + '|' + (area || '');
      if (key === placeKey) return; placeKey = key;
      if (!poi && !area) { placeEl.hidden = true; return; }
      place1.textContent = ''; place2.textContent = '';
      if (poi) {
        var sw = document.createElement('i'); sw.className = 'sw'; sw.style.background = CATS[poi.cat] ? CATS[poi.cat].color : '#888';
        place1.appendChild(sw); place1.appendChild(document.createTextNode(poi.name));
        place2.textContent = (CATS[poi.cat] ? CATS[poi.cat].name : '') + (area ? ' / ' + area : '');
        place2.hidden = false;
      } else { place1.textContent = area; place2.hidden = true; }
      placeEl.hidden = false;
    }
    function onArrive() {
      var l = labelAt(P.tx, P.ty);
      lastLabel = l; nearPoi = nearestPoi(P.tx, P.ty);
      setPlace(nearPoi, l); layoutTags(P.tx, P.ty);
    }
    onArrive();

    // ---- draw
    function playerPos() {
      var x = P.tx * TILE, y = P.ty * TILE;
      if (P.moving) { x = (P.fx + (P.tx - P.fx) * P.t) * TILE; y = (P.fy + (P.ty - P.fy) * P.t) * TILE; }
      return [x, y];
    }
    function frame() {
      if (P.moving) return P.t < 0.6 ? (P.parity ? 0 : 2) : 1;
      if (P.bump > 0) return Math.floor(P.bump / 0.09) % 2 ? 0 : 2;
      return 1;
    }
    function draw() {
      var s = view.scale, pp = playerPos(), px = pp[0], py = pp[1];
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.imageSmoothingEnabled = false;
      ctx.fillStyle = '#1d2330'; ctx.fillRect(0, 0, view.cw, view.ch);
      var usableH = view.ch - view.bottomUI, topUI = 0;
      var cx = Math.round(view.cw / 2 - (px + TILE / 2) * s);
      var cy = Math.round(topUI + (usableH - topUI) / 2 - (py + TILE / 2) * s);
      var mw = W * TILE * s, mh = H * TILE * s;
      if (mw <= view.cw) cx = Math.round((view.cw - mw) / 2); else cx = Math.min(0, Math.max(view.cw - mw, cx));
      if (mh <= view.ch) cy = Math.round((view.ch - mh) / 2); else cy = Math.min(0, Math.max(view.ch - mh, cy));
      ctx.setTransform(s, 0, 0, s, cx, cy);
      var x0 = Math.max(0, Math.floor(-cx / (TILE * s))), x1 = Math.min(W, Math.ceil((view.cw - cx) / (TILE * s)));
      var y0 = Math.max(0, Math.floor(-cy / (TILE * s)) - 1), y1 = Math.min(H, Math.ceil((view.ch - cy) / (TILE * s)) + 1);
      drawTiles(baseL, x0, y0, x1, y1);
      var row = Math.round(py / TILE) + 1;         // rows up to and including the player's row go behind the player
      drawTiles(decoL, x0, y0, x1, Math.min(y1, row));
      drawObjects(x0, y0, x1, y1, 0, row);
      ctx.drawImage(playerImg, frame() * 16, ROW[P.dir] * 24, 16, 24, Math.round(px), Math.round(py) - 8, 16, 24);
      if (row < y1) drawTiles(decoL, x0, Math.max(y0, row), x1, y1);
      drawObjects(x0, y0, x1, y1, row, 1e9);
      drawTiles(overL, x0, y0, x1, y1);
      view.cx = cx; view.cy = cy;
      drawTags(cx, cy);
    }

    var last = performance.now();
    function loop(t) {
      var dt = Math.min(0.1, (t - last) / 1000); last = t;
      update(dt); draw();
      if (moved && hintEl && !hintEl.classList.contains('gone')) hintEl.classList.add('gone');
      requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);

    // test / debug access (read-only state; teleport only with ?debug)
    window.__soka = {
      state: function () { return { pois: POIS.length, tx: P.tx, ty: P.ty, dir: P.dir, moving: P.moving, scale: view.scale, camX: view.cx, camY: view.cy, label: lastLabel, mini: miniOpen, w: W, h: H }; },
      isSolid: isSolid, map: map, labelAt: labelAt,
      pois: function () { return POIS; },
      tags: function () { return tags.map(function (t) { return { name: t.p.name, cat: t.p.cat, d: t.d, x: t.x, y: t.y, w: t.w, h: t.h }; }); },
      nearPoi: function () { return nearPoi ? nearPoi.name : null; },
      placeText: function () { return placeEl.hidden ? null : [place1.textContent, place2.hidden ? '' : place2.textContent]; },
      baseName: function (x, y) { var i = baseL[y * W + x]; return i >= 0 ? map.names[i] : null; },
      decoName: function (x, y) { var i = decoL[y * W + x]; return i >= 0 ? map.names[i] : null; },
      overName: function (x, y) { var i = overL[y * W + x]; return i >= 0 ? map.names[i] : null; }
    };
    if (DEBUG) window.__soka.teleport = function (x, y, d) { P.tx = x; P.ty = y; P.moving = false; if (d) P.dir = d; onArrive(); };
  }
})();
