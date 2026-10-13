/* 🧊 3D views (3d-force-graph / three.js, loaded on first use).
   graph3d.mount(box, …) is the one 3D view used everywhere: the Home map and the chat graph (a copy of the 2D graph,
   kept in sync with ▶ Play and focus rings) and the diagram cards / Scenarios page (the playbook's steps, laid out in
   step order and driven by the step player). Drag to rotate, wheel to zoom, right-drag to pan. Hover a node: its
   direct neighbours blink, the links between them glow and only those names show. ● Flow = particles from source to
   target, only on links that are in play. Deep-space background: js/scene.js. */
(() => {
  const LIB = "https://cdn.jsdelivr.net/npm/3d-force-graph@1.73.4/dist/3d-force-graph.min.js";
  const THREE_LIB = "https://cdn.jsdelivr.net/npm/three@0.168.0/build/three.module.min.js";  // r168, as bundled in 3d-force-graph
  let loading = null, THREE = null;
  const loadLib = () => loading || (loading = Promise.all([
    new Promise((resolve, reject) => {
      if (window.ForceGraph3D) return resolve();
      const s = document.createElement("script");
      s.src = LIB;
      s.onload = resolve;
      s.onerror = () => { loading = null; reject(new Error("Couldn't load the 3D library (cdn.jsdelivr.net). Check the network.")); };
      document.head.appendChild(s);
    }),
    import(THREE_LIB).then(m => { THREE = m; }).catch(() => { THREE = null; }),  // without it: plain spheres
  ]));

  /* ---- node look: a shape per type / step kind, glossy and softly lit, with a halo that carries the highlight */
  const SHAPE = {
    trigger: "cone", decision: "diamond", manual_input: "dodeca", connector: "cube", reference: "ringed", child_start: "cone",
    Playbook: "sphere", SolutionPack: "gem", PlaybookCollection: "torus", Connector: "cube", ConnectorOperation: "pill",
    Module: "cylinder", Document: "sheet",
  };
  let haloTexture = null;
  function halo() {
    if (!haloTexture) {
      const c = document.createElement("canvas");
      c.width = c.height = 128;
      const x = c.getContext("2d"), g = x.createRadialGradient(64, 64, 0, 64, 64, 64);
      g.addColorStop(0, "rgba(255,255,255,0.9)"); g.addColorStop(0.35, "rgba(255,255,255,0.35)"); g.addColorStop(1, "rgba(255,255,255,0)");
      x.fillStyle = g; x.fillRect(0, 0, 128, 128);
      haloTexture = new THREE.CanvasTexture(c);
    }
    return new THREE.Sprite(new THREE.SpriteMaterial({ map: haloTexture, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0.3 }));
  }
  function buildNode(n) {
    const r = Math.cbrt(n.val || 1.6) * 4, shape = SHAPE[n.shape] || "sphere";
    const geo = {
      sphere: () => new THREE.SphereGeometry(r, 28, 18),
      cone: () => new THREE.ConeGeometry(r * 1.05, r * 2.1, 24),
      diamond: () => new THREE.OctahedronGeometry(r * 1.3),
      dodeca: () => new THREE.DodecahedronGeometry(r * 1.15),
      cube: () => new THREE.BoxGeometry(r * 1.55, r * 1.55, r * 1.55),
      gem: () => new THREE.IcosahedronGeometry(r * 1.2, 0),
      torus: () => new THREE.TorusGeometry(r * 0.9, r * 0.38, 14, 32),
      pill: () => new THREE.CapsuleGeometry(r * 0.6, r * 1.1, 6, 14),
      cylinder: () => new THREE.CylinderGeometry(r, r, r * 1.5, 24),
      sheet: () => new THREE.BoxGeometry(r * 1.4, r * 1.8, r * 0.35),
      ringed: () => new THREE.SphereGeometry(r * 0.9, 24, 16),
    }[shape]();
    const mat = new THREE.MeshPhongMaterial({ color: n.color, emissive: n.color, emissiveIntensity: 0.22, shininess: 90,
                                              specular: 0x555555, transparent: true });
    const group = new THREE.Group();
    group.add(new THREE.Mesh(geo, mat));
    if (shape === "ringed") {  // a child playbook: a ring around the sphere
      const ring = new THREE.Mesh(new THREE.TorusGeometry(r * 1.45, r * 0.12, 8, 40), mat);
      ring.rotation.x = Math.PI / 2.6;
      group.add(ring);
    }
    const glow = halo();
    glow.scale.setScalar(r * 4.2);
    group.add(glow);
    n.__mat = mat; n.__halo = glow; n.__spin = shape !== "sphere" && shape !== "ringed" ? 0.004 + Math.random() * 0.004 : 0;
    return group;
  }
  // colour (#hex or rgba) -> material colour + opacity; halo strength follows the highlight
  function look(n, color, haloAlpha) {
    let rgb = color, a = 1;
    const m = /rgba?\(([^)]+)\)/.exec(color);
    if (m) { const p = m[1].split(",").map(Number); rgb = `rgb(${p[0]},${p[1]},${p[2]})`; a = p[3] ?? 1; }
    n.__mat.color.setStyle(rgb);
    n.__mat.emissive.setStyle(rgb);
    n.__mat.opacity = a;
    n.__mat.depthWrite = a >= 0.99;
    n.__halo.material.color.setStyle(rgb);
    n.__halo.material.opacity = haloAlpha * a;
  }

  // "#abc" / "rgb(…)" -> the same colour, mostly transparent (3d-force-graph turns the alpha into opacity)
  const faintCache = new Map();
  function faint(color, alpha) {
    const key = `${color}|${alpha}`;
    if (faintCache.has(key)) return faintCache.get(key);
    const c = document.createElement("canvas").getContext("2d");
    c.fillStyle = color;
    const hex = c.fillStyle, n = parseInt(hex.slice(1), 16);
    const out = /^#[0-9a-f]{6}$/i.test(hex) ? `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${alpha})` : color;
    faintCache.set(key, out);
    return out;
  }
  const linkEnd = e => (typeof e === "object" ? e.id : e);
  // a + (b - a) * t for two CSS colours (via a canvas, which normalises any colour to #rrggbb)
  const mixCache = new Map();
  function mix(a, b, t) {
    const key = `${a}|${b}|${t}`;
    if (mixCache.has(key)) return mixCache.get(key);
    const c = document.createElement("canvas").getContext("2d");
    const hex = col => { c.fillStyle = "#000"; c.fillStyle = col; const v = c.fillStyle; return /^#[0-9a-f]{6}$/i.test(v) ? parseInt(v.slice(1), 16) : null; };
    const x = hex(a), y = hex(b);
    if (x === null || y === null) return a;
    const ch = (v, s) => (v >> s) & 255, m = s => Math.round(ch(x, s) + (ch(y, s) - ch(x, s)) * t);
    const out = `#${((m(16) << 16) | (m(8) << 8) | m(0)).toString(16).padStart(6, "0")}`;
    mixCache.set(key, out);
    return out;
  }

  /* Node / link flags the looks are built from (set by whoever drives the view):
       off     faded: not on the walked path / scenario, or outside a focus ring
       walked  the step or link being walked right now
     Each view also has a hover set (direct neighbourhood of the hovered node). */
  function paint(fg) {
    const on = flowDots && !reducedMotion, active = css("--active"), accent = css("--accent"), grey = css("--muted"), ok = css("--ok");
    const { hover: hov, offNode, offLink, offMix, offWidth } = fg.__view;
    const glow = l => hov?.links.has(l);
    const quiet = n => hov && !hov.nodes.has(n.id);  // not the hovered node or a direct neighbour
    // faded = the node's own colour pulled towards grey (offMix) at offNode strength
    // complete = a finished walk: its whole path in the success colour
    const nodeCol = n => n.walked ? active : n.complete ? ok : n.off ? faint(mix(n.color, grey, offMix), offNode) : quiet(n) ? faint(n.color, 0.35) : n.color;
    for (const n of fg.graphData().nodes) {
      if (n.__mat) look(n, nodeCol(n), n.walked ? 0.9 : n.complete ? 0.65 : hov?.nodes.has(n.id) ? 0.55 : n.off ? 0.05 : quiet(n) ? 0.08 : 0.28);
    }
    fg.nodeColor(nodeCol)
      .linkColor(l => glow(l) || l.walked ? active : l.complete ? ok : l.off ? faint(mix(l.color, grey, offMix), offLink) : hov ? faint(l.color, 0.25) : l.color)
      .linkWidth(l => glow(l) ? 2.8 : l.walked || l.complete ? 2.2 : l.off ? offWidth : 0.7)
      .linkDirectionalArrowColor(l => glow(l) || l.walked ? active : l.complete ? ok : l.off ? faint(l.color, 0.06) : l.color)
      .linkDirectionalParticles(l => on && !l.off ? (l.walked ? 4 : l.complete ? 3 : 2) : 0)  // ● Flow: only links in play
      .linkDirectionalParticleSpeed(l => l.walked || l.complete ? 0.012 : 0.006)
      .linkDirectionalParticleWidth(l => l.walked || l.complete ? 3.2 : 2.2)
      .linkDirectionalParticleColor(l => l.walked ? active : l.complete ? ok : accent);
  }

  /* Mount a 3D view in box (a positioned element). data: { nodes: [{ id, name, color, val?, tip? }], links: [{ source,
     target, color, label? }] }. dag: "td" / "lr" lays nodes out in link order (diagram cards). Returns a handle. */
  // fixed: nodes carry fx / fy / fz (a precomputed layout); they start there, so nothing waits for the simulation
  const place = data => { for (const n of data.nodes) if (Number.isFinite(n.fx)) { n.x = n.fx; n.y = n.fy; n.z = n.fz; } return data; };
  // offNode / offLink: how visible faded steps and links stay (cards keep the whole workflow readable);
  // allLabels: every node keeps a quiet name label, not just the hovered neighbourhood and pinned nodes
  // tip(node) -> HTML for the hover card (metrics); offMix / offWidth: how grey and how thick faded things are
  async function mount(box, { data, dag = null, onNode = null, onRight = null, dagLevel = 60, fixed = false, offNode = 0.12, offLink = 0.06,
                              offMix = 1, offWidth = 0.3, allLabels = false, tip = null }) {
    await loadLib();
    const el = document.createElement("div");
    el.className = "graph3d";
    box.appendChild(el);
    const labelsEl = document.createElement("div");
    labelsEl.className = "g3-labels";
    const view = { hover: null, pinned: new Set(), pulse: new Set(), spans: new Map(), raf: 0, alive: true, offNode, offLink, offMix, offWidth };
    const fg = ForceGraph3D()(el)
      .width(el.clientWidth).height(el.clientHeight)
      .backgroundColor("rgba(0,0,0,0)")
      .nodeLabel(n => tip ? tip(n) : esc(n.tip || ""))  // hover card with the node's metrics
      .nodeVal(n => n.val || 1.6).nodeOpacity(0.95).nodeResolution(12)
      .linkOpacity(0.55)
      .linkDirectionalArrowLength(3).linkDirectionalArrowRelPos(1)
      .linkLabel(l => esc(l.label || ""))
      .onNodeHover(n => { view.hoverNode = n; setHover(n); })
      .onNodeClick(n => clickNode(n))
      .onNodeRightClick(n => onRight?.(n))
      .enableNodeDrag(!THREE)  // with our shapes, dragging is handled below
      .onNodeDragEnd(n => { n.fx = n.x; n.fy = n.y; n.fz = n.z; });  // a dragged node stays where it is dropped
    function clickNode(n) { fly(n.id); onNode?.(n); }
    el.addEventListener("contextmenu", e => e.preventDefault());  // right-click belongs to the nodes here
    fg.__view = view;
    if (THREE) fg.nodeThreeObject(buildNode);
    if (dag) fg.dagMode(dag).dagLevelDistance(dagLevel).onDagError(() => {});  // loops (retry branches) are fine
    if (fixed) fg.cooldownTicks(60);  // pinned layout; a few ticks keep links following a dragged node
    fg.graphData(fixed ? place(data) : data);
    el.appendChild(labelsEl);
    // 🔒 Lock: freeze the camera and the nodes (no rotate / zoom / pan / drag); the step player can still move the camera
    const tools = document.createElement("div");
    tools.className = "g3-tools";
    tools.innerHTML = `<button type="button" class="g3-lock" aria-pressed="false" title="Lock the view: no rotating, zooming, panning or dragging nodes">🔓 Unlocked</button>`;
    el.appendChild(tools);
    const lockBtn = tools.querySelector(".g3-lock");
    const setLock = on => {
      fg.enableNavigationControls(!on).enableNodeDrag(!on);
      lockBtn.setAttribute("aria-pressed", String(on));
      lockBtn.textContent = on ? "🔒 Locked" : "🔓 Unlocked";
      lockBtn.title = on ? "Unlock: rotate, zoom, pan and drag nodes again" : "Lock the view: no rotating, zooming, panning or dragging nodes";
    };
    lockBtn.onclick = () => setLock(lockBtn.getAttribute("aria-pressed") !== "true");

    /* Drag a node (our shapes): press on it and move. It follows the pointer in the plane facing the camera through
       its position, its links follow, and it stays where it is dropped. A press without movement is a click. */
    if (THREE) {
      const dom = fg.renderer().domElement, ray = new THREE.Raycaster(), hit = new THREE.Vector3();
      let drag = null;
      const ndc = e => { const r = dom.getBoundingClientRect(); return new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1); };
      const nodeAt = e => {  // the node under the pointer, found here (not from hover, which only updates while frames are drawn)
        ray.setFromCamera(ndc(e), fg.camera());
        const objs = fg.graphData().nodes.map(n => n.__threeObj).filter(Boolean);
        for (const h of ray.intersectObjects(objs, true)) {
          if (h.object.isSprite) continue;  // the glow halo is not a handle
          let o = h.object;
          while (o && !o.__data) o = o.parent;
          if (o) return o.__data;
        }
        return null;
      };
      dom.addEventListener("pointerdown", e => {
        if (e.button !== 0 || lockBtn.getAttribute("aria-pressed") === "true") return;
        const n = nodeAt(e);
        if (!n || !Number.isFinite(n.x)) return;
        e.stopImmediatePropagation();  // the camera controls must not start turning
        e.preventDefault();
        const normal = new THREE.Vector3();
        fg.camera().getWorldDirection(normal);
        drag = { n, id: e.pointerId, x: e.clientX, y: e.clientY, moved: false, plane: new THREE.Plane().setFromNormalAndCoplanarPoint(normal, new THREE.Vector3(n.x, n.y, n.z)) };
        fg.controls().enabled = false;
        try { dom.setPointerCapture(e.pointerId); } catch { /* synthetic or already-ended pointer: moves still arrive */ }
      }, true);
      dom.addEventListener("pointermove", e => {
        if (!drag || e.pointerId !== drag.id) return;
        e.stopImmediatePropagation();
        if (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 4) return;
        drag.moved = true;
        ray.setFromCamera(ndc(e), fg.camera());
        if (!ray.ray.intersectPlane(drag.plane, hit)) return;
        const n = drag.n;
        n.fx = n.x = hit.x; n.fy = n.y = hit.y; n.fz = n.z = hit.z;
        n.__threeObj?.position.set(hit.x, hit.y, hit.z);
        fg.d3ReheatSimulation();  // a few ticks bring its links along
      }, true);
      const end = e => {
        if (!drag || e.pointerId !== drag.id) return;
        e.stopImmediatePropagation();
        const { n, moved } = drag;
        drag = null;
        fg.controls().enabled = lockBtn.getAttribute("aria-pressed") !== "true";
        try { dom.releasePointerCapture(e.pointerId); } catch { /* already released */ }
        if (!moved && e.type === "pointerup") clickNode(n);
      };
      dom.addEventListener("pointerup", end, true);
      dom.addEventListener("pointercancel", end, true);
    }
    paint(fg);
    const scene = window.scene3d?.(fg, el);  // deep space: stars and a grid floor
    const observer = new ResizeObserver(() => fg.width(el.clientWidth).height(el.clientHeight));
    observer.observe(el);

    // labels: the hovered node + its direct neighbours, and any pinned nodes (e.g. the current step)
    function relabel() {
      const want = new Set([...(allLabels ? fg.graphData().nodes.map(n => n.id) : []), ...view.pinned, ...(view.hover?.nodes || [])]);
      for (const [id, span] of view.spans) if (!want.has(id)) { span.remove(); view.spans.delete(id); }
      const byId = new Map(fg.graphData().nodes.map(n => [n.id, n]));
      for (const id of want) {
        const n = byId.get(id);
        if (!n) continue;
        let span = view.spans.get(id);
        if (!span) { span = document.createElement("span"); labelsEl.appendChild(span); view.spans.set(id, span); }
        span.textContent = n.name;
        const main = id === view.hover?.id || (view.pinned.has(id) && !view.hover);
        const loud = main || view.hover?.nodes.has(id) || view.pinned.has(id);
        span.className = main ? "g3-main" : loud ? "" : `g3-ghost${n.off ? " g3-off" : ""}`;
      }
    }
    function setHover(node) {
      if ((view.hover?.id || null) === (node?.id || null)) return;
      view.hover?.blink.forEach(n => n.__threeObj?.scale.setScalar(view.pulse.has(n.id) ? 1.5 : 1));
      view.hover = null;
      el.style.cursor = node ? "pointer" : "";
      if (node) {
        const links = new Set(), ids = new Set([node.id]);
        for (const l of fg.graphData().links) {
          const s = linkEnd(l.source), t = linkEnd(l.target);
          if (s === node.id || t === node.id) { links.add(l); ids.add(s); ids.add(t); }
        }
        const blink = fg.graphData().nodes.filter(n => ids.has(n.id) && n.id !== node.id);
        view.hover = { id: node.id, nodes: ids, links, blink };
      }
      relabel();
      paint(fg);
    }
    const tick = t => {
      if (!view.alive) return;
      if (!el.isConnected) { api.destroy(); return; }  // its card was closed or redrawn
      view.raf = requestAnimationFrame(tick);
      const wave = 0.5 + 0.5 * Math.sin(t / 130);  // ~1.2 Hz
      if (!reducedMotion) for (const n of fg.graphData().nodes) if (n.__spin && n.__threeObj) n.__threeObj.rotation.y += n.__spin;
      view.hover?.blink.forEach(n => n.__threeObj?.scale.setScalar(reducedMotion ? 1.35 : 1.25 + 0.45 * wave));
      for (const n of fg.graphData().nodes) {
        if (view.pulse.has(n.id) && !view.hover?.nodes.has(n.id)) n.__threeObj?.scale.setScalar(reducedMotion ? 1.5 : 1.35 + 0.35 * wave);
      }
      if (!view.spans.size) return;
      const byId = new Map(fg.graphData().nodes.map(n => [n.id, n]));
      for (const [id, span] of view.spans) {  // keep each name just above its node
        const n = byId.get(id);
        if (!n) continue;
        const p = fg.graph2ScreenCoords(n.x || 0, n.y || 0, n.z || 0);
        span.style.transform = `translate(-50%, -100%) translate(${p.x}px, ${p.y - 10}px)`;
      }
    };
    view.raf = requestAnimationFrame(tick);

    function fly(id, ms = 900) {  // camera to a node, keeping it in front
      const n = fg.graphData().nodes.find(x => x.id === id);
      if (!n || !Number.isFinite(n.x)) return;
      const d = 110, len = Math.hypot(n.x, n.y, n.z) || 1, r = 1 + d / len;
      fg.cameraPosition(len > 1 ? { x: n.x * r, y: n.y * r, z: n.z * r } : { x: n.x, y: n.y, z: n.z + d }, n, ms);
    }
    const api = {
      fg, el,
      setData(next) { setHover(null); fg.graphData(fixed ? place(next) : next); relabel(); paint(fg); },
      repaint() { relabel(); paint(fg); },
      pin(ids) {  // names always shown for these nodes; they also pulse
        for (const n of fg.graphData().nodes) if (view.pulse.has(n.id) && !ids.includes(n.id)) n.__threeObj?.scale.setScalar(1);
        view.pinned = new Set(ids); view.pulse = new Set(ids);
        relabel();
      },
      fit(ms = 800) { fg.zoomToFit(ms, 40); },
      fly,
      destroy() {
        view.alive = false;
        cancelAnimationFrame(view.raf);
        scene?.stop();
        observer.disconnect();
        fg.pauseAnimation();
        fg._destructor?.();
        el.remove();
      },
    };
    el.__graph3d = api;  // for inspection from the console
    setTimeout(() => view.alive && api.fit(fixed ? 0 : 800), fixed ? 50 : 1200);
    return api;
  }

  /* ---- the Home map and the chat graph: a 3D copy of the cytoscape graph, kept in sync */
  const views = new Map();  // cytoscape graph -> { api, button }
  const isOff = el => el.hasClass("dim") || el.hasClass("faded");
  function dataOf(g) {
    // shown = not filtered out by the legend; n.visible() can lag a class change, so read the class itself
    const nodes = g.nodes().filter(n => !n.hasClass("hidden") && !hiddenTypes.has(n.data("type"))).map(n => {
      const w = parseFloat(n.pstyle("width").strValue) || 20;
      return { id: n.id(), name: n.data("label"), tip: LABELS[n.data("type")] || n.data("type"),
               shape: n.data("type") === "Step" ? n.data("props")?.kind : n.data("type"),
               color: css(TYPES[n.data("type")]?.[0] || "--muted") || n.pstyle("background-color").strValue,
               val: Math.max(0.6, (w / 26) ** 3), off: isOff(n), walked: n.hasClass("active") };
    });
    const ids = new Set(nodes.map(n => n.id));
    const links = g.edges().filter(e => !e.isLoop() && ids.has(e.source().id()) && ids.has(e.target().id()))
      .map(e => ({ id: e.id(), source: e.source().id(), target: e.target().id(), label: e.data("type"),
                   color: e.pstyle("line-color").strValue, off: isOff(e), walked: e.hasClass("active") }));
    return { nodes, links };
  }
  function refresh(g) { views.get(g)?.api.setData(dataOf(g)); }
  function sync(g) {  // the 2D highlight changed (Play step, focus ring, clear): copy the flags in place
    const v = views.get(g);
    if (!v) return;
    const { nodes, links } = v.api.fg.graphData();
    for (const n of nodes) { const el = g.getElementById(n.id); if (el.length) { n.off = isOff(el); n.walked = el.hasClass("active"); } }
    for (const l of links) { const el = g.getElementById(l.id); if (el.length) { l.off = isOff(el); l.walked = el.hasClass("active"); } }
    v.api.repaint();
  }
  function close(g) {
    const v = views.get(g);
    if (!v) return;
    v.api.destroy();
    v.button.setAttribute("aria-pressed", "false");
    g.container().parentElement.classList.remove("is-3d");
    views.delete(g);
    window.refreshIndexPop?.();
  }
  function wire(g, button, onNode) {
    button.onclick = async () => {
      if (views.has(g)) { close(g); return; }
      button.setAttribute("aria-pressed", "true");
      button.disabled = true;
      try {
        const wrap = g.container().parentElement;
        const api = await mount(wrap, { data: dataOf(g), onNode: n => { const node = g.getElementById(n.id); if (node.length) onNode(node); },
                                        onRight: n => { const node = g.getElementById(n.id); if (node.length) openScenariosFor(node); },
                                        tip: n => { const node = g.getElementById(n.id); return node.length ? nodeMetrics(node) : esc(n.name); } });
        views.set(g, { api, button });
        wrap.classList.add("is-3d");
        window.refreshIndexPop?.();
      } catch (err) { button.setAttribute("aria-pressed", "false"); notify(err.message, true); }
      finally { button.disabled = false; }
    };
    let pending = null;  // the 2D graph changed (new answer, Home view, filter): redraw the 3D one too
    g.on("add remove", () => { if (!views.has(g)) return; clearTimeout(pending); pending = setTimeout(() => refresh(g), 300); });
    let restyle = null;  // classes changed (▶ Play step, focus ring): update the highlight and the moving dots
    g.on("class style", () => { if (!views.has(g) || restyle) return; restyle = setTimeout(() => { restyle = null; sync(g); }, 60); });
  }
  const mounted = new Set();  // diagram-card views, so theme / ● Flow changes reach them too
  window.graph3d = {
    mount: async (box, opts) => { const api = await mount(box, opts); mounted.add(api); const d = api.destroy; api.destroy = () => { mounted.delete(api); d(); }; return api; },
    active: g => views.has(g),
    reset: g => views.get(g)?.api.fit(),
    refreshAll: () => { views.forEach((_, g) => refresh(g)); mounted.forEach(api => api.repaint()); },
    view: g => views.get(g)?.api.fg,  // the 3D graph instance (for inspection)
    close,
  };
  wire(cyHome, $("btn-home-3d"), node => showHomeDetails(node));
  wire(cy, $("btn-graph-3d"), node => showDetails(node));
})();
