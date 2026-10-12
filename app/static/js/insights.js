/* Insights page: graph analysis, no AI. Routes: #insights/<tab>[/<param>]
   tabs: impact (connector impact) · duplicates · gaps · chains · security · changes */
(() => {
  const TABS = { impact: "Connector impact", duplicates: "Duplicates", gaps: "Coverage gaps", chains: "Call chains", security: "Security", changes: "Changes" };
  const root = $("view-insights");
  const S = { tab: "impact", param: null, cache: {} };
  const pbLink = (uuid, name) => uuid ? `<a href="#browse/playbooks/${enc(uuid)}">${esc(name || uuid)}</a>` : esc(name || "");

  function frame() {
    if (root.dataset.ready) return;
    root.dataset.ready = "1";
    root.innerHTML = `<div class="page single"><div class="page-main" style="border-right:0">
      <div class="page-head"><h1>Insights</h1>
        <p class="lead">What the graph says about your automation estate: blast radius of connectors, duplicated playbooks, gaps, call chains,
          security findings and what changed. Computed from the graph and review results; no AI.</p></div>
      <nav class="tabs" id="in-tabs" aria-label="Insights sections"></nav>
      <div class="page-body" id="in-body"></div></div></div>`;
  }

  async function show(r) {
    frame();
    S.tab = TABS[r.kind] ? r.kind : "impact";
    S.param = r.id;
    $("in-tabs").innerHTML = Object.entries(TABS).map(([k, t]) => `<a href="#insights/${k}" aria-current="${k === S.tab ? "page" : "false"}">${t}</a>`).join("");
    const body = $("in-body");
    body.innerHTML = `<div class="empty-state">Analysing the graph…</div>`;
    try { await ({ impact, duplicates, gaps, chains, security, changes })[S.tab](body); }
    catch (err) { body.innerHTML = `<div class="error" style="padding:16px">${esc(err.message)}</div>`; }
  }

  const toolbar = (inner, csvId = "in-csv") => `<div class="toolbar">${inner}<span style="flex:1"></span>
    <button type="button" id="in-link" title="Copy a link to this view">🔗 Copy link</button><button type="button" id="${csvId}">⤓ CSV</button></div>`;
  const wireLink = () => { $("in-link").onclick = () => copyLink(location.hash || `#insights/${S.tab}`); };
  const cards = items => `<div class="cards" style="padding:10px 16px 0">${items.map(([v, l]) => `<div class="card"><b>${typeof v === "number" ? num(v) : esc(v)}</b><span>${esc(l)}</span></div>`).join("")}</div>`;
  const table = (cols, rows, empty = "Nothing here.") => `<table class="grid"><thead><tr>${cols.map(c => `<th class="${c[2] || ""}">${c[0]}</th>`).join("")}</tr></thead>
    <tbody>${rows.join("") || `<tr><td colspan="${cols.length}" class="muted">${empty}</td></tr>`}</tbody></table>`;

  /* ------------------------------------------------------------ connector impact */
  async function impact(body) {
    const list = S.cache.connectors ||= await api("/insights/connectors");
    body.innerHTML = toolbar(`<label class="muted" style="font-size:12px">Connector</label>
      <select id="in-con" style="min-width:260px">${list.filter(c => c.playbooks).map(c => `<option value="${esc(c.name)}">${esc(c.label || c.name)} (${c.playbooks})</option>`).join("")}
        <optgroup label="Not used by any playbook">${list.filter(c => !c.playbooks).map(c => `<option value="${esc(c.name)}">${esc(c.label || c.name)}</option>`).join("")}</optgroup></select>`)
      + `<div id="in-impact"></div>`;
    const sel = $("in-con");
    if (S.param && [...sel.options].some(o => o.value === S.param)) sel.value = S.param;
    sel.onchange = () => { location.hash = `#insights/impact/${enc(sel.value)}`; };
    wireLink();
    const name = sel.value;
    if (!name) { $("in-impact").innerHTML = `<div class="empty-state">No connectors in the graph yet.</div>`; return; }
    const d = await api(`/insights/connector/${enc(name)}`);
    const s = d.summary;
    $("in-csv").onclick = () => downloadCSV(`impact-${safeName(name)}`, [["name", "Playbook"], ["pack", "Pack"], ["trigger", "Trigger"], ["ops", "Operations"],
      ["steps", "Steps using it"], ["modules", "Trigger modules"], ["active", "Active"], ["secret", "Hard-coded secret"]], d.playbooks);
    $("in-impact").innerHTML = cards([[s.direct, "playbooks use it directly"], [s.indirect, "more call those (indirect)"], [s.packs, "solution packs / sources"],
        [s.uploads, "of yours"], [s.active, "active"], [s.with_secrets, "hard-code a credential in it"]])
      + `<div class="section"><h2>If ${esc(name)} breaks or its credentials expire…</h2>
        <p class="lead">${s.direct} playbook(s) fail at the steps that call it${s.indirect ? `, and ${s.indirect} parent playbook(s) that call those are affected too` : ""}.
          ${d.trigger_modules.length ? `They run on ${esc(d.trigger_modules.join(", "))} events.` : ""}
          ${d.acts_on.length ? ` They create / update / find ${esc(d.acts_on.map(a => a.module).join(", "))} records.` : ""}</p>
        <div class="btnrow">${d.operations.map(([op, n]) => `<span class="pill">${esc(op)} · ${n}</span>`).join("")}</div></div>
      <div class="section"><h2>Direct (${d.playbooks.length})</h2>${table([["Playbook"], ["Pack / source"], ["Trigger"], ["Operations"], ["Steps", "", "num"]],
        d.playbooks.map(p => `<tr><td>${pbLink(p.uuid, p.name)}${p.active === false ? ` <span class="pill">inactive</span>` : ""}${p.secret ? ` <span class="pill warn">⚠ secret</span>` : ""}</td>
          <td>${esc(p.pack || "–")}${p.pack_type === "upload" ? ` <span class="pill ok">yours</span>` : ""}</td><td>${esc((p.trigger || "").replace("_", " "))}<div class="sub">${esc((p.modules || []).join(", "))}</div></td>
          <td class="sub">${esc(p.ops.join(", "))}</td><td class="num">${p.steps}</td></tr>`))}</div>
      <div class="section"><h2>Indirect: playbooks that call the ones above (${d.indirect.length})</h2>${table([["Playbook"], ["Pack"], ["Hops", "", "num"], ["Via"]],
        d.indirect.map(p => `<tr><td>${pbLink(p.uuid, p.name)}</td><td>${esc(p.pack || "–")}</td><td class="num">${p.hops}</td><td class="sub">${esc((p.via || []).join(", "))}</td></tr>`),
        "No other playbooks call them.")}</div>`;
  }

  /* ------------------------------------------------------------ duplicates */
  async function duplicates(body) {
    const threshold = +(S.param || 0.85);
    const d = await api(`/insights/duplicates?threshold=${threshold}`);
    body.innerHTML = toolbar(`<label class="muted" style="font-size:12px">Near-duplicate similarity ≥
        <select id="in-th">${[0.95, 0.9, 0.85, 0.8, 0.7].map(t => `<option ${t === threshold ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      <span class="count">${num(d.playbooks_compared)} playbooks compared (3+ steps)</span>`)
      + cards([[d.summary.exact_groups, "groups of identical playbooks"], [d.summary.exact_playbooks, "playbooks in those groups"], [d.summary.near_pairs, "near-duplicate pairs"]])
      + `<div class="section"><h2>Identical structure</h2><p class="lead">Same steps, step types and connector operations. Often the same playbook shipped in several packs, or a copy that was never changed: candidates to consolidate.</p>
        ${table([["Playbooks"], ["Steps", "", "num"], ["Copies", "", "num"]], d.exact.slice(0, 150).map(g =>
          `<tr><td>${g.playbooks.map(p => `${pbLink(p.uuid, p.name)} <span class="sub">${esc(p.pack || "")}${p.pack_type === "upload" ? " (yours)" : ""}</span>`).join("<br>")}</td><td class="num">${g.steps}</td><td class="num">${g.size}</td></tr>`))}</div>
      <div class="section"><h2>Near duplicates</h2><p class="lead">Jaccard similarity of steps (type + operation + name). High similarity with a few differing steps usually means a forked copy.</p>
        ${table([["Similarity", "", "num"], ["Playbook A"], ["Playbook B"], ["Only in A", "", "num"], ["Only in B", "", "num"]], d.near.slice(0, 300).map(p =>
          `<tr><td class="num">${(p.similarity * 100).toFixed(0)}%</td><td>${pbLink(p.a.uuid, p.a.name)}<div class="sub">${esc(p.a.pack || "")}</div></td>
           <td>${pbLink(p.b.uuid, p.b.name)}<div class="sub">${esc(p.b.pack || "")}</div></td><td class="num">${p.only_a}</td><td class="num">${p.only_b}</td></tr>`))}</div>`;
    $("in-th").onchange = () => { location.hash = `#insights/duplicates/${$("in-th").value}`; };
    wireLink();
    $("in-csv").onclick = () => downloadCSV("near-duplicates", [["similarity", "Similarity"], ["a", "Playbook A", r => r.a.name], ["pa", "Pack A", r => r.a.pack],
      ["b", "Playbook B", r => r.b.name], ["pb", "Pack B", r => r.b.pack], ["only_a", "Only in A"], ["only_b", "Only in B"]], d.near);
  }

  /* ------------------------------------------------------------ gaps */
  async function gaps(body) {
    const d = await api("/insights/gaps");
    const s = d.summary;
    body.innerHTML = toolbar(`<span class="count">Things nothing covers, or that point nowhere</span>`)
      + cards([[s.modules_without_triggers, "modules no playbook triggers on"], [s.unused_connectors, "connectors no playbook uses"],
               [s.orphan_child_playbooks, "child playbooks nobody calls"], [s.unresolved_calls, "calls to missing playbooks"],
               [s.undocumented, "undocumented playbooks"], [s.inactive, "inactive playbooks"]])
      + `<div class="section"><h2>Modules</h2><p class="lead">Record types and how many playbooks are triggered by them or act on them. Zero triggers means nothing starts automatically on those records.</p>
        ${table([["Module"], ["Triggered by", "", "num"], ["Acted on by", "", "num"]], d.modules.map(m =>
          `<tr><td><a href="#browse/playbooks">${esc(m.module)}</a></td><td class="num">${m.triggered_by || `<span class="pill warn">0</span>`}</td><td class="num">${m.acted_on_by}</td></tr>`))}</div>
      <div class="section"><h2>Child playbooks nobody calls (${d.orphan_child_playbooks.length})</h2><p class="lead">Their trigger is "referenced", but no playbook in the graph calls them: dead code, or the caller isn't loaded yet.</p>
        ${table([["Playbook"], ["Pack"]], d.orphan_child_playbooks.slice(0, 300).map(p => `<tr><td>${pbLink(p.uuid, p.name)}</td><td>${esc(p.pack || "–")}</td></tr>`))}</div>
      <div class="section"><h2>Calls to playbooks that aren't loaded (${d.unresolved_calls.length})</h2>
        ${table([["Calling playbook"], ["Pack"], ["Missing uuid"]], d.unresolved_calls.slice(0, 300).map(p => `<tr><td>${pbLink(p.uuid, p.caller)}</td><td>${esc(p.pack || "–")}</td><td class="sub">${esc(p.missing)}</td></tr>`))}</div>
      <div class="section"><h2>Connectors no playbook uses (${d.unused_connectors.length})</h2>
        <div class="btnrow">${d.unused_connectors.slice(0, 400).map(c => `<a class="pill" href="#browse/connectors/${enc(c.name)}">${esc(c.label || c.name)}</a>`).join("")}</div></div>
      <div class="section"><h2>No description and no runbook (${d.undocumented.length})</h2>
        ${table([["Playbook"], ["Pack"]], d.undocumented.slice(0, 300).map(p => `<tr><td>${pbLink(p.uuid, p.name)}</td><td>${esc(p.pack || "–")}</td></tr>`))}</div>`;
    wireLink();
    $("in-csv").onclick = () => downloadCSV("coverage-modules", [["module", "Module"], ["triggered_by", "Triggered by"], ["acted_on_by", "Acted on by"]], d.modules);
  }

  /* ------------------------------------------------------------ call chains */
  async function chains(body) {
    if (S.param) {
      const d = await api(`/insights/chains?root=${enc(S.param)}`);
      const kids = {};
      d.edges.forEach(e => (kids[e.from] ||= []).push(e.to));
      const seen = new Set();
      const node = (u, depth) => {
        const n = d.nodes[u] || { name: u };
        const loop = seen.has(u);
        seen.add(u);
        const children = loop || depth > 8 ? [] : [...new Set(kids[u] || [])];
        return `<li>${n.placeholder ? `<span class="pill warn">missing</span> ${esc(n.name)}` : pbLink(u, n.name)} <span class="sub">${esc(n.pack || "")}</span>${loop ? ` <span class="pill warn">↻ already shown</span>` : ""}
          ${children.length ? `<ul>${children.map(c => node(c, depth + 1)).join("")}</ul>` : ""}</li>`;
      };
      body.innerHTML = toolbar(`<a href="#insights/chains"><button type="button">← All chains</button></a><b>${esc(d.root_name)}</b>`)
        + `<div class="section"><h2>Calls (child playbooks, ${d.edges.length} link(s))</h2>${d.cycle ? `<div class="notice">This playbook ends up calling itself (a cycle).</div>` : ""}
          <div class="tree"><ul style="border:0;padding-left:0">${node(d.root, 0)}</ul></div></div>
        <div class="section"><h2>Called by (${d.callers.length})</h2>${table([["Playbook"], ["Hops", "", "num"]], d.callers.map(c => `<tr><td>${pbLink(c.uuid, c.name)}</td><td class="num">${c.hops}</td></tr>`), "Nothing calls it.")}</div>`;
      wireLink();
      $("in-csv").onclick = () => downloadCSV(`chain-${safeName(d.root_name)}`, [["from", "From", e => d.nodes[e.from]?.name], ["to", "To", e => d.nodes[e.to]?.name]], d.edges);
      return;
    }
    const d = await api("/insights/chains");
    body.innerHTML = toolbar(`<input id="in-root" placeholder="Playbook name or uuid…" style="max-width:340px"><button type="button" id="in-go">Show its chain</button>`)
      + (d.cycles.length ? `<div class="section"><h2>Cycles (${d.cycles.length})</h2><p class="lead">Playbooks that eventually call themselves; they can loop forever.</p>
          ${table([["Playbook"], ["Cycle length", "", "num"]], d.cycles.map(c => `<tr><td>${pbLink(c.uuid, c.name)}</td><td class="num">${c.length}</td></tr>`))}</div>` : "")
      + `<div class="section"><h2>Deepest chains</h2><p class="lead">Top-level playbooks with the longest chains of child playbooks. Deep chains are hard to debug and slow.</p>
        ${table([["Top playbook"], ["Pack"], ["Depth", "", "num"], ["Descendants", "", "num"], [""]], d.deepest.map(c => `<tr><td>${pbLink(c.uuid, c.name)}</td><td>${esc(c.pack || "–")}</td>
          <td class="num">${c.depth}</td><td class="num">${c.descendants}</td><td><a href="#insights/chains/${enc(c.uuid)}">tree →</a></td></tr>`))}</div>
      <div class="section"><h2>Most-called child playbooks</h2><p class="lead">Shared building blocks: changing them affects every caller.</p>
        ${table([["Playbook"], ["Pack"], ["Callers", "", "num"], [""]], d.most_called.map(c => `<tr><td>${pbLink(c.uuid, c.name)}</td><td>${esc(c.pack || "–")}</td><td class="num">${c.callers}</td>
          <td><a href="#insights/chains/${enc(c.uuid)}">tree →</a></td></tr>`))}</div>`;
    const go = () => { const v = $("in-root").value.trim(); if (v) location.hash = `#insights/chains/${enc(v)}`; };
    $("in-go").onclick = go;
    $("in-root").onkeydown = e => { if (e.key === "Enter") go(); };
    wireLink();
    $("in-csv").onclick = () => downloadCSV("deepest-chains", [["name", "Playbook"], ["pack", "Pack"], ["depth", "Depth"], ["descendants", "Descendants"]], d.deepest);
  }

  /* ------------------------------------------------------------ security */
  async function security(body) {
    const d = await api("/insights/security");
    const s = d.summary;
    const origin = S.param || "";
    const rows = d.findings.filter(f => !origin || f.origin === origin);
    body.innerHTML = toolbar(`<select id="in-origin"><option value="">Yours + Fortinet</option><option value="upload" ${origin === "upload" ? "selected" : ""}>Your playbooks</option>
        <option value="github" ${origin === "github" ? "selected" : ""}>Fortinet GitHub</option></select>
      <span class="count">${d.reviews_generated ? `Fortinet scores from ${esc(agoIso(d.reviews_generated))}` : "Fortinet playbooks not scored yet (Review → Score Fortinet playbooks)"}</span>`)
      + cards([[s.playbooks_with_secrets, "playbooks with hard-coded credentials"], [s.critical, "critical findings"], [s.high, "high findings"], [s.findings, "security findings in total"]])
      + `<div class="section"><h2>By rule</h2>${table([["Rule"], ["Severity"], ["Findings", "", "num"], ["Playbooks", "", "num"]], d.by_rule.map(r =>
          `<tr><td><code class="sub">${esc(r.rule)}</code> ${esc(r.title)}</td><td>${sevPill(r.severity)}</td><td class="num">${r.findings}</td><td class="num">${r.playbooks}</td></tr>`))}</div>
      <div class="section"><h2>Hard-coded credentials (${d.secrets.length})</h2><p class="lead">Detected at ingestion; the values were redacted before storage.</p>
        ${table([["Playbook"], ["Pack / source"], ["Steps"]], d.secrets.map(p => `<tr><td>${pbLink(p.uuid, p.name)}</td><td>${esc(p.pack || "–")}${p.pack_type === "upload" ? ` <span class="pill ok">yours</span>` : ""}</td><td class="sub">${esc(p.steps.join(", "))}</td></tr>`))}</div>
      <div class="section"><h2>Findings (${rows.length})</h2>${table([["Severity"], ["Finding"], ["Playbook"], ["Step"], ["Detail"]], rows.slice(0, 500).map(f =>
          `<tr><td>${sevPill(f.severity)}</td><td>${esc(f.title)}</td><td><a href="#review/reviews/${encKey(f.key)}">${esc(f.playbook)}</a><div class="sub">${f.origin === "upload" ? "yours · " : ""}${esc(f.source || "")}</div></td>
           <td class="sub">${esc(f.step || "–")}</td><td class="sub">${esc(f.detail)}</td></tr>`))}</div>`;
    $("in-origin").onchange = () => { location.hash = `#insights/security${$("in-origin").value ? "/" + $("in-origin").value : ""}`; };
    wireLink();
    $("in-csv").onclick = () => downloadCSV("security-findings", [["severity", "Severity"], ["rule", "Rule"], ["title", "Finding"], ["playbook", "Playbook"],
      ["origin", "Origin"], ["source", "Source"], ["step", "Step"], ["detail", "Detail"]], rows);
  }

  /* ------------------------------------------------------------ changes */
  async function changes(body) {
    const d = await api("/insights/changes");
    const diffCell = x => {
      const df = x.diff;
      if (!df) return `<span class="sub">${x.embedded != null ? `${x.embedded} embedded` : ""}${x.embedded_skipped ? ` · ${x.embedded_skipped} left pending` : ""}</span>`;
      if (df.first_load) return `<span class="sub">first load: ${df.counts.added} playbooks</span>`;
      const parts = [["added", "add", "+"], ["removed", "rem", "−"], ["changed", "chg", "~"]].filter(([k]) => df.counts[k])
        .map(([k, cls, sym]) => `<span class="${cls}" title="${esc(df[k].join(", "))}">${sym}${df.counts[k]} ${k}</span>`);
      return parts.length ? `<span class="diffbox" style="padding:2px 6px">${parts.join(" ")}</span>` : `<span class="sub">no playbook changes</span>`;
    };
    body.innerHTML = toolbar(`<span class="count">GitHub ingests (with playbook diffs) and your source syncs</span>`)
      + `<div class="section"><h2>GitHub ingests (${d.ingests.length})</h2>${table([["When"], ["Repo"], ["Mode"], ["Cap", "", "num"], ["Playbook changes"]], d.ingests.map(r =>
          `<tr><td>${esc(fmtWhen(r.at))}</td><td><a href="#github">${esc(r.repo)}</a></td><td>${esc(r.mode)}</td><td class="num">${r.cap ?? "–"}</td><td>${diffCell(r)}</td></tr>`),
          "No ingests recorded yet (history starts from the GitHub page's Ingest / Re-ingest / Continue).")}</div>
      <div class="section"><h2>Your playbooks that changed (${d.uploads.length})</h2>${table([["Playbook"], ["Source"], ["Latest change"], ["Score history"]], d.uploads.map(u =>
          `<tr><td><a href="#review/reviews/${encKey(u.key)}">${esc(u.name)}</a></td><td>${esc(u.source)}</td>
           <td>${u.changes ? Object.entries(u.changes).filter(([, v]) => v?.length).map(([k, v]) => `<span class="pill" title="${esc(v.join(", "))}">${v.length} ${k}</span>`).join("") : "–"}</td>
           <td class="sub">${u.history.map(h => `${h.score} (${esc(h.at.slice(0, 10))})`).join(" ← ")}</td></tr>`), "No re-uploaded or re-synced playbooks have changed yet.")}</div>`;
    wireLink();
    $("in-csv").onclick = () => downloadCSV("ingest-history", [["at", "When"], ["repo", "Repo"], ["mode", "Mode"], ["cap", "Cap"],
      ["added", "Added", r => r.diff?.counts?.added], ["removed", "Removed", r => r.diff?.counts?.removed], ["changed", "Changed", r => r.diff?.counts?.changed]], d.ingests);
  }

  window.VIEWS.insights = { show };
})();
