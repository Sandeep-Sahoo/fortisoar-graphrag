/* Backgrounds for the graph views, matching the sign-in screen.
   2D (Home map, chat graph): the deep radial gradient plus a dot grid that pans and zooms with the graph
   (every 5th dot a little brighter, so scale is easy to read).
   3D: deep space behind a transparent WebGL canvas, with a star field and a perspective grid floor drawn through the
   3D camera's own projection, so they turn and zoom with the scene. Nothing here ends up in a PNG export. */
(() => {
  const rgbVar = name => css(name) || "127,127,127";

  /* ---- 2D dot grid */
  function dotGrid(g) {
    const wrap = g.container().parentElement;
    const cv = document.createElement("canvas");
    cv.className = "dot-grid";
    wrap.insertBefore(cv, wrap.firstChild);
    let queued = false;
    const draw = () => {
      queued = false;
      const w = wrap.clientWidth, h = wrap.clientHeight, dpr = devicePixelRatio || 1;
      if (!w || !h) return;
      if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
        cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
        cv.style.width = `${w}px`; cv.style.height = `${h}px`;
      }
      const ctx = cv.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const z = g.zoom(), pan = g.pan();
      let step = 26 * z;                 // 26 graph units between dots, kept between 14 and 56 px on screen
      while (step < 14) step *= 2;
      while (step > 56) step /= 2;
      const r = Math.max(0.7, Math.min(1.5, 1.05 * Math.sqrt(step / 26)));
      const dot = rgbVar("--scene-dot");
      const i0 = Math.ceil(-pan.x / step), j0 = Math.ceil(-pan.y / step);
      const minor = new Path2D(), major = new Path2D();
      for (let i = i0, x = pan.x + i0 * step; x <= w; i++, x += step) {
        for (let j = j0, y = pan.y + j0 * step; y <= h; j++, y += step) {
          const p = i % 5 === 0 && j % 5 === 0 ? major : minor;
          p.moveTo(x + r, y);
          p.arc(x, y, r, 0, Math.PI * 2);
        }
      }
      ctx.fillStyle = `rgba(${dot},.22)`; ctx.fill(minor);
      ctx.fillStyle = `rgba(${dot},.45)`; ctx.fill(major);
    };
    const queue = () => { if (!queued) { queued = true; requestAnimationFrame(draw); } };
    g.on("viewport", queue);
    new ResizeObserver(queue).observe(wrap);
    return queue;
  }
  const redraw2d = [dotGrid(cyHome), dotGrid(cy)];

  /* ---- 3D deep space: stars + grid floor, projected with the 3D camera */
  function scene3d(fg, el) {
    fg.backgroundColor("rgba(0,0,0,0)");  // the gradient behind shows through
    const cv = document.createElement("canvas");
    cv.className = "g3-bg";
    el.insertBefore(cv, el.firstChild);
    let seed = 11;
    const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const stars = Array.from({ length: 240 }, () => {  // directions on a sphere, placed around the graph each frame
      const u = rnd() * 2 - 1, t = rnd() * Math.PI * 2, s = Math.sqrt(1 - u * u);
      return { d: [s * Math.cos(t), u, s * Math.sin(t)], k: 0.75 + rnd() * 0.6, tw: rnd() * Math.PI * 2, r: 0.5 + rnd() * 1.1 };
    });
    let box = { c: [0, 0, 0], r: 300, floor: -200 }, frameNo = 0, raf = 0;
    const measure = () => {  // where the graph is: centre, radius, lowest point (the floor sits under it)
      const ns = fg.graphData().nodes.filter(n => Number.isFinite(n.x));
      if (!ns.length) return;
      const c = [0, 1, 2].map(k => ns.reduce((a, n) => a + [n.x, n.y, n.z][k], 0) / ns.length);
      const r = Math.max(150, ...ns.map(n => Math.hypot(n.x - c[0], n.y - c[1], n.z - c[2])));
      box = { c, r, floor: Math.min(...ns.map(n => n.y)) - r * 0.25 - 40 };
    };
    const frame = now => {
      if (!el.isConnected) return;
      raf = requestAnimationFrame(frame);
      if (document.hidden) return;
      const w = el.clientWidth, h = el.clientHeight, dpr = devicePixelRatio || 1;
      if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
        cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
        cv.style.width = `${w}px`; cv.style.height = `${h}px`;
      }
      if (frameNo++ % 45 === 0) measure();
      const ctx = cv.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const cam = fg.camera(), pos = cam.position, m = cam.matrixWorld.elements;
      const fwd = [-m[8], -m[9], -m[10]];
      const inFront = (x, y, z) => (x - pos.x) * fwd[0] + (y - pos.y) * fwd[1] + (z - pos.z) * fwd[2] > 5;
      const dot = rgbVar("--scene-dot"), star = rgbVar("--scene-star");
      // stars: far shell around the graph, twinkling gently
      const R = box.r * 6 + 1500;
      for (const s of stars) {
        const x = box.c[0] + s.d[0] * R * s.k, y = box.c[1] + s.d[1] * R * s.k, z = box.c[2] + s.d[2] * R * s.k;
        if (!inFront(x, y, z)) continue;
        const p = fg.graph2ScreenCoords(x, y, z);
        if (p.x < -5 || p.y < -5 || p.x > w + 5 || p.y > h + 5) continue;
        const a = reducedMotion ? 0.6 : 0.45 + 0.35 * Math.sin(now / 900 + s.tw);
        ctx.fillStyle = `rgba(${star},${a})`;
        ctx.beginPath(); ctx.arc(p.x, p.y, s.r, 0, Math.PI * 2); ctx.fill();
      }
      // grid floor under the graph: lines drawn in short pieces, skipping anything behind the camera
      const S = box.r * 2.4 + 300, n = 12, step = (2 * S) / n, y = box.floor;
      ctx.lineWidth = 1;
      for (let axis = 0; axis < 2; axis++) {
        for (let i = 0; i <= n; i++) {
          const off = -S + i * step, fade = 1 - Math.abs(off) / S;
          ctx.strokeStyle = `rgba(${dot},${(0.05 + 0.13 * fade).toFixed(3)})`;
          ctx.beginPath();
          let drawing = false;
          for (let k = 0; k <= 24; k++) {
            const t = -S + (k / 24) * 2 * S;
            const x = box.c[0] + (axis ? off : t), z = box.c[2] + (axis ? t : off);
            if (!inFront(x, y, z)) { drawing = false; continue; }
            const p = fg.graph2ScreenCoords(x, y, z);
            if (drawing) ctx.lineTo(p.x, p.y); else { ctx.moveTo(p.x, p.y); drawing = true; }
          }
          ctx.stroke();
        }
      }
    };
    raf = requestAnimationFrame(frame);
    return { stop: () => cancelAnimationFrame(raf) };
  }

  window.scene3d = scene3d;
  window.redrawScenes = () => redraw2d.forEach(q => q());  // after a theme change
})();
