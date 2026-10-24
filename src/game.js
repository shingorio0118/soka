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
    loadImage('assets/tiles.png'), loadImage('assets/player.png'), loadImage('assets/sign_soka.png')
  ]).then(function (a) { start(a[0], a[1], a[2], a[3]); }).catch(function (e) {
    console.error(e); document.getElementById('title').textContent = '読み込みに失敗しました';
  });

  function start(map, tiles, playerImg, signImg) {
    var W = map.w, H = map.h, cols = Math.floor(tiles.width / TILE);
    var solid = map.solid;
    var sprites = { sign_soka: signImg };

    // ---- pre-render layers
    var baseC = fixedCanvas(W * TILE, H * TILE), decoC = fixedCanvas(W * TILE, H * TILE), overC = fixedCanvas(W * TILE, H * TILE);
    function drawLayer(c, layer) {
      var x = c.getContext('2d'); x.imageSmoothingEnabled = false;
      for (var ty = 0; ty < H; ty++) for (var tx = 0; tx < W; tx++) {
        var id = layer[ty][tx]; if (id < 0) continue;
        x.drawImage(tiles, (id % cols) * TILE, Math.floor(id / cols) * TILE, TILE, TILE, tx * TILE, ty * TILE, TILE, TILE);
      }
    }
    drawLayer(baseC, map.layers.base); drawLayer(decoC, map.layers.deco); drawLayer(overC, map.layers.over);
    var dx = decoC.getContext('2d');
    map.objects.forEach(function (o) { dx.drawImage(sprites[o.sprite], o.x * TILE, o.y * TILE); });

    // ---- state
    if (window.matchMedia && window.matchMedia('(pointer: coarse)').matches && hintEl) hintEl.textContent = '十字ボタンで歩けます';
    var DIRS = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] };
    var ROW = { down: 0, left: 1, right: 2, up: 3 };
    var P = { tx: map.spawn.x, ty: map.spawn.y, dir: map.spawn.dir || 'down', moving: false, fx: 0, fy: 0, t: 0, parity: 0, bump: 0, turnLock: 0 };
    var held = [];            // most recent last
    var now = 0, moved = false;

    function isSolid(x, y) { return x < 0 || y < 0 || x >= W || y >= H || solid[y].charCodeAt(x) === 49; }
    function press(d) { var i = held.indexOf(d); if (i >= 0) held.splice(i, 1); held.push(d); }
    function release(d) { var i = held.indexOf(d); if (i >= 0) held.splice(i, 1); }
    function wanted() { return held.length ? held[held.length - 1] : null; }

    // ---- keyboard
    var KEYMAP = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right', w: 'up', a: 'left', s: 'down', d: 'right', W: 'up', A: 'left', S: 'down', D: 'right' };
    window.addEventListener('keydown', function (e) {
      var d = KEYMAP[e.key]; if (!d) return; e.preventDefault(); if (!e.repeat) press(d);
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
      var best = null, bd = 1e9;
      map.labels.forEach(function (l) {
        var d = Math.hypot(tx - l.x, ty - l.y);
        if (d <= l.r && d / l.r < bd) { bd = d / l.r; best = l; }
      });
      return best ? best.name : null;
    }

    // ---- update
    function tryStart(d) {
      var v = DIRS[d], nx = P.tx + v[0], ny = P.ty + v[1];
      if (isSolid(nx, ny)) { P.bump = 0.18; return false; }
      P.moving = true; P.fx = P.tx; P.fy = P.ty; P.tx = nx; P.ty = ny; P.t = 0; P.parity ^= 1; return true;
    }
    function update(dt) {
      now += dt;
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
      ctx.drawImage(baseC, 0, 0);
      var row = Math.round(py / TILE);             // row the feet are in
      var split = (row + 1) * TILE;                // deco rows above/including player row first
      ctx.drawImage(decoC, 0, 0, W * TILE, split, 0, 0, W * TILE, split);
      ctx.drawImage(playerImg, frame() * 16, ROW[P.dir] * 24, 16, 24, Math.round(px), Math.round(py) - 8, 16, 24);
      ctx.drawImage(decoC, 0, split, W * TILE, H * TILE - split, 0, split, W * TILE, H * TILE - split);
      ctx.drawImage(overC, 0, 0);
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
      state: function () { return { tx: P.tx, ty: P.ty, dir: P.dir, moving: P.moving, scale: view.scale, camX: view.cx, camY: view.cy, label: lastLabel, w: W, h: H }; },
      isSolid: isSolid, map: map
    };
    if (DEBUG) window.__soka.teleport = function (x, y, d) { P.tx = x; P.ty = y; P.moving = false; if (d) P.dir = d; onArrive(); };
  }
})();
