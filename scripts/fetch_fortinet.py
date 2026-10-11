"""Download public FortiSOAR content into data/fortinet/ (re-runnable; use it to re-sync).

  python scripts/fetch_fortinet.py              # repos + product guides
  python scripts/fetch_fortinet.py --repos-only
  python scripts/fetch_fortinet.py --docs-only

GitHub (github.com/fortinet-fortisoar):
  * the repo list comes from the REST API (8 calls, cached in data/fortinet/repos.jsonl);
  * each repo is fetched as a codeload tarball (not API rate-limited) and only content files are
    kept: playbook JSON, info.json, README / release notes / docs/*.md. Code, images and CI files
    are skipped. Extraction never writes outside the target folder.

docs.fortinet.com (FortiSOAR 7.6, Playbooks Guide only by default):
  * for each guide in GUIDES, the latest 7.6.x release's landing page is fetched to find the guide's PDF;
  * robots.txt asks for Crawl-delay: 2 and no query-string URLs, so requests to docs.fortinet.com
    are spaced 2s apart and never use query strings. PDFs are served from Fortinet's S3 bucket.

Everything downloaded is treated as data: it's parsed, never executed.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import tarfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "fortinet"
UA = "fortisoar-graphrag-ingest/1.0 (+internal knowledge base)"
ORG = "fortinet-fortisoar"

KEEP_PREFIXES = ("connector-", "solution-", "widget-", "ai-")
KEEP_FILE = re.compile(
    r"(^|/)(info\.json|README\.md|release_notes\.md|collection\.metadata\.json)$"
    r"|(^|/)docs/[^/]+\.md$"
    r"|(^|/)playbooks/.+\.json$",
    re.I,
)

# Product guides are not ingested: the knowledge base uses GitHub content only. To add guides
# later, list them here (e.g. "playbooks-guide": "7.6.5") and run with --docs-only.
GUIDES: dict[str, str] = {}


def _ssl_context():
    # python.org builds on macOS ship without root certificates; use certifi's bundle when available
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL = _ssl_context()


def http_get(url: str, timeout: int = 120) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL) as resp:
        return resp.read(), resp.geturl()


# ------------------------------------------------------------------ GitHub

def list_repos(refresh: bool = False) -> list[dict]:
    cache = OUT / "repos.jsonl"
    if cache.exists() and not refresh:
        return [json.loads(line) for line in cache.read_text().splitlines() if line.strip()]
    repos, page = [], 1
    while True:
        body, _ = http_get(f"https://api.github.com/orgs/{ORG}/repos?per_page=100&page={page}&type=public", 60)
        batch = json.loads(body)
        if not batch:
            break
        repos += [{"name": r["name"], "branch": r["default_branch"], "archived": r["archived"],
                   "pushed": r["pushed_at"], "description": r.get("description") or ""} for r in batch]
        page += 1
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("\n".join(json.dumps(r) for r in repos) + "\n")
    return repos


def fetch_repo(repo: dict, dest_root: Path, branch: str | None = None) -> tuple[str, int, str]:
    name = repo["name"]
    dest = dest_root / name
    marker = dest / ".pushed_at"
    if branch is None and marker.exists() and marker.read_text().split("|")[0] == repo["pushed"]:
        return name, -1, "unchanged"
    branch = branch or repo["branch"]
    url = f"https://codeload.github.com/{ORG}/{name}/tar.gz/refs/heads/{branch}"
    for attempt in range(3):
        try:
            data, _ = http_get(url, 300)
            break
        except Exception as exc:  # transient network errors
            if attempt == 2:
                return name, 0, f"error: {exc}"
            time.sleep(3 * (attempt + 1))
    kept = 0
    staging = dest.with_name(dest.name + ".tmp")
    if staging.exists():
        _rmtree(staging)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            parts = PurePosixPath(member.name).parts[1:]  # strip "<repo>-<branch>/"
            if not parts or any(p in ("..", "") or p.startswith("/") for p in parts):
                continue
            rel = "/".join(parts)
            if not KEEP_FILE.search(rel) or member.size > 25_000_000:
                continue
            target = staging.joinpath(*parts)
            if not str(target.resolve()).startswith(str(staging.resolve())):
                continue  # path traversal guard
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as src:
                target.write_bytes(src.read())
            kept += 1
    staging.mkdir(parents=True, exist_ok=True)
    (staging / ".pushed_at").write_text(f"{repo['pushed']}|{branch}")
    if dest.exists():
        _rmtree(dest)
    staging.rename(dest)
    return name, kept, "ok"


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def fetch_repos(workers: int = 8) -> None:
    repos = [r for r in list_repos() if r["name"].startswith(KEEP_PREFIXES) and not r["archived"]]
    dest_root = OUT / "github"
    dest_root.mkdir(parents=True, exist_ok=True)
    print(f"fetching {len(repos)} repos ({workers} parallel)…", flush=True)
    done = files = errors = unchanged = 0
    with ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(fetch_repo, r, dest_root) for r in repos]
        for fut in as_completed(futures):
            name, kept, status = fut.result()
            done += 1
            if status == "unchanged":
                unchanged += 1
            elif status == "ok":
                files += kept
            else:
                errors += 1
                print(f"  ! {name}: {status}", flush=True)
            if done % 50 == 0:
                print(f"  {done}/{len(repos)} repos", flush=True)
    print(f"repos done: {done}, files kept: {files}, unchanged: {unchanged}, errors: {errors}")


def has_content(path: Path) -> bool:
    return any(path.rglob("info.json")) or any(path.rglob("playbooks/*.json")) or any(path.rglob("playbooks/*/*.json"))


def release_branches(name: str) -> list[str]:
    """Branch names via `git ls-remote` (git protocol, not subject to the REST API rate limit)."""
    import subprocess

    out = subprocess.run(
        ["git", "-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=20", "ls-remote", "--heads",
         f"https://github.com/{ORG}/{name}.git"],
        capture_output=True, text=True, timeout=60, env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"},
    ).stdout
    branches = [line.split("refs/heads/", 1)[1] for line in out.splitlines() if "refs/heads/" in line]
    version = lambda b: tuple(int(x) for x in re.findall(r"\d+", b))
    return sorted((b for b in branches if b.startswith("release/")), key=version, reverse=True)


def fetch_fallback_branches() -> None:
    """Some repos keep content only on release/x.y.z branches; their default branch is empty."""
    repos = {r["name"]: r for r in list_repos()}
    dest_root = OUT / "github"
    empty = [d.name for d in sorted(dest_root.iterdir()) if d.is_dir() and not d.name.endswith(".tmp")
             and d.name in repos and not has_content(d)]
    print(f"{len(empty)} repos have no content on their default branch; checking release branches…", flush=True)
    recovered = 0
    for name in empty:
        try:
            branches = release_branches(name)
        except Exception as exc:
            print(f"  ! {name}: {exc}")
            continue
        if not branches:
            continue
        _, kept, status = fetch_repo(repos[name], dest_root, branch=branches[0])
        if status == "ok" and has_content(dest_root / name):
            recovered += 1
            print(f"  {name}: {branches[0]} ({kept} files)", flush=True)
    print(f"recovered {recovered}/{len(empty)} from release branches")


# ------------------------------------------------------------------ docs.fortinet.com

def fetch_guides() -> None:
    dest = OUT / "docs"
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for i, (guide, version) in enumerate(GUIDES.items()):
        pdf_path = dest / f"{guide}-{version}.pdf"
        if pdf_path.exists() and pdf_path.stat().st_size > 10_000:
            manifest[guide] = {"version": version, "file": pdf_path.name}
            continue
        if i:
            time.sleep(2)  # robots.txt Crawl-delay: 2
        page_url = f"https://docs.fortinet.com/document/fortisoar/{version}/{guide}"
        try:
            html, final_url = http_get(page_url, 60)
        except Exception as exc:
            print(f"  ! {guide}: {exc}")
            continue
        pdfs = re.findall(rb'https://fortinetweb\.s3\.amazonaws\.com/docs\.fortinet\.com/v2/attachments/[0-9a-f-]+/[^"\'\s<>]+\.pdf', html)
        if not pdfs:
            print(f"  ! {guide}: no PDF link on {final_url}")
            continue
        pdf_url = pdfs[0].decode()
        data, _ = http_get(pdf_url, 300)
        pdf_path.write_bytes(data)
        manifest[guide] = {"version": version, "file": pdf_path.name, "pdf": pdf_url, "page": final_url}
        print(f"  {guide} {version}: {len(data) / 1e6:.1f} MB", flush=True)
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"guides: {len(manifest)}/{len(GUIDES)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos-only", action="store_true")
    parser.add_argument("--docs-only", action="store_true")
    parser.add_argument("--refresh-list", action="store_true", help="re-read the repo list from the GitHub API")
    args = parser.parse_args()
    if args.refresh_list:
        list_repos(refresh=True)
    if not args.docs_only:
        fetch_repos()
        fetch_fallback_branches()
    if not args.repos_only and GUIDES:
        fetch_guides()
