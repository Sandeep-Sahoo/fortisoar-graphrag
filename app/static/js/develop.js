/* Develop page: chat-style playbook design.
   Use case -> questions with suggested answers -> proposed design (workflow + sequence diagrams) -> change requests
   -> confirm -> developed playbook (import file), review score and customer document. Route: #develop[/<session id>] */
(() => {
  const root = $("view-develop");
  const D = { sid: null, session: null, sessions: [], busy: false, panel: "design", picked: new Set(), connector: null };
  const KIND_LABEL = { extract: "extract", enrich: "enrich", verdict: "verdict", decision: "decision", approval: "approval", contain: "response",
                       notify: "notify", update_record: "update record", create_record: "create record", ticket: "ticket", wait: "wait", note: "note" };

  function frame() {
    if (root.dataset.ready) return;
    root.dataset.ready = "1";
    root.innerHTML = `<div class="dv-layout">
      <nav class="dv-sessions" aria-label="Designs">
        <div class="top"><button type="button" class="primary" id="dv-new" data-min-role="analyst">+ New playbook</button></div>
        <ul id="dv-list"></ul>
      </nav>
      <section class="dv-chat">
        <div class="dv-thread" id="dv-thread" aria-live="polite"></div>
        <form class="dv-composer" id="dv-form">
          <div class="row"><textarea id="dv-text" rows="1" placeholder="Describe the use case…" data-min-role="analyst"></textarea>
            <button type="submit" class="primary" id="dv-send" data-min-role="analyst">Send</button></div>
          <div class="hint"><label class="mode" data-min-role="analyst" title="Use the AI model to understand free text (1 answer request per message)">
            <input type="checkbox" id="dv-ai"> AI <span class="muted">(1 request / message)</span></label>
            <span class="muted" id="dv-hint">Guided mode: no AI calls. Enter to send · Shift+Enter for a new line</span></div>
          <div class="error" id="dv-error"></div>
        </form>
      </section>
      <aside class="dv-panel">
        <nav class="tabs" id="dv-tabs"></nav>
        <div class="dv-panel-body" id="dv-panel"></div>
      </aside>
    </div>`;
    $("dv-new").onclick = newSession;
    $("dv-form").onsubmit = e => { e.preventDefault(); send({ text: $("dv-text").value.trim() }); };
    $("dv-text").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("dv-form").requestSubmit(); } });
    $("dv-text").addEventListener("input", () => { const t = $("dv-text"); t.style.height = "auto"; t.style.height = Math.min(160, t.scrollHeight) + "px"; });
    $("dv-ai").addEventListener("change", async e => {
      if (e.target.checked && !(await confirmAI("AI mode in the designer", 1))) { e.target.checked = false; return; }
      $("dv-hint").textContent = e.target.checked ? "AI mode: each message uses 1 answer request (free if identical)." : "Guided mode: no AI calls. Enter to send · Shift+Enter for a new line";
    });
  }

  async function show(r) {
    frame();
    await loadSessions();
    const sid = r.kind || D.sid || D.sessions[0]?.id;
    if (sid && sid !== D.sid) await open(sid);
    else if (!sid) renderEmpty();
    else renderAll();
  }

  async function loadSessions() {
    try { D.sessions = await api("/develop/sessions"); } catch (err) { $("dv-list").innerHTML = `<li class="error">${esc(err.message)}</li>`; return; }
    $("dv-list").innerHTML = D.sessions.map(s => `<li data-id="${esc(s.id)}" class="${s.id === D.sid ? "active" : ""}">
        <span class="title">${esc(s.title || "New playbook")}</span>
        <span class="meta">${esc(s.stage)}${s.score != null ? ` · ${gradeBadge(s.grade)} ${s.score}` : ""} · ${esc(agoIso(s.updated))}</span>
        <button type="button" class="del" data-min-role="analyst" aria-label="Delete design">✕</button></li>`).join("")
      || `<li class="muted" style="cursor:default">No designs yet.</li>`;
    $("dv-list").querySelectorAll("li[data-id]").forEach(li => {
      li.onclick = e => { if (!e.target.classList.contains("del")) location.hash = `#develop/${li.dataset.id}`; };
      li.querySelector(".del").onclick = async () => {
        if (!confirm("Delete this design and its generated files?")) return;
        await api(`/develop/sessions/${li.dataset.id}`, { method: "DELETE" }).catch(err => notify(err.message, true));
        if (li.dataset.id === D.sid) { D.sid = null; D.session = null; history.replaceState(null, "", "#develop"); }
        await loadSessions(); if (!D.sid) renderEmpty();
      };
    });
  }

  async function newSession() {
    try {
      const s = await post("/develop/sessions");
      D.sid = s.id; D.session = s;
      history.replaceState(null, "", `#develop/${s.id}`);
      await loadSessions(); renderAll(); $("dv-text").focus();
    } catch (err) { notify(err.message, true); }
  }

  async function open(sid) {
    try { D.session = await api(`/develop/sessions/${sid}`); D.sid = sid; }
    catch (err) { notify(err.message, true); D.sid = null; renderEmpty(); return; }
    await loadSessions();
    renderAll();
  }

  function renderEmpty() {
    $("dv-thread").innerHTML = `<div class="dv-empty"><h2>Develop a playbook</h2>
      <p>Describe a use case in plain words. I'll ask a few questions (with suggested answers based on the connectors and operations Fortinet's
        own playbooks use), propose a <b>workflow diagram</b> and a <b>sequence diagram</b> for you to confirm or change, then develop a
        best-practice FortiSOAR playbook you can import, with a review score and a document to share with the customer.</p>
      <ol><li>Use case &amp; questions</li><li>Proposed design (diagrams) → change requests</li><li>Confirm → develop → customer view</li></ol>
      <button type="button" class="primary" data-min-role="analyst" id="dv-start">+ Start a new playbook</button></div>`;
    document.getElementById("dv-start")?.addEventListener("click", newSession);
    $("dv-tabs").innerHTML = ""; $("dv-panel").innerHTML = `<div class="muted" style="padding:16px">The design appears here once the questions are answered.</div>`;
  }

  function renderAll() {
    const s = D.session;
    if (!s) { renderEmpty(); return; }
    renderThread();
    $("dv-text").placeholder = s.stage === "intake" ? (s.question === "usecase" ? "Describe the use case…" : "Type an answer, or use the options above…")
      : "Ask for a change, e.g. “add an approval”, “notify on Teams”, “trigger on update”…";
    renderPanel();
  }

  /* ------------------------------------------------------------ thread */
  function renderThread() {
    const s = D.session, box = $("dv-thread");
    const lastQ = s.stage === "intake" ? s.messages.length - 1 : -1;
    const lastP = s.messages.map(m => m.type).lastIndexOf("proposal");
    box.innerHTML = "";
    s.messages.forEach((m, i) => box.appendChild(renderMessage(m, i === lastQ, i === lastP)));
    box.scrollTop = box.scrollHeight;
  }

  function renderMessage(m, liveQuestion, liveProposal) {
    const el = document.createElement("div");
    el.className = `dv-msg ${m.role}`;
    if (m.role === "user") { el.innerHTML = `<div class="bubble">${esc(m.text)}</div>`; return el; }
    if (m.type === "text") { el.innerHTML = `<div class="md">${md(m.text)}</div>`; return el; }
    if (m.type === "question") {
      D.picked = new Set(m.default || []);
      el.innerHTML = `<div class="md">${md(m.text)}</div>
        ${m.chips?.length ? `<div class="dv-chips" role="group">${m.chips.map(c => `<button type="button" class="chip" data-v="${esc(c.value)}" aria-pressed="${(m.default || []).includes(c.value)}" ${liveQuestion ? "" : "disabled"}>${esc(c.label)}</button>`).join("")}</div>` : ""}
        ${liveQuestion && m.key !== "usecase" ? `<div class="btnrow" style="margin-top:8px" data-min-role="analyst">
          ${m.chips?.length ? `<button type="button" class="primary" data-act="continue">Continue →</button>` : ""}
          <button type="button" data-act="skip" title="Fill the remaining questions with the suggestions and show the design">Use suggestions for the rest</button></div>` : ""}`;
      if (liveQuestion) {
        el.querySelectorAll(".chip").forEach(b => b.onclick = () => {
          const v = b.dataset.v;
          if (!m.multi) { send({ values: [v] }); return; }
          if (v === "none") D.picked.clear(); else D.picked.delete("none");
          D.picked.has(v) ? D.picked.delete(v) : D.picked.add(v);
          el.querySelectorAll(".chip").forEach(x => x.setAttribute("aria-pressed", D.picked.has(x.dataset.v)));
        });
        el.querySelector('[data-act="continue"]')?.addEventListener("click", () => send({ values: [...D.picked] }));
        el.querySelector('[data-act="skip"]')?.addEventListener("click", () => send({ skip: true }));
      }
      return el;
    }
    if (m.type === "proposal") {
      const r = m.review;
      el.innerHTML = `<div class="dv-card">
        <div class="dv-card-head"><b>Proposed design v${m.version}</b> <span class="muted">${esc(m.note || "")}</span>
          <span class="spacer"></span>${gradeBadge(r.grade)} <span title="Automated best-practice review of this draft"><b>${r.score}</b>/100</span> ${verdictPill(r.verdict)}</div>
        <div class="md">${md(m.summary)}</div>
        <div class="seg2" role="group" aria-label="Diagram">${["flowchart", "sequence", "glance"].map((k, i) =>
          `<button type="button" data-d="${k}" aria-pressed="${i === 0}">${{ flowchart: "Workflow", sequence: "Sequence", glance: "At a glance" }[k]}</button>`).join("")}</div>
        <div class="dv-diagram" title="Click to enlarge"></div>
        ${m.problems?.length ? `<div class="notice">${m.problems.map(esc).join("<br>")}</div>` : ""}
        ${r.findings?.length ? `<details><summary class="muted">Draft review: ${r.findings.length} finding(s) to know about</summary>
          ${r.findings.map(f => `<div class="sub">${sevPill(f.severity)} ${esc(f.title)}${f.step ? ` — ${esc(f.step)}` : ""}: ${esc(f.detail)}</div>`).join("")}</details>` : ""}
        ${m.rollback ? `<div class="sub">＋ A manual <i>Rollback</i> playbook is included for the response actions.</div>` : ""}
        ${m.required_config?.length ? `<div class="sub">Values to set after import: ${m.required_config.map(c => `<code>${esc(c)}</code>`).join(", ")}</div>` : ""}
        ${liveProposal ? `<div class="btnrow" style="margin-top:10px" data-min-role="analyst">
          <button type="button" class="primary" data-act="develop">✅ Confirm &amp; develop</button>
          <button type="button" data-act="change">✏️ Request changes</button>
          <button type="button" data-act="edit">Edit in panel</button>
          <button type="button" data-act="preview" title="The customer document for this draft">👁 Customer preview</button></div>` : ""}
      </div>`;
      const box = el.querySelector(".dv-diagram");
      const draw = async kind => {
        box.innerHTML = "";
        try { await drawMermaid(box, m[kind]); } catch (err) { box.innerHTML = `<div class="error">Diagram failed: ${esc(err.message)}</div>`; }
      };
      el.querySelectorAll("[data-d]").forEach(b => b.onclick = () => {
        el.querySelectorAll("[data-d]").forEach(x => x.setAttribute("aria-pressed", x === b));
        draw(b.dataset.d);
      });
      box.onclick = () => { const svg = box.querySelector("svg"); if (svg) openViewer(`Design v${m.version}`, svg); };
      requestAnimationFrame(() => draw("flowchart"));
      if (liveProposal) {
        el.querySelector('[data-act="develop"]').onclick = developNow;
        el.querySelector('[data-act="change"]').onclick = () => { $("dv-text").focus(); $("dv-text").placeholder = "What should change? e.g. “add an approval”, “remove the email step”…"; };
        el.querySelector('[data-act="edit"]').onclick = () => { D.panel = "design"; renderPanel(); };
        el.querySelector('[data-act="preview"]').onclick = () => window.open(`/develop/sessions/${D.sid}/view`, "_blank", "noopener");
      }
      return el;
    }
    if (m.type === "developed") {
      el.innerHTML = developedCard(m, true);
      wireDeveloped(el, m);
      return el;
    }
    el.innerHTML = `<div class="md">${md(m.text || "")}</div>`;
    return el;
  }

  function developedCard(m, inThread) {
    const r = m.review;
    return `<div class="dv-card dv-developed">
      <div class="dv-card-head"><b>✅ Developed — build ${m.version}</b> <span class="muted">from design v${m.design_version} · ${esc(fmtWhen(m.at))}</span></div>
      <div class="scorehead"><div class="ring" style="--p:${r.score};--c:${scoreColor(r.score)}"><b>${r.score}</b></div>
        <div>${gradeBadge(r.grade)} ${verdictPill(r.verdict)}
          <div class="muted" style="margin-top:4px">${esc(m.workflows.join(" + "))}</div>
          <div class="sub">${Object.entries(r.counts).filter(([, n]) => n).map(([s, n]) => `${n} ${s}`).join(" · ") || "no findings"}${(r.others || []).map(o => ` · ${esc(o.name)}: ${o.score}`).join("")}</div></div></div>
      <div class="btnrow">
        <button type="button" class="primary" data-act="json">⤓ Playbook import file (.json)</button>
        <button type="button" data-act="view">👁 Customer view / PDF</button>
        <button type="button" data-act="doc">⤓ Design document (.md)</button>
        <button type="button" data-act="review" data-min-role="analyst" title="Load it into the graph and the Review page">→ Send to Review</button>
        <button type="button" data-act="link">🔗 Link</button>
      </div>
      ${inThread ? `<div class="sub" style="margin-top:8px">Import it in FortiSOAR (Automation → Playbooks → Import) into a staging instance first: it arrives inactive.
        The customer view lists the prerequisites, the values to set and the test plan.</div>` : ""}
    </div>`;
  }

  function wireDeveloped(el, m) {
    const q = `?version=${m.version}`;
    el.querySelector('[data-act="json"]').onclick = () => { location.href = `/develop/sessions/${D.sid}/playbook.json${q}`; };
    el.querySelector('[data-act="doc"]').onclick = () => { location.href = `/develop/sessions/${D.sid}/document.md${q}`; };
    el.querySelector('[data-act="view"]').onclick = () => window.open(`/develop/sessions/${D.sid}/view${q}`, "_blank", "noopener");
    el.querySelector('[data-act="link"]').onclick = () => copyLink(`#develop/${D.sid}`);
    el.querySelector('[data-act="review"]').onclick = async () => {
      try { const r = await post(`/develop/sessions/${D.sid}/to-review`); notify(`Added to Review (score ${r.summary.avg_score}).`); location.hash = `#review/reviews/${encKey(r.review_key)}`; }
      catch (err) { notify(err.message, true); }
    };
  }

  async function send(body) {
    if (D.busy || !D.sid) return;
    const ai = $("dv-ai").checked;
    if (!body.text && !body.values?.length && !body.skip) return;
    D.busy = true; $("dv-send").disabled = true; $("dv-error").textContent = "";
    $("dv-text").value = ""; $("dv-text").style.height = "auto";
    const pending = document.createElement("div");
    pending.className = "dv-msg bot"; pending.innerHTML = `<div class="muted">${ai ? "Asking the model…" : "Working…"}</div>`;
    $("dv-thread").appendChild(pending); $("dv-thread").scrollTop = 1e9;
    try {
      const r = await post(`/develop/sessions/${D.sid}/message`, { ...body, ai });
      D.session = r.session;
      renderAll(); loadSessions();
      if (ai) refreshUsage();
    } catch (err) { pending.remove(); $("dv-error").textContent = err.message; }
    D.busy = false; $("dv-send").disabled = false;
  }

  async function developNow() {
    if (D.busy) return;
    D.busy = true;
    try {
      const r = await post(`/develop/sessions/${D.sid}/develop`);
      D.session = r.session; D.panel = "output";
      renderAll(); loadSessions();
    } catch (err) { notify(err.message, true); }
    D.busy = false;
  }

  /* ------------------------------------------------------------ design panel */
  function renderPanel() {
    const s = D.session;
    $("dv-tabs").innerHTML = [["design", "Design"], ["diagrams", "Diagrams"], ["output", `Output${s.generated?.length ? ` (${s.generated.length})` : ""}`]]
      .map(([k, l]) => `<a href="#" data-tab="${k}" aria-current="${D.panel === k ? "page" : "false"}">${l}</a>`).join("");
    $("dv-tabs").querySelectorAll("a").forEach(a => a.onclick = e => { e.preventDefault(); D.panel = a.dataset.tab; renderPanel(); });
    if (D.panel === "diagrams") return panelDiagrams();
    if (D.panel === "output") return panelOutput();
    panelDesign();
  }

  async function panelDesign() {
    const box = $("dv-panel");
    if (!D.session.spec) { box.innerHTML = `<div class="muted" style="padding:16px">Answer the questions (or press <b>Use suggestions for the rest</b>) to get a design you can edit here.</div>`; return; }
    const { spec } = await api(`/develop/sessions/${D.sid}/spec`);
    const t = spec.trigger, edit = hasRole("analyst");
    const main = spec.steps.filter(st => !st.branch_only), branch = spec.steps.filter(st => st.branch_only);
    box.innerHTML = `
      <h3>Playbook</h3>
      <div class="stack"><input id="dp-name" value="${esc(spec.name)}" ${edit ? "" : "readonly"} maxlength="120" aria-label="Name">
        <textarea id="dp-desc" rows="3" ${edit ? "" : "readonly"} aria-label="Description">${esc(spec.description || "")}</textarea>
        <button type="button" id="dp-meta" data-min-role="analyst">Save name &amp; description</button></div>
      <h3>Trigger</h3>
      <div class="form-grid"><label>Runs<select id="dp-ttype" ${edit ? "" : "disabled"}>${Object.entries({ on_create: "When created", on_update: "When updated", manual: "Manual button", referenced: "Called by another playbook" })
          .map(([k, l]) => `<option value="${k}" ${k === t.type ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label>Record type<select id="dp-tmod" ${edit ? "" : "disabled"}>${["alerts", "incidents", "indicators"].map(m => `<option ${m === t.module ? "selected" : ""}>${m}</option>`).join("")}</select></label></div>
      <h3>Steps</h3>
      <ol class="dp-steps">${main.map(st => stepRow(st, edit)).join("")}</ol>
      ${branch.length ? `<div class="sub">Other branch: ${branch.map(st => esc(st.name)).join(", ")}</div>` : ""}
      <div data-min-role="analyst">
        <h3>Add a step</h3>
        <div class="form-grid"><label>Kind<select id="dp-kind">
          ${[["enrich", "Enrichment (connector)"], ["contain", "Response action (connector)"], ["notify", "Notification (connector)"], ["ticket", "Ticket (connector)"],
             ["approval", "Approval"], ["decision", "Decision"], ["wait", "Wait"], ["update_record", "Update record"], ["note", "Note"]]
            .map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select></label>
          <label id="dp-oplabel">Operation<select id="dp-op"></select></label></div>
        <input id="dp-search" placeholder="Search other connectors…" style="margin-top:6px;width:100%">
        <div id="dp-results" class="sub"></div>
        <div class="btnrow" style="margin-top:6px"><button type="button" class="primary" id="dp-add">Add step</button>
          <label class="sub"><input type="checkbox" id="dp-approval" ${spec.safety?.approval !== false && spec.steps.some(st => st.kind === "approval") ? "checked" : ""}> approval before response actions</label></div>
      </div>
      <h3>Configuration values</h3>
      <p class="sub">Set in the playbook's Configuration step. Blank required values are listed in the customer view.</p>
      <table class="grid dp-config"><tbody>${spec.config.map(c => `<tr><td><code>${esc(c.name)}</code>${c.required ? ` <span class="pill warn">required</span>` : ""}<div class="sub">${esc(c.description)}</div></td>
        <td><input data-cfg="${esc(c.name)}" value="${esc(c.value || "")}" ${edit ? "" : "readonly"} style="width:100%"></td></tr>`).join("")}</tbody></table>
      <button type="button" id="dp-cfg" data-min-role="analyst" style="margin-top:6px">Save values</button>`;
    const doEdit = async e => {
      try { const r = await post(`/develop/sessions/${D.sid}/edit`, { edit: e }); D.session = r.session; renderAll(); }
      catch (err) { notify(err.message, true); }
    };
    $("dp-meta").onclick = () => doEdit({ op: "meta", name: $("dp-name").value.trim(), description: $("dp-desc").value.trim() });
    $("dp-ttype").onchange = () => doEdit({ op: "trigger", type: $("dp-ttype").value });
    $("dp-tmod").onchange = () => doEdit({ op: "trigger", module: $("dp-tmod").value });
    $("dp-approval").onchange = e => doEdit({ op: "approval", on: e.target.checked });
    $("dp-cfg").onclick = () => {
      const values = {};
      box.querySelectorAll("[data-cfg]").forEach(i => { values[i.dataset.cfg] = i.value; });
      doEdit({ op: "config", values });
    };
    box.querySelectorAll(".dp-steps [data-op]").forEach(b => b.onclick = () => {
      const id = b.closest("li").dataset.id, op = b.dataset.op;
      if (op === "rename") { const n = prompt("Step name", b.closest("li").dataset.name); if (n?.trim()) doEdit({ op, id, name: n.trim() }); }
      else if (op !== "remove" || confirm("Remove this step?")) doEdit({ op, id });
    });
    const loadOps = async () => {
      const kind = $("dp-kind").value, connectorKinds = ["enrich", "contain", "notify", "ticket"];
      $("dp-oplabel").hidden = !connectorKinds.includes(kind);
      $("dp-search").hidden = !connectorKinds.includes(kind);
      if (!connectorKinds.includes(kind)) return;
      const inds = kind === "enrich" || kind === "contain" ? spec.indicators.join(",") : "";
      const { suggestions } = await api(`/develop/connectors?purpose=${kind}&indicators=${enc(inds)}`);
      const src = x => document.body.dataset.github ? "" : x.source === "fortinet" ? " · Fortinet GitHub" : ` · ${x.source}`;
      $("dp-op").innerHTML = suggestions.map(x => `<option value="${esc(x.connector)}.${esc(x.operation)}">${esc(x.label)} — ${esc(x.title)}${esc(src(x))}</option>`).join("");
    };
    $("dp-kind").onchange = loadOps;
    $("dp-search").oninput = async () => {
      const q = $("dp-search").value.trim();
      if (q.length < 2) { $("dp-results").innerHTML = ""; return; }
      const { connectors } = await api(`/develop/connectors?q=${enc(q)}`);
      $("dp-results").innerHTML = connectors.slice(0, 8).map(c => `<a href="#" data-c="${esc(c.name)}">${esc(c.label)}</a> (${c.operations}${document.body.dataset.github ? "" : c.source === "fortinet" ? ", Fortinet GitHub" : `, ${esc(c.source)}`})`).join(" · ") || "no match";
      $("dp-results").querySelectorAll("a").forEach(a => a.onclick = async e => {
        e.preventDefault();
        const c = await api(`/develop/connectors/${enc(a.dataset.c)}`);
        $("dp-op").innerHTML = c.operations.map(o => `<option value="${esc(c.name)}.${esc(o.operation)}">${esc(c.label)} — ${esc(o.title)}${o.purpose ? ` (${o.purpose})` : ""}</option>`).join("");
      });
    };
    $("dp-add").onclick = () => {
      const kind = $("dp-kind").value;
      if (["enrich", "contain", "notify", "ticket"].includes(kind)) {
        const v = $("dp-op").value;
        if (!v) { notify("Pick an operation.", true); return; }
        const [connector, ...rest] = v.split(".");
        doEdit({ op: "add_connector", kind, connector, operation: rest.join(".") });
      } else {
        const name = prompt("Step name", { approval: "Approve response actions", decision: "Check result", wait: "Wait 5 minutes", update_record: "Update record", note: "Note" }[kind]);
        if (name?.trim()) doEdit({ op: "add_step", kind, name: name.trim() });
      }
    };
    loadOps();
  }

  function stepRow(st, edit) {
    return `<li data-id="${esc(st.id)}" data-name="${esc(st.name)}"><span class="pill">${esc(KIND_LABEL[st.kind] || st.kind)}</span> <b>${esc(st.name)}</b>
      ${st.connector ? `<div class="sub"><code>${esc(st.connector)}.${esc(st.operation)}</code></div>` : ""}
      ${edit ? `<span class="dp-acts"><button type="button" data-op="up" title="Move up">↑</button><button type="button" data-op="down" title="Move down">↓</button>
        <button type="button" data-op="rename" title="Rename">✎</button><button type="button" data-op="remove" title="Remove">✕</button></span>` : ""}</li>`;
  }

  function panelDiagrams() {
    const box = $("dv-panel");
    const p = [...D.session.messages].reverse().find(m => m.type === "proposal");
    if (!p) { box.innerHTML = `<div class="muted" style="padding:16px">No design yet.</div>`; return; }
    box.innerHTML = `<p class="sub">Design v${p.version} · click a diagram to enlarge (and download it as SVG / PNG).</p>
      ${["flowchart", "sequence", "glance"].map(k => `<h3>${{ flowchart: "Workflow", sequence: "Sequence", glance: "At a glance" }[k]}</h3><div class="dv-diagram" data-k="${k}"></div>`).join("")}`;
    box.querySelectorAll(".dv-diagram").forEach(async el => {
      try { await drawMermaid(el, p[el.dataset.k]); } catch (err) { el.innerHTML = `<div class="error">${esc(err.message)}</div>`; }
      el.onclick = () => { const svg = el.querySelector("svg"); if (svg) openViewer(`Design v${p.version}`, svg); };
    });
  }

  function panelOutput() {
    const s = D.session, box = $("dv-panel");
    const builds = [...(s.generated || [])].reverse();
    box.innerHTML = `${s.stage !== "intake" ? `<div class="btnrow" data-min-role="analyst"><button type="button" class="primary" id="dpo-dev">${builds.length ? "Develop again from the current design" : "✅ Confirm &amp; develop"}</button>
        <button type="button" id="dpo-prev">👁 Customer preview</button></div>` : `<div class="muted">Finish the design first.</div>`}
      ${builds.map((m, i) => `<div data-v="${m.version}" style="margin-top:12px">${developedCard(m, false)}
        ${i === 0 ? `<details open><summary class="muted">Review findings (${m.review.findings.length})</summary>
          ${m.review.findings.map(f => `<div class="finding"><div class="head">${sevPill(f.severity)} <b>${esc(f.title)}</b> <code class="sub">${esc(f.rule)}</code></div>
            <div class="meta">${f.step ? `Step “${esc(f.step)}” · ` : ""}${esc(f.detail)}</div>${f.suggestion ? `<div class="sugg">${esc(f.suggestion)}</div>` : ""}</div>`).join("") || `<div class="muted">No findings.</div>`}</details>` : ""}</div>`).join("")}
      <h3>Design history</h3>
      <table class="grid"><tbody>${[...(s.versions || [])].reverse().map(v => `<tr><td>v${v.n}</td><td>${esc(v.note || "")}</td><td class="sub">${esc(agoIso(v.at))}</td></tr>`).join("")}</tbody></table>`;
    document.getElementById("dpo-dev")?.addEventListener("click", developNow);
    document.getElementById("dpo-prev")?.addEventListener("click", () => window.open(`/develop/sessions/${D.sid}/view`, "_blank", "noopener"));
    box.querySelectorAll("[data-v]").forEach(el => wireDeveloped(el, s.generated.find(g => g.version === +el.dataset.v)));
  }

  window.VIEWS.develop = { show };
})();
