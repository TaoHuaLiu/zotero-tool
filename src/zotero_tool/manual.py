"""Hand-off for the PDFs that cannot be downloaded automatically.

`links_page` writes an HTML checklist of the missing references (from the
report of `pdfs fetch`) with the links to open in a browser. After the person
saves PDFs (usually into ~/Downloads), `import_pdfs` reads the first pages of
each new PDF with `pdftotext`, matches it to a missing reference by DOI or
title, and copies it into the PDF folder under the reference's file name.
"""

from __future__ import annotations

import csv
import html
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from .pdfs import SKIP_TYPES, Fetcher, clean_doi

SECTIONS = [
    ("failed", "Free copy exists, automatic download blocked", True),
    ("not_found", "Not on OpenAlex (link from Zotero)", True),
    ("no_oa", "No free copy known (paywalled: needs library access)", False),
]


def read_report(outdir: Path) -> dict[str, dict[str, str]]:
    path = outdir / "report.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run `zotero-tool pdfs fetch` first")
    with path.open(encoding="utf-8") as f:
        return {r["key"]: r for r in csv.DictReader(f)}


def missing(items: list[dict[str, Any]], outdir: Path) -> list[dict[str, Any]]:
    """References with no PDF in `outdir`, joined with their report row."""
    report = read_report(outdir)
    out = []
    for it in items:
        if it["data"]["itemType"] in SKIP_TYPES:
            continue
        name = Fetcher.filename(it)
        if (outdir / name).exists():
            continue
        row = report.get(it["key"], {})
        out.append(
            {
                "item": it,
                "file": name,
                "status": row.get("status", "not_found"),
                "links": (row.get("links") or "").split(),
            }
        )
    return out


def links_page(todo: list[dict[str, Any]], outdir: Path) -> Path:
    def li(m: dict[str, Any]) -> str:
        d = m["item"]["data"]
        c = (d.get("creators") or [{}])[0]
        who = c.get("lastName") or c.get("name") or ""
        year = (m["item"].get("meta", {}).get("parsedDate") or d.get("date") or "")[:4]
        links = m["links"] or [
            f"https://scholar.google.com/scholar?q={quote_plus(d.get('title', ''))}"
        ]
        a = " · ".join(
            f'<a href="{html.escape(u)}" target="_blank">{html.escape(_host(u))}</a>' for u in links
        )
        key = m["item"]["key"]
        return (
            f'<li><label><input type="checkbox" data-k="{key}"> '
            f"<b>{html.escape(d.get('title', ''))}</b></label><br>"
            f"<span class=m>{html.escape(who)} {year} · {key}</span> {a}</li>"
        )

    parts = []
    for status, title, open_ in SECTIONS:
        group = [m for m in todo if m["status"] == status]
        if group:
            body = "\n".join(li(m) for m in group)
            parts.append(
                f"<details{' open' if open_ else ''}><summary>{title} ({len(group)})</summary>"
                f"<ol>{body}</ol></details>"
            )
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Missing PDFs ({len(todo)})</title><style>
body{{font:15px/1.45 -apple-system,system-ui,sans-serif;max-width:60rem;
 margin:2rem auto;padding:0 1rem}}
li{{margin:.6rem 0}} .m{{color:#666;font-size:13px}}
summary{{font-weight:600;font-size:17px;margin:1rem 0}}
li:has(input:checked){{opacity:.4}} code{{background:#eee;padding:0 .3rem}}
</style></head><body>
<h1>Missing PDFs: {len(todo)}</h1>
<p>Open the links, save each PDF (any name) to <code>~/Downloads</code>, tick it here, then run
<code>uv run zotero-tool pdfs import</code>: files are matched to references by DOI or title.</p>
{"".join(parts)}
<script>
document.querySelectorAll('input[data-k]').forEach(b=>{{
 b.checked=localStorage.getItem('pdf-'+b.dataset.k)==='1';
 b.onchange=()=>localStorage.setItem('pdf-'+b.dataset.k,b.checked?'1':'0');}});
</script></body></html>"""
    path = outdir / "missing.html"
    path.write_text(page, encoding="utf-8")
    return path


def _host(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else url


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def pdf_text(path: Path, pages: int = 2) -> str:
    try:
        r = subprocess.run(
            ["pdftotext", "-l", str(pages), str(path), "-"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return r.stdout


def match(text: str, todo: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The missing reference a PDF's text belongs to: DOI first, then title."""
    low = text.lower()
    for m in todo:
        doi = clean_doi(m["item"]["data"].get("DOI", "")).lower()
        if doi and doi in low:
            return m
    body = _norm(text)
    best, best_len = None, 0
    for m in todo:
        title = _norm(m["item"]["data"].get("title", "").lstrip("… "))
        probe = " ".join(title.split()[:10])  # titles wrap and get cut; first words suffice
        if len(probe) >= 20 and probe in body and len(probe) > best_len:
            best, best_len = m, len(probe)
    return best


def import_pdfs(
    src: Path, todo: list[dict[str, Any]], outdir: Path, since_hours: float | None = 24
) -> tuple[list[tuple[Path, str]], list[Path]]:
    """Copy matching PDFs from `src` into `outdir`. Returns (imported, unmatched)."""
    cutoff = time.time() - since_hours * 3600 if since_hours else 0
    imported, unmatched = [], []
    remaining = list(todo)
    for pdf in sorted(src.glob("*.pdf"), key=lambda p: p.stat().st_mtime):
        if pdf.stat().st_mtime < cutoff:
            continue
        m = match(pdf_text(pdf), remaining)
        if not m:
            unmatched.append(pdf)
            continue
        shutil.copy2(pdf, outdir / m["file"])
        imported.append((pdf, m["file"]))
        remaining.remove(m)
    return imported, unmatched
