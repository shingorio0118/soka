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
  var placeEl = document.getElementById('place');
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
    window.addEventListener('resize', resize); window.addEventListener('orientationchange', resize); resize();

    // ---- place labels
    var lastLabel = null;
    function labelAt(tx, ty) {
      var id = labelGrid[ty * W + tx];
      return id ? map.labelNames[id - 1] : null;
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
    function drawMini() {
      if (!miniBase) buildMini();
      var dpr = window.devicePixelRatio || 1, aw = window.innerWidth - 20, ah = window.innerHeight - 120;
      var k = Math.min(aw / W, ah / H), cssW = Math.floor(W * k), cssH = Math.floor(H * k);
      miniC.style.width = cssW + 'px'; miniC.style.height = cssH + 'px';
      miniC.width = Math.floor(cssW * dpr); miniC.height = Math.floor(cssH * dpr);
      var x = miniC.getContext('2d'); x.imageSmoothingEnabled = false;
      x.drawImage(miniBase, 0, 0, miniC.width, miniC.height);
      var sx = miniC.width / W, sy = miniC.height / H, q = Math.max(2, Math.round(3 * dpr));
      function mark(tx, ty, col) { x.fillStyle = '#ffffff'; x.fillRect(tx * sx - q, ty * sy - q, q * 2 + sx, q * 2 + sy); x.fillStyle = col; x.fillRect(tx * sx - q + 2, ty * sy - q + 2, q * 2 + sx - 4, q * 2 + sy - 4); }
      mark(map.station.x, map.station.y, '#2a5db0');
      x.fillStyle = '#10151f'; x.font = 'bold ' + Math.round(11 * dpr) + 'px sans-serif'; x.textBaseline = 'middle';
      x.fillText('草加駅', map.station.x * sx + q * 2 + 4, map.station.y * sy);
      var pp = playerPos(); mark(Math.round(pp[0] / TILE), Math.round(pp[1] / TILE), '#d4322a');
    }
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
    function onArrive() {
      var l = labelAt(P.tx, P.ty);
      if (l !== lastLabel) { lastLabel = l; if (l) { placeEl.textContent = l; placeEl.hidden = false; } else placeEl.hidden = true; }
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
      state: function () { return { tx: P.tx, ty: P.ty, dir: P.dir, moving: P.moving, scale: view.scale, camX: view.cx, camY: view.cy, label: lastLabel, mini: miniOpen, w: W, h: H }; },
      isSolid: isSolid, map: map, labelAt: labelAt,
      baseName: function (x, y) { var i = baseL[y * W + x]; return i >= 0 ? map.names[i] : null; },
      decoName: function (x, y) { var i = decoL[y * W + x]; return i >= 0 ? map.names[i] : null; },
      overName: function (x, y) { var i = overL[y * W + x]; return i >= 0 ? map.names[i] : null; }
    };
    if (DEBUG) window.__soka.teleport = function (x, y, d) { P.tx = x; P.ty = y; P.moving = false; if (d) P.dir = d; onArrive(); };
  }
})();
