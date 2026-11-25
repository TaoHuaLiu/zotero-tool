"""`zotero-tool` command line: CRUD on a Zotero group (shared) or user library."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from pyzotero import zotero_errors as ze
from rich.console import Console
from rich.table import Table

from .config import ConfigError, load_api_key, load_settings, lookup_user_id
from .library import Library, connect, parse_assignments, parse_creator, summarize

app = typer.Typer(no_args_is_help=True, help=__doc__, pretty_exceptions_show_locals=False)
items_app = typer.Typer(no_args_is_help=True, help="Create, read, update and delete items.")
cols_app = typer.Typer(no_args_is_help=True, help="Manage collections.")
tags_app = typer.Typer(no_args_is_help=True, help="Manage tags.")
app.add_typer(items_app, name="items")
app.add_typer(cols_app, name="collections")
app.add_typer(tags_app, name="tags")

out = Console()
err = Console(stderr=True)


class State:
    group: str | None = None
    user: bool = False
    as_json: bool = False
    dry_run: bool = False


state = State()


@app.callback()
def main_options(
    group: Annotated[
        str | None, typer.Option("--group", "-g", help="Group ID (default: ZOTERO_GROUP_ID).")
    ] = None,
    user: Annotated[bool, typer.Option("--user", help="Use your personal library.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print raw JSON.")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Show what a write would send; change nothing.")
    ] = False,
) -> None:
    state.group, state.user, state.as_json, state.dry_run = group, user, as_json, dry_run


def lib() -> Library:
    try:
        settings = load_settings(state.group, state.user)
    except ConfigError as e:
        err.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from e
    return Library(connect(settings), dry_run=state.dry_run)


def emit(data: Any, columns: list[str] | None = None, rows: list[dict] | None = None) -> None:
    """JSON when --json (or no table given), otherwise a table of `rows`."""
    if state.as_json or rows is None:
        out.print_json(json.dumps(data, ensure_ascii=False, default=str))
        return
    table = Table(show_lines=False)
    for c in columns or (list(rows[0]) if rows else []):
        table.add_column(c, overflow="fold")
    for r in rows:
        table.add_row(*(str(r.get(c, "")) for c in (columns or list(r))))
    out.print(table)


def confirm(msg: str, yes: bool) -> None:
    if yes or state.dry_run:
        return
    if not typer.confirm(msg, default=False):
        raise typer.Abort()


def read_json_arg(path: str) -> Any:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return json.loads(text)


# ---------------------------------------------------------------- library info


@app.command()
def groups() -> None:
    """List the groups your API key can reach."""
    from pyzotero import zotero

    try:
        api_key = load_api_key()
    except ConfigError as e:
        err.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from e
    z = zotero.Zotero(lookup_user_id(api_key), "user", api_key)
    gs = z.groups()
    rows = [
        {"id": g["id"], "name": g["data"]["name"], "items": g["meta"].get("numItems", "")}
        for g in gs
    ]
    emit(gs, ["id", "name", "items"], rows)


@app.command()
def types() -> None:
    """List item types (for `items create --type`)."""
    ts = lib().z.item_types()
    emit(ts, ["itemType", "localized"], ts)


@app.command()
def fields(item_type: str) -> None:
    """List the fields and creator types valid for ITEM_TYPE."""
    z = lib().z
    fs = z.item_type_fields(item_type)
    cts = [c["creatorType"] for c in z.item_creator_types(item_type)]
    if state.as_json:
        emit({"fields": fs, "creatorTypes": cts})
        return
    emit(fs, ["field", "localized"], fs)
    out.print(f"creator types: {', '.join(cts)}")


# ---------------------------------------------------------------- items

ITEM_COLS = ["key", "type", "creator", "year", "title"]


@items_app.command("list")
def items_list(
    query: Annotated[str | None, typer.Option("--query", "-q", help="Quick search.")] = None,
    everything: Annotated[
        bool, typer.Option("--everything", help="Search all fields and full text.")
    ] = False,
    tag: Annotated[str | None, typer.Option("--tag", "-t")] = None,
    collection: Annotated[str | None, typer.Option("--collection", "-c")] = None,
    item_type: Annotated[str | None, typer.Option("--type")] = None,
    children: Annotated[bool, typer.Option("--all", help="Include notes and attachments.")] = False,
    limit: Annotated[int, typer.Option("--limit", "-l", help="0 = no limit.")] = 50,
    sort: Annotated[
        str, typer.Option(help="dateModified, dateAdded, title, creator, date…")
    ] = "dateModified",
) -> None:
    """List items (top-level only unless --all)."""
    items = lib().list_items(
        query=query,
        tag=tag,
        collection=collection,
        item_type=item_type,
        top=not children,
        limit=limit or None,
        everything_mode="everything" if everything else "titleCreatorYear",
        sort=sort,
    )
    emit(items, ITEM_COLS, [summarize(i) for i in items])


@items_app.command("get")
def items_get(
    key: str,
    children: Annotated[
        bool, typer.Option("--children", help="Also list notes/attachments.")
    ] = False,
) -> None:
    """Show one item's full data."""
    library = lib()
    item = library.get_item(key)
    data: Any = item["data"]
    if children:
        data = {"item": data, "children": [c["data"] for c in library.children(key)]}
    emit(data)


@items_app.command("create")
def items_create(
    item_type: Annotated[
        str | None, typer.Option("--type", help="e.g. journalArticle, book.")
    ] = None,
    set_: Annotated[list[str], typer.Option("--set", "-s", help="FIELD=VALUE, repeatable.")] = [],
    creator: Annotated[
        list[str],
        typer.Option("--creator", help='"author:Last, First" or "editor:Org", repeatable.'),
    ] = [],
    tag: Annotated[list[str], typer.Option("--tag", "-t")] = [],
    collection: Annotated[
        list[str], typer.Option("--collection", "-c", help="Collection key.")
    ] = [],
    from_json: Annotated[
        str | None,
        typer.Option("--from-json", help="File (or - for stdin) with one item or a list of items."),
    ] = None,
) -> None:
    """Create item(s) from flags or from a JSON file."""
    library = lib()
    if from_json:
        data = read_json_arg(from_json)
        payloads = data if isinstance(data, list) else [data]
    elif item_type:
        try:
            payloads = [
                library.new_item(
                    item_type,
                    parse_assignments(set_),
                    [parse_creator(c) for c in creator],
                    tag,
                    collection,
                )
            ]
        except ValueError as e:
            err.print(f"[red]{e}[/red]")
            raise typer.Exit(2) from e
    else:
        err.print("[red]Give --type (with --set …) or --from-json.[/red]")
        raise typer.Exit(2)
    emit(library.create_items(payloads))


@items_app.command("update")
def items_update(
    key: str,
    set_: Annotated[list[str], typer.Option("--set", "-s", help="FIELD=VALUE, repeatable.")] = [],
    add_tag: Annotated[list[str], typer.Option("--add-tag")] = [],
    remove_tag: Annotated[list[str], typer.Option("--remove-tag")] = [],
    add_collection: Annotated[list[str], typer.Option("--add-collection")] = [],
    remove_collection: Annotated[list[str], typer.Option("--remove-collection")] = [],
    creator: Annotated[
        list[str], typer.Option("--creator", help="Replace all creators; repeatable.")
    ] = [],
) -> None:
    """Change fields, tags, collections or creators of one item."""
    try:
        res = lib().update_item(
            key,
            fields=parse_assignments(set_),
            add_tags=add_tag,
            remove_tags=remove_tag,
            add_collections=add_collection,
            remove_collections=remove_collection,
            creators=[parse_creator(c) for c in creator] if creator else None,
        )
    except ValueError as e:
        err.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from e
    emit(res)


@items_app.command("trash")
def items_trash(keys: list[str], yes: Annotated[bool, typer.Option("--yes", "-y")] = False) -> None:
    """Move items to the trash (recoverable with `items restore`)."""
    confirm(f"Move {len(keys)} item(s) to the trash?", yes)
    emit(lib().trash_items(keys))


@items_app.command("restore")
def items_restore(keys: list[str]) -> None:
    """Take items out of the trash."""
    emit(lib().restore_items(keys))


@items_app.command("delete")
def items_delete(
    keys: list[str], yes: Annotated[bool, typer.Option("--yes", "-y")] = False
) -> None:
    """Delete items PERMANENTLY (skips the trash). Prefer `items trash`."""
    confirm(f"Permanently delete {len(keys)} item(s) for every member of the library?", yes)
    emit(lib().delete_items(keys))


# ---------------------------------------------------------------- collections


@cols_app.command("list")
def cols_list() -> None:
    """List all collections."""
    cs = lib().list_collections()
    names = {c["key"]: c["data"]["name"] for c in cs}
    rows = [
        {
            "key": c["key"],
            "name": c["data"]["name"],
            "parent": names.get(c["data"].get("parentCollection") or "", ""),
            "items": c["meta"].get("numItems", ""),
        }
        for c in sorted(cs, key=lambda c: c["data"]["name"].lower())
    ]
    emit(cs, ["key", "name", "parent", "items"], rows)


@cols_app.command("create")
def cols_create(
    name: str, parent: Annotated[str | None, typer.Option("--parent", help="Parent key.")] = None
) -> None:
    """Create a collection."""
    emit(lib().create_collection(name, parent))


@cols_app.command("update")
def cols_update(
    key: str,
    name: Annotated[str | None, typer.Option("--name")] = None,
    parent: Annotated[
        str | None, typer.Option("--parent", help='New parent key, "" for top level.')
    ] = None,
) -> None:
    """Rename or move a collection."""
    emit(lib().update_collection(key, name, parent))


@cols_app.command("delete")
def cols_delete(key: str, yes: Annotated[bool, typer.Option("--yes", "-y")] = False) -> None:
    """Delete a collection (its items stay in the library)."""
    confirm(f"Delete collection {key}?", yes)
    emit(lib().delete_collection(key))


# ---------------------------------------------------------------- tags


@tags_app.command("list")
def tags_list() -> None:
    """List all tags in the library."""
    ts = sorted(lib().list_tags(), key=str.lower)
    emit(ts, ["tag"], [{"tag": t} for t in ts])


@tags_app.command("rename")
def tags_rename(
    old: str, new: str, yes: Annotated[bool, typer.Option("--yes", "-y")] = False
) -> None:
    """Rename a tag on every item carrying it."""
    confirm(f"Rename tag {old!r} to {new!r} on all items?", yes)
    emit(lib().rename_tag(old, new))


@tags_app.command("delete")
def tags_delete(tags: list[str], yes: Annotated[bool, typer.Option("--yes", "-y")] = False) -> None:
    """Remove tags from every item."""
    confirm(f"Remove {len(tags)} tag(s) from all items?", yes)
    emit(lib().delete_tags(tags))


def main() -> None:
    try:
        app()
    except ze.PreConditionFailedError:
        err.print(
            "[red]Conflict: the object changed on the server since it was read "
            "(someone else edited it). Re-run the command.[/red]"
        )
        sys.exit(1)
    except ze.PyZoteroError as e:
        err.print(f"[red]Zotero API error: {e}[/red]")
        sys.exit(1)
