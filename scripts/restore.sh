#!/usr/bin/env bash
# Restore a backup made with scripts/backup.sh into this project folder (on a new machine, or to roll back).
#
# Usage:  scripts/restore.sh <backup-folder>
# Replaces: .env, the data/ folder, and both Neo4j databases. The app server must not be running.
set -euo pipefail
cd "$(dirname "$0")/.."

SRC=${1:?usage: scripts/restore.sh <backup-folder>}
SRC=$(cd "$SRC" && pwd)
PROJECT=$(basename "$PWD")
IMAGE=neo4j:5.26-community

[ -f "$SRC/app-data.tar.gz" ] || { echo "! $SRC has no app-data.tar.gz — is this a backup folder?" >&2; exit 1; }
if lsof -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "! The app server is running on port 8000. Stop it first." >&2; exit 1
fi

echo "Checking file integrity…"
(cd "$SRC" && grep -E '^[0-9a-f]{64} ' MANIFEST.txt | shasum -a 256 -c --quiet) && echo "  checksums OK"

read -r -p "This replaces .env, data/ and both databases in $PWD. Type RESTORE to continue: " answer
[ "$answer" = "RESTORE" ] || { echo "Cancelled."; exit 1; }

if [ -f "$SRC/env.secret" ]; then
  [ -f .env ] && cp .env ".env.before-restore"
  cp "$SRC/env.secret" .env
  chmod 600 .env
  echo "• .env restored (previous one saved as .env.before-restore)"
fi

if [ -d data ]; then
  mv data "data.before-restore-$(date +%Y%m%d-%H%M%S)"
  echo "• previous data/ folder kept as data.before-restore-*"
fi
tar -xzf "$SRC/app-data.tar.gz"
echo "• data/ restored"

load() {  # $1 = compose service, $2 = volume, $3 = dump sub-folder
  local service=$1 volume=$2 dump="$SRC/$3"
  [ -f "$dump/neo4j.dump" ] || { echo "  (no dump for $service — skipping)"; return; }
  echo "• Preparing $service (first start creates the database with the password from .env)…"
  docker compose up -d "$service" >/dev/null
  for _ in $(seq 1 30); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' "${PROJECT}-${service}-1" 2>/dev/null)" = healthy ] && break
    sleep 4
  done
  docker compose stop "$service" >/dev/null
  echo "• Loading $3 into $service…"
  # copy the dump into a throwaway container (no host folder mounts, see backup.sh), then load it
  docker rm -f graphrag-restore-tmp >/dev/null 2>&1 || true
  docker create --name graphrag-restore-tmp --user 7474:7474 --entrypoint neo4j-admin -v "$volume:/data" "$IMAGE" \
    database load neo4j --from-path=/backups --overwrite-destination=true >/dev/null
  docker cp "$dump" graphrag-restore-tmp:/backups
  docker start -a graphrag-restore-tmp
  docker rm -f graphrag-restore-tmp >/dev/null
  docker compose start "$service" >/dev/null
}

load neo4j "${PROJECT}_neo4j_data" neo4j-fortinet
load neo4j-marlink "${PROJECT}_neo4j_marlink_data" neo4j-marlink

echo
echo "Restore complete. Start the app with:  .venv/bin/python -m app.cli serve"
echo "Then check Settings → Workspaces: both databases should show their node counts."
