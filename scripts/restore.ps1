# Windows counterpart of scripts/restore.sh: restore a backup made with scripts/backup.sh into this project folder.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\restore.ps1 <backup-folder> [-Yes] [-DatabasesOnly]
#   -Yes            skip the "type RESTORE" confirmation
#   -DatabasesOnly  only load the two Neo4j dumps (leave .env and data/ as they are)
# Replaces: .env, the data/ folder, and both Neo4j databases. The app server must not be running.
param(
  [Parameter(Mandatory = $true)][string]$Src,
  [switch]$Yes,
  [switch]$DatabasesOnly
)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

$Src = (Resolve-Path $Src).Path
$Project = Split-Path -Leaf (Get-Location)
$Image = 'neo4j:5.26-community'

if (-not (Test-Path "$Src\app-data.tar.gz")) { throw "$Src has no app-data.tar.gz - is this a backup folder?" }
if (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue) {
  throw 'The app server is running on port 8000. Stop it first.'
}

Write-Host 'Checking file integrity...'
foreach ($line in Get-Content "$Src\MANIFEST.txt" | Where-Object { $_ -match '^[0-9a-f]{64} ' }) {
  $hash, $file = $line -split '\s+', 2
  $actual = (Get-FileHash (Join-Path $Src $file.TrimStart('.', '/'))).Hash.ToLower()
  if ($actual -ne $hash) { throw "checksum mismatch: $file" }
}
Write-Host '  checksums OK'

if (-not $Yes) {
  $answer = Read-Host "This replaces .env, data/ and both databases in $(Get-Location). Type RESTORE to continue"
  if ($answer -ne 'RESTORE') { Write-Host 'Cancelled.'; exit 1 }
}

if (-not $DatabasesOnly) {
  if (Test-Path "$Src\env.secret") {
    if (Test-Path .env) { Copy-Item .env .env.before-restore -Force }
    Copy-Item "$Src\env.secret" .env -Force
    Write-Host '- .env restored (previous one saved as .env.before-restore)'
  }
  if (Test-Path data) {
    Rename-Item data "data.before-restore-$(Get-Date -Format yyyyMMdd-HHmmss)"
    Write-Host '- previous data/ folder kept as data.before-restore-*'
  }
  # skip the ._* AppleDouble files a macOS tar adds
  tar -xzf "$Src\app-data.tar.gz" --exclude='._*' --exclude='*/._*'
  if ($LASTEXITCODE) { throw 'extracting app-data.tar.gz failed' }
  Write-Host '- data/ restored'
}

function Load-Dump([string]$Service, [string]$Volume, [string]$DumpDir) {
  $dump = Join-Path $Src $DumpDir
  if (-not (Test-Path "$dump\neo4j.dump")) { Write-Host "  (no dump for $Service - skipping)"; return }
  Write-Host "- Preparing $Service (first start creates the database with the password from .env)..."
  docker compose up -d $Service | Out-Null
  for ($i = 0; $i -lt 30; $i++) {
    $id = docker compose ps -q $Service  # the container, whatever it is named (fortinet-database / marlink-database)
    if ($id -and (docker inspect -f '{{.State.Health.Status}}' $id 2>$null) -eq 'healthy') { break }
    Start-Sleep 4
  }
  docker compose stop $Service | Out-Null
  Write-Host "- Loading $DumpDir into $Service..."
  docker rm -f graphrag-restore-tmp 2>$null | Out-Null
  docker create --name graphrag-restore-tmp --user 7474:7474 --entrypoint neo4j-admin -v "${Volume}:/data" $Image `
    database load neo4j --from-path=/backups --overwrite-destination=true | Out-Null
  docker cp $dump graphrag-restore-tmp:/backups
  docker start -a graphrag-restore-tmp
  $failed = $LASTEXITCODE
  docker rm -f graphrag-restore-tmp | Out-Null
  if ($failed) { throw "loading $DumpDir failed" }
  docker compose start $Service | Out-Null
}

Load-Dump 'neo4j' "${Project}_neo4j_data" 'neo4j-fortinet'
Load-Dump 'neo4j-marlink' "${Project}_neo4j_marlink_data" 'neo4j-marlink'

Write-Host ''
Write-Host 'Restore complete. Start the app with:  .venv\Scripts\python.exe -m app.cli serve'
Write-Host 'Then check Settings -> Workspaces: both databases should show their node counts.'
