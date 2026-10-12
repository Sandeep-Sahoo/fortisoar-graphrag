# FortiSOAR Graph RAG

Answers FortiSOAR questions and ties them to **your deployed playbooks** and internal docs.
Playbook JSON is parsed deterministically into a Neo4j graph. Vendor and internal docs are chunked and embedded and linked into that graph. Retrieval combines vector search, keyword search and graph traversal, and every answer cites its sources (`[C#]` company playbook, `[D#]` internal doc, `[V#]` vendor doc).

## Modes

| `APP_MODE`   | Neo4j                         | LLM / embeddings (default)        | Data leaves the machine? |
|--------------|-------------------------------|-----------------------------------|--------------------------|
| `local`      | Docker on this host           | Gemini / Gemini (current testing) | Yes, to the LLM API      |
| `cloud`      | Aura or a remote Neo4j (`NEO4J_URI`) | Gemini / Gemini            | Yes                      |
| `full-local` | Docker on this host           | Ollama / Ollama                   | **No** (enforced at startup) |

The mode only sets defaults. `LLM_PROVIDER` (`gemini` | `anthropic` | `ollama`) and `EMBED_PROVIDER` (`gemini` | `ollama`) override it. In `full-local`, the app refuses to start if any provider or Neo4j/Ollama URL is non-local.

> **Switching embedding model** (for example Gemini to Ollama) makes the old vectors incompatible. The app detects this and stops. Run `python -m app.cli wipe --yes` and re-ingest, or point the new mode at a different Neo4j database.

## Quick start (local mode, Gemini)

```bash
cp .env.example .env        # set GEMINI_API_KEY, NEO4J_PASSWORD, SCRUB_TERMS
docker compose up -d neo4j  # COMPOSE_PROFILES=local in .env
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python -m app.cli ingest-playbooks data/playbooks           # your JSON exports
.venv/bin/python -m app.cli ingest-docs data/vendor_docs --source vendor --version 7.6
.venv/bin/python -m app.cli ingest-docs data/internal_docs --source internal
.venv/bin/python -m app.cli link-docs                                 # name match + LLM proposals
.venv/bin/python -m app.cli links list                                # review proposed links
.venv/bin/python -m app.cli links approve --doc internal:runbook.md --playbook <uuid>
.venv/bin/python -m app.cli ask "How do we block phishing senders?"
.venv/bin/python -m app.cli serve                                     # web UI + REST API on :8000
```

Open **http://localhost:8000** and sign in. The default login is **admin / admin**; set `APP_USERNAME` and `APP_PASSWORD` in `.env` to change it. `cloud` mode refuses to start with the default. Sessions are held in memory, so restarting the server signs everyone out.

* **Home** shows every playbook as a circular map: collections in the centre, then playbooks, modules and connectors, animating out ring by ring. Click a node for details and **Ask about this in chat →**, or use **Chat with your playbooks →**. The Home and Chat tabs in the header (and the browser Back button) switch views.
* **Catalog:** click a Home stat tile (Playbooks, Collections, Connectors, Documents) or the Catalog tab. Each tab has a searchable, sortable table (plus a collection or source filter), and clicking a row opens everything linked to it:
  * **Playbooks:** trigger, ordered steps with connector operations and hard-coded-secret flags, calls and callers, runbooks and vendor docs, inline Flowchart/Sequence, Show on Home, Ask in chat.
  * **Collections:** their playbooks and the runbooks and vendor docs they roll up to.
  * **Connectors:** each operation with the playbooks and steps that use it, and the docs that cover it.
  * **Documents:** linked playbooks (with link status and method), what the doc mentions, and the full (scrubbed) text.

  Every name is a link, each item has its own URL (`#browse/<kind>/<id>`), and the browser Back button works.
* **GitHub** (menu): every downloaded repo in a table with its kind, version and branch, its playbooks (how many are in the graph and embedded), docs (chunks embedded), pending embeddings and status (not ingested, partial, complete, no content). It has search, filters and sorting. Use **Select** on each row, then **Ingest selected**, optionally including docs and capping new embeddings. Only the selected repos are loaded; nothing else is pruned.
* **Embedding budgets:** `--max-embeddings N` (on `ingest-fortinet` and `ingest-guide`, and the GitHub page's cap) limits new embeddings for a run. Everything is still stored and keyword-searchable, and items over the budget stay **pending**. `embed-pending --limit N` gives them vectors later, highest priority first (solution-pack playbooks before connector samples, and the most useful guide sections first).
* **Product guide:** `ingest-guide data/vendor_docs/fortinet/FortiSOAR-7.6.5-Playbooks_Guide.pdf --max-embeddings 100` splits the PDF into its 86 bookmarked sections (cut at each heading, with page headers and footers removed). Playbooks link to the sections for the step types they use (Core / Evaluate / Execute / References Steps, Trigger Steps, Security Considerations). Add `--dry-run` to preview what would be embedded.
* **Theme:** the ◐ / ☀ / ☾ button in the header cycles System → Light → Dark (also under Settings → Interface preferences). The choice is remembered per browser and applied before the page paints, and graphs and diagrams redraw in the new colours.
* **Logging and errors:**
  * Logs go to the console and to `data/logs/app.log` (rotated at 5 MB, 5 files kept), at the level set by `LOG_LEVEL` (changeable live in Settings).
  * Every request gets an ID, which appears in the log, in the `X-Request-ID` response header and as "(ref …)" on error messages. Sign-ins, failed sign-ins, settings changes (key names only), operations, chat failures and model retries are logged, and secrets are masked in log lines.
  * Unexpected exceptions are logged with a traceback, and the user gets a clean message. A Neo4j outage or bad database credentials return a 503 with a clear explanation.
  * Browser errors are reported to the server log and shown as a toast.
  * Settings → Logs lets you filter the log by level and text (for example a request ID), with auto-refresh.
* **Settings** (menu): one place to control the app.
  * **Configuration:** all settings grouped into General, AI models, Database, Security, Retrieval & chat and Data sources. Changes are validated before they're applied and are rolled back if invalid. They're written to `.env` (permissions 600) and take effect immediately, with no restart. Secrets are write-only and only show set / not set.
  * **Operations:** fetch, ingest (optionally wipe first), rebuild derived links, link docs, create indexes and wipe. They run one at a time in the background from an allow-list, with a live log and cancel. Wipe needs `WIPE` typed to confirm.
  * Doc ↔ playbook link review, connection tests (Neo4j, embeddings, answer model), and browser preferences (default Home view, animation speed and mode, hidden node types).
  * New settings: `RETRIEVAL_PLAYBOOKS_K`, `RETRIEVAL_VENDOR_K`, `RETRIEVAL_INTERNAL_K`, `CHAT_HISTORY_TURNS`, `OVERVIEW_PLAYBOOK_LIMIT`, `FETCH_ORG` and `FETCH_PREFIXES`.
* **Guide** (menu): an in-app manual covering what the app is, where the data comes from and how it's fetched, a live breakdown of what's loaded (node and relationship counts, step kinds, triggers, top packs and connectors, fetch details), how the graph is built (parsing, step-type inference, scrubbing, derived links, embeddings), the graph model with a live-count diagram, how each screen displays it, how chat answers are produced, modes, limitations, a glossary and commands. The live parts come from `GET /about`.
* **Double-click any node** (on Home or in the chat graph) to pull its directly linked nodes into a readable ring around it. Labels point outward, the view fits to the ring, and filtered-out types are shown for that node. Double-click again, click the background, or press Esc to restore.

The chat view:
* **Chats:** the sidebar keeps separate chat sessions, stored in Neo4j so they work in every mode. Follow-ups such as "which of those need approval?" are rewritten into a standalone question using the chat history before searching. The rewrite is shown as "Interpreted as…". Double-click a chat to rename it.
* **Live answers:** each answer shows an animated pipeline (Understand → Search → Graph → Answer) with what each stage found, then streams the text with clickable citations.
* **Diagrams:** every playbook an answer cites or names gets a diagram card, generated from the graph rather than by the LLM:
  * **Flowchart:** step logic, with decisions and branch labels, approvals as hexagons, child playbooks as subroutines, and hard-coded secrets outlined in red.
  * **Sequence:** who talks to whom: trigger → playbook → connectors, child playbooks and the analyst.
* **Graph panel:** shows the cited playbooks. **▶ Play**, or **▶ Animate** on a diagram, walks through the steps in execution order and lights up each step's connector operation. Click a node for its properties, and double-click a playbook to expand it.

Each chat turn makes 2 LLM calls (follow-up rewrite + answer) plus 1 embedding call. On Gemini's free tier, `gemini-2.5-flash` is limited to a small daily request quota. For heavier testing, enable billing, try `LLM_MODEL=gemini-2.5-flash-lite`, or switch to `full-local` mode.

## Fortinet content from GitHub

The knowledge base can be built entirely from the public [fortinet-fortisoar](https://github.com/fortinet-fortisoar) GitHub org (product docs from docs.fortinet.com are not used):

```bash
.venv/bin/python scripts/fetch_fortinet.py --repos-only   # ~58 MB of content files into data/fortinet/github
.venv/bin/python -m app.cli ingest-fortinet --wipe --yes  # replaces everything in the graph (incl. chats)
```

* **Fetch:** the repo list comes from the GitHub API (8 calls). Each repo is downloaded as a codeload tarball, and only playbook JSON, `info.json`, README, release notes and `docs/*.md` are kept. Repos whose default branch is empty are retried from their newest `release/x.y.z` branch (found with `git ls-remote`, which doesn't count against the API limit). Re-running only downloads repos that changed.
* **What's loaded:** solution packs (Content Hub layout, one JSON per playbook), connector sample playbooks (collection exports), every connector's `info.json` (configuration and all actions), and pack, connector and widget docs. Many solution packs publish only a README on GitHub; their playbooks ship through Content Hub, so only their docs are included.
* **Graph additions:** `SolutionPack` nodes (`INCLUDES` collections, `DEPENDS_ON` other packs, `DOCUMENTED_BY` their docs), richer `Connector` and `ConnectorOperation` nodes (`HAS_SAMPLES`, titles, descriptions, parameters), and a generated reference doc per connector.
* **Step types** are inferred from step arguments (connector, child playbook, decision, approval, create/update/find record, wait, set variable). Export files only carry opaque step-type IDs.
* **Scale:** about 4,300 playbooks, 14,000 steps and 1,350 docs. Embeddings are cached on disk (`EMBED_CACHE`) and paced (`EMBED_TPM`), so a free-tier Gemini key can do a first full load in about 1.5–2 hours, and later re-syncs only embed what changed. Transient network errors and rate limits are retried automatically.
* **UI at scale:** Home shows the ecosystem (solution packs and the connectors they use) instead of individual playbooks, and Catalog tables render the first 300 matches until you search or filter.

## Test corpus

`python scripts/generate_samples.py` writes a synthetic test set to `data/samples/`: 18 playbooks in 5 collections, 15 vendor-style docs (written for this project, not Fortinet's), 12 internal runbooks, and `eval_questions.md` with 18 questions and the sources a good answer should cite. The corpus is built to exercise:

* **Every trigger type** and **calls between playbooks** (shared subroutines like Block IP Address).
* **Scrubbing:** fake passwords, API keys, bearer tokens, IPs and emails are planted in step args and docs.
* **LLM doc linking:** 5 internal docs describe a playbook without naming it.
* **Security findings:** steps with hard-coded credentials are flagged (`has_secret`) and called out in the playbook summary.

```bash
.venv/bin/python scripts/generate_samples.py
.venv/bin/python -m app.cli ingest-playbooks data/samples/playbooks --prune
.venv/bin/python -m app.cli ingest-docs data/samples/vendor_docs --source vendor
.venv/bin/python -m app.cli ingest-docs data/samples/internal_docs --source internal
.venv/bin/python -m app.cli link-docs
```

Questions that list everything matching a condition ("which playbooks…") depend on top-k search, so they can miss results at scale. Use `run_cypher` (or the MCP tools) for those, for example:
`MATCH (p:Playbook)-[:HAS_STEP]->(s:Step {has_secret: true}) RETURN p.name, collect(s.name)`

## Full-local mode (Ollama)

```bash
# .env: APP_MODE=full-local, COMPOSE_PROFILES=full-local
docker compose up -d
docker compose exec ollama ollama pull qwen2.5:14b
docker compose exec ollama ollama pull nomic-embed-text
```

On a Mac, Docker can't use the GPU. Run Ollama natively instead (`brew install ollama`, then `ollama serve`), and set `DOCKER_OLLAMA_URL=http://host.docker.internal:11434`, or `OLLAMA_URL=http://localhost:11434` when running the app on the host.

## Cloud mode

Set `APP_MODE=cloud`, `COMPOSE_PROFILES=cloud`, and `NEO4J_URI=neo4j+s://<id>.databases.neo4j.io` (plus `DOCKER_NEO4J_URI` with the same value for the container). Then deploy the `Dockerfile` image to your container platform. Put the API behind auth: the app has none built in.

## Interfaces

* **CLI**: `python -m app.cli --help`
* **REST**: `GET/POST /admin/settings`, `GET /admin/operations`, `POST /admin/jobs`, `GET /admin/jobs/{id}`, `POST /admin/test/{neo4j|llm|embeddings}`, `GET/POST /admin/links`, `POST /admin/cache/clear`, `GET /about` (live stats for the Guide), `GET /catalog/{playbooks|collections|connectors|documents}`, `GET /connectors/{name}`, `GET /documents?id=`, `GET/POST /chats`, `GET/PATCH/DELETE /chats/{id}`, `POST /chats/{id}/messages` (server-sent events: `stage` / `token` / `done` / `error`), `GET /playbooks/{name|uuid}/diagram?kind=flowchart|sequence` (Mermaid source), `POST /ask` (`with_graph: true` adds nodes/edges), `GET /graph/overview`, `GET /graph/playbook/{name|uuid}`, `GET /search/playbooks?q=`, `GET /search/docs?q=&source=vendor|internal`, `GET /playbooks/{name|uuid}`, `GET /playbooks/{name|uuid}/dependents`, `POST /cypher` (read-only), `GET /health`
* **MCP** (Claude Desktop / Claude Code): `python -m app.mcp_server` exposes `search_vendor_docs`, `search_internal_docs`, `search_company_playbooks`, `get_playbook`, `find_dependents`, `run_cypher`, `ask`.

```json
{"mcpServers": {"fortisoar": {"command": "/path/to/fortisoar-graphrag/.venv/bin/python",
  "args": ["-m", "app.mcp_server"], "cwd": "/path/to/fortisoar-graphrag"}}}
```

## How playbooks, collections and documents are linked

| Link | How it's made | Review |
|---|---|---|
| Collection `CONTAINS` playbook | From the export structure | none |
| Playbook `DOCUMENTED_BY` internal doc | 1. The playbook's name or UUID appears in the doc. 2. Otherwise `link-docs --method auto` asks the LLM to choose among similar playbooks, and falls back to embedding similarity (a clear winner ≥ 0.90 only) if the LLM fails, for example on quota | 1 is auto-approved. 2 is `proposed`, and only approved links are used in answers. |
| Playbook `REFERENCES` vendor doc | Derived: the connectors the playbook uses, plus the features its steps use (approvals, decisions, child playbooks, trigger type, hard-coded secrets → credential guidance) | none, rebuilt automatically |
| Collection `DOCUMENTED_BY` / `REFERENCES` doc | Rolled up from its playbooks' links, with a `playbooks` list and `count` | none, rebuilt automatically |

```bash
.venv/bin/python -m app.cli link-docs --method auto      # or llm | embedding | none
.venv/bin/python -m app.cli links list                   # review the proposals
.venv/bin/python -m app.cli links approve --all --min-confidence 0.9
.venv/bin/python -m app.cli links reject --doc internal:x.md --playbook "Name"
.venv/bin/python -m app.cli links add --doc internal:x.md --playbook "Name"   # link by hand
.venv/bin/python -m app.cli derive-links                 # runs automatically after every ingest and review
```

`GET /collections` and `GET /collections/{name}` (and the `get_collection` MCP tool) return a collection with its playbooks and every linked internal and vendor doc.

## Graph model

```
(PlaybookCollection)-[:CONTAINS]->(Playbook)-[:HAS_STEP]->(Step)-[:NEXT]->(Step)
(Step)-[:USES]->(ConnectorOperation)-[:OF]->(Connector)
(Playbook)-[:CALLS]->(Playbook)          (Playbook)-[:TRIGGERED_ON]->(Module)
(Playbook)-[:DOCUMENTED_BY {status}]->(Document)-[:HAS_CHUNK]->(Chunk {source: vendor|internal})
(Playbook)-[:REFERENCES {reasons}]->(Document {source: vendor})
(PlaybookCollection)-[:DOCUMENTED_BY|REFERENCES {playbooks, count}]->(Document)
(Chunk)-[:EXPLAINS]->(Connector|Concept)
```

## Safety notes

* **Scrubbing** runs at ingestion, before storage or embedding. It removes IPs, emails, credentials in URLs, values of key/token/password fields, long opaque strings, and anything listed in `SCRUB_TERMS`. Spot-check your own exports, because the scrubber is regex-based. See `tests/test_parser.py`.
* In `local` and `cloud` modes, scrubbed playbook summaries and doc text go to the hosted LLM and embedding API. Check that this fits the data classification of your playbooks under the AI policy before you load production exports.
* LLM doc links are stored as `proposed` and are ignored in answers until approved (`INCLUDE_PROPOSED_LINKS=true` overrides this).
* Trigger detection (`manual`, `record_event`, `api_endpoint`, `referenced`) is heuristic. Verify it against a few real exports and extend `_trigger()` in `app/ingest/playbooks.py`.

## Tests

```bash
.venv/bin/python -m pytest -q tests
```
