# Operations: restart, back up, move to a new machine

## What makes up a running system

| Part | Where | Moves via |
|---|---|---|
| Code | this folder (`app/`, `scripts/`, `tests/`, `docker-compose.yml`, `requirements.txt`) | git (`github.com/Sandeep-Sahoo/fortisoar-graphrag`) |
| Fortinet knowledge database | Docker volume `fortisoar-graphrag_neo4j_data` (service `neo4j`, ports 7474 / 7687) | backup → `neo4j-fortinet/neo4j.dump` |
| Marlink knowledge database | Docker volume `fortisoar-graphrag_neo4j_marlink_data` (service `neo4j-marlink`, ports 7475 / 7688) | backup → `neo4j-marlink/neo4j.dump` |
| App data | `data/` — reviews, rules, uploads, Develop designs, documents, users, audit log, usage meter, logs (`data/logs/app.log`), job logs, embedding + answer caches (`data/.cache/`), downloaded GitHub content, Marlink's files (`data/workspaces/marlink/`) | backup → `app-data.tar.gz` |
| Settings and secrets | `.env` (API keys, Neo4j password, sign-in, `*_MARLINK` secrets) | backup → `env.secret` (copy privately) |
| Python environment | `.venv/` | recreated on the new machine |

## 1. Restart the service

Run these in the project folder (`fortisoar-graphrag/`).

1. **Stop the app** — press `Ctrl-C` in the terminal where `app.cli serve` runs.
2. **Check the databases** (they keep running in Docker between app restarts):
   ```bash
   docker compose ps
   ```
   Both `neo4j` and `neo4j-marlink` should be `running`. If not:
   ```bash
   docker compose up -d neo4j neo4j-marlink
   ```
   Wait until both show `(healthy)` in `docker compose ps` (about 20–40 s).
3. **Start the app**:
   ```bash
   .venv/bin/python -m app.cli serve
   ```
4. Open http://localhost:8000 and sign in again (sign-ins don't survive a restart).

After a full computer reboot: start Docker Desktop first, then steps 2–4.

To stop everything: `Ctrl-C` for the app, then `docker compose stop`.

## 2. Back up

1. Stop the app (`Ctrl-C`).
2. Run:
   ```bash
   scripts/backup.sh
   ```
   It briefly stops each database for a consistent dump, restarts it, archives `data/`, copies `.env` to
   `env.secret`, and writes `MANIFEST.txt` with checksums. Output: `backups/graphrag-<date>/`.
3. Start the app again.

`backups/` is git-ignored. `env.secret` holds your API keys and passwords: move it only over a private channel
(encrypted USB, password manager attachment, `scp` to a machine you control) — never email, chat or git.

## 3. Move to a new machine

**On the new machine — install once**
1. Docker Desktop (start it), git, Python 3.12 or newer.
2. Get the code:
   ```bash
   git clone https://github.com/Sandeep-Sahoo/fortisoar-graphrag.git
   cd fortisoar-graphrag
   ```
   (commit and push any local code changes on the old machine first, or copy the project folder instead of cloning)
3. Python environment:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

**On the old machine**
4. `scripts/backup.sh` (app stopped), then copy the `backups/graphrag-<date>/` folder to the new machine
   (e.g. into `fortisoar-graphrag/backups/`).

**On the new machine — restore**
5. ```bash
   scripts/restore.sh backups/graphrag-<date>
   ```
   It verifies the checksums, asks you to type `RESTORE`, puts back `.env` and `data/`, creates both Neo4j
   containers with the password from `.env`, loads both database dumps and starts them.
6. Start the app: `.venv/bin/python -m app.cli serve`, sign in, then check:
   - **Settings → Workspaces**: Fortinet and Marlink both *connected*, with the same node counts as before;
   - **Review**, **Develop**, **Docs**: your items are there; **Settings → Audit log** shows the history;
   - the header usage meter shows today's counts.

**On Windows** (PowerShell, in the project folder) use the PowerShell versions of the commands above:
```powershell
[Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'User')   # once; open a new terminal afterwards
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\restore.ps1 backups\graphrag-<date>
.venv\Scripts\python.exe -m app.cli serve
```
`PYTHONUTF8=1` makes Python read and write files as UTF-8, as on macOS / Linux; without it, saving text such as
`→` fails with `UnicodeEncodeError`. `restore.ps1` takes `-Yes` to skip the confirmation and `-DatabasesOnly` to
load just the two Neo4j dumps.

Nothing needs re-embedding: vectors are inside the database dumps, and the embedding / answer caches travel in
`data/.cache/`. No AI calls are needed for the move.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Banner “… graph database isn't reachable” | `docker compose up -d neo4j neo4j-marlink`, wait for `(healthy)`, reload the page |
| `Address already in use` on port 8000 | the app is already running elsewhere: stop that terminal, or `lsof -iTCP:8000` to find it |
| Neo4j authentication error after restore | the password in `.env` differs from the one the database was created with: restore `env.secret` again, or reset the container (see below) |
| Port 7474/7687 or 7475/7688 taken | another Neo4j is running: stop it, or change the ports in `docker-compose.yml` and `NEO4J_URI` / the Marlink URI in Settings |

To re-create a database container from scratch (deletes that database — restore a dump afterwards):
`docker compose rm -sf neo4j-marlink && docker volume rm fortisoar-graphrag_neo4j_marlink_data`.
