# zotero-tool

Command-line CRUD for Zotero **group (shared) libraries** through the [Zotero Web API v3](https://www.zotero.org/support/dev/web_api/v3/start), built on [pyzotero](https://github.com/urschrei/pyzotero).

- Items: list / search, get, create (from flags or JSON), update fields, tags, collections and creators, trash / restore, permanent delete
- Collections: list, create, rename / move, delete
- Tags: list, rename across the library, delete
- PDFs: download one PDF per reference, from the library's stored attachments or else a legal open-access copy found on [OpenAlex](https://openalex.org) (by DOI, or by close title match)
- Safe in a shared library: every write sends the object's version, so a change made meanwhile by another member is refused (HTTP 412) instead of overwritten; `--dry-run` shows the payload; destructive commands ask for confirmation; `items trash` is recoverable from Zotero.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/TaoHuaLiu/zotero-tool && cd zotero-tool
uv sync
cp .env.example .env   # then fill in ZOTERO_API_KEY (and ZOTERO_GROUP_ID)
uv run zotero-tool groups
```

Create the API key at <https://www.zotero.org/settings/keys> with read/write access to the group. Configuration is read from `.env` in the current directory or a parent, or from the environment:

| Variable | Use |
| --- | --- |
| `ZOTERO_API_KEY` | required |
| `ZOTERO_GROUP_ID` | default library; overridden by `--group/-g` |
| `ZOTERO_USER_ID` | only with `--user`; looked up from the key if unset |

## Usage

Global options go **before** the subcommand: `--group/-g ID`, `--user`, `--json`, `--dry-run/-n`.

```sh
# read
uv run zotero-tool items list -q "trust" --limit 20
uv run zotero-tool items list --collection PBDXD24Z --tag to-read
uv run zotero-tool items get ABCD1234 --children
uv run zotero-tool --json items list --limit 0 > _build/all.json

# create
uv run zotero-tool fields journalArticle          # valid fields + creator types
uv run zotero-tool items create --type journalArticle \
    --set title="A paper" --set date=2025 --set DOI=10.1000/xyz \
    --creator "author:Doe, Jane" --tag to-read --collection PBDXD24Z
uv run zotero-tool items create --from-json items.json   # one object or a list (batched by 50)

# update
uv run zotero-tool -n items update ABCD1234 --set title="Fixed" --add-tag read --remove-tag to-read
uv run zotero-tool items update ABCD1234 --add-collection ZR4UDK5P

# delete
uv run zotero-tool items trash ABCD1234 EFGH5678
uv run zotero-tool items restore ABCD1234
uv run zotero-tool items delete ABCD1234 --yes     # permanent

# collections and tags
uv run zotero-tool collections create "Week 3" --parent PBDXD24Z
uv run zotero-tool collections update KEY --name "Week 3 (done)"
uv run zotero-tool tags rename "to read" to-read
```

## PDFs

```sh
uv run zotero-tool pdfs fetch --out pdfs/            # whole library; re-runs skip files already there
uv run zotero-tool pdfs fetch -c PBDXD24Z -o pdfs/   # one collection
```

Every reference gets a line in `pdfs/report.csv`: `zotero` / `openalex` (downloaded), `exists`, `no_oa` (found, no free copy known), `not_found` (no OpenAlex match), `failed` (free copy known but the host refused or served a web page). Paywalled publisher PDFs are not fetched. Set `OPENALEX_API_KEY` for a dedicated OpenAlex quota (optional). `pdfs/` is git-ignored.

## Development

```sh
uv run pytest            # unit tests, no network
uv run ruff check . && uv run ruff format .
```

## License

MIT
