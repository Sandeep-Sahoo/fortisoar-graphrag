/* Platform features shared across pages: usage meter, roles, exports (CSV / PNG / SVG / Markdown / PDF),
   share links, graph-only filters + saved searches + synonyms, "Explain" (AI), users, audit log,
   embedding planner and scheduled sync. Loaded after index.html's main script and uses its helpers. */
window.VIEWS = window.VIEWS || {};

/* ---------------------------------------------------------------- helpers */
const ROLE_RANK = { viewer: 0, analyst: 1, admin: 2 };
const hasRole = min => (ROLE_RANK[window.ME?.role] ?? 0) >= ROLE_RANK[min];
const encKey = k => encodeURIComponent(k);

function downloadBlob(filename, blob) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
const downloadText = (filename, text, type = "text/plain") => downloadBlob(filename, new Blob([text], { type }));
const safeName = s => String(s || "export").replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "export";

function toCSV(columns, rows) {
  const cell = v => {
    const s = Array.isArray(v) ? v.join("; ") : v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map(c => cell(c[1])).join(","), ...rows.map(r => columns.map(c => cell(c[2] ? c[2](r) : r[c[0]])).join(","))].join("\n");
}
function downloadCSV(filename, columns, rows) {
  if (!rows.length) { notify("Nothing to export.", true); return; }
  downloadText(filename.endsWith(".csv") ? filename : `${filename}.csv`, "﻿" + toCSV(columns, rows), "text/csv");
}

async function copyLink(hash) {
  const url = `${location.origin}/${hash.startsWith("#") ? hash : "#" + hash}`;
  try { await navigator.clipboard.writeText(url); notify("Link copied. Anyone signed in to this app can open it."); }
  catch { prompt("Copy this link", url); }
}

const fileToBase64 = file => new Promise((resolve, reject) => {
  const r = new FileReader();
  r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
  r.onerror = () => reject(new Error(`Couldn't read ${file.name}`));
  r.readAsDataURL(file);
});
async function filesPayload(files) {
  const out = [];
  for (const f of files) out.push({ name: f.webkitRelativePath || f.name, content_base64: await fileToBase64(f) });
  return out;
}

const scoreColor = s => s >= 90 ? "#0F6E56" : s >= 80 ? "#378ADD" : s >= 70 ? "#BA7517" : s >= 60 ? "#D85A30" : "#A32D2D";
const gradeBadge = g => g ? `<span class="grade ${esc(g)}" title="Grade ${esc(g)}">${esc(g)}</span>` : "";
const sevPill = s => `<span class="sev ${esc(s)}">${esc(s)}</span>`;
const verdictPill = v => !v ? "" : `<span class="verdict ${v === "Approve" ? "approve" : v === "Request changes" ? "request" : "changes"}">${esc(v)}</span>`;
const scoreCell = (score, grade) => `${gradeBadge(grade)} <b>${score}</b><span class="scorebar"><span style="width:${score}%;background:${scoreColor(score)}"></span></span>`;
const md = text => DOMPurify.sanitize(marked.parse(text || "", { gfm: true }));
const when = iso => iso ? `${fmtWhen(iso)} <span class="muted">(${esc(agoIso(iso))})</span>` : "–";

/** Ask before anything that uses the AI model; says how much of today's quota is left. */
async function confirmAI(what, requests = 1) {
  let left = "";
  try {
    const u = await api("/usage");
    if (u.llm.local) left = " Your answer model is local (Ollama), so there is no quota.";
    else if (u.llm.limit) left = ` ${u.llm.remaining ?? (u.llm.limit - u.llm.used)} of ${u.llm.limit} answer requests are left today.`;
  } catch { /* still ask */ }
  return confirm(`${what} uses ${requests} AI answer request${requests === 1 ? "" : "s"} (free if the same request was answered before).${left}\n\nContinue?`);
}

/* ---------------------------------------------------------------- usage meter (header) */
let usageTimer = null;
async function refreshUsage() {
  const chip = $("usage-chip");
  try {
    const u = await api("/usage");
    window.USAGE = u;
    const part = (label, x) => x.local ? `${label} local` : x.limit ? `${label} ${x.used}/${x.limit}` : `${label} ${x.used}`;
    chip.textContent = `${part("AI", u.llm)} · ${part("Emb", u.embeddings)}`;
    const ratio = x => x.limit ? x.used / x.limit : 0;
    const worst = Math.max(ratio(u.llm), ratio(u.embeddings));
    chip.classList.toggle("out", worst >= 1);
    chip.classList.toggle("warn", worst >= 0.8 && worst < 1);
    chip.hidden = false;
  } catch { chip.hidden = true; }
}
function usagePopover() {
  document.querySelector(".usage-pop")?.remove();
  const u = window.USAGE;
  if (!u) return;
  const row = (title, x, unit) => {
    const pct = x.limit ? Math.min(100, Math.round(100 * x.used / x.limit)) : 0;
    return `<div class="row"><b>${title}</b><span>${x.local ? "local model · unlimited" : x.limit ? `${x.used} / ${x.limit} ${unit}` : `${x.used} ${unit} (no limit set)`}</span></div>
      ${x.limit && !x.local ? `<div class="meter2"><span class="${pct >= 100 ? "out" : pct >= 80 ? "warn" : ""}" style="width:${pct}%"></span></div>` : ""}
      <div class="muted">${esc(x.model)} · ${x.cached} served from cache (free)</div>`;
  };
  const pop = document.createElement("div");
  pop.className = "usage-pop";
  pop.innerHTML = `<h3>Model usage today (${esc(u.date)})</h3>
    ${row("Answers", u.llm, "requests")}${row("Embeddings", u.embeddings, "items")}
    <div class="note">${u.enforced ? "Limits are enforced: answers stop and embeddings stay pending once a limit is reached." : "Limits are shown but not enforced."}
      Counts are this app's own calls, so other apps using the same API key aren't included.</div>
    <div class="note">Free options: <b>Graph only</b> chat mode (0 calls), repeated questions (cached), and a local Ollama model (Settings → Mode: full-local).</div>
    <div class="btnrow" style="margin-top:10px">${hasRole("admin") ? `<button type="button" data-go="#settings">Limits in Settings</button>` : ""}<button type="button" data-close>Close</button></div>`;
  document.body.appendChild(pop);
  pop.querySelector("[data-close]").onclick = () => pop.remove();
  pop.querySelector("[data-go]")?.addEventListener("click", () => { pop.remove(); location.hash = "#settings"; setTimeout(() => document.getElementById("s-usage")?.scrollIntoView(), 400); });
  setTimeout(() => document.addEventListener("click", function off(e) { if (!pop.contains(e.target) && e.target !== $("usage-chip")) { pop.remove(); document.removeEventListener("click", off); } }), 0);
}
$("usage-chip").onclick = usagePopover;

/* ---------------------------------------------------------------- graph + diagram exports */
$("btn-graph-png").onclick = () => {
  if (!cy.elements().length) { notify("The graph is empty.", true); return; }
  const bg = getComputedStyle(document.body).backgroundColor;
  downloadBlob(`${safeName($("graph-title").textContent)}.png`, cy.png({ full: true, scale: 2, bg, output: "blob" }));
};

function sizedSvg(text) {
  // Mermaid sizes SVGs with width="100%"; files and canvases need real pixel dimensions.
  const doc = new DOMParser().parseFromString(text, "image/svg+xml");
  const el = doc.documentElement;
  const vb = (el.getAttribute("viewBox") || "").split(/[\s,]+/).map(Number);
  if (vb.length === 4 && vb[2] > 0 && vb[3] > 0) {
    el.setAttribute("width", Math.ceil(vb[2]));
    el.setAttribute("height", Math.ceil(vb[3]));
    el.style.removeProperty("max-width");
  }
  el.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  return new XMLSerializer().serializeToString(el);
}
async function diagramSvgText(svg) {
  return sizedSvg(await rawDiagramSvg(svg));
}
async function rawDiagramSvg(svg) {
  // Re-render without HTML labels so the SVG is self-contained (and can be drawn onto a canvas for PNG).
  const source = svg.closest("[data-mmd]")?.dataset.mmd || $("viewer-body").dataset.mmd;
  if (source) {
    try {
      const code = `%%{init: {"htmlLabels": false, "flowchart": {"htmlLabels": false}} }%%\n${source}`;
      const { svg: text } = await mermaid.render(`exp${Date.now()}`, code);
      return text;
    } catch { /* fall back to the on-screen SVG */ }
  }
  const clone = svg.cloneNode(true);
  clone.querySelectorAll(".flow-dot").forEach(d => d.remove());
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  return clone.outerHTML;
}
$("viewer-svg").onclick = async () => {
  const svg = $("viewer-body").querySelector("svg");
  if (!svg) return;
  downloadText(`${safeName($("viewer-title").textContent)}.svg`, await diagramSvgText(svg), "image/svg+xml");
};
$("viewer-png").onclick = async () => {
  const svg = $("viewer-body").querySelector("svg");
  if (!svg) return;
  const text = await diagramSvgText(svg);
  const img = new Image();
  img.onload = () => {
    const w = img.naturalWidth || svg.getBoundingClientRect().width || 1200, h = img.naturalHeight || svg.getBoundingClientRect().height || 800;
    const scale = Math.max(0.25, Math.min(2, 16000 / Math.max(w, h)));  // browsers cap canvas sides at ~16k px
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(w * scale); canvas.height = Math.round(h * scale);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.scale(scale, scale); ctx.drawImage(img, 0, 0, w, h);
    try { canvas.toBlob(b => b ? downloadBlob(`${safeName($("viewer-title").textContent)}.png`, b) : notify("PNG export failed; use SVG.", true)); }
    catch { notify("This diagram can't be converted to PNG in the browser; use SVG.", true); }
  };
  img.onerror = () => notify("PNG export failed; use SVG.", true);
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(text);
};
// diagrams in the catalog detail open in the viewer (with its export buttons)
$("browse-detail").addEventListener("click", e => {
  const svg = e.target.closest(".canvas")?.querySelector("svg");
  if (svg) openViewer($("browse-detail").querySelector("h2")?.textContent || "Diagram", svg);
});

/* ---------------------------------------------------------------- catalog detail additions */
const reviewKeyFor = uuid => uuid.startsWith("u-") ? (() => { const [, sid, ...rest] = uuid.split("-"); return `u:${sid}:${rest.join("-")}`; })() : `g:${uuid}`;

window.afterCatalogDetail = async (kind, id, panel) => {
  const actions = panel.querySelector(".actions");
  if (!actions) return;
  const add = (html, fn) => { const t = document.createElement("template"); t.innerHTML = html.trim(); const b = t.content.firstChild; b.onclick = fn; actions.appendChild(b); return b; };
  add(`<button type="button" title="Copy a link to this page">🔗 Copy link</button>`, () => copyLink(location.hash));
  if (["playbooks", "collections", "packs"].includes(kind) && hasRole("analyst")) {
    const docKind = { playbooks: "playbook", collections: "collection", packs: "pack" }[kind];
    add(`<button type="button" title="Documentation in the FortiSOAR playbook-docs format (with or without AI)">📄 Document</button>`, async () => {
      let target = id;
      if (kind === "playbooks") target = (await api(`/playbooks/${enc(id)}`).catch(() => ({}))).uuid || id;
      window.openDocDialog(docKind, target, panel.querySelector("h2")?.textContent || id);
    });
  }
  if (kind === "playbooks") {
    const p = await api(`/playbooks/${enc(id)}`).catch(() => null);
    if (!p?.uuid) return;
    add(`<button type="button" title="Download Markdown documentation (overview, connectors, diagrams, steps, version history)">⤓ Doc (.md)</button>`,
        () => { location.href = `/playbooks/${enc(p.uuid)}/export.md`; });
    add(`<button type="button" title="Open a print-ready page; use Print → Save as PDF">⎙ Print / PDF</button>`,
        () => window.open(`/playbooks/${enc(p.uuid)}/export.html`, "_blank", "noopener"));
    const key = reviewKeyFor(p.uuid);
    add(`<button type="button" title="Best-practice review and score">✓ Review</button>`, () => { location.hash = `#review/reviews/${encKey(key)}`; });
    const slot = document.createElement("div");
    slot.className = "review-slot";
    panel.querySelector("[data-slot=diagram]").before(slot);
    try {
      const r = await api(`/review/playbooks/${encKey(key)}`);
      slot.innerHTML = `<div class="scorehead" style="margin:8px 0">${gradeBadge(r.grade)} <b>${r.score}/100</b> ${verdictPill(r.verdict)}
        <span class="muted">${Object.entries(r.counts).filter(([, n]) => n).map(([s, n]) => `${n} ${s}`).join(" · ") || "no findings"}</span>
        <a href="#review/reviews/${encKey(key)}">full review →</a></div>`;
    } catch { /* not reviewable (e.g. not on disk) */ }
    const ex = document.createElement("div");
    ex.className = "explain-slot";
    slot.after(ex);
    renderExplain(p.uuid, ex);
  } else if (kind === "packs") {
    add(`<button type="button">⤓ Doc (.md)</button>`, () => { location.href = `/packs/${enc(id)}/export.md`; });
    add(`<button type="button">⎙ Print / PDF</button>`, () => window.open(`/packs/${enc(id)}/export.html`, "_blank", "noopener"));
  } else if (kind === "connectors") {
    add(`<button type="button" title="Everything that depends on this connector">Impact analysis →</button>`, () => { location.hash = `#insights/impact/${enc(id)}`; });
  }
};

async function renderExplain(uuid, slot) {
  const cached = await api(`/playbooks/${enc(uuid)}/explain`).catch(() => ({ text: null }));
  if (cached.text) {
    slot.innerHTML = `<h3>AI explanation</h3><div class="md">${md(cached.text)}</div>
      <div class="sub">${esc(cached.model || "")} · ${esc(fmtWhen(cached.at))}${cached.by ? ` · requested by ${esc(cached.by)}` : ""}</div>`;
    return;
  }
  if (!hasRole("analyst")) return;
  slot.innerHTML = `<button type="button" class="explain-btn" title="Uses 1 AI answer request (free if cached)">✨ Explain this playbook (AI)</button>`;
  slot.querySelector("button").onclick = async () => {
    if (!(await confirmAI("Explaining this playbook"))) return;
    slot.innerHTML = `<div class="muted">Asking the model…</div>`;
    try { await post(`/playbooks/${enc(uuid)}/explain`); renderExplain(uuid, slot); }
    catch (err) { slot.innerHTML = `<div class="error">${esc(err.message)}</div>`; }
    refreshUsage();
  };
}

/* ---------------------------------------------------------------- chat: filters, saved searches, synonyms, share links */
const FILTER_LABELS = { pack: "Any pack", connector: "Any connector", trigger: "Any trigger", module: "Any module", origin: "GitHub + uploads" };
let filterOptions = null;
async function renderFilters() {
  const box = $("chat-filters");
  box.hidden = !$("graph-only").checked;
  if (box.hidden || box.dataset.ready) return;
  box.dataset.ready = "1";
  try { filterOptions = await api("/search/filters"); } catch { filterOptions = {}; }
  box.innerHTML = Object.keys(FILTER_LABELS).map(k => `<select data-filter="${k}" aria-label="${FILTER_LABELS[k]}">
      <option value="">${FILTER_LABELS[k]}</option>${(filterOptions[k] || []).map(v => `<option value="${esc(v)}">${esc(k === "origin" ? (v === "upload" ? "Your uploads" : "Fortinet GitHub") : v.replace(/_/g, " "))}</option>`).join("")}</select>`).join("")
    + `<button type="button" id="btn-save-search" data-min-role="analyst" title="Save this search to re-run it later">☆ Save</button>
       <button type="button" id="btn-share-search" title="Copy a link that runs this search">🔗 Link</button>
       <button type="button" id="btn-synonyms" title="Words that count as the same in graph-only search">Synonyms</button>`;
  $("btn-save-search").onclick = saveSearch;
  $("btn-share-search").onclick = () => {
    const q = $("question").value.trim();
    if (!q && !Object.keys(window.chatFilters()).length) { notify("Type a search or pick a filter first.", true); return; }
    copyLink(`#search/${enc(q || "-")}/${enc(JSON.stringify(window.chatFilters()))}`);
  };
  $("btn-synonyms").onclick = editSynonyms;
}
window.chatFilters = () => {
  const out = {};
  $("chat-filters").querySelectorAll("select[data-filter]").forEach(s => { if (s.value) out[s.dataset.filter] = s.value; });
  return out;
};
function setFilters(filters = {}) {
  $("chat-filters").querySelectorAll("select[data-filter]").forEach(s => {
    const v = filters[s.dataset.filter] || "";
    if (v && ![...s.options].some(o => o.value === v)) s.add(new Option(v, v));
    s.value = v;
  });
}
$("graph-only").addEventListener("change", renderFilters);

async function saveSearch() {
  const q = $("question").value.trim() || sessions.find(s => s.id === currentId)?.title || "";
  if (!q) { notify("Type the search first.", true); return; }
  const name = prompt("Name this saved search", q.slice(0, 60));
  if (!name?.trim()) return;
  try { await post("/search/saved", { name: name.trim(), query: q, filters: window.chatFilters() }); notify("Search saved."); loadSavedSearches(); }
  catch (err) { notify(err.message, true); }
}

async function loadSavedSearches() {
  const box = $("saved-searches");
  let items = [];
  try { items = await api("/search/saved"); } catch { box.innerHTML = ""; return; }
  box.innerHTML = `<h3><span>Saved searches</span><span title="Graph-only, no AI calls">0 API</span></h3>` + (items.length ? `<ul>${items.map(s => {
      const delta = s.previous_count != null && s.last_count != null ? s.last_count - s.previous_count : 0;
      return `<li data-id="${esc(s.id)}" title="${esc(s.query)}${Object.keys(s.filters || {}).length ? " · " + esc(Object.entries(s.filters).map(([k, v]) => `${k}=${v}`).join(", ")) : ""}">
        <span>☆ ${esc(s.name)}</span><span class="n">${s.last_count ?? ""}${delta ? ` (${delta > 0 ? "+" : ""}${delta})` : ""}</span>
        <button class="del" data-min-role="analyst" aria-label="Delete saved search">✕</button></li>`;
    }).join("")}</ul>` : `<div class="muted" style="padding:0 6px">Save a graph-only search to re-run it after each ingest.</div>`);
  box.querySelectorAll("li[data-id]").forEach(li => {
    li.onclick = e => { if (!e.target.classList.contains("del")) runSavedSearch(items.find(s => s.id === li.dataset.id)); };
    li.querySelector(".del").onclick = async () => {
      if (!confirm("Delete this saved search?")) return;
      await api(`/search/saved/${li.dataset.id}`, { method: "DELETE" }).catch(err => notify(err.message, true));
      loadSavedSearches();
    };
  });
}

async function runGraphSearch(query, filters) {
  if (busy) return;
  $("graph-only").checked = true;
  $("graph-only").dispatchEvent(new Event("change"));
  await renderFilters();
  setFilters(filters);
  currentId = null; store.set("graphrag.chat", null); renderSessions(); showEmpty();
  $("question").value = query === "-" ? "" : query;
  if (!$("question").value && Object.keys(filters || {}).length) $("question").value = "playbooks";
  await send();
}
async function runSavedSearch(s) {
  try {
    const res = await post(`/search/saved/${s.id}/run`);
    const prev = res.search.previous_count;
    if (prev != null && prev !== res.search.last_count) notify(`${s.name}: ${res.search.last_count} playbooks (was ${prev}).`);
  } catch { /* counting is optional */ }
  await runGraphSearch(s.query, s.filters || {});
  loadSavedSearches();
}
window.runSharedSearch = async (q, filtersJson) => {
  let filters = {};
  try { filters = filtersJson ? JSON.parse(filtersJson) : {}; } catch { /* ignore bad links */ }
  history.replaceState(null, "", "#chat");
  await showView("chat");
  runGraphSearch(q || "-", filters);
};
window.openSharedChat = async id => {
  if (!sessions.length) await loadSessions().catch(() => {});
  if (sessions.some(s => s.id === id)) { openSession(id); history.replaceState(null, "", "#chat"); }
  else notify("That chat doesn't exist (or was deleted).", true);
};
// copy-link button for the open chat
(() => {
  const b = document.createElement("button");
  b.type = "button"; b.title = "Copy a link to this chat"; b.textContent = "🔗"; b.style.marginLeft = "6px";
  b.onclick = () => currentId ? copyLink(`#chat/${currentId}`) : notify("Open or start a chat first.", true);
  $("btn-new").after(b);
})();

async function editSynonyms() {
  const data = await api("/search/synonyms").catch(err => { notify(err.message, true); return null; });
  if (!data) return;
  const dlg = document.createElement("div");
  dlg.className = "usage-pop";
  dlg.style.cssText = "top:80px;right:auto;left:50%;transform:translateX(-50%);width:min(560px,calc(100vw - 32px))";
  const lines = Object.entries(data.synonyms).map(([k, v]) => `${k}: ${v.join(", ")}`).join("\n");
  const canEdit = hasRole("analyst");
  dlg.innerHTML = `<h3>Search synonyms</h3>
    <div class="muted">One per line: <code>term: word, word, …</code>. In graph-only search a term also matches its synonyms. Phrases (e.g. <code>lock out</code>) work too.</div>
    <textarea style="width:100%;height:300px;margin-top:8px;font:12px ui-monospace,Menlo,monospace;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)" ${canEdit ? "" : "readonly"}>${esc(lines)}</textarea>
    <div class="btnrow" style="margin-top:8px">${canEdit ? `<button type="button" class="primary" data-save>Save</button><button type="button" data-reset>Reset to defaults</button>` : ""}<span style="flex:1"></span><button type="button" data-close>Close</button></div>`;
  document.body.appendChild(dlg);
  dlg.querySelector("[data-close]").onclick = () => dlg.remove();
  dlg.querySelector("[data-save]")?.addEventListener("click", async () => {
    const map = {};
    dlg.querySelector("textarea").value.split("\n").forEach(line => {
      const i = line.indexOf(":");
      if (i > 0) map[line.slice(0, i).trim()] = line.slice(i + 1).split(",").map(x => x.trim()).filter(Boolean);
    });
    try { await post("/search/synonyms", { synonyms: map }, "PUT"); notify("Synonyms saved."); dlg.remove(); }
    catch (err) { notify(err.message, true); }
  });
  dlg.querySelector("[data-reset]")?.addEventListener("click", async () => {
    if (!confirm("Replace your synonyms with the defaults?")) return;
    await post("/search/synonyms", { reset: true }, "PUT").catch(err => notify(err.message, true));
    dlg.remove(); editSynonyms();
  });
}

/* ---------------------------------------------------------------- GitHub page: embedding planner + sync */
const origLoadGithub = loadGithub;
loadGithub = async (...args) => { await origLoadGithub(...args); renderPlanner(); };
async function renderPlanner() {
  let box = document.getElementById("gh-planner");
  if (!box) {
    box = document.createElement("details");
    box.id = "gh-planner";
    box.className = "planner";
    box.style.cssText = "margin:0 0 10px;font-size:13px";
    $("gh-cards").after(box);
  }
  const open = box.open;
  let plan, sync;
  try { [plan, sync] = await Promise.all([api("/embeddings/plan"), api("/sync/status")]); }
  catch (err) { box.innerHTML = `<summary>Embedding planner</summary><div class="error">${esc(err.message)}</div>`; return; }
  const t = plan.today;
  box.innerHTML = `<summary><b>Embedding planner</b> · ${num(plan.total_pending)} pending · ${t.local ? "local model" : t.limit ? `${t.remaining ?? 0} of ${t.limit} left today` : "no daily limit"}
      ${plan.days_needed ? ` · ≈ ${plan.days_needed} day(s) at the daily limit` : ""} · sync: ${esc(sync.schedule)}${sync.last_run ? `, last ${esc(agoIso(sync.last_run))}` : ""}</summary>
    <div style="padding:8px 0">
      <table class="grid"><thead><tr><th>Order</th><th>What</th><th class="num">Pending</th><th class="num">Cumulative</th><th>Done by</th></tr></thead>
      <tbody>${plan.groups.map((g, i) => `<tr><td>${i + 1}</td><td>${esc(g.group)}</td><td class="num">${num(g.pending)}</td><td class="num">${num(g.cumulative)}</td>
        <td class="day">${g.done_by_day ? (g.done_by_day === 1 ? "today" : `day ${g.done_by_day}`) : "–"}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">Nothing pending.</td></tr>`}</tbody></table>
      ${(() => { const tt = window.ghData?.totals || (typeof ghData !== "undefined" ? ghData?.totals : null);
                  if (!tt) return "";
                  const pbs = tt.playbooks - tt.playbooks_loaded, docs = tt.docs - tt.docs_loaded;
                  return pbs || docs ? `<div class="sub" style="margin-top:6px">The planner counts items already in the graph. Not loaded yet: ${num(pbs)} playbooks and ${num(docs)} docs
                    on disk; load them with <b>Ingest</b> in the table (a 0 cap loads them without embedding).</div>` : ""; })()}
      <div class="btnrow" style="margin-top:8px">
        <span class="muted">Embedding makes items findable by meaning (semantic search). Graph-only search already covers them.</span>
        <span style="flex:1"></span>
        <button type="button" data-min-role="admin" id="btn-embed-today" ${plan.suggested_batch ? "" : "disabled"}>Embed today's batch (${num(plan.suggested_batch)})</button>
        <button type="button" data-min-role="admin" id="btn-sync-now" title="Re-download changed repos from GitHub (no AI calls)">⟳ Sync now</button>
      </div>
      ${sync.last_result ? `<div class="sub" style="margin-top:6px">Last sync: ${esc(JSON.stringify(sync.last_result).slice(0, 300))}</div>` : ""}
    </div>`;
  box.open = open;
  document.getElementById("btn-embed-today")?.addEventListener("click", async () => {
    const n = plan.suggested_batch;
    if (!confirm(`Embed the ${num(n)} highest-priority pending items now?\n\nWhy: makes them findable by meaning in AI chat answers (keyword search already finds them).\nCost: up to ${num(n)} embedding calls from today's quota (${t.remaining ?? "no"} left). Nothing else is changed.`)) return;
    try { const job = await post("/admin/jobs", { operation: "embed_today", options: { limit: n } }); showJob(job.id, "gh-job", () => { loadGithub(true); refreshUsage(); }); }
    catch (err) { notify(err.message, true); }
  });
  document.getElementById("btn-sync-now")?.addEventListener("click", async () => {
    if (!confirm("Re-download changed repos from GitHub now? (GitHub only, no AI calls. Re-ingest and Git-source sync follow your Settings.)")) return;
    try { const job = await post("/admin/jobs", { operation: "scheduled_sync" }); showJob(job.id, "gh-job", () => loadGithub(true)); }
    catch (err) { notify(err.message, true); }
  });
}

/* ---------------------------------------------------------------- settings: users, audit, usage, sync */
window.afterSettings = () => {
  const nav = $("settings-nav");
  if (!nav.querySelector("[data-platform]")) {
    nav.insertAdjacentHTML("beforeend", `<h2 data-platform>Access &amp; usage</h2>
      <button type="button" data-target="s-users" data-super>Users &amp; roles</button><button type="button" data-target="s-audit">Audit log</button>
      <button type="button" data-target="s-usage" data-super>Usage &amp; caches</button><button type="button" data-target="s-workspaces">Workspaces</button>
      <button type="button" data-target="s-sync" data-super>Scheduled sync</button>`);
    nav.querySelectorAll("button[data-target]").forEach(b => b.onclick = () => document.getElementById(b.dataset.target)?.scrollIntoView({ behavior: "smooth" }));
  }
  if (!document.getElementById("s-users")) {
    $("settings").insertAdjacentHTML("beforeend", `
      <div data-super>
      <h2 id="s-users">Users &amp; roles</h2>
      <p class="lead">Each person gets a role <b>per company</b>: <b>admin</b> (that company's settings, operations and audit) · <b>analyst</b> (AI answers, uploads, reviews,
        best practices, designs) · <b>viewer</b> (read-only, graph-only chat) · or no access. <b>Super-admins</b> have every role everywhere and manage General
        settings, users, logs and caches. The .env account is always a super-admin.</p>
      <div id="users-box"><div class="muted">Loading…</div></div>
      </div>
      <h2 id="s-audit">Audit log</h2>
      <p class="lead">Who signed in, changed settings, ran jobs, uploaded or reviewed playbooks, used the AI. Values of settings and secrets are never recorded.</p>
      <div class="btnrow"><input id="audit-user" placeholder="user" style="width:120px"><input id="audit-action" placeholder="action (e.g. job, review, auth)" style="width:200px">
        <input id="audit-q" placeholder="text" style="flex:1;min-width:120px"><button type="button" id="audit-refresh">Refresh</button><button type="button" id="audit-csv">⤓ CSV</button></div>
      <div id="audit-box" style="max-height:420px;overflow:auto;margin-top:8px"></div>
      <div data-super>
      <h2 id="s-usage">Usage &amp; caches</h2>
      <p class="lead">Daily limits are set above (AI models: <i>Answer requests per day</i>, <i>Embeddings per day</i>, <i>Stop at the daily limits</i>).</p>
      <div id="usage-box"></div>
      </div>
      <h2 id="s-workspaces">Workspaces</h2>
      <p class="lead">Each workspace has its own Neo4j database and data folder. Users, the audit log, usage limits and the AI caches are shared.
        Switch with the selector in the header (your choice is remembered in this browser).</p>
      <div id="ws-box"><div class="muted">Loading…</div></div>
      <div data-super>
      <h2 id="s-sync">Scheduled sync</h2>
      <p class="lead">Set <i>Scheduled sync</i> under Data sources. The sync only talks to GitHub (and your connected Git repos), never to the AI model.</p>
      <div id="sync-box"></div>
      </div>`);
    ["audit-user", "audit-action", "audit-q"].forEach(id => $(id).addEventListener("keydown", e => { if (e.key === "Enter") loadAudit(); }));
    $("audit-refresh").onclick = loadAudit;
    $("audit-csv").onclick = () => downloadCSV("audit-log", [["at", "Time"], ["user", "User"], ["ws", "Workspace"], ["action", "Action"], ["target", "Target"], ["details", "Details"]], window.AUDIT || []);
  }
  if (window.ME?.superadmin) { loadUsers(); loadUsageBox(); loadSyncBox(); }
  loadAudit(); loadWorkspacesBox();
};

async function loadUsers() {
  const box = $("users-box");
  let data;
  try { data = await api("/admin/users"); } catch (err) { box.innerHTML = `<div class="error">${esc(err.message)}</div>`; return; }
  const W = data.workspaces;
  const roleSel = (attr, user, ws, value) => `<select ${attr}="${esc(user)}" data-ws="${esc(ws)}">${["none", ...data.roles].map(r =>
    `<option value="${r}" ${r === (value || "none") ? "selected" : ""}>${r === "none" ? "— no access" : r}</option>`).join("")}</select>`;
  box.innerHTML = `<table class="grid"><thead><tr><th>User</th><th>Super-admin</th>${W.map(w => `<th>${esc(w.label)}</th>`).join("")}<th>Status</th><th>Last sign-in</th><th></th></tr></thead><tbody>
    <tr><td><b>${esc(data.builtin_admin)}</b> <span class="sub">(.env)</span></td><td>✓</td>${W.map(() => `<td>admin</td>`).join("")}<td>active</td><td>–</td><td></td></tr>
    ${data.users.map(u => `<tr><td><b>${esc(u.username)}</b><div class="sub">by ${esc(u.created_by || "–")} · ${esc(fmtWhen(u.created))}</div></td>
      <td><input type="checkbox" data-super-of="${esc(u.username)}" ${u.superadmin ? "checked" : ""} aria-label="Super-admin"></td>
      ${W.map(w => `<td>${u.superadmin ? `<span class="sub">admin</span>` : roleSel("data-role-of", u.username, w.id, u.access[w.id])}</td>`).join("")}
      <td>${u.disabled ? `<span class="pill warn">disabled</span>` : "active"}</td><td>${u.last_login ? esc(fmtWhen(u.last_login)) : "never"}</td>
      <td><div class="btnrow"><button type="button" data-pw="${esc(u.username)}">Reset password</button>
        <button type="button" data-toggle="${esc(u.username)}" data-disabled="${u.disabled}">${u.disabled ? "Enable" : "Disable"}</button>
        <button type="button" data-del="${esc(u.username)}">Delete</button></div></td></tr>`).join("")}</tbody></table>
    <h3 style="font-size:13px;margin:14px 0 6px">Add a user</h3>
    <div class="form-grid" style="max-width:860px"><label>Username<input id="nu-name" autocomplete="off"></label>
      <label>Password (min 8)<input id="nu-pw" type="password" autocomplete="new-password"></label>
      ${W.map(w => `<label>${esc(w.label)} role${roleSel("data-new-role", "_new", w.id, "none")}</label>`).join("")}
      <label><span><input type="checkbox" id="nu-super"> super-admin (everything)</span></label>
      <label style="justify-content:flex-end"><button type="button" class="primary" id="nu-add">Add user</button></label></div>
    <div class="muted" style="margin-top:6px">Share the password with the person directly. A user only sees the companies they have a role in.</div>`;
  const patch = async (name, body, msg) => {
    try { await post(`/admin/users/${enc(name)}`, { username: name, ...body }, "PATCH"); notify(msg); } catch (err) { notify(err.message, true); }
    loadUsers();
  };
  box.querySelectorAll("select[data-role-of]").forEach(sel => sel.onchange = () => {
    const name = sel.dataset.roleOf, access = {};
    box.querySelectorAll(`select[data-role-of="${CSS.escape(name)}"]`).forEach(x => { access[x.dataset.ws] = x.value; });
    patch(name, { access }, "Access updated.");
  });
  box.querySelectorAll("[data-super-of]").forEach(cb => cb.onchange = () => {
    if (!cb.checked || confirm(`Make ${cb.dataset.superOf} a super-admin (all companies, users, General settings)?`)) patch(cb.dataset.superOf, { superadmin: cb.checked }, "Updated.");
    else cb.checked = false;
  });
  box.querySelectorAll("[data-pw]").forEach(b => b.onclick = () => {
    const pw = prompt(`New password for ${b.dataset.pw} (min 8 characters)`);
    if (pw) patch(b.dataset.pw, { password: pw }, "Password changed.");
  });
  box.querySelectorAll("[data-toggle]").forEach(b => b.onclick = () => patch(b.dataset.toggle, { disabled: b.dataset.disabled !== "true" }, "Updated."));
  box.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    if (!confirm(`Delete user ${b.dataset.del}?`)) return;
    try { await api(`/admin/users/${enc(b.dataset.del)}`, { method: "DELETE" }); loadUsers(); } catch (err) { notify(err.message, true); }
  });
  $("nu-add").onclick = async () => {
    const access = {};
    box.querySelectorAll("select[data-new-role]").forEach(x => { access[x.dataset.ws] = x.value; });
    try {
      await post("/admin/users", { username: $("nu-name").value.trim(), password: $("nu-pw").value, access, superadmin: $("nu-super").checked });
      notify("User added."); loadUsers();
    } catch (err) { notify(err.message, true); }
  };
}

async function loadAudit() {
  const box = $("audit-box");
  const qs = new URLSearchParams({ limit: 300, user: $("audit-user").value.trim(), action: $("audit-action").value.trim(), q: $("audit-q").value.trim() });
  try { window.AUDIT = (await api(`/admin/audit?${qs}`)).entries; } catch (err) { box.innerHTML = `<div class="error">${esc(err.message)}</div>`; return; }
  box.innerHTML = `<table class="grid"><thead><tr><th>Time</th><th>User</th><th>Workspace</th><th>Action</th><th>Target</th><th>Details</th></tr></thead><tbody>
    ${window.AUDIT.map(e => `<tr><td>${esc(fmtWhen(e.at))}</td><td>${esc(e.user)}</td><td>${esc(e.ws || "fortinet")}</td><td><code>${esc(e.action)}</code></td><td>${esc(e.target || "")}</td>
      <td class="sub">${esc(e.details ? Object.entries(e.details).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`).join(" · ") : "")}</td></tr>`).join("")
    || `<tr><td colspan="6" class="muted">No entries yet.</td></tr>`}</tbody></table>`;
}

async function loadUsageBox() {
  const box = $("usage-box");
  let u;
  try { u = await api("/usage?days=14"); } catch (err) { box.innerHTML = `<div class="error">${esc(err.message)}</div>`; return; }
  const days = Object.entries(u.history || {}).reverse();
  box.innerHTML = `<table class="grid" style="max-width:640px"><thead><tr><th>Day</th><th class="num">Answer requests</th><th class="num">Cached answers</th><th class="num">Embeddings</th><th class="num">Cached embeddings</th></tr></thead>
    <tbody>${days.map(([d, c]) => `<tr><td>${esc(d)}</td><td class="num">${c.llm || 0}</td><td class="num">${c.llm_cached || 0}</td><td class="num">${num(c.embed || 0)}</td><td class="num">${num(c.embed_cached || 0)}</td></tr>`).join("")
      || `<tr><td colspan="5" class="muted">No model calls recorded yet.</td></tr>`}</tbody></table>
    <div class="btnrow" style="margin-top:8px"><button type="button" id="clear-answer-cache">Clear answer cache</button>
      <span class="muted">Cached answers are reused for identical questions over identical context. Clearing makes the next ones use quota again.</span></div>`;
  $("clear-answer-cache").onclick = async () => {
    if (!confirm("Clear all cached AI answers?")) return;
    try { const r = await post("/admin/cache/clear-answers"); notify(`Removed ${r.removed} cached answers.`); } catch (err) { notify(err.message, true); }
  };
}

async function loadSyncBox() {
  const box = $("sync-box");
  try {
    const s = await api("/sync/status");
    box.innerHTML = `<dl class="kv"><dt>Schedule</dt><dd>${esc(s.schedule)}</dd><dt>Re-ingest changed repos</dt><dd>${s.auto_ingest ? "yes (0 new embeddings)" : "no"}</dd>
      <dt>Sync Git sources</dt><dd>${s.sources ? "yes" : "no"}</dd><dt>Last run</dt><dd>${when(s.last_run)}</dd><dt>Next due</dt><dd>${s.next_due ? esc(fmtWhen(s.next_due)) : "–"}</dd>
      <dt>Last result</dt><dd class="sub">${s.last_result ? esc(JSON.stringify(s.last_result)) : "–"}</dd></dl>
      <div class="btnrow" style="margin-top:8px"><button type="button" id="sync-now-settings">⟳ Sync now</button></div>`;
    $("sync-now-settings").onclick = async () => {
      if (!confirm("Run the sync now? (GitHub only, no AI calls.)")) return;
      try { const job = await post("/admin/jobs", { operation: "scheduled_sync" }); showJob(job.id, "job", loadSyncBox); document.getElementById("s-ops")?.scrollIntoView(); }
      catch (err) { notify(err.message, true); }
    };
  } catch (err) { box.innerHTML = `<div class="error">${esc(err.message)}</div>`; }
}

/* ---------------------------------------------------------------- workspaces */
async function initWorkspaces(me) {
  const ws = me.workspace || { id: "fortinet", features: ["github"] };
  window.WORKSPACE = ws;
  document.documentElement.style.setProperty("--ws-color", ws.color || "var(--accent)");
  document.body.dataset.workspace = ws.id;
  if ((ws.features || []).includes("github")) document.body.dataset.github = "1"; else delete document.body.dataset.github;
  document.querySelectorAll('.nav a[data-view="github"]').forEach(a => { if (!(ws.features || []).includes("github")) a.hidden = true; });
  const sel = $("ws-select");
  try {
    const list = await api("/workspaces");
    sel.innerHTML = list.workspaces.map(w => `<option value="${esc(w.id)}" ${w.id === ws.id ? "selected" : ""} title="${esc(w.description)}">${esc(w.label)}</option>`).join("");
  } catch { sel.innerHTML = `<option>${esc(ws.label || ws.id)}</option>`; }
  sel.onchange = () => {
    document.cookie = `graphrag_ws=${encodeURIComponent(sel.value)}; path=/; max-age=31536000; samesite=lax`;
    try { localStorage.removeItem("graphrag.chat"); } catch { /* per-workspace chats */ }
    const view = location.hash.replace(/^#/, "").split("/")[0];
    location.hash = ["home", "browse", "chat", "review", "insights", "develop", "guide", "settings"].includes(view) ? `#${view}` : "#home";
    location.reload();
  };
  if (location.hash.startsWith("#github") && !(ws.features || []).includes("github")) location.hash = "#home";
}

/* rule import (Review → Best practices) */
window.openRuleImport = async onDone => {
  const list = (await api("/workspaces")).workspaces.filter(w => w.id !== window.WORKSPACE?.id);
  if (!list.length) { notify("There is no other workspace to import from.", true); return; }
  const dlg = document.createElement("div");
  dlg.className = "usage-pop";
  dlg.style.cssText = "top:70px;right:auto;left:50%;transform:translateX(-50%);width:min(680px,calc(100vw - 32px));max-height:80vh;overflow:auto";
  dlg.innerHTML = `<h3>Import best practices</h3>
    <div class="muted">Copies selected rules (and built-in rule settings) from another workspace. Nothing else is shared. Re-importing updates rules instead of duplicating them.</div>
    <div class="btnrow" style="margin:8px 0">From <select id="ri-from">${list.map(w => `<option value="${esc(w.id)}">${esc(w.label)}</option>`).join("")}</select></div>
    <div id="ri-body" class="muted">Loading…</div>
    <div class="btnrow" style="margin-top:10px"><button type="button" class="primary" id="ri-go">Import selected</button><span style="flex:1"></span><button type="button" id="ri-close">Close</button></div>`;
  document.body.appendChild(dlg);
  const load = async () => {
    const p = await api(`/review/rules/import-preview?source=${enc($("ri-from").value)}`).catch(err => ({ error: err.message }));
    if (p.error) { $("ri-body").innerHTML = `<div class="error">${esc(p.error)}</div>`; return; }
    const pill = st => `<span class="pill ${st === "new" ? "ok" : st === "changed" ? "warn" : ""}">${st}</span>`;
    $("ri-body").innerHTML = (p.custom.length ? `<h3 style="margin-top:8px">Custom rules (${p.custom.length})</h3>` + p.custom.map(r => `<label style="display:flex;gap:8px;align-items:flex-start;margin:4px 0">
        <input type="checkbox" data-rule="${esc(r.id)}" ${r.import_status !== "same" ? "checked" : ""}>
        <span>${pill(r.import_status)} <b>${esc(r.title)}</b> ${sevPill(r.severity)} <span class="sub">${esc(r.category)} · ${esc(r.check)}</span></span></label>`).join("") : `<div class="muted">No custom rules there.</div>`)
      + (p.overrides.length ? `<h3 style="margin-top:8px">Built-in rule settings (${p.overrides.length})</h3>` + p.overrides.map(o => `<label style="display:flex;gap:8px;margin:4px 0">
        <input type="checkbox" data-ov="${esc(o.id)}" ${o.import_status !== "same" ? "checked" : ""}>
        <span>${pill(o.import_status)} <code>${esc(o.id)}</code> ${esc(o.title)} <span class="sub">${esc(Object.entries(o.theirs).map(([k, v]) => `${k}: ${v}`).join(", "))}</span></span></label>`).join("") : "");
  };
  $("ri-from").onchange = load;
  $("ri-close").onclick = () => dlg.remove();
  $("ri-go").onclick = async () => {
    const rules = [...dlg.querySelectorAll("[data-rule]:checked")].map(x => x.dataset.rule);
    const overrides = [...dlg.querySelectorAll("[data-ov]:checked")].map(x => x.dataset.ov);
    if (!rules.length && !overrides.length) { notify("Select something to import.", true); return; }
    try {
      const r = await post("/review/rules/import", { source: $("ri-from").value, rules, overrides });
      notify(`Imported: ${r.added} new, ${r.updated} updated, ${r.overrides} built-in setting(s). Re-score to apply.`);
      dlg.remove(); onDone?.();
    } catch (err) { notify(err.message, true); }
  };
  load();
};

/* settings: workspaces */
async function loadWorkspacesBox() {
  const box = document.getElementById("ws-box");
  if (!box) return;
  try {
    const d = await api("/admin/workspaces");
    box.innerHTML = `<table class="grid"><thead><tr><th>Workspace</th><th>Database</th><th>Data folder</th><th>Contents</th></tr></thead><tbody>
      ${d.workspaces.map(w => `<tr><td><b>${esc(w.label)}</b>${w.id === window.WORKSPACE?.id ? ` <span class="pill ok">current</span>` : ""}<div class="sub">${esc(w.features.join(", ") || "no Fortinet features")}</div></td>
        <td><code>${esc(w.neo4j_uri)}</code><div class="sub">${w.ok ? "connected" : `<span class="error">${esc(w.error || "unreachable")}</span>`}</div></td>
        <td class="sub">${esc(w.data_dir)}</td>
        <td class="sub">${w.graph ? esc(Object.entries(w.graph).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join(" · ") || "empty") : "–"}</td></tr>`).join("")}</tbody></table>`;
  } catch (err) { box.innerHTML = `<div class="error">${esc(err.message)}</div>`; }
}

/* ---------------------------------------------------------------- boot */
window.initPlatform = me => {
  initWorkspaces(me);
  refreshUsage();
  clearInterval(usageTimer);
  usageTimer = setInterval(() => { if (!document.hidden) refreshUsage(); }, 30000);
  loadSavedSearches();
  renderFilters();
  if (me.superadmin) document.body.dataset.super = "1"; else delete document.body.dataset.super;
  if (me.role !== "admin") document.querySelectorAll('.nav a[data-view="settings"], .nav a[data-view="github"]').forEach(a => a.hidden = true);
  if (!me.can_use_ai) {
    const cb = $("graph-only");
    cb.checked = true; cb.disabled = true; cb.dispatchEvent(new Event("change"));
    cb.closest("label").title = "Your role uses graph-only search (no AI).";
  }
};
