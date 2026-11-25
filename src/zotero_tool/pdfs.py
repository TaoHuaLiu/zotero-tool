"""Get a PDF for each reference of a library.

Order of sources for one item:
1. a PDF attachment stored in the Zotero library itself (downloaded via the API);
2. a legal open-access copy found on OpenAlex, by DOI, or by title when the item
   has no DOI (accepted only if the titles match closely).

Paywalled publisher PDFs are not fetched. Results go to `<outdir>/report.csv`.
"""

from __future__ import annotations

import csv
import difflib
import os
import re
import time
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

OPENALEX = "https://api.openalex.org"
SKIP_TYPES = {"attachment", "note", "annotation"}
TITLE_MATCH = 0.9
WORKERS = 8
UA = "zotero-tool (+https://github.com/TaoHuaLiu/zotero-tool)"


@dataclass
class Result:
    key: str
    title: str
    status: str  # zotero | openalex | exists | no_oa | not_found | failed
    source: str = ""
    file: str = ""
    detail: str = ""


@dataclass
class Fetcher:
    outdir: Path
    zot: Any  # pyzotero client
    http: httpx.Client = field(
        default_factory=lambda: httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(20, connect=10),
            headers={"User-Agent": UA},
            limits=httpx.Limits(max_connections=WORKERS * 2),
        )
    )
    openalex_key: str | None = field(default_factory=lambda: os.environ.get("OPENALEX_API_KEY"))

    # ---------- naming ----------

    @staticmethod
    def filename(item: dict[str, Any]) -> str:
        d = item["data"]
        creators = d.get("creators") or [{}]
        who = creators[0].get("lastName") or creators[0].get("name") or "anon"
        year = (item.get("meta", {}).get("parsedDate") or d.get("date") or "")[:4] or "nd"
        title = " ".join((d.get("title") or "untitled").split()[:8])
        stem = f"{who}_{year}_{title}"
        stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
        stem = re.sub(r"[^\w\-]+", "_", stem).strip("_")[:120]
        return f"{stem}_{d['key']}.pdf"

    # ---------- sources ----------

    def zotero_pdf(self, item: dict[str, Any], children: list[dict[str, Any]]) -> str | None:
        """Key of a stored PDF attachment of `item`, if any."""
        for c in children:
            cd = c["data"]
            if (
                cd.get("parentItem") == item["key"]
                and cd.get("itemType") == "attachment"
                and cd.get("contentType") == "application/pdf"
                and cd.get("linkMode") in ("imported_file", "imported_url")
            ):
                return cd["key"]
        return None

    def _openalex(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        params = dict(params or {})
        if self.openalex_key:
            params["api_key"] = self.openalex_key
        for attempt in range(3):
            r = self.http.get(f"{OPENALEX}{path}", params=params)
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        return None

    def openalex_work(self, item: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        d = item["data"]
        sel = {"select": "id,title,doi,open_access,best_oa_location,locations"}
        doi = clean_doi(d.get("DOI") or d.get("extra", ""))
        if doi:
            w = self._openalex(f"/works/doi:{doi}", sel)
            if w:
                return w, f"doi:{doi}"
        title = (d.get("title") or "").strip()
        if not title:
            return None, ""
        q = re.sub(r"[,:|()]", " ", title)
        res = self._openalex("/works", {**sel, "filter": f"title.search:{q}", "per_page": 5})
        best, score = None, 0.0
        for w in (res or {}).get("results", []):
            s = similarity(title, w.get("title") or "")
            if s > score:
                best, score = w, s
        if best and score >= TITLE_MATCH:
            return best, f"title~{score:.2f}"
        return None, ""

    @staticmethod
    def pdf_urls(work: dict[str, Any]) -> list[str]:
        urls = []
        best = work.get("best_oa_location") or {}
        for u in [best.get("pdf_url"), (work.get("open_access") or {}).get("oa_url")]:
            if u:
                urls.append(u)
        for loc in work.get("locations") or []:
            if loc.get("is_oa") and loc.get("pdf_url"):
                urls.append(loc["pdf_url"])
        return list(dict.fromkeys(urls))

    def download(self, url: str, dest: Path) -> str:
        """Save `url` to `dest` if it really is a PDF. Returns '' or an error."""
        try:
            r = self.http.get(url)
        except httpx.HTTPError as e:
            return f"{type(e).__name__}"
        if r.status_code != 200:
            return f"HTTP {r.status_code}"
        if not r.content.startswith(b"%PDF"):
            return "not a PDF (landing page or bot check)"
        dest.write_bytes(r.content)
        return ""

    # ---------- driver ----------

    def fetch_all(
        self,
        items: list[dict[str, Any]],
        progress: Callable[[Result], None] | None = None,
    ) -> list[Result]:
        self.outdir.mkdir(parents=True, exist_ok=True)
        refs = [i for i in items if i["data"]["itemType"] not in SKIP_TYPES]
        results = []
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for res in pool.map(lambda it: self.fetch_one(it, items), refs):
                results.append(res)  # pool.map keeps the library's order
                if progress:
                    progress(res)
        self.write_report(results)
        return results

    def fetch_one(self, item: dict[str, Any], all_items: list[dict[str, Any]]) -> Result:
        d = item["data"]
        name = self.filename(item)
        dest = self.outdir / name
        res = Result(item["key"], d.get("title", ""), "", file=name)
        if dest.exists():
            res.status = "exists"
            return res
        att = self.zotero_pdf(item, all_items)
        if att:
            try:
                self.zot.dump(att, name, str(self.outdir))
                res.status, res.source = "zotero", f"attachment {att}"
                return res
            except Exception as e:  # fall back to OpenAlex
                res.detail = f"zotero file: {e}; "
        try:
            work, how = self.openalex_work(item)
        except httpx.HTTPError as e:
            res.status, res.detail, res.file = "failed", f"OpenAlex: {e}", ""
            return res
        if not work:
            res.status, res.file = "not_found", ""
            res.detail += "no OpenAlex match"
            return res
        urls = self.pdf_urls(work)
        if not urls:
            res.status, res.file, res.source = "no_oa", "", f"{work['id']} ({how})"
            res.detail += "no open-access PDF known"
            return res
        errors = []
        for url in urls:
            err = self.download(url, dest)
            if not err:
                res.status, res.source = "openalex", url
                return res
            errors.append(f"{url} -> {err}")
        res.status, res.file, res.source = "failed", "", f"{work['id']} ({how})"
        res.detail += " | ".join(errors)
        return res

    def write_report(self, results: list[Result]) -> Path:
        path = self.outdir / "report.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["key", "status", "file", "source", "title", "detail"])
            for r in results:
                w.writerow([r.key, r.status, r.file, r.source, r.title, r.detail])
        return path


def clean_doi(text: str) -> str:
    m = re.search(r"10\.\d{4,9}/[^\s\"<>]+", text or "")
    return m.group(0).rstrip(".,;") if m else ""


def similarity(a: str, b: str) -> float:
    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
        return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())

    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()
