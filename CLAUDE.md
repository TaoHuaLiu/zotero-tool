# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`zotero-tool`: a Typer CLI doing CRUD on Zotero group (shared) libraries via the Zotero Web API v3, on top of pyzotero. Public GitHub repo: secrets live only in `.env` (git-ignored; `.env.example` is the committed template).

## Commands

```sh
uv sync                                   # install (never pip)
uv run zotero-tool --help                 # CLI; global options go BEFORE the subcommand
uv run zotero-tool -g 6761034 items list  # group "MR", the shared library this was built for
uv run pytest                             # all tests (offline)
uv run pytest tests/test_library.py::test_trash_sets_deleted_flag   # one test
uv run zotero-tool -g 6761034 pdfs fetch -o pdfs/   # MR has no stored files: all PDFs come from OpenAlex
uv run ruff check . && uv run ruff format .
```

## Architecture

- `config.py`: loads `.env` (searched upward from cwd) and resolves the target library: `--user` > `--group` > `ZOTERO_GROUP_ID`.
- `library.py`: `Library` wraps a pyzotero client and holds all CRUD logic. Conventions every write follows:
  - **Optimistic concurrency**: read the object, send its `version` (pyzotero turns it into `If-Unmodified-Since-Version`). A concurrent edit by another group member raises `PreConditionFailedError` (412), reported by `cli.main()` as a conflict. Never drop the version to "make a write go through".
  - **Updates are PATCHes of changed fields only** (plus `key` and `version`), so other members' edits to other fields survive.
  - **`dry_run`** returns `{"dry_run": payload}` before any write call.
  - Delete has two levels: `trash_items` PATCHes `deleted: 1` (recoverable), `delete_items` is permanent.
  - Writes are chunked at 50 objects (API limit); `create_items` re-offsets the per-batch result indexes.
- `cli.py`: thin Typer layer. Sub-apps `items`, `collections`, `tags`; global flags live in a module-level `state` set by the callback. `emit()` prints a rich table or JSON (`--json`). Destructive commands call `confirm()` (skipped by `--yes` or `--dry-run`).
- `pdfs.py`: `Fetcher` behind `pdfs fetch`. Per reference: stored PDF attachment (`zot.dump`), else OpenAlex by DOI, else OpenAlex `title.search` accepted only at similarity ≥ 0.9; a download counts only if the body starts with `%PDF`. Writes `report.csv` in the output folder. Downloaded PDFs are copyrighted: `pdfs/` and `*.pdf` are git-ignored and must stay so in this public repo.
- `manual.py`: the hand-off for blocked downloads. `pdfs links` writes `pdfs/missing.html` from `report.csv` (its `links` column) and opens it in Chrome; `pdfs import` matches PDFs in `~/Downloads` to missing references (DOI, then title prefix in `pdftotext` output) and copies them under `Fetcher.filename`. The item key is always the last `_` part of a PDF's file name; `pdfs upload` relies on it.
- `pdfs upload` (`Library.attach_pdf` / `fill_attachment`) writes files into the shared group: add-only, skips items that already have a PDF. Run it with `-n` first and with `-k` keys to limit it.
- Do not try to get past publishers' bot blocks (browser User-Agent spoofing etc.): Claude Code's permission checks refuse it, and blocked free copies go through `pdfs links` → Chrome → `pdfs import` instead.
- `tests/test_library.py` uses a `FakeZotero` in-memory stand-in; add the pyzotero methods a new feature calls to it rather than hitting the network.

## Gotchas

- The working folder is under iCloud-synced `~/Documents`: iCloud sets the `hidden` flag on venv files (Python 3.12 then skips `zotero_tool.pth` → `ModuleNotFoundError: zotero_tool`) and creates `… 2.pth` duplicates. The venv therefore lives in `.venv.nosync/` (not synced) with `.venv` as a symlink to it. If the error comes back: `chflags -R nohidden .venv.nosync` and delete any `* 2.*` files in site-packages.
- The API key has write access to the groups but **not** to the personal library (`--user` is read-only with this key).
- Live write tests touch a library other people use: prefer `--dry-run`, and trash anything created for testing.
