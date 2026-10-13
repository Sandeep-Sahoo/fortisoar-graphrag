#!/usr/bin/env bash
# Back up everything needed to run this app on another machine:
#   * both knowledge databases (Neo4j dumps of the Fortinet and Marlink workspaces)
#   * the app data folder (reviews, uploads, designs, documents, rules, users, audit log, usage, logs,
#     embedding + answer caches, downloaded GitHub content)
#   * .env (settings and secrets) as a separate file — keep it private
#
# Usage:  scripts/backup.sh [output-folder]        (default: backups/graphrag-<date>)
# Stop the app server first (Ctrl-C in its terminal): the databases are stopped briefly for a consistent dump.
set -euo pipefail
cd "$(dirname "$0")/.."

STAMP=$(date +%Y%m%d-%H%M)
OUT=${1:-backups/graphrag-$STAMP}
PROJECT=$(basename "$PWD")
IMAGE=neo4j:5.26-community
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)

if lsof -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "! The app server is still running on port 8000. Stop it (Ctrl-C in its terminal) and run this again." >&2
  exit 1
fi

STOPPED=""
restart_stopped() {  # always bring stopped databases back, even if a step fails
  for svc in $STOPPED; do docker compose start "$svc" >/dev/null 2>&1 && echo "• $svc started again"; done
  docker rm -f graphrag-backup-tmp >/dev/null 2>&1 || true
}
trap restart_stopped EXIT

dump() {  # $1 = compose service, $2 = volume, $3 = output sub-folder
  local service=$1 volume=$2 dest="$OUT/$3"
  if ! docker volume inspect "$volume" >/dev/null 2>&1; then
    echo "  (no volume $volume — skipping $service)"; return
  fi
  mkdir -p "$dest"
  echo "• Stopping $service for a consistent dump…"
  docker compose stop "$service" >/dev/null
  STOPPED="$STOPPED $service"
  # dump inside a throwaway container and copy it out (no host folder mounts: works even where macOS
  # privacy settings stop Docker from writing to Desktop / Documents)
  docker rm -f graphrag-backup-tmp >/dev/null 2>&1 || true
  docker run --name graphrag-backup-tmp --user 7474:7474 --entrypoint sh -v "$volume:/data" "$IMAGE" \
    -c 'mkdir -p /tmp/dump && neo4j-admin database dump neo4j --to-path=/tmp/dump --overwrite-destination=true' 2>&1 | grep -Ev '^(Files: |$)'
  docker cp graphrag-backup-tmp:/tmp/dump/neo4j.dump "$dest/neo4j.dump"
  docker rm -f graphrag-backup-tmp >/dev/null
  echo "• Starting $service again"
  docker compose start "$service" >/dev/null
  STOPPED="${STOPPED/ $service/}"
}

echo "Backing up to $OUT"
dump neo4j "${PROJECT}_neo4j_data" neo4j-fortinet
dump neo4j-marlink "${PROJECT}_neo4j_marlink_data" neo4j-marlink

echo "• Archiving the app data folder (incl. logs and caches)…"
tar -czf "$OUT/app-data.tar.gz" data

if [ -f .env ]; then
  cp .env "$OUT/env.secret"
  chmod 600 "$OUT/env.secret"
fi

{
  echo "FortiSOAR Graph RAG backup — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "source machine: $(hostname)"
  echo "git commit: $(git rev-parse --short HEAD 2>/dev/null || echo n/a)"
  echo "neo4j image: $IMAGE"
  echo
  (cd "$OUT" && find . -type f ! -name MANIFEST.txt -exec shasum -a 256 {} \;)
} > "$OUT/MANIFEST.txt"

echo
echo "Done: $OUT"
du -sh "$OUT"/* | sed 's/^/  /'
echo
echo "env.secret contains your API keys and passwords: copy it privately (never by email, chat or git)."
