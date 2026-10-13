/* Playbook diagrams: the diagram card (Flowchart / Sequence / Linear / Scenarios, vertical or horizontal, zoom,
   step player with a whole-drawing or follow-the-step camera), where an expanded card goes (chat: docked on the
   right, the graph minimised; elsewhere: full screen), and the Scenarios page. Route: #scenarios[/<playbook>]
   or #scenarios/develop/<design session id>. */
const DIAGRAM_KINDS = { flowchart: "Flowchart", sequence: "Sequence", linear: "Linear", scenarios: "Scenarios" };
const KIND_ICONS = { linear: "☰ ", scenarios: "⑂ " };
const LIN_STEP_MS = 1800;  // time per step at 1× in the step player
const FOLLOW_ZOOM = 1;     // the follow camera never shows a step smaller than this

// kids: also draw the steps of the child playbooks the playbook calls, inline
const diagramUrl = (entry, kind, dir, kids = false) => entry.sid
  ? `/develop/sessions/${encodeURIComponent(entry.sid)}/diagram?kind=${kind}&dir=${dir}&children=${kids}`
  : `/playbooks/${encodeURIComponent(entry.uuid)}/diagram?kind=${kind}&dir=${dir}&children=${kids}`;

/* entries: [{ uuid, name } | { sid, name }] (a select appears when there are several).
   layout "card" (in a chat answer / catalog page) or "explorer" (fills its box; the scenario list becomes a side column).
   state: what to restore when a card is re-created elsewhere (expanded): { uuid, kind, dir, cam, scenario, idx, playing }. */
function diagramCard(entries, { autoload = true, kind = "flowchart", graphAnimate = true, layout = "card", state = {}, place = "inline", onClose = null } = {}) {
  kind = state.kind || kind;
  const card = document.createElement("div");
  card.className = `diagram${layout === "explorer" ? " explorer" : ""}`;
  const options = entries.map((p, i) => `<option value="${i}">${esc(p.name)}</option>`).join("");
  const kindButton = (k, title) => `<button type="button" data-kind="${k}" aria-pressed="${autoload && k === kind}"${title ? ` title="${title}"` : ""}>${KIND_ICONS[k] || ""}${DIAGRAM_KINDS[k]}</button>`;
  const expandLabel = place === "dock" ? "⛶ Full screen" : "⤢ Expand";
  const expandTitle = place === "dock" ? "Open this diagram full screen" : "Open this diagram larger (in chat: on the right, the graph minimises)";
  card.innerHTML = `
    <div class="bar">
      ${entries.length > 1 ? `<select aria-label="Playbook">${options}</select>` : `<strong class="dg-name">${esc(entries[0].name)}</strong>`}
      <span class="seg" role="group" aria-label="Diagram type">
        ${kindButton("flowchart")}${kindButton("sequence")}${kindButton("linear", "Every step in order, one after the other, animated step by step")}${kindButton("scenarios", "Every way the playbook can run end to end (one per branch combination); pick one to play it")}
      </span>
      <span class="seg kids" role="radiogroup" aria-label="Child playbooks">
        <button type="button" role="radio" data-kids="0" title="Only this playbook's own steps">This playbook</button><button type="button" role="radio" data-kids="1" title="Also draw the steps of every child playbook it calls (up to 3 levels), inline, in labelled boxes; scenarios then run through the children's branches too">+ Child playbooks</button>
      </span>
      <span class="seg dirs" role="group" aria-label="Orientation">
        <button type="button" data-dir="TB" title="Top to bottom">↕ Vertical</button><button type="button" data-dir="LR" title="Left to right">↔ Horizontal</button>
      </span>
      <span class="spacer"></span>
      <button type="button" class="flowdots-toggle" aria-pressed="false" title="Flow dots: a dot travels every link from its source to its destination, continuously">● Flow</button>
      <button type="button" class="dg-index" title="Index: what each symbol means (follows the 2D / 3D view)" aria-haspopup="dialog" aria-expanded="false">◎ Index</button>
      <button type="button" class="dg-3d" aria-pressed="false" title="Show the steps in 3D: drag to rotate, wheel to zoom, right-drag to pan; hover a step for its neighbours, click one to jump there">🧊 3D</button>
      <span class="zoombar"></span>
      ${place === "viewer" ? "" : `<button type="button" class="enlarge" title="${expandTitle}">${expandLabel}</button>`}
      ${graphAnimate ? `<button type="button" class="animate" title="Walk through this playbook step by step in the graph panel">▶ Animate</button>` : ""}
      ${place === "dock" ? `<button type="button" class="dg-min" title="Shrink this to its title bar and show the graph; Restore opens it again">▾ Minimise</button>` : ""}
      ${onClose ? `<button type="button" class="dg-close" title="Close and show the graph again">✕ Close</button>` : ""}
    </div>
    <div class="docked-note">Shown on the right → <button type="button" class="undock">Show here</button></div>
    <div class="xbody">
      <div class="scnbar" hidden></div>
      <div class="xmain">
        <div class="linbar" hidden>
          <button type="button" data-lin="prev" title="Previous step (←)">◀</button>
          <button type="button" data-lin="play">▶ Play</button>
          <button type="button" data-lin="next" title="Next step (→)">▶</button>
          <span class="linstep" role="status" aria-live="polite"></span>
          <span class="seg cams" role="group" aria-label="Camera">
            <button type="button" data-cam="whole" title="Show the whole drawing, fixed; the current step lights up">▣ Whole</button><button type="button" data-cam="follow" title="Zoom in and move along with the current step (hawk-eye)">◎ Follow</button>
          </span>
          <label title="Animation speed">Speed <input type="range" min="0.25" max="4" step="0.25" aria-label="Animation speed"><output></output></label>
        </div>
        <div class="canvas zoomable"><span class="muted">${autoload ? "Loading diagram…" : "Pick Flowchart, Sequence, Linear or Scenarios to show the workflow."}</span></div>
        <div class="dg3d" hidden></div>
      </div>
    </div>`;
  const q = sel => card.querySelector(sel);
  const canvas = q(".canvas"), linbar = q(".linbar"), scnbar = q(".scnbar"), xbody = q(".xbody");
  const zoom = zoomControl(canvas, q(".zoombar"), { onReset: () => {
    // the view the diagram opened with: default zoom, scrolled to the start, camera re-applied to the current step
    if (kind === "sequence") zoom.set(1); else zoom.fit(1);
    canvas.scrollTo(0, 0);
    if (canvas.classList.contains("lin")) { if (lin.cam === "whole") zoom.fit(2, true); paintLinear(true); }
  } });
  let entryIdx = Math.max(0, entries.findIndex(e => state.uuid && (e.uuid === state.uuid || e.sid === state.uuid)));
  if (entries.length > 1) q("select").value = String(entryIdx);
  const entry = () => entries[entryIdx];
  let dir = state.dir || (store.get("graphrag.dir") === "LR" ? "LR" : "TB");
  let kids = state.kids ?? store.get("graphrag.kids") === "1";

  /* 🧊 3D: the same steps as a layered 3D graph (top-down or left-right, following ↕ / ↔), driven by the step player:
     the current step glows, pulses and keeps its name; steps off the chosen scenario fade; ● Flow runs along the path. */
  const box3d = q(".dg3d"), btn3d = q(".dg-3d");
  let d3 = null, d3on = !!state.d3, lastGraph = null, mounting = null;
  const KIND_VAR = { trigger: "--pb", decision: "--mod", manual_input: "--doc", connector: "--con", reference: "--coll", child_start: "--coll" };
  const KIND_NAME = { trigger: "Trigger", decision: "Decision", manual_input: "Approval", connector: "Connector", reference: "Child playbook",
                      child_start: "Child playbook start" };
  const end = x => (typeof x === "object" ? x.id : x);
  /* Fixed layered layout, in step order: a step's level is the longest forward path to it (links back to an earlier
     step, i.e. loops, don't count). Levels are LEVEL apart along the flow axis (down for ↕, right for ↔); steps on the
     same level sit on a ring around that axis so the depth shows, and a plain chain becomes a gentle helix. */
  const LEVEL = 55;
  function layout3d(gr) {
    const n = gr.nodes.length, idx = id => +id.slice(1);
    const fwd = gr.links.map(l => [idx(l.source), idx(l.target)]).filter(([a, b]) => b > a);
    const level = new Array(n).fill(0);
    for (let i = 0; i < n; i++) for (const [a, b] of fwd) if (a === i) level[b] = Math.max(level[b], level[a] + 1);
    const byLevel = new Map();
    level.forEach((lv, i) => byLevel.set(lv, [...(byLevel.get(lv) || []), i]));
    const depth = Math.max(...level, 0), pos = [];
    byLevel.forEach((members, lv) => members.forEach((i, k) => {
      const ring = members.length > 1 ? 22 + 14 * members.length : 18;  // single steps: small helix offset
      const angle = members.length > 1 ? (k / members.length) * Math.PI * 2 : lv * 0.9;
      const along = (lv - depth / 2) * LEVEL, a = Math.cos(angle) * ring, b = Math.sin(angle) * ring;
      pos[i] = dir === "LR" ? { x: along, y: a, z: b } : { x: a, y: -along, z: b };
    }));
    return pos;
  }
  const toData = gr => {
    const pos = layout3d(gr);
    return {
      nodes: gr.nodes.map((n, i) => ({ id: n.id, name: n.name, shape: n.kind, color: css(KIND_VAR[n.kind] || "--step"), val: n.kind === "trigger" ? 3.2 : 1.8,
                                       tip: `${KIND_NAME[n.kind] || (n.kind || "step").replace(/_/g, " ")}${n.detail ? ` · ${n.detail}` : ""}`,
                                       fx: pos[i].x, fy: pos[i].y, fz: pos[i].z })),
      links: gr.links.map(l => ({ ...l, color: css("--edge") })),
    };
  };
  function sync3d(moveCamera = false) {
    if (!d3) return;
    const { nodes, links } = d3.fg.graphData();
    const playing = canvas.classList.contains("lin");
    const path = playing ? (lin.path || nodes.map((_, i) => i)) : [];
    const rank = new Map(path.map((s, k) => [s, k]));
    const hop = new Map(path.slice(1).map((s, k) => [`${path[k]}>${s}`, k + 1]));
    const complete = playing && path.length > 0 && lin.idx >= path.length;
    for (const n of nodes) {
      const k = rank.get(+n.id.slice(1));
      n.off = playing && k === undefined;
      n.walked = playing && k === lin.idx;
      n.complete = complete && k !== undefined;
    }
    for (const l of links) {
      const k = hop.get(`${end(l.source).slice(1)}>${end(l.target).slice(1)}`);
      l.off = playing && k === undefined;
      l.walked = playing && k === lin.idx;
      l.complete = complete && k !== undefined;
    }
    const current = playing && lin.idx < path.length ? `s${path[lin.idx]}` : null;
    d3.pin(complete ? path.map(s => `s${s}`) : current ? [current] : []);  // finished: every step on the path named and pulsing
    d3.repaint();
    if (moveCamera && complete) d3.fit(900);
    else if (moveCamera && current && lin.cam === "follow") d3.fly(current, 700);
  }
  async function show3d() {
    if (!lastGraph || mounting) return;
    canvas.hidden = true;
    q(".zoombar").hidden = true;
    box3d.hidden = false;
    d3?.destroy();
    d3 = null;
    try {
      mounting = graph3d.mount(box3d, { data: toData(lastGraph), fixed: true, offNode: 0.85, offLink: 0.6, offMix: 0.55, offWidth: 0.8,
                                        allLabels: true, tip: n => stepMetrics(+n.id.slice(1)), onNode: n => {
        const k = (lin.path || []).indexOf(+n.id.slice(1));  // clicking a step on the played path jumps there
        if (canvas.classList.contains("lin") && (k >= 0 || !lin.path)) { lin.playing = false; linGo(k >= 0 ? k : +n.id.slice(1)); }
      } });
      d3 = await mounting;
      sync3d(false);
      setTimeout(() => { if (d3) lin.cam === "follow" && canvas.classList.contains("lin") ? sync3d(true) : d3.fit(); }, 1400);
    } catch (err) { notify(err.message, true); hide3d(true); }
    finally { mounting = null; }
  }
  /* Metrics for one step (hover card in 2D and 3D): type, operation, links, branches, scenario coverage and where it
     sits in the scenario being played. */
  function stepMetrics(i) {
    const gr = lastGraph, n = gr?.nodes[i];
    if (!n) return "";
    const id = `s${i}`, ins = gr.links.filter(l => l.target === id), outs = gr.links.filter(l => l.source === id);
    const branches = outs.map(l => l.label).filter(Boolean);
    const list = lin.list || [];
    const inScen = list.filter(sc => sc.path.includes(i)).length;
    let here = "";
    if (canvas.classList.contains("lin")) {
      const path = lin.path || gr.nodes.map((_, k) => k), k = path.indexOf(i);
      here = k < 0 ? "not on this path" : `step ${k + 1} of ${path.length} · ${k === lin.idx ? "current" : k < lin.idx ? "done" : "upcoming"}`;
    }
    const nextNames = outs.map(l => `${l.label ? `${l.label} → ` : ""}${gr.nodes[+l.target.slice(1)]?.name || l.target}`).join("; ");
    return metricTip(n.name, KIND_NAME[n.kind] || (n.kind || "step").replace(/_/g, " "), [
      ["Detail", n.detail], ["In child playbook", n.group], ["Step", `${i + 1} of ${gr.nodes.length}`],
      ["Links", `${ins.length} in · ${outs.length} out`], ["Branches", branches.join(" / ")], ["Next", nextNames],
      ["Scenarios", list.length ? `in ${inScen} of ${list.length}` : ""], [lin.scenario ? `Scenario #${lin.scenario.id}` : "Walk-through", here],
    ]);
  }
  // 2D: the same metric card when hovering a step of the drawing
  const tip2d = document.createElement("div");
  tip2d.className = "mtip-float";
  tip2d.hidden = true;
  q(".xmain").style.position = "relative";
  q(".xmain").appendChild(tip2d);
  canvas.addEventListener("mouseover", e => {
    const g = e.target.closest("g.node");
    const m = g && /flowchart-s(\d+)-\d+$/.exec(g.id);
    if (!m || !lastGraph) { tip2d.hidden = true; return; }
    tip2d.innerHTML = stepMetrics(+m[1]);
    tip2d.hidden = false;
    const r = g.getBoundingClientRect(), box = q(".xmain").getBoundingClientRect();
    tip2d.style.left = `${Math.max(6, Math.min(r.right - box.left + 8, box.width - tip2d.offsetWidth - 6))}px`;
    tip2d.style.top = `${Math.max(6, r.top - box.top - 4)}px`;
  });
  canvas.addEventListener("mouseleave", () => { tip2d.hidden = true; });
  canvas.addEventListener("scroll", () => { tip2d.hidden = true; });

  function hide3d(turnOff = false) {
    d3?.destroy();
    d3 = null;
    box3d.hidden = true;
    canvas.hidden = false;
    q(".zoombar").hidden = false;
    if (turnOff) { d3on = false; btn3d.setAttribute("aria-pressed", "false"); }
  }
  btn3d.addEventListener("click", () => {
    d3on = !d3on;
    btn3d.setAttribute("aria-pressed", String(d3on));
    if (d3on) show3d(); else hide3d();
    window.refreshIndexPop?.();
  });
  q(".dg-index").addEventListener("click", e => { e.stopPropagation(); openIndexPop(e.currentTarget, () => d3on ? "3d" : "2d"); });

  /* Step player: walks a path of step indexes (Linear: every step in order; Scenarios: one branch combination).
     Done steps keep an accent outline, later ones are faded, steps off the path almost disappear. */
  const lin = { idx: 0, n: 0, path: null, choices: new Map(), scenario: null, list: [], playing: false, timer: null,
                cam: state.cam || (store.get("graphrag.cam") === "whole" ? "whole" : "follow"),
                speed: Math.min(4, parseFloat(store.get("graphrag.linspeed")) || 1) };
  const speedIn = linbar.querySelector("input"), speedOut = linbar.querySelector("output");
  speedIn.value = lin.speed;
  speedOut.textContent = `${lin.speed}×`;
  const linNodes = () => {
    const nodes = [];
    canvas.querySelectorAll("g.node").forEach(g => { const m = /flowchart-s(\d+)-\d+$/.exec(g.id); if (m) nodes[+m[1]] = g; });
    return nodes;
  };
  const linEdges = () => [...canvas.querySelectorAll("path[id]")]
    .map(p => ({ p, m: /L[-_]s(\d+)[-_]s(\d+)(?:[-_]\d+)?$/.exec(p.id) })).filter(e => e.m).map(({ p, m }) => ({ p, from: +m[1], to: +m[2] }));
  const nodeLines = g => {
    // from the markup, not innerText: innerText loses the line breaks while the drawing is hidden (3D on)
    const label = g?.querySelector(".nodeLabel, foreignObject div");
    if (!label) return (g?.textContent || "").split("\n").map(s => s.trim()).filter(Boolean);
    const tmp = document.createElement("template");  // inert: nothing in it loads or runs
    tmp.innerHTML = label.innerHTML.replace(/<br\s*\/?>/gi, "\n").replace(/<\/(p|div)>/gi, "\n");
    return tmp.content.textContent.split("\n").map(s => s.trim()).filter(Boolean);
  };
  const nodeText = g => {
    const lines = nodeLines(g);
    return `<b>${esc((lines[0] || "").replace(/^\d+\s*·\s*/, ""))}</b>${lines.length > 1 ? ` · ${esc(lines.slice(1).join(" · "))}` : ""}`;
  };
  // Mermaid writes classDef colours as inline !important styles, so the outline has to be inline too;
  // the original style is kept on the element and restored before each repaint.
  const outline = (el, color, width) => {
    if (el.dataset.linStyle === undefined) el.dataset.linStyle = el.getAttribute("style") || "";
    el.setAttribute("style", el.dataset.linStyle);
    if (color) { el.style.setProperty("stroke", color, "important"); el.style.setProperty("stroke-width", width, "important"); }
  };
  function camera(g, smooth = true) {
    if (lin.cam === "whole" || !g) return;
    if (zoom.z < FOLLOW_ZOOM) zoom.set(FOLLOW_ZOOM);
    const r = g.getBoundingClientRect(), c = canvas.getBoundingClientRect();
    canvas.scrollTo({ top: canvas.scrollTop + (r.top - c.top) - (c.height - r.height) / 2,
                      left: canvas.scrollLeft + (r.left - c.left) - (c.width - r.width) / 2,
                      behavior: smooth && !reducedMotion ? "smooth" : "auto" });
  }
  function paintLinear(scroll = true) {
    const nodes = linNodes(), { idx } = lin;
    const path = lin.path || nodes.map((_, i) => i);
    lin.n = path.length;
    const finished = idx >= lin.n;
    const active = css("--active"), accent = css("--accent"), ok = css("--ok");
    const complete = finished && lin.n > 0;  // the walk reached the end: the whole path lights up
    const rank = new Map(path.map((step, k) => [step, k]));                              // step -> position on the path
    const hop = new Map(path.slice(1).map((step, k) => [`${path[k]}>${step}`, k + 1]));  // link -> position of its target
    const paint = (el, k, isNode) => {
      const off = k === undefined;
      el.classList.toggle("lin-off", off);
      el.classList.toggle("lin-done", !off && k < idx);
      el.classList.toggle(isNode ? "lin-active" : "lin-flow", k === idx);
      el.classList.toggle("lin-todo", !off && k > idx);
      el.classList.toggle("lin-complete", complete && !off);
      const color = off ? "" : complete ? ok : k === idx ? active : k < idx ? accent : "";
      const width = complete || k === idx ? (isNode ? "4px" : "3.5px") : "2.5px";
      (isNode ? [...el.querySelectorAll("rect, path, polygon, circle")].filter(s => !s.closest(".nbadge")) : [el]).forEach(s => outline(s, color, width));
    };
    nodes.forEach((g, i) => g && paint(g, rank.get(i), true));
    linEdges().forEach(({ p, from, to }) => paint(p, hop.get(`${from}>${to}`), false));
    const sc = lin.scenario, choice = lin.choices.get(path[idx]);
    linbar.querySelector(".linstep").innerHTML = finished
      ? (sc ? `<b class="done">Finished</b> · scenario #${sc.id} ${sc.end === "loop" ? `then loops back to “${esc(sc.loops_to_name)}”` : `ends at “${esc(sc.last)}”`} · full path highlighted`
            : `<b class="done">Finished</b> · walked through all ${lin.n} steps`)
      : `Step ${idx + 1}/${lin.n} · ${nodeText(nodes[path[idx]])}${choice ? ` · <span class="pick">→ ${esc(choice)}</span>` : ""}`;
    linbar.querySelector("[data-lin=prev]").disabled = idx <= 0;
    linbar.querySelector("[data-lin=next]").disabled = finished;
    linbar.querySelector("[data-lin=play]").textContent = finished ? "↺ Replay" : lin.playing ? "❚❚ Pause" : "▶ Play";
    linbar.querySelectorAll("[data-cam]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.cam === lin.cam)));
    if (scroll && complete) { zoom.fit(2, true); canvas.scrollTo({ top: 0, left: 0, behavior: reducedMotion ? "auto" : "smooth" }); }  // show it all
    else if (scroll) camera(nodes[path[Math.min(idx, lin.n - 1)]]);
    sync3d(scroll);
  }
  function linGo(i) {
    clearTimeout(lin.timer);
    const n = (lin.path || linNodes()).length;
    lin.idx = Math.max(0, Math.min(i, n));
    if (lin.idx >= n) lin.playing = false;
    paintLinear();
    if (lin.playing) lin.timer = setTimeout(() => card.isConnected ? linGo(lin.idx + 1) : stopLinear(), LIN_STEP_MS / lin.speed);
  }
  function stopLinear() {
    clearTimeout(lin.timer);
    lin.playing = false;
    Object.assign(lin, { path: null, choices: new Map(), scenario: null, list: [] });
    linbar.hidden = true;
    scnbar.hidden = true;
    xbody.classList.add("noscn");
    canvas.classList.remove("lin");
  }
  function setCam(cam) {
    lin.cam = cam;
    store.set("graphrag.cam", cam);
    if (cam === "whole") zoom.fit(2, true);
    paintLinear(true);
    if (cam === "whole") d3?.fit();
  }
  function startPlayer(at = 0, playing = !reducedMotion) {
    canvas.classList.add("lin");
    linbar.hidden = false;
    lin.playing = playing;
    if (lin.cam === "whole") zoom.fit(2, true);
    linGo(at);
  }
  /* Scenarios: list every end-to-end path; picking one plays it on the flowchart. */
  function renderScenarios(d) {
    const list = lin.list = d.scenarios || [];
    scnbar.innerHTML = list.length ? `
      <div class="scnhead">${list.length}${d.truncated ? "+" : ""} scenario${list.length === 1 ? "" : "s"} to run this ${entry().sid ? "design" : "playbook"} end to end${d.truncated ? ` (showing the first ${list.length})` : ""}. Pick one to play it.</div>
      <ol class="scnlist">${list.map((sc, i) => `
        <li><button type="button" data-scn="${i}" aria-pressed="false" title="${esc(sc.summary || "No branches: every run takes this path")}">
          <span class="scn-id">#${sc.id}</span><span class="scn-name">${esc(sc.name)}</span>
          <span class="scn-meta">${sc.steps} steps · ${sc.end === "loop" ? "↺ loops back" : `ends at ${esc(sc.last)}`}</span></button></li>`).join("")}</ol>`
      : `<div class="scnhead">No steps found for this playbook.</div>`;
    scnbar.hidden = false;
    xbody.classList.remove("noscn");
    const nodes = linNodes();
    list.forEach(sc => {
      if (sc.end === "loop") sc.loops_to_name = nodeLines(nodes[sc.loops_to])[0]?.replace(/^\d+\s*·\s*/, "") || "an earlier step";
    });
    scnbar.querySelectorAll("[data-scn]").forEach(b => b.onclick = () => selectScenario(+b.dataset.scn, 0, true));
  }
  function selectScenario(i, at = 0, autoplay = false) {
    const sc = lin.list[i];
    if (!sc) return;
    scnbar.querySelectorAll("[data-scn]").forEach(b => b.setAttribute("aria-pressed", String(+b.dataset.scn === i)));
    scnbar.querySelector(`[data-scn="${i}"]`)?.scrollIntoView({ block: "nearest" });
    Object.assign(lin, { path: sc.path, scenario: sc, choices: new Map((sc.choices || []).map(c => [c.step, c.label])) });
    startPlayer(at, autoplay && !reducedMotion);
  }
  linbar.addEventListener("click", e => {
    const act = e.target.closest("[data-lin]")?.dataset.lin;
    const cam = e.target.closest("[data-cam]")?.dataset.cam;
    if (cam) setCam(cam);
    else if (act === "prev") { lin.playing = false; linGo(lin.idx - 1); }
    else if (act === "next") { lin.playing = false; linGo(lin.idx + 1); }
    else if (act === "play") {
      if (lin.idx >= lin.n) { lin.idx = 0; lin.playing = true; } else lin.playing = !lin.playing;
      linGo(lin.idx);
    }
  });
  card.addEventListener("keydown", e => {  // ← / → / Space step through when focus is inside this card
    if (linbar.hidden || e.target.closest("input, select, textarea")) return;
    if (e.key === "ArrowRight") { e.preventDefault(); lin.playing = false; linGo(lin.idx + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); lin.playing = false; linGo(lin.idx - 1); }
  });
  speedIn.addEventListener("input", () => {
    lin.speed = +speedIn.value;
    speedOut.textContent = `${lin.speed}×`;
    store.set("graphrag.linspeed", String(lin.speed));
    if (lin.playing) linGo(lin.idx);  // restart this step's countdown at the new speed
  });
  canvas.addEventListener("diagram-drawn", () => { if (canvas.classList.contains("lin")) paintLinear(false); });

  const showDirs = () => {
    q(".dirs").hidden = kind === "sequence";
    btn3d.hidden = kind === "sequence";  // a sequence diagram has no step graph
    q(".kids").querySelectorAll("[data-kids]").forEach(b => { const on = (b.dataset.kids === "1") === kids; b.setAttribute("aria-pressed", String(on)); b.setAttribute("aria-checked", String(on)); });
    btn3d.setAttribute("aria-pressed", String(d3on));
    q(".dirs").querySelectorAll("[data-dir]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.dir === dir)));
  };
  // restore = { scenario, idx, playing }: where a re-created (expanded) card should pick up
  const draw = async (restore = null) => {
    stopLinear();
    showDirs();
    if (d3) hide3d();  // rebuilt below for the new diagram / orientation
    canvas.innerHTML = `<span class="muted">Loading diagram…</span>`;
    try {
      const d = await api(diagramUrl(entry(), kind, dir, kids));
      lastGraph = d.graph || null;
      canvas.dataset.kind = kind;
      await drawMermaid(canvas, d.mermaid);
      canvas.scrollTop = 0;
      canvas.scrollLeft = 0;
      if (kind === "sequence") zoom.set(1); else zoom.fit(1);
      if (kind === "linear") startPlayer(restore?.idx || 0, restore ? !!restore.playing : !reducedMotion);
      if (kind === "scenarios") {
        renderScenarios(d);
        if (lin.list.length) selectScenario(restore?.scenario ?? 0, restore?.idx || 0, !!restore?.playing);
      }
      if (d3on && lastGraph) await show3d();
    } catch (err) { canvas.innerHTML = `<span class="error">Diagram failed: ${esc(err.message)}</span>`; }
  };
  card.querySelectorAll(".seg [data-kind]").forEach(b => b.onclick = () => {
    kind = b.dataset.kind;
    card.querySelectorAll(".seg [data-kind]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
    draw();
  });
  q(".dirs").addEventListener("click", e => {
    const b = e.target.closest("[data-dir]");
    if (!b || b.dataset.dir === dir) return;
    dir = b.dataset.dir;
    store.set("graphrag.dir", dir);
    const keep = lin.scenario ? { scenario: lin.list.indexOf(lin.scenario), idx: lin.idx, playing: lin.playing }
      : canvas.classList.contains("lin") ? { idx: lin.idx, playing: lin.playing } : null;
    draw(keep);  // same step, new layout
  });
  q("select")?.addEventListener("change", e => { entryIdx = +e.target.value; draw(); });
  q(".kids").addEventListener("click", e => {
    const b = e.target.closest("[data-kids]");
    if (!b || (b.dataset.kids === "1") === kids) return;
    kids = b.dataset.kids === "1";
    store.set("graphrag.kids", kids ? "1" : "0");
    draw();  // every view of this card is redrawn with (or without) the child playbooks
  });

  const snapshot = () => ({ uuid: entry().uuid || entry().sid, kind, dir, kids, cam: lin.cam, d3: d3on,
                            scenario: lin.scenario ? lin.list.indexOf(lin.scenario) : undefined, idx: lin.idx, playing: lin.playing });
  q(".enlarge")?.addEventListener("click", () => {
    const st = snapshot();
    lin.playing = false;  // the expanded copy takes over the animation
    clearTimeout(lin.timer);
    if (canvas.classList.contains("lin")) paintLinear(false);
    if (place === "inline" && card.closest("#view-chat")) dockDiagram(entries, st, card);
    else openDiagramFull(entries, st);
  });
  q(".undock").onclick = () => closeDock();
  q(".dg-close")?.addEventListener("click", onClose);
  q(".dg-min")?.addEventListener("click", () => {
    const min = $("graphpanel").classList.toggle("dock-min");
    q(".dg-min").textContent = min ? "▴ Restore" : "▾ Minimise";
    q(".dg-min").title = min ? "Open the diagram on the right again" : "Shrink this to its title bar and show the graph; Restore opens it again";
    if (min) { lin.playing = false; clearTimeout(lin.timer); if (canvas.classList.contains("lin")) paintLinear(false); }
    requestAnimationFrame(() => cy.resize());
  });
  // in a chat answer, Expand moves the diagram to the right-hand pane: say so on the button
  requestAnimationFrame(() => {
    const b = q(".enlarge");
    if (b && place === "inline" && card.closest("#view-chat")) { b.textContent = "⇥ Open on right"; b.title = "Move this diagram to the right-hand pane for a bigger view (the graph minimises; ▾ Minimise or ✕ Close there)"; }
  });
  const animate = q(".animate");
  if (animate) animate.onclick = async () => {
    const { uuid, name } = entry();
    stopPlay();
    if (!stepsOf(uuid).length) await showPlaybook(uuid, name, { replace: false });
    setCurrentPlaybook(uuid, name);
    play();
  };
  showDirs();
  xbody.classList.add("noscn");
  if (autoload) {
    const hasRestore = state.idx !== undefined || state.scenario !== undefined;
    draw(hasRestore ? { scenario: state.scenario, idx: state.idx, playing: state.playing } : null);
  }
  return card;
}

/* Chat: the expanded card moves into the right-hand panel; the graph minimises to its toolbar and the card in the
   answer collapses to a note, until the docked copy is closed. */
function dockDiagram(entries, state, source) {
  closeDock();
  const panel = $("graphpanel"), dock = $("scn-dock");
  if (typeof stopPlay === "function") stopPlay();
  const docked = diagramCard(entries, { layout: "explorer", place: "dock", state, graphAnimate: false, onClose: closeDock });
  dock.replaceChildren(docked);
  dock.hidden = false;
  panel.classList.add("docked");
  source?.classList.add("docked-away");
  if (typeof isExpanded === "function" && !isExpanded() && matchMedia("(max-width: 1150px)").matches) panel.scrollIntoView({ behavior: "smooth" });
}
/* Everywhere else: a live explorer card full screen (scenario list, camera, orientation and zoom all work). */
function openDiagramFull(entries, state) {
  const e = entries.find(x => (x.uuid || x.sid) === state.uuid) || entries[0];
  openViewer(`${e.name} · ${DIAGRAM_KINDS[state.kind] || "Diagram"}`, null);
  const body = $("viewer-body");
  body.className = "body live";
  delete body.dataset.mmd;
  body.replaceChildren(diagramCard(entries, { layout: "explorer", place: "viewer", state, graphAnimate: false }));
}

/* ---- Scenarios page */
(() => {
  const root = $("view-scenarios");
  const P = { tab: "playbooks", rows: null, designs: null, filter: "", current: null };

  function frame() {
    if (root.dataset.ready) return;
    root.dataset.ready = "1";
    root.innerHTML = `<div class="scn-page">
      <aside class="scn-pick">
        <div class="scn-pick-head">
          <h1>Scenarios</h1>
          <p class="lead">Every way a playbook can run end to end: one scenario per combination of decisions and approvals. Pick a playbook, then a scenario to play it.</p>
          <nav class="tabs" id="scn-tabs" aria-label="Source"></nav>
          <input id="scn-search" type="search" placeholder="Search playbooks…" aria-label="Search">
          <div class="sub" id="scn-count"></div>
        </div>
        <ul class="scn-pbs" id="scn-pbs"></ul>
      </aside>
      <main class="scn-main" id="scn-main"><div class="empty-state">Pick a playbook on the left to see its scenarios.</div></main>
    </div>`;
    $("scn-search").addEventListener("input", e => { P.filter = e.target.value.toLowerCase(); renderList(); });
  }
  const href = row => P.tab === "designs" ? `#scenarios/develop/${enc(row.id)}` : `#scenarios/${enc(row.id)}`;
  function renderList() {
    $("scn-tabs").innerHTML = [["playbooks", "Playbooks"], ["designs", "Develop designs"]]
      .map(([k, t]) => `<a href="${k === "designs" ? "#scenarios/develop" : "#scenarios"}" aria-current="${k === P.tab ? "page" : "false"}">${t}</a>`).join("");
    $("scn-search").placeholder = P.tab === "designs" ? "Search designs…" : "Search playbooks…";
    const rows = (P.tab === "designs" ? P.designs : P.rows) || [];
    const f = P.filter;
    const shown = rows.filter(r => !f || [r.name, r.title, r.collection, r.pack].some(v => String(v || "").toLowerCase().includes(f)));
    $("scn-count").textContent = rows.length ? `${shown.length} of ${rows.length}` : "";
    $("scn-pbs").innerHTML = shown.slice(0, 400).map(r => `
      <li><a href="${href(r)}" aria-current="${r.id === P.current ? "true" : "false"}">
        <span class="scn-pb-name">${esc(r.name || r.title || "Untitled design")}</span>
        <span class="sub">${P.tab === "designs"
          ? `${esc(r.stage || "")}${r.versions ? ` · v${r.versions}` : ""}${r.updated ? ` · ${esc(new Date(r.updated).toLocaleDateString())}` : ""}`
          : `${esc(r.collection || r.pack || "")}${r.steps ? ` · ${r.steps} steps` : ""}${r.trigger ? ` · ${esc(String(r.trigger).replace("_", " "))}` : ""}`}</span></a></li>`).join("")
      || `<li class="empty-state">${rows.length ? "Nothing matches." : P.tab === "designs" ? "No designs with a proposed workflow yet." : "No playbooks loaded."}</li>`;
  }
  async function load() {
    if (P.tab === "designs" && !P.designs) P.designs = (await api("/develop/sessions")).filter(s => s.versions > 0).map(s => ({ ...s, name: s.title }));
    if (P.tab === "playbooks" && !P.rows) P.rows = (await api("/catalog/playbooks")).filter(r => r.steps > 0);
  }
  function openEntry(entry) {
    const main = $("scn-main");
    const links = entry.sid
      ? `<a href="#develop/${enc(entry.sid)}">Open in Develop →</a>`
      : `<a href="#browse/playbooks/${enc(entry.uuid)}">Open in Catalog →</a>`;
    main.innerHTML = `<div class="scn-main-head"><h2>${esc(entry.name)}</h2><span class="sub">${links}</span></div>`;
    main.appendChild(diagramCard([entry], { kind: "scenarios", layout: "explorer", place: "page", graphAnimate: false }));
  }
  async function show(r) {
    frame();
    P.tab = r.kind === "develop" ? "designs" : "playbooks";
    const id = r.kind === "develop" ? r.id : r.kind;
    P.current = id || null;
    try { await load(); } catch (err) { $("scn-pbs").innerHTML = `<li class="error">${esc(err.message)}</li>`; }
    renderList();
    if (!id) {
      $("scn-main").innerHTML = `<div class="empty-state">Pick a ${P.tab === "designs" ? "design" : "playbook"} on the left to see its scenarios.</div>`;
      return;
    }
    const cur = $("scn-pbs").querySelector('[aria-current="true"]');
    cur?.scrollIntoView({ block: "center" });
    const flash = window.scenarioFlash === id;  // opened by right-clicking a playbook node: make it obvious which one
    window.scenarioFlash = null;
    if (flash) setTimeout(() => { cur?.classList.add("flash"); $("scn-main").querySelector(".scn-main-head")?.classList.add("flash"); }, 50);
    if (P.tab === "designs") {
      const d = (P.designs || []).find(x => x.id === id);
      openEntry({ sid: id, name: d?.name || "Design" });
    } else {
      const row = (P.rows || []).find(x => x.id === id || x.name === id);
      openEntry({ uuid: row?.id || id, name: row?.name || id });
    }
  }
  window.VIEWS.scenarios = { show };
})();
