/* Review page: your playbooks (upload / Git), best-practice reviews with scores, and the best-practice catalogue.
   Routes: #review/sources · #review/reviews[/<key>] · #review/rules */
(() => {
  const TABS = { sources: "Your playbooks", reviews: "Reviews & scores", rules: "Best practices" };
  const S = { tab: "sources", key: null, rows: null, meta: null, rules: null, sources: null, sort: { key: "score", dir: 1 },
              mode: "upload", files: [], docFiles: [] };
  const root = $("view-review");

  function frame() {
    if (root.dataset.ready) return;
    root.dataset.ready = "1";
    root.innerHTML = `<div class="page">
      <div class="page-main">
        <div class="page-head">
          <h1>Playbook review</h1>
          <p class="lead">Upload your own playbooks or connect a Git repository. Every playbook is checked against the best practices,
            scored out of 100 and given a reviewer verdict. Fortinet's GitHub playbooks can be scored too. No AI is used unless you ask for AI notes.</p>
          <div class="cards" id="rv-cards"></div>
        </div>
        <nav class="tabs" id="rv-tabs" aria-label="Review sections"></nav>
        <div class="page-body" id="rv-body"></div>
      </div>
      <aside class="page-side" id="rv-side"></aside>
    </div>`;
  }

  async function show(r) {
    frame();
    S.tab = TABS[r.kind] ? r.kind : "sources";
    S.key = r.id || null;
    $("rv-tabs").innerHTML = Object.entries(TABS).map(([k, t]) =>
      `<a href="#review/${k}" style="--c: var(--accent)" aria-current="${k === S.tab ? "page" : "false"}">${t}</a>`).join("");
    loadCards();
    if (S.tab === "sources") await renderSources();
    else if (S.tab === "reviews") await renderReviews();
    else await renderRules();
    if (S.key) renderDetail(S.key); else renderSideHelp();
  }

  async function loadCards() {
    try {
      const s = await api("/review/summary");
      const card = (label, value, sub, go) => `<div class="card ${go ? "click" : ""}" ${go ? `data-go="${go}"` : ""}><b>${value}</b><span>${label}${sub ? ` · ${sub}` : ""}</span></div>`;
      $("rv-cards").innerHTML =
        card("your sources", num(s.sources), "", "#review/sources") +
        card("your playbooks reviewed", num(s.upload.playbooks), s.upload.avg_score != null ? `avg ${s.upload.avg_score}` : "", "#review/reviews") +
        card("need changes (yours)", num(s.upload.request_changes), "", "#review/reviews") +
        (document.body.dataset.github ? card("Fortinet playbooks scored", num(s.github.playbooks), s.github.avg_score != null ? `avg ${s.github.avg_score}` : "not yet", "#review/reviews") : "") +
        card("best practices active", num(s.rules), "", "#review/rules");
      $("rv-cards").querySelectorAll("[data-go]").forEach(c => c.onclick = () => { location.hash = c.dataset.go; });
    } catch (err) { $("rv-cards").innerHTML = `<div class="error">${esc(err.message)}</div>`; }
  }

  /* ------------------------------------------------------------ sources tab */
  async function renderSources() {
    const body = $("rv-body");
    body.innerHTML = `<div class="section" data-min-role="analyst">
        <h2>Add your playbooks</h2>
        <p class="lead">FortiSOAR playbook exports (a collection export or single playbook JSON), or ZIPs of them. Files are parsed as data, never run.
          Your playbooks are linked into the graph to the connectors, modules and Fortinet playbooks they use, and reviewed straight away.</p>
        <div class="seg2" role="group" aria-label="How to add">
          <button type="button" data-mode="upload" aria-pressed="${S.mode === "upload"}">Upload files</button>
          <button type="button" data-mode="git" aria-pressed="${S.mode === "git"}">Connect Git</button>
        </div>
        <div id="rv-add" style="margin-top:12px"></div>
      </div>
      <div class="section">
        <h2>Your sources</h2>
        <div id="rv-sources"><div class="muted">Loading…</div></div>
      </div>
      <div class="section" data-min-role="analyst">
        <h2>Internal docs (runbooks, SOPs)</h2>
        <p class="lead">Markdown, text, PDF or HTML. They become searchable and are linked to playbooks they mention by name. Sensitive values are masked. No embeddings are made.</p>
        <div class="dropzone" id="rv-doczone">Drop docs here or click to choose<input type="file" id="rv-docinput" multiple accept=".md,.markdown,.txt,.pdf,.html,.htm"></div>
        <div class="filelist" id="rv-docfiles"></div>
        <div class="btnrow" style="margin-top:8px"><button type="button" class="primary" id="rv-docupload" disabled>Upload docs</button><span id="rv-docresult" class="muted"></span></div>
      </div>`;
    body.querySelectorAll(".seg2 button").forEach(b => b.onclick = () => { S.mode = b.dataset.mode; renderSources(); });
    renderAddForm();
    wireDrop($("rv-doczone"), $("rv-docinput"), files => { S.docFiles = files; $("rv-docfiles").textContent = files.map(f => f.name).join(", "); $("rv-docupload").disabled = !files.length; });
    $("rv-docupload").onclick = async () => {
      const btn = $("rv-docupload");
      btn.disabled = true; $("rv-docresult").textContent = "Uploading…";
      try {
        const r = await post("/uploads/docs", { files: await filesPayload(S.docFiles) });
        $("rv-docresult").textContent = `${r.documents} doc(s), ${r.chunks} passages added; ${r.linked_by_name} playbook link(s) by name.`;
        S.docFiles = []; $("rv-docfiles").textContent = "";
      } catch (err) { $("rv-docresult").innerHTML = `<span class="error">${esc(err.message)}</span>`; }
      btn.disabled = !S.docFiles.length;
    };
    await loadSources();
  }

  function renderAddForm() {
    const box = $("rv-add");
    if (S.mode === "upload") {
      box.innerHTML = `<div class="form-grid"><label class="wide">Source name<input id="rv-name" placeholder="e.g. SOC prod export Oct 2026" maxlength="80"></label></div>
        <div class="dropzone" id="rv-zone" style="margin-top:10px">Drop .json or .zip exports here, or click to choose<input type="file" id="rv-input" multiple accept=".json,.zip"></div>
        <div class="filelist" id="rv-files"></div>
        <div class="btnrow" style="margin-top:8px"><button type="button" class="primary" id="rv-upload" disabled>Upload &amp; review</button><span id="rv-upresult" class="muted"></span></div>`;
      wireDrop($("rv-zone"), $("rv-input"), files => {
        S.files = files;
        $("rv-files").textContent = files.map(f => `${f.name} (${Math.ceil(f.size / 1024)} KB)`).join(", ");
        $("rv-upload").disabled = !files.length;
        if (!$("rv-name").value && files[0]) $("rv-name").value = files[0].name.replace(/\.(json|zip)$/i, "");
      });
      $("rv-upload").onclick = async () => {
        const name = $("rv-name").value.trim();
        if (!name) { notify("Give the source a name.", true); return; }
        $("rv-upload").disabled = true; $("rv-upresult").textContent = "Uploading, linking and reviewing…";
        try {
          const r = await post("/uploads", { name, files: await filesPayload(S.files) });
          $("rv-upresult").textContent = summaryText(r.summary);
          S.files = []; $("rv-files").textContent = "";
          await loadSources(); loadCards();
          if (r.summary.playbooks) setTimeout(() => { location.hash = `#review/reviews`; }, 900);
        } catch (err) { $("rv-upresult").innerHTML = `<span class="error">${esc(err.message)}</span>`; }
        $("rv-upload").disabled = !S.files.length;
      };
    } else {
      box.innerHTML = `<div class="form-grid">
          <label>Source name<input id="rv-gname" placeholder="e.g. SOC playbooks repo" maxlength="80"></label>
          <label class="wide">Repository URL (https)<input id="rv-gurl" placeholder="https://github.com/your-org/fortisoar-playbooks.git"></label>
          <label>Branch (blank = default)<input id="rv-gbranch" placeholder="main"></label>
          <label>Folder (optional)<input id="rv-gpath" placeholder="playbooks/"></label></div>
        <div class="muted" style="margin-top:6px" id="rv-gtoken"></div>
        <div class="btnrow" style="margin-top:8px"><button type="button" class="primary" id="rv-gconnect">Connect &amp; review</button><span id="rv-gresult" class="muted"></span></div>`;
      api("/uploads").then(d => { $("rv-gtoken").innerHTML = d.git_token_set
        ? "A Git access token is configured (Settings → Data sources) and is used for private repositories."
        : "Public repositories work as-is. For private ones, an admin sets a read-only token in Settings → Data sources → Git access token."; }).catch(() => {});
      $("rv-gconnect").onclick = async () => {
        const body = { name: $("rv-gname").value.trim(), url: $("rv-gurl").value.trim(), branch: $("rv-gbranch").value.trim(), path: $("rv-gpath").value.trim() };
        if (!body.name || !body.url) { notify("Name and repository URL are required.", true); return; }
        $("rv-gconnect").disabled = true; $("rv-gresult").textContent = "Cloning (shallow), linking and reviewing…";
        try {
          const r = await post("/uploads/git", body);
          $("rv-gresult").textContent = summaryText(r.summary) + (r.source.commit ? ` · commit ${r.source.commit}` : "");
          await loadSources(); loadCards();
        } catch (err) { $("rv-gresult").innerHTML = `<span class="error">${esc(err.message)}</span>`; }
        $("rv-gconnect").disabled = false;
      };
    }
  }

  const summaryText = s => `${s.playbooks} playbook(s) from ${s.files} file(s) reviewed${s.avg_score != null ? `, average score ${s.avg_score}` : ""}`
    + `${s.copies_of_fortinet ? `; ${s.copies_of_fortinet} are copies of Fortinet playbooks` : ""}${s.errors ? `; ${s.errors} file(s) couldn't be read` : ""}`
    + `${s.removed ? `; ${s.removed} removed since last time` : ""}${s.unchanged ? " (no new commits)" : ""}.`;

  function wireDrop(zone, input, onFiles) {
    zone.onclick = e => { if (e.target !== input) input.click(); };
    input.onchange = () => onFiles([...input.files]);
    zone.ondragover = e => { e.preventDefault(); zone.classList.add("over"); };
    zone.ondragleave = () => zone.classList.remove("over");
    zone.ondrop = e => { e.preventDefault(); zone.classList.remove("over"); onFiles([...e.dataTransfer.files]); };
  }

  async function loadSources() {
    const box = $("rv-sources");
    try { S.sources = (await api("/uploads")).sources; } catch (err) { box.innerHTML = `<div class="error">${esc(err.message)}</div>`; return; }
    if (!S.sources.length) { box.innerHTML = `<div class="empty-state">No sources yet. Upload an export or connect a repository above.</div>`; return; }
    box.innerHTML = `<table class="grid"><thead><tr><th>Source</th><th>Type</th><th class="num">Playbooks</th><th class="num">Avg score</th><th>Last synced</th><th>Status</th><th></th></tr></thead><tbody>
      ${S.sources.map(s => `<tr data-id="${esc(s.id)}">
        <td><b>${esc(s.name)}</b><div class="sub">added by ${esc(s.created_by || "–")} · ${esc(fmtWhen(s.created))}</div>
          ${(s.errors || []).length ? `<div class="sub error">${s.errors.length} file(s) not read: ${esc(s.errors.slice(0, 3).map(e => `${e.file} (${e.error})`).join("; "))}</div>` : ""}</td>
        <td>${s.kind === "git" ? `Git<div class="sub">${esc(s.url)}${s.branch ? ` @ ${esc(s.branch)}` : ""}${s.path ? ` / ${esc(s.path)}` : ""}${s.commit ? ` · ${esc(s.commit)}` : ""}</div>` : "Upload"}</td>
        <td class="num">${s.playbooks}</td><td class="num">${s.avg_score ?? "–"}</td>
        <td>${s.last_synced ? esc(agoIso(s.last_synced)) : "–"}</td>
        <td>${s.status === "error" ? `<span class="pill warn" title="${esc(s.error || "")}">error</span>` : `<span class="pill">${esc(s.status)}</span>`}</td>
        <td><div class="btnrow">
          <button type="button" data-act="view">Reviews</button>
          <button type="button" data-act="doc" data-min-role="analyst" title="Document every playbook in this source">📄 Document</button>
          <button type="button" data-act="sync" data-min-role="analyst" title="${s.kind === "git" ? "Pull the latest commit and re-review" : "Re-review with the current best practices"}">${s.kind === "git" ? "⟳ Pull" : "↻ Re-review"}</button>
          ${s.kind === "upload" ? `<button type="button" data-act="add" data-min-role="analyst" title="Add or replace files">+ Files</button>` : ""}
          <button type="button" data-act="del" data-min-role="analyst">Delete</button></div></td></tr>`).join("")}</tbody></table>`;
    box.querySelectorAll("tr[data-id]").forEach(tr => {
      const src = S.sources.find(s => s.id === tr.dataset.id);
      tr.querySelectorAll("button[data-act]").forEach(b => b.onclick = async e => {
        e.stopPropagation();
        const act = b.dataset.act;
        if (act === "view") { S.sourceFilter = src.id; location.hash = "#review/reviews"; return; }
        if (act === "doc") { window.openDocDialog("source", src.id, src.name); return; }
        if (act === "del") {
          if (!confirm(`Delete "${src.name}"? Its playbooks are removed from the graph and its reviews, answers and history are deleted.`)) return;
          try { await api(`/uploads/${src.id}`, { method: "DELETE" }); notify("Source deleted."); loadSources(); loadCards(); } catch (err) { notify(err.message, true); }
          return;
        }
        if (act === "sync") {
          b.disabled = true; b.textContent = "Working…";
          try { const r = await post(`/uploads/${src.id}/sync`); notify(`${src.name}: ${summaryText(r.summary)}`); } catch (err) { notify(err.message, true); }
          loadSources(); loadCards();
          return;
        }
        if (act === "add") {
          const input = document.createElement("input");
          input.type = "file"; input.multiple = true; input.accept = ".json,.zip";
          input.onchange = async () => {
            const replace = confirm("Replace all existing files in this source?\n\nOK = replace · Cancel = add to / overwrite matching files");
            try { const r = await post(`/uploads/${src.id}/files`, { files: await filesPayload([...input.files]), replace }); notify(summaryText(r.summary)); }
            catch (err) { notify(err.message, true); }
            loadSources(); loadCards();
          };
          input.click();
        }
      });
    });
  }

  /* ------------------------------------------------------------ reviews tab */
  async function loadRows(force) {
    if (S.rows && !force) return;
    const d = await api("/review/playbooks?source=all");
    S.rows = d.rows; S.meta = d;
  }

  async function renderReviews() {
    const body = $("rv-body");
    body.innerHTML = `<div class="toolbar">
        <input id="rv-q" type="search" placeholder="Search playbooks, sources, issues…" aria-label="Search reviews">
        <select id="rv-origin" data-feature="github"><option value="">Yours + Fortinet</option><option value="upload">Your playbooks</option><option value="github">Fortinet GitHub</option></select>
        <select id="rv-source"><option value="">Any source</option></select>
        <select id="rv-grade"><option value="">Any grade</option>${["A", "B", "C", "D", "F"].map(g => `<option>${g}</option>`).join("")}</select>
        <select id="rv-verdict"><option value="">Any verdict</option><option>Request changes</option><option>Approve with changes</option><option>Approve</option></select>
        <select id="rv-status"><option value="">Any status</option><option>open</option><option>in review</option><option>changes requested</option><option>approved</option></select>
        <span class="count" id="rv-count"></span>
        <button type="button" id="rv-csv" title="Download the listed reviews">⤓ CSV</button>
        <button type="button" id="rv-score-gh" data-min-role="admin" data-feature="github" title="Score every downloaded Fortinet playbook (no AI, about a minute)">Score Fortinet playbooks</button>
      </div>
      <div id="rv-banner"></div>
      <div id="rv-job" style="padding:0 12px"></div>
      <div id="rv-table-wrap"><table class="grid" id="rv-table"><tbody><tr><td class="muted">Loading…</td></tr></tbody></table></div>`;
    try { await loadRows(true); } catch (err) { $("rv-table").innerHTML = `<tbody><tr><td class="error">${esc(err.message)}</td></tr></tbody>`; return; }
    const sources = [...new Map(S.rows.filter(r => r.origin === "upload").map(r => [r.source_id, r.source])).entries()];
    $("rv-source").innerHTML = `<option value="">Any source</option>` + sources.map(([id, name]) => `<option value="${esc(id)}">${esc(name)}</option>`).join("");
    if (S.sourceFilter) { $("rv-source").value = S.sourceFilter; $("rv-origin").value = "upload"; S.sourceFilter = null; }
    ["rv-q", "rv-origin", "rv-source", "rv-grade", "rv-verdict", "rv-status"].forEach(id => $(id).addEventListener(id === "rv-q" ? "input" : "change", renderTable));
    $("rv-csv").onclick = () => downloadCSV("playbook-reviews", [
      ["name", "Playbook"], ["origin", "Origin"], ["source", "Source"], ["collection", "Collection"], ["score", "Score"], ["grade", "Grade"],
      ["verdict", "Verdict"], ["status", "Status"], ["critical", "Critical", r => r.counts?.critical], ["high", "High", r => r.counts?.high],
      ["medium", "Medium", r => r.counts?.medium], ["low", "Low", r => r.counts?.low], ["top", "Top issues"], ["steps", "Steps"],
      ["connectors", "Connectors"], ["reviewed_at", "Reviewed"]], filtered());
    $("rv-score-gh").onclick = async () => {
      if (!confirm("Score every downloaded Fortinet playbook against the best practices?\n\nRuns locally (no AI calls), takes about a minute.")) return;
      try { const job = await post("/admin/jobs", { operation: "review_github" }); showJob(job.id, "rv-job", async () => { await loadRows(true); renderTable(); loadCards(); }); }
      catch (err) { notify(err.message, true); }
    };
    const gh = S.meta.github, banner = [];
    if (!document.body.dataset.github) { /* Fortinet scores live in the Fortinet workspace */ }
    else if (!gh.generated) banner.push(`Fortinet's playbooks haven't been scored yet${hasRole("admin") ? ": use <b>Score Fortinet playbooks</b> (no AI, about a minute)" : ""}.`);
    else if (gh.rules_version !== S.meta.rules_version) banner.push(`Best practices changed since Fortinet playbooks were scored (${esc(agoIso(gh.generated))}); re-score to update.`);
    if (S.rows.some(r => r.origin === "upload" && r.rules_version !== S.meta.rules_version))
      banner.push(`Best practices changed since some of your playbooks were reviewed. <button type="button" id="rv-rescore" data-min-role="analyst">Re-score your playbooks</button>`);
    $("rv-banner").innerHTML = banner.length ? `<div class="notice" style="margin:10px 12px">${banner.join("<br>")}</div>` : "";
    document.getElementById("rv-rescore")?.addEventListener("click", rescore);
    renderTable();
  }

  async function rescore() {
    try { await post("/review/rescore"); notify("Your playbooks were re-scored."); await loadRows(true); if (S.tab === "reviews") renderReviews(); loadCards(); }
    catch (err) { notify(err.message, true); }
  }

  function filtered() {
    const q = $("rv-q")?.value.trim().toLowerCase() || "", origin = $("rv-origin")?.value, source = $("rv-source")?.value,
          grade = $("rv-grade")?.value, verdict = $("rv-verdict")?.value, status = $("rv-status")?.value;
    return (S.rows || []).filter(r => (!origin || r.origin === origin) && (!source || r.source_id === source) && (!grade || r.grade === grade)
      && (!verdict || r.verdict === verdict) && (!status || r.status === status)
      && (!q || [r.name, r.source, r.collection, ...(r.top || []), ...(r.connectors || [])].join(" ").toLowerCase().includes(q)));
  }

  const COLS = [["name", "Playbook"], ["score", "Score"], ["verdict", "Verdict"], ["findings", "Findings"], ["top", "Main issues"], ["status", "Status"], ["changes", "Changes"]];
  function renderTable() {
    let rows = filtered();
    const { key, dir } = S.sort;
    const val = r => key === "findings" ? (r.counts?.critical || 0) * 1000 + (r.counts?.high || 0) * 100 + (r.counts?.medium || 0) * 10 + (r.counts?.low || 0)
      : key === "changes" ? (r.changes ? 1 : 0) : r[key] ?? "";
    rows.sort((a, b) => { const x = val(a), y = val(b); return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * dir; });
    const total = rows.length;
    rows = rows.slice(0, ROW_CAP);
    $("rv-count").textContent = total > ROW_CAP ? `showing ${ROW_CAP} of ${total}` : `${total} reviewed`;
    const chips = c => ["critical", "high", "medium", "low"].filter(s => c?.[s]).map(s => `<span class="sev ${s}" title="${s}">${c[s]}</span>`).join(" ") || `<span class="sub">none</span>`;
    const changes = c => !c ? "" : ["added", "removed", "changed"].filter(k => c[k]?.length).map(k => `<span class="pill">${c[k].length} ${k}</span>`).join("");
    $("rv-table").innerHTML = `<thead><tr>${COLS.map(([k, l]) => `<th data-key="${k}" aria-sort="${S.sort.key === k ? (S.sort.dir > 0 ? "ascending" : "descending") : "none"}">${l}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(r => `<tr data-key="${esc(r.key)}" class="${r.key === S.key ? "on" : ""}">
        <td><b>${esc(r.name)}</b><div class="sub">${r.origin === "upload" ? `yours · ${esc(r.source)}` : `Fortinet · ${esc(r.source || "")}`}${r.copy_of ? " · copy of a Fortinet playbook" : ""}</div></td>
        <td style="white-space:nowrap">${scoreCell(r.score, r.grade)}${r.provisional ? `<div class="sub">provisional</div>` : ""}</td>
        <td>${verdictPill(r.verdict)}</td><td><span class="chips">${chips(r.counts)}</span></td>
        <td class="sub">${esc((r.top || []).join(" · "))}</td><td><span class="pill">${esc(r.status || "open")}</span></td><td>${changes(r.changes)}</td></tr>`).join("")
        || `<tr><td colspan="${COLS.length}" class="muted">${S.rows?.length ? "Nothing matches the filters." : "No reviews yet. Add your playbooks, or score the Fortinet playbooks."}</td></tr>`}</tbody>`;
    $("rv-table").querySelectorAll("th").forEach(th => th.onclick = () => {
      S.sort = { key: th.dataset.key, dir: S.sort.key === th.dataset.key ? -S.sort.dir : 1 }; renderTable();
    });
    $("rv-table").querySelectorAll("tbody tr[data-key]").forEach(tr => tr.onclick = () => { location.hash = `#review/reviews/${encKey(tr.dataset.key)}`; });
  }

  /* ------------------------------------------------------------ review detail (side panel) */
  function renderSideHelp() {
    $("rv-side").innerHTML = `<h2>How scoring works</h2>
      <p class="md">Each playbook starts at <b>100</b>. Every finding deducts points by severity, multiplied by the rule's weight:
        critical 25 · high 12 · medium 6 · low 2 · info 0. One rule can deduct at most twice its severity, so a repeated issue doesn't zero the score.</p>
      <div class="kv"><dt>Grade</dt><dd>A ≥ 90 · B ≥ 80 · C ≥ 70 · D ≥ 60 · F below. Any critical finding caps the grade at D.</dd>
        <dt>Verdict</dt><dd><b>Request changes</b>: a critical finding or score below 70 · <b>Approve with changes</b>: a high finding or score below 85 · otherwise <b>Approve</b>.</dd>
        <dt>Manual checks</dt><dd>Governance items a reviewer answers (pass / fail / n.a.). Until they're answered the score is <i>provisional</i>; a fail deducts points.</dd>
        <dt>Your rules</dt><dd>Add your own best practices on the <a href="#review/rules">Best practices</a> tab; scores update when you re-score.</dd></div>
      <h3>Select a playbook</h3><p class="muted">Pick a row to see its findings, the reviewer summary, the manual checklist and the score history.</p>`;
  }

  async function renderDetail(key) {
    const side = $("rv-side");
    side.innerHTML = `<div class="muted">Reviewing…</div>`;
    let r;
    try { r = await api(`/review/playbooks/${encKey(key)}`); } catch (err) { side.innerHTML = `<div class="error">${esc(err.message)}</div>`; return; }
    drawDetail(r);
  }

  function drawDetail(r) {
    const side = $("rv-side"), key = r.key;
    const bySev = ["critical", "high", "medium", "low", "info"].map(s => [s, r.findings.filter(f => f.severity === s)]).filter(([, fs]) => fs.length);
    const meta = r.meta || {};
    side.innerHTML = `
      <h2>${esc(r.name)}</h2>
      <div class="muted">${r.origin === "upload" ? `Your playbook · ${esc(r.source)}${r.file ? ` · ${esc(r.file)}` : ""}` : `Fortinet GitHub · ${esc(r.source)}`} · ${esc(r.collection || "")}</div>
      <div class="scorehead">
        <div class="ring" style="--p:${r.score};--c:${scoreColor(r.score)}"><b>${r.score}</b></div>
        <div>${gradeBadge(r.grade)} ${verdictPill(r.verdict)}
          <div class="muted" style="margin-top:4px">${r.steps} steps · ${esc((r.trigger_type || "").replace("_", " "))}${r.modules?.length ? ` on ${esc(r.modules.join(", "))}` : ""}</div>
          ${r.provisional ? `<div class="sub">Provisional: ${r.manual_pending} manual check(s) not answered.</div>` : ""}</div>
      </div>
      <div class="btnrow">
        ${r.graph_uuid ? `<a href="#browse/playbooks/${enc(r.graph_uuid)}"><button type="button">Open in catalog</button></a>` : ""}
        <button type="button" id="rd-md">⤓ Review (.md)</button>
        <button type="button" id="rd-csv">⤓ Findings (.csv)</button>
        <button type="button" id="rd-link">🔗 Copy link</button>
        ${r.graph_uuid ? `<button type="button" id="rd-doc" data-min-role="analyst">📄 Document</button>` : ""}
      </div>
      <h3>Category scores</h3>
      <div class="catbars">${Object.entries(r.categories).map(([c, v]) => `<span>${esc(c)}</span><div class="bar"><span style="width:${v}%;background:${scoreColor(v)}"></span></div><b>${v}</b>`).join("")}</div>
      <h3>Reviewer summary</h3><div class="md">${md(r.summary)}</div>
      ${r.fortinet ? `<h3>Compared with the Fortinet original</h3><div class="diffbox">Fortinet's version (${esc(r.fortinet.repo)}) scores <b>${r.fortinet.score}</b> (${esc(r.fortinet.grade)}).
        ${diffHtml(r.fortinet.diff) || "Same steps as the original."}</div>` : ""}
      ${r.changes ? `<h3>Changed since the previous version</h3><div class="diffbox">${diffHtml(r.changes)}</div>` : ""}
      <h3>Findings (${r.findings.length})</h3>
      ${bySev.map(([s, fs]) => fs.map(f => `<div class="finding"><div class="head">${sevPill(f.severity)} <b>${esc(f.title)}</b> <code class="sub">${esc(f.rule)}</code>
          ${f.penalty_share ? `<span class="sub">−${f.penalty_share}</span>` : ""}</div>
        <div class="meta">${f.step ? `Step “${esc(f.step)}” · ` : ""}${esc(f.detail)}${f.evidence ? ` <span class="pill">${esc(f.evidence)}</span>` : ""}</div>
        ${f.suggestion ? `<div class="sugg">${esc(f.suggestion)}</div>` : ""}</div>`).join("")).join("") || `<div class="muted">No automated findings.</div>`}
      <h3>Manual checklist</h3>
      ${r.checklist.map(c => `<div class="check" data-rule="${esc(c.rule)}"><div>${sevPill(c.severity)} <b>${esc(c.title)}</b></div>
        <div class="muted">${esc(c.question)}</div>
        <div class="opts" role="group" aria-label="Answer">${["pass", "fail", "na"].map(a => `<button type="button" class="${a}" data-answer="${a}" aria-pressed="${c.answer === a}" data-min-role="analyst">${{ pass: "Pass", fail: "Fail", na: "N/A" }[a]}</button>`).join("")}
          ${c.by ? `<span class="sub" style="align-self:center;margin-left:6px">${esc(c.by)} · ${esc(agoIso(c.at))}</span>` : ""}</div>
        <input type="text" placeholder="Note (optional)" value="${esc(c.note || "")}" data-min-role="analyst" maxlength="500"></div>`).join("") || `<div class="muted">No manual checks configured.</div>`}
      <h3>Review status</h3>
      <div class="stack">
        <select id="rd-status" ${hasRole("analyst") ? "" : "disabled"}>${["open", "in review", "changes requested", "approved"].map(s => `<option ${s === (meta.status || "open") ? "selected" : ""}>${s}</option>`).join("")}</select>
        <textarea id="rd-notes" rows="3" placeholder="Reviewer notes" ${hasRole("analyst") ? "" : "readonly"}>${esc(meta.notes || "")}</textarea>
        <div class="btnrow"><button type="button" class="primary" id="rd-save" data-min-role="analyst">Save status</button>
          ${meta.status_by ? `<span class="sub">last set by ${esc(meta.status_by)} · ${esc(agoIso(meta.status_at))}</span>` : ""}</div>
      </div>
      <h3>AI reviewer notes</h3>
      <div id="rd-ai">${r.ai_note ? `<div class="md">${md(r.ai_note.text)}</div><div class="sub">${esc(r.ai_note.model)} · ${esc(fmtWhen(r.ai_note.at))} · by ${esc(r.ai_note.by)} · score then ${r.ai_note.score_at_time}</div>` : `<div class="muted">Optional: a senior-reviewer write-up from the AI model, based on the structure and findings above.</div>`}
        <div class="btnrow" style="margin-top:6px"><button type="button" id="rd-ai-btn" data-min-role="analyst" title="Uses 1 AI answer request (free if cached)">✨ ${r.ai_note ? "Refresh" : "Get"} AI notes</button></div></div>
      ${(r.history || []).length ? `<h3>Score history</h3><table class="grid"><thead><tr><th>When</th><th>Score</th><th>Verdict</th><th>Changes</th></tr></thead><tbody>
        ${r.history.map(h => `<tr><td>${esc(fmtWhen(h.at))}${h.version ? `<div class="sub">${esc(String(h.version).slice(0, 19))}</div>` : ""}</td><td>${gradeBadge(h.grade)} ${h.score}</td><td>${verdictPill(h.verdict)}</td>
          <td class="sub">${h.changes ? Object.entries(h.changes).filter(([, v]) => v?.length).map(([k, v]) => `${v.length} ${k}`).join(", ") : "–"}</td></tr>`).join("")}</tbody></table>` : ""}
      <h3>Checks passed (${r.rules.filter(x => x.passed && !x.pending).length} of ${r.rules.length})</h3>
      <details><summary class="muted">Show every rule</summary><ul style="font-size:12px;padding-left:18px">${r.rules.map(x => `<li>${x.passed ? (x.pending ? "◻" : "✓") : "✗"} <code>${esc(x.id)}</code> ${esc(x.title)}${x.penalty ? ` <span class="sub">−${x.penalty}</span>` : ""}</li>`).join("")}</ul></details>`;
    $("rd-md").onclick = () => { location.href = `/review/playbooks/${encKey(key)}/export.md`; };
    $("rd-csv").onclick = () => downloadCSV(`findings-${safeName(r.name)}`, [["severity", "Severity"], ["rule", "Rule"], ["title", "Title"], ["category", "Category"],
      ["step", "Step"], ["detail", "Finding"], ["evidence", "Evidence"], ["suggestion", "Suggestion"]], r.findings);
    $("rd-link").onclick = () => copyLink(`#review/reviews/${encKey(key)}`);
    document.getElementById("rd-doc")?.addEventListener("click", () => window.openDocDialog("playbook", r.graph_uuid, r.name));
    side.querySelectorAll(".check").forEach(box => {
      const rule = box.dataset.rule, note = box.querySelector("input");
      box.querySelectorAll("button[data-answer]").forEach(b => b.onclick = async () => {
        const answer = b.getAttribute("aria-pressed") === "true" ? null : b.dataset.answer;
        try { const updated = await post(`/review/answers/${encKey(key)}`, { rule, answer, note: note.value }); drawDetail({ ...updated, key }); refreshRow(updated); }
        catch (err) { notify(err.message, true); }
      });
      note.onchange = async () => {
        const current = box.querySelector('button[aria-pressed="true"]')?.dataset.answer;
        if (!current) return;
        try { await post(`/review/answers/${encKey(key)}`, { rule, answer: current, note: note.value }); notify("Note saved."); } catch (err) { notify(err.message, true); }
      };
    });
    $("rd-save").onclick = async () => {
      try { await post(`/review/status/${encKey(key)}`, { status: $("rd-status").value, notes: $("rd-notes").value }); notify("Status saved.");
            const row = S.rows?.find(x => x.key === key); if (row) { row.status = $("rd-status").value; if (S.tab === "reviews") renderTable(); } }
      catch (err) { notify(err.message, true); }
    };
    $("rd-ai-btn").onclick = async () => {
      if (!(await confirmAI("AI reviewer notes for this playbook"))) return;
      $("rd-ai").innerHTML = `<div class="muted">Asking the model…</div>`;
      try { await post(`/review/ai/${encKey(key)}`); renderDetail(key); } catch (err) { $("rd-ai").innerHTML = `<div class="error">${esc(err.message)}</div>`; }
      refreshUsage();
    };
  }

  function refreshRow(r) {
    const row = S.rows?.find(x => x.key === r.key);
    if (!row) return;
    Object.assign(row, { score: r.score, grade: r.grade, verdict: r.verdict, provisional: r.provisional, counts: r.counts });
    if (S.tab === "reviews") renderTable();
  }

  const diffHtml = d => !d ? "" : [["added", "add", "+"], ["removed", "rem", "−"], ["changed", "chg", "~"]]
    .filter(([k]) => d[k]?.length).map(([k, cls, sym]) => `<div class="${cls}">${sym} ${d[k].length} step(s) ${k}: ${esc(d[k].slice(0, 8).join(", "))}${d[k].length > 8 ? "…" : ""}</div>`).join("");

  /* ------------------------------------------------------------ best practices tab */
  async function renderRules() {
    const body = $("rv-body");
    try { S.rules = await api("/review/rules"); } catch (err) { body.innerHTML = `<div class="error" style="padding:16px">${esc(err.message)}</div>`; return; }
    const R = S.rules;
    const byCat = R.categories.map(c => [c, R.rules.filter(r => r.category === c)]);
    body.innerHTML = `
      <div class="scoring-help"><b>How rules score.</b> Points deducted per finding: ${Object.entries(R.severities).map(([s, p]) => `${sevPill(s)} ${p}`).join(" ")}
        × the rule's weight (0–5), capped at twice the severity per rule. <i>Manual</i> rules are a reviewer checklist. Built-in rules can be disabled or re-weighted; your own rules can also be edited or deleted.
        <div class="btnrow" style="margin-top:8px">
          <button type="button" class="primary" id="rr-new" data-min-role="analyst">+ Add a best practice</button>
          <button type="button" id="rr-rescore" data-min-role="analyst" title="Re-review your uploaded playbooks with the current rules">↻ Re-score your playbooks</button>
          <button type="button" id="rr-import" data-min-role="analyst" title="Copy best practices from another workspace">⇩ Import from workspace…</button>
          <button type="button" id="rr-csv">⤓ CSV</button>
          <span class="muted">${R.rules.filter(r => r.enabled).length} of ${R.rules.length} active · rules version ${R.version}</span></div>
        <div id="rr-form"></div></div>
      ${byCat.map(([cat, rules]) => `<div class="rules-cat"><h2>${esc(cat)} <span class="muted">(${rules.length})</span></h2>${rules.map(ruleCard).join("")}</div>`).join("")}`;
    $("rr-new").onclick = () => ruleForm();
    $("rr-rescore").onclick = rescore;
    $("rr-import").onclick = () => window.openRuleImport(renderRules);
    $("rr-csv").onclick = () => downloadCSV("best-practices", [["id", "ID"], ["title", "Title"], ["category", "Category"], ["severity", "Severity"],
      ["weight", "Weight"], ["enabled", "Enabled"], ["check", "Check"], ["description", "Description"], ["suggestion", "Suggestion"], ["rationale", "Why"],
      ["builtin", "Built-in"], ["created_by", "Added by"]], R.rules);
    body.querySelectorAll(".rule").forEach(card => wireRule(card, R.rules.find(r => r.id === card.dataset.id)));
  }

  const CHECK_LABELS = { auto: "automatic (built-in)", manual: "manual checklist", text_forbidden: "text must not appear", text_required: "text must appear",
    name_pattern: "playbook name pattern", step_name_pattern: "step name pattern", step_kind_required: "step type required",
    step_kind_forbidden: "step type not allowed", connector_forbidden: "connectors not allowed", max_steps: "maximum steps", field_required: "field required" };

  function paramsText(r) {
    const p = r.params || {};
    if (p.pattern) return `pattern <code>${esc(p.pattern)}</code>${p.scope ? ` in ${esc(p.scope)}` : ""}`;
    if (p.kind) return `step type <b>${esc(p.kind.replace("_", " "))}</b>`;
    if (p.connectors) return `connectors: ${esc(p.connectors)}`;
    if (p.max) return `max ${p.max} steps`;
    if (p.field) return `field <b>${esc(p.field)}</b>`;
    if (p.question) return `asks: ${esc(p.question)}`;
    return "";
  }

  function ruleCard(r) {
    const canEdit = hasRole("analyst");
    return `<div class="rule ${r.enabled ? "" : "off"}" data-id="${esc(r.id)}">
      <div class="head"><code>${esc(r.id)}</code> <b>${esc(r.title)}</b> ${sevPill(r.severity)} <span class="pill">${esc(CHECK_LABELS[r.check] || r.check)}</span>
        ${r.builtin ? "" : `<span class="pill ok">yours${r.created_by ? ` · ${esc(r.created_by)}` : ""}</span>`}
        ${r.origin ? `<span class="pill" title="Imported ${esc(r.origin.imported || "")} by ${esc(r.origin.by || "")}">from ${esc(r.origin.workspace)}</span>` : ""}<span class="spacer"></span>
        <span class="ctrl">${canEdit ? `<label><input type="checkbox" data-f="enabled" ${r.enabled ? "checked" : ""}> on</label>
          <label>weight <input type="number" data-f="weight" min="0" max="5" step="0.5" value="${r.weight}"></label>
          <select data-f="severity">${Object.keys(S.rules.severities).map(s => `<option ${s === r.severity ? "selected" : ""}>${s}</option>`).join("")}</select>
          ${r.builtin ? `<button type="button" data-act="reset" title="Back to the default enabled / weight / severity">Reset</button>`
                      : `<button type="button" data-act="edit">Edit</button><button type="button" data-act="del">Delete</button>`}` : (r.enabled ? "" : "disabled")}</span></div>
      <p>${esc(r.description)}${paramsText(r) ? ` <span class="sub">(${paramsText(r)})</span>` : ""}</p>
      ${r.suggestion ? `<p class="sub">→ ${esc(r.suggestion)}</p>` : ""}
      ${r.rationale ? `<p class="why">Why: ${esc(r.rationale)}</p>` : ""}</div>`;
  }

  function wireRule(card, rule) {
    const patch = async changes => {
      try { await post(`/review/rules/${enc(rule.id)}`, changes, "PATCH"); notify("Rule updated. Re-score to apply it to existing reviews."); renderRules(); }
      catch (err) { notify(err.message, true); renderRules(); }
    };
    card.querySelector('[data-f="enabled"]')?.addEventListener("change", e => patch({ enabled: e.target.checked }));
    card.querySelector('[data-f="weight"]')?.addEventListener("change", e => patch({ weight: +e.target.value }));
    card.querySelector('[data-f="severity"]')?.addEventListener("change", e => patch({ severity: e.target.value }));
    card.querySelector('[data-act="reset"]')?.addEventListener("click", async () => {
      try { await post(`/review/rules/${enc(rule.id)}/reset`); renderRules(); } catch (err) { notify(err.message, true); }
    });
    card.querySelector('[data-act="del"]')?.addEventListener("click", async () => {
      if (!confirm(`Delete "${rule.title}"?`)) return;
      try { await api(`/review/rules/${enc(rule.id)}`, { method: "DELETE" }); renderRules(); } catch (err) { notify(err.message, true); }
    });
    card.querySelector('[data-act="edit"]')?.addEventListener("click", () => ruleForm(rule));
  }

  function ruleForm(rule = null) {
    const R = S.rules, box = $("rr-form");
    const v = rule || { category: "Governance", severity: "medium", check: "manual", weight: 1, params: {} };
    box.innerHTML = `<div style="margin-top:12px;border-top:1px solid var(--border);padding-top:12px">
      <b>${rule ? `Edit ${esc(rule.id)}` : "New best practice"}</b>
      <div class="form-grid" style="margin-top:8px">
        <label class="wide">Title<input id="rf-title" maxlength="120" value="${esc(v.title || "")}" placeholder="e.g. Containment playbooks notify the SOC lead"></label>
        <label>Category<select id="rf-cat">${R.categories.map(c => `<option ${c === v.category ? "selected" : ""}>${c}</option>`).join("")}</select></label>
        <label>Severity<select id="rf-sev">${Object.keys(R.severities).map(s => `<option ${s === v.severity ? "selected" : ""}>${s}</option>`).join("")}</select></label>
        <label>Weight (0–5)<input id="rf-weight" type="number" min="0" max="5" step="0.5" value="${v.weight ?? 1}"></label>
        <label>How it's checked<select id="rf-check">${R.check_types.map(c => `<option value="${c}" ${c === v.check ? "selected" : ""}>${esc(CHECK_LABELS[c] || c)}</option>`).join("")}</select></label>
        <div class="wide" id="rf-params"></div>
        <label class="wide">Description (what the rule expects)<textarea id="rf-desc" rows="2" maxlength="1000">${esc(v.description || "")}</textarea></label>
        <label class="wide">Suggestion (how to fix)<textarea id="rf-sugg" rows="2" maxlength="1000">${esc(v.suggestion || "")}</textarea></label>
        <label class="wide">Why it matters (optional)<textarea id="rf-why" rows="2" maxlength="1000">${esc(v.rationale || "")}</textarea></label>
      </div>
      <div class="btnrow" style="margin-top:8px"><button type="button" class="primary" id="rf-save">${rule ? "Save changes" : "Add best practice"}</button><button type="button" id="rf-cancel">Cancel</button></div></div>`;
    const params = () => {
      const c = $("rf-check").value, p = v.params || {};
      const field = (id, label, html) => `<label>${label}${html}</label>`;
      $("rf-params").innerHTML = `<div class="form-grid">${
        ["text_forbidden", "text_required", "name_pattern", "step_name_pattern"].includes(c)
          ? field("rf-pattern", "Regular expression", `<input id="rf-pattern" maxlength="300" value="${esc(p.pattern || "")}" placeholder="${c === "name_pattern" ? "^[A-Z][A-Za-z ]+ - .+" : "e.g. password|secret"}">`)
            + `<label><span><input type="checkbox" id="rf-icase" ${p.ignore_case !== false ? "checked" : ""}> ignore case</span></label>`
            + (c === "text_forbidden" ? field("rf-scope", "Look in", `<select id="rf-scope">${["args", "names", "all"].map(x => `<option ${x === (p.scope || "args") ? "selected" : ""}>${x}</option>`).join("")}</select>`) : "")
        : ["step_kind_required", "step_kind_forbidden"].includes(c)
          ? field("rf-kind", "Step type", `<select id="rf-kind">${R.step_kinds.map(k => `<option value="${k}" ${k === p.kind ? "selected" : ""}>${k.replace("_", " ")}</option>`).join("")}</select>`)
        : c === "connector_forbidden" ? field("rf-cons", "Connector names (comma-separated)", `<input id="rf-cons" value="${esc(p.connectors || "")}" placeholder="code-snippet, smtp">`)
        : c === "max_steps" ? field("rf-max", "Maximum steps", `<input id="rf-max" type="number" min="1" value="${p.max || 30}">`)
        : c === "field_required" ? field("rf-field", "Field", `<select id="rf-field">${R.fields.map(f => `<option ${f === p.field ? "selected" : ""}>${f}</option>`).join("")}</select>`)
        : c === "manual" ? `<label class="wide">Question for the reviewer<input id="rf-q" maxlength="300" value="${esc(p.question || "")}" placeholder="e.g. Has the SOC lead signed off the containment steps?"></label>`
        : ""}</div>`;
    };
    params();
    $("rf-check").onchange = params;
    $("rf-cancel").onclick = () => { box.innerHTML = ""; };
    $("rf-save").onclick = async () => {
      const c = $("rf-check").value, p = {};
      const val = id => document.getElementById(id)?.value;
      if (val("rf-pattern") != null) { p.pattern = val("rf-pattern"); p.ignore_case = $("rf-icase").checked; if (val("rf-scope")) p.scope = val("rf-scope"); }
      if (val("rf-kind")) p.kind = val("rf-kind");
      if (val("rf-cons") != null) p.connectors = val("rf-cons");
      if (val("rf-max")) p.max = +val("rf-max");
      if (val("rf-field")) p.field = val("rf-field");
      if (val("rf-q") != null) p.question = val("rf-q");
      const body = { title: val("rf-title").trim(), category: val("rf-cat"), severity: val("rf-sev"), weight: +val("rf-weight"), check: c, params: p,
                     description: val("rf-desc"), suggestion: val("rf-sugg"), rationale: val("rf-why") };
      try {
        if (rule) await post(`/review/rules/${enc(rule.id)}`, body, "PATCH"); else await post("/review/rules", body);
        notify(rule ? "Rule saved." : "Best practice added. Re-score your playbooks to apply it.");
        renderRules();
      } catch (err) { notify(err.message, true); }
    };
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  window.VIEWS.review = { show };
})();
