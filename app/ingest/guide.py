"""Split a Fortinet product guide PDF into one document per bookmarked section, with an embedding priority.

Sections come from the PDF's outline (bookmarks). A parent section keeps only its own pages (up to its first
child), children get theirs, so nothing is duplicated. Repeated page headers/footers are removed.
"""
from __future__ import annotations

import re
from pathlib import Path

GUIDE_URL = "https://docs.fortinet.com/document/fortisoar/{version}/playbooks-guide"

# How useful each part of the Playbooks Guide is for questions about playbooks (higher = embedded first).
SECTION_WEIGHTS = [
    (r"core steps|evaluate steps|execute steps|references steps|trigger steps", 10),
    (r"playbook triggers|trigger block|trigger payloads|playbook steps|email step|authentication step|extending playbook steps", 9),
    (r"common errors|troubleshooting|frequently asked|optimiz", 8),
    (r"debugging|playbook execution|executed playbook logs|recovery|reference blocks|trigger blocks", 7),
    (r"jinja|dynamic values|dynamic variables|yaql|reserved keywords|custom functions", 6),
    (r"maintaining|security considerations|handling playbook errors|testing|designing for|templates", 5),
    (r"designer|creating new|importing|exporting|versions|editing playbooks|collections|ownership|logging levels", 4),
    (r"introduction|content hub|expression view", 2),
    (r"change log", 0),
]


def section_weight(path: str) -> int:
    lower = path.lower()
    return next((w for pattern, w in SECTION_WEIGHTS if re.search(pattern, lower)), 3)


def split_guide(pdf: Path, guide: str = "Playbooks Guide", version: str = "7.6.5") -> list[dict]:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf))
    pages = [p.extract_text() or "" for p in reader.pages]

    entries: list[tuple[int, str, int]] = []   # (depth, title, start page index)

    def walk(items, depth=0):
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
            else:
                entries.append((depth, item.title.strip(), reader.get_destination_page_number(item)))
    walk(reader.outline)

    chapters = {t for d, t, _ in entries if d == 0}
    noise = re.compile(r"^(fortinet inc\.?|fortisoar \d[\d.]* .*guide|\d{1,3}|" + "|".join(map(re.escape, chapters)) + r")$", re.I)

    def clean(page_text: str) -> str:
        return "\n".join(l for l in page_text.splitlines() if not noise.match(l.strip()))

    # Concatenate the cleaned pages, then cut at each section title's position in the text (several
    # sections can start on the same page), falling back to the page start if the title isn't found.
    full, page_offset = "", []
    for page in pages:
        page_offset.append(len(full))
        full += clean(page) + "\n"
    page_offset.append(len(full))
    flat = full.lower()

    def find_title(title: str, start_page: int, not_before: int) -> int:
        lo = max(page_offset[min(start_page, len(pages))], not_before)
        hi = page_offset[min(start_page + 2, len(pages))]  # the heading is on its page (or wraps to the next)
        words = [re.escape(w) for w in title.lower().split()]
        if not words:
            return lo
        m = re.compile(r"\s+".join(words)).search(flat, lo, hi)
        if not m and len(words[0]) > 2:  # PDF text sometimes drops a heading's first letter ("oo Many…")
            m = re.compile(r"\s+".join([words[0][1:]] + words[1:])).search(flat, lo, hi)
        return m.start() if m else lo

    positions, cursor = [], 0
    for depth, title, start_page in entries:
        pos = find_title(title, start_page, cursor)
        positions.append(pos)
        cursor = pos + 1

    docs, stack = [], []
    for i, (depth, title, start_page) in enumerate(entries):
        stack = stack[:depth] + [title]
        begin, finish = positions[i], positions[i + 1] if i + 1 < len(entries) else len(full)
        text = full[begin:finish].strip()
        last_page = max(p for p in range(len(pages)) if page_offset[p] <= max(begin, finish - 1))
        path = " › ".join(stack)
        if len(text) < 80:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")[:90]
        docs.append({
            "id": f"vendor:guide/{guide.lower().replace(' ', '-')}/{slug}",
            "title": f"{guide} › {path}",
            "text": text if text.lower().startswith(title.lower()[:20]) else f"# {title}\n\n{text}",
            "source": "vendor", "version": version, "path": f"{pdf.name} p{start_page + 1}-{last_page + 1}",
            "props": {"kind": "guide", "guide": guide, "section": path, "pages": f"{start_page + 1}-{last_page + 1}",
                      "url": GUIDE_URL.format(version=version), "weight": section_weight(path)},
        })
    return docs


def guide_priority(doc: dict, chunk_index: int) -> float:
    """Spread a small budget across sections: every important section's first chunks before deep chunks."""
    return doc["props"]["weight"] - 1.25 * chunk_index
