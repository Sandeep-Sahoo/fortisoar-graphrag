# Handoff: FortiSOAR Graph RAG — context for a new chat

Paste this into a new Claude chat (opened in the `fortisoar-graphrag/` folder) to continue the work.

## What it is
A Graph RAG web app over FortiSOAR playbooks and docs: Neo4j 5.26 Community (graph + vector + full-text indexes),
FastAPI backend, single-page UI (`app/static/index.html` + `app/static/js/*.js`), Python 3.12+ venv in `.venv/`.
AI: Gemini (gemini-2.5-flash answers, gemini-embedding-001, 768-d) on the **free tier**; modes local / cloud / full-local (Ollama).
Repo: github.com/Sandeep-Sahoo/fortisoar-graphrag (branch main). Runbook for restart / backup / migration: `OPERATIONS.md`.

## Working rules (important)
- **Never make model API calls (Gemini/Anthropic answers or embeddings, Settings test buttons, AI modes) without first
  stating the reason and the request count and getting my approval.** Free tier ≈ 20 answer requests and 1,000 embeddings per day.
  Free to run: pytest, Neo4j/Cypher, browser checks that don't call the model, `--dry-run`, derive-links, status.
- Data scope: Fortinet content comes **only from GitHub org fortinet-fortisoar** (no docs.fortinet.com). Remove any synthetic
  test data you create. Don't share personal data in chat.
- Tests: `.venv/bin/python -m pytest -q tests` (64 pass). `tests/conftest.py` redirects all data files to a temp dir — keep it so.

## Run it
```bash
docker compose up -d neo4j neo4j-marlink      # wait for (healthy)
.venv/bin/python -m app.cli serve             # http://localhost:8000, sign in with the .env admin account
```

## Workspaces (companies) — fully separate
| | Fortinet (default) | Marlink |
|---|---|---|
| Neo4j | service `neo4j`, bolt 7687 / browser 7474, volume `fortisoar-graphrag_neo4j_data` | service `neo4j-marlink`, bolt 7688 / browser 7475, volume `fortisoar-graphrag_neo4j_marlink_data` |
| App data | `data/` | `data/workspaces/marlink/` |
| Contents | GitHub playbooks/packs/connectors/docs | empty, ready for Marlink uploads |
Header selector switches every tab (cookie `graphrag_ws`, per browser). Only best-practice **rules** can be imported across
workspaces. Develop in Marlink suggests Marlink's own connectors first, then the Fortinet GitHub catalog. Users, audit log,
usage limits and AI caches are shared. Code: `app/workspace.py` (`workspace.path()` relocates per-workspace files).

## Access
Roles per company (admin / analyst / viewer / none) + a super-admin flag; the .env admin is always super-admin
(`app/users.py`, `data/users.json`). Settings tabs: **General** (super-admin: models, keys, limits, sign-in, retrieval) /
**Fortinet** / **Marlink** (that company's admin: database, company name, redaction terms, Git token; Fortinet also GitHub fetch/sync).
Marlink values in `data/workspaces.json`, its secrets in `.env` as `KEY_MARLINK`.

## Pages / features
Home (graph views) · Catalog · Chat (AI or **Graph only, no AI**: synonyms, filters, saved searches) · **Develop** (chat-style
playbook designer → workflow + sequence diagrams → change requests → hardened FortiSOAR import JSON + customer document;
guided mode no AI, AI mode opt-in) · **Review** (upload JSON/ZIP or connect Git; 31 built-in best-practice rules + custom rules;
score/grade/verdict, manual checklist, AI notes on request) · **Docs** (documentation in the fortisoar-playbook-docs skill format
for a playbook / collection / pack / source / build; **With AI** = 1 request per playbook, or **Without AI**) · Insights
(connector impact, duplicates, gaps, call chains, security, changes) · GitHub (Fortinet only: per-repo ingest / re-ingest /
continue, embedding planner) · Guide · Settings (users, audit, usage & caches, workspaces, scheduled sync, operations, logs).
Header: usage meter vs daily limits (enforced), answer cache.

## Key code
`app/api.py` (routes, auth/workspace middleware) · `app/services.py` (per-workspace services) · `app/retrieval.py`, `app/chat.py` ·
`app/ingest/` (GitHub/docs/guide ingestion, linking) · `app/review/` (rules, engine, sources, store, api) · `app/develop/`
(catalog, spec, generate, conversation, api) · `app/docgen.py` · `app/insights.py` · `app/exports.py` · `app/usage.py` ·
`app/settings_store.py` · `app/jobs.py` · `app/scheduler.py` · `app/cli.py` · `scripts/fetch_fortinet.py`, `scripts/backup.sh`, `scripts/restore.sh`.

## Current data state
- Fortinet graph: ~4,270 nodes; ~400 solution-pack playbooks loaded and embedded. 715 repos downloaded to `data/fortinet/github`
  (3,893 playbooks and 1,350 docs downloaded but not loaded — ingestion was stopped on purpose).
- All 4,279 Fortinet playbooks scored by the reviewer (`data/review/github.json`).
- Marlink graph: empty. Rules imported from Fortinet (31 active).
- My own work: a Develop design "Indicator - IP address, URL triage" (score 100), sent to Review as source "Developed playbooks".

## Open items
1. **Awaiting my approval:** ingest `data/vendor_docs/fortinet/FortiSOAR-7.6.5-Playbooks_Guide.pdf` into the Fortinet workspace
   with 100 embeddings (`ingest-guide … --max-embeddings 100`).
2. **Git:** runtime data was untracked (`git rm --cached`, `.gitignore` updated) but **not committed yet**; copies pushed earlier
   remain in GitHub history (history rewrite not done). Uncommitted code changes also pending (Docs feature etc.).
3. Not yet tried against the real model: Develop AI mode, Docs "With AI", Review AI notes, Explain playbook (all tested with stand-in models only).
4. Generated playbooks have not yet been imported into a real FortiSOAR instance (staging test pending).

## Move to this machine (if not done yet)
On the old machine: `scripts/backup.sh` (app stopped) → copy `backups/graphrag-<date>/` (send `env.secret` privately).
Here: `git clone …`, `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, Docker Desktop running,
`scripts/restore.sh backups/graphrag-<date>`, then start the app and check Settings → Workspaces.
Last verified backup: `backups/graphrag-20261013-2113` (both dumps load; checksums OK).
