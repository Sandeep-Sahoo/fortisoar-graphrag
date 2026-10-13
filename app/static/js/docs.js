/* Docs page: playbook / collection / pack documentation in the fortisoar-playbook-docs format.
   Route: #docs[/<document id>]. window.openDocDialog(kind, target, label) starts a generation from anywhere. */
(() => {
  const root = $("view-docs");
  const KIND = { playbook: "Playbook", collection: "Collection", pack: "Solution pack", source: "Your source", develop: "Developed playbook" };
  let current = null;

  function frame() {
    if (root.dataset.ready) return;
    root.dataset.ready = "1";
    root.innerHTML = `<div class="dv-layout docs-layout">
      <nav class="dv-sessions" aria-label="Documents"><div class="top"><div class="muted" style="font-size:12px">Generate documents from a playbook, collection
        or pack in the Catalog, from Review or from Develop (📄 Document).</div></div><ul id="docs-list"></ul></nav>
      <section class="docs-main"><div class="docs-bar" id="docs-bar"></div><article class="docs-body md" id="docs-body"></article></section>
    </div>`;
  }

  async function show(r) {
    frame();
    await loadList();
    const id = r.kind || current?.id || (window.DOCS_LIST?.[0]?.id);
    if (id) await open(id); else empty();
  }

  async function loadList() {
    try { window.DOCS_LIST = await api("/documentation"); } catch (err) { $("docs-list").innerHTML = `<li class="error">${esc(err.message)}</li>`; return; }
    $("docs-list").innerHTML = window.DOCS_LIST.map(d => `<li data-id="${esc(d.id)}" class="${d.id === current?.id ? "active" : ""}">
        <span class="title">${esc(d.title)}</span>
        <span class="meta">${esc(KIND[d.kind] || d.kind)} · v${esc(d.history.at(-1)?.version || "1.0")}${d.ai ? " · AI" : ""} · ${esc(agoIso(d.updated))}</span></li>`).join("")
      || `<li class="muted" style="cursor:default">No documents yet.</li>`;
    $("docs-list").querySelectorAll("li[data-id]").forEach(li => li.onclick = () => { location.hash = `#docs/${li.dataset.id}`; });
  }

  function empty() {
    $("docs-bar").innerHTML = "";
    $("docs-body").innerHTML = `<div class="dv-empty"><h2>Playbook documentation</h2>
      <p>Readable documentation for a playbook, a collection or a whole solution pack: overview and <b>why it matters to the SOC team</b>,
        dependencies, connectors, the collection table, and for every playbook its trigger, configuration variables, a <b>Flow at a Glance</b> and a
        <b>Detailed Flow</b> diagram and a phased step-by-step description, then operational notes, record sets and an append-only version history.</p>
      <p>Open a playbook, collection or solution pack in the <a href="#browse/packs">Catalog</a> (or a playbook in Review / Develop) and press <b>📄 Document</b>.
        Choose <b>Without AI</b> (no cost) or <b>With AI</b> (1 answer request per playbook) for the written parts.</p></div>`;
  }

  async function open(id) {
    let d;
    try { d = await api(`/documentation/${id}`); } catch (err) { $("docs-body").innerHTML = `<div class="error">${esc(err.message)}</div>`; return; }
    current = d;
    $("docs-list").querySelectorAll("li[data-id]").forEach(li => li.classList.toggle("active", li.dataset.id === id));
    const last = d.history.at(-1) || {};
    $("docs-bar").innerHTML = `<b>${esc(d.title)}</b> <span class="pill">${esc(KIND[d.kind] || d.kind)}</span>
      <span class="muted">v${esc(last.version)} · ${esc(last.date)} · ${esc(last.by)}${d.ai ? " · AI-assisted write-up" : " · standard write-up"}</span>
      <span style="flex:1"></span>
      <button type="button" id="doc-md">⤓ Markdown</button><button type="button" id="doc-print">⎙ Print / PDF</button>
      <button type="button" id="doc-link">🔗 Link</button>
      <button type="button" class="primary" id="doc-regen" data-min-role="analyst">↻ Regenerate</button>
      <button type="button" id="doc-del" data-min-role="analyst">Delete</button>`;
    $("doc-md").onclick = () => { location.href = `/documentation/${id}/export.md`; };
    $("doc-print").onclick = () => window.open(`/documentation/${id}/view`, "_blank", "noopener");
    $("doc-link").onclick = () => copyLink(`#docs/${id}`);
    $("doc-regen").onclick = () => window.openDocDialog(d.kind, d.target, d.title);
    $("doc-del").onclick = async () => {
      if (!confirm("Delete this document and its version history?")) return;
      await api(`/documentation/${id}`, { method: "DELETE" }).catch(err => notify(err.message, true));
      current = null; history.replaceState(null, "", "#docs"); await loadList(); empty();
    };
    await renderMarkdownWithDiagrams($("docs-body"), d.markdown);
  }

  async function renderMarkdownWithDiagrams(el, text) {
    el.innerHTML = DOMPurify.sanitize(marked.parse(text || "", { gfm: true }));
    const blocks = [...el.querySelectorAll("pre code.language-mermaid")];
    for (const code of blocks) {
      const box = document.createElement("div");
      box.className = "dv-diagram";
      code.closest("pre").replaceWith(box);
      try { await drawMermaid(box, code.textContent); } catch (err) { box.innerHTML = `<div class="error">Diagram failed: ${esc(err.message)}</div>`; }
      box.onclick = () => { const svg = box.querySelector("svg"); if (svg) openViewer(current?.title || "Diagram", svg); };
    }
  }

  /* ------------------------------------------------------------ generate dialog */
  window.openDocDialog = async (kind, target, label) => {
    document.querySelector(".doc-dialog")?.remove();
    const dlg = document.createElement("div");
    dlg.className = "usage-pop doc-dialog";
    dlg.style.cssText = "top:70px;right:auto;left:50%;transform:translateX(-50%);width:min(560px,calc(100vw - 32px))";
    dlg.innerHTML = `<h3>Generate documentation</h3><div class="muted">${esc(KIND[kind] || kind)}: <b>${esc(label || target)}</b></div>
      <div id="dd-est" class="muted" style="margin:8px 0">Counting playbooks…</div>`;
    document.body.appendChild(dlg);
    let est;
    try { est = await api(`/documentation/estimate?kind=${enc(kind)}&target=${enc(target)}`); }
    catch (err) { $("dd-est").innerHTML = `<span class="error">${esc(err.message)}</span><div class="btnrow" style="margin-top:8px"><button type="button" id="dd-x">Close</button></div>`;
                  $("dd-x").onclick = () => dlg.remove(); return; }
    const u = est.usage || {}, canAI = !!window.ME?.can_use_ai;
    const left = u.local ? "local model, no quota" : u.limit ? `${u.remaining ?? (u.limit - u.used)} of ${u.limit} answer requests left today` : "no daily limit set";
    $("dd-est").innerHTML = `${est.playbooks} playbook${est.playbooks === 1 ? "" : "s"} will be documented.`;
    dlg.insertAdjacentHTML("beforeend", `
      <div class="stack" style="margin-top:6px">
        <label class="dd-opt"><input type="radio" name="dd-ai" value="no" checked>
          <span><b>Without AI</b> — 0 requests. Diagrams, tables, steps, branches, variables and notes come straight from the playbook JSON and the pack's own files;
          the "why it matters", glance diagram and phase introductions use standard wording.</span></label>
        <label class="dd-opt"><input type="radio" name="dd-ai" value="yes" ${canAI ? "" : "disabled"}>
          <span><b>With AI</b> — ${est.ai_requests} answer request${est.ai_requests === 1 ? "" : "s"} (1 per playbook${est.playbooks > est.ai_limit ? `, first ${est.ai_limit} only` : ""}; free if the same
          playbook was written up before). The model rewrites only the narrative; every fact is still taken from the JSON. <span class="muted">${esc(left)}.</span>
          ${canAI ? "" : `<br><span class="error">Your role can't use the AI model.</span>`}</span></label>
        <label>Requested by (version history)<input id="dd-by" value="${esc(window.ME?.user || "")}" maxlength="60"></label>
        <label>Note for this version (optional)<input id="dd-note" maxlength="200" placeholder="e.g. For the Marlink SOC handover"></label>
      </div>
      <div class="btnrow" style="margin-top:10px"><button type="button" class="primary" id="dd-go">Generate</button><span id="dd-status" class="muted"></span>
        <span style="flex:1"></span><button type="button" id="dd-close">Cancel</button></div>`);
    $("dd-close").onclick = () => dlg.remove();
    $("dd-go").onclick = async () => {
      const ai = dlg.querySelector('input[name="dd-ai"]:checked').value === "yes";
      if (ai && !confirm(`Use ${est.ai_requests} AI answer request(s) for the write-up? (${left})`)) return;
      $("dd-go").disabled = true; $("dd-status").textContent = ai ? "Writing with the model…" : "Generating…";
      try {
        const d = await post("/documentation", { kind, target, ai, requested_by: $("dd-by").value.trim(), note: $("dd-note").value.trim() });
        dlg.remove();
        if (d.stats?.ai_errors?.length) notify(`AI write-up not applied for ${d.stats.ai_errors.length} playbook(s); standard wording used.`, true);
        if (ai) refreshUsage();
        current = null;
        location.hash = `#docs/${d.id}`;
        if (parseHash().view === "docs") show({ kind: d.id });
      } catch (err) { $("dd-status").innerHTML = `<span class="error">${esc(err.message)}</span>`; $("dd-go").disabled = false; }
    };
  };

  window.VIEWS.docs = { show };
})();
