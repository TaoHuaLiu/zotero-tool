"""CRUD operations on one Zotero library (group or user), on top of pyzotero.

Every write goes through the object's current `version`, so the Zotero API
rejects it with 412 if someone else changed the object in the meantime: in a
shared library this is what stops silent overwrites. Writes honour `dry_run`
and then only return what would be sent.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pyzotero import zotero

from .config import Settings

MAX_BATCH = 50  # Zotero API limit for writes in one request


def connect(settings: Settings) -> zotero.Zotero:
    return zotero.Zotero(settings.library_id, settings.library_type, settings.api_key)


def chunks(seq: list[Any], size: int = MAX_BATCH) -> Iterable[list[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def parse_assignments(pairs: Iterable[str]) -> dict[str, str]:
    """`["title=Foo", "date=2024"]` -> `{"title": "Foo", "date": "2024"}`."""
    out: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Expected FIELD=VALUE, got {pair!r}")
        field, value = pair.split("=", 1)
        out[field.strip()] = value
    return out


def parse_creator(spec: str) -> dict[str, str]:
    """`"author:Doe, Jane"` -> two-field creator; `"editor:ACME Corp"` -> single-field name.

    The creator type defaults to `author` when no `type:` prefix is given.
    """
    ctype, _, name = spec.partition(":") if ":" in spec else ("author", "", spec)
    ctype, name = ctype.strip() or "author", name.strip()
    if "," in name:
        last, first = (p.strip() for p in name.split(",", 1))
        return {"creatorType": ctype, "lastName": last, "firstName": first}
    return {"creatorType": ctype, "name": name}


def summarize(item: dict[str, Any]) -> dict[str, Any]:
    """Short view of an item for tables: key, type, first creator, year, title."""
    d = item.get("data", item)
    creators = d.get("creators") or []
    first = ""
    if creators:
        c = creators[0]
        first = c.get("lastName") or c.get("name") or ""
        if len(creators) > 1:
            first += " et al."
    title = d.get("title") or d.get("note", "")[:60] or d.get("name", "")
    return {
        "key": d.get("key", ""),
        "type": d.get("itemType", ""),
        "creator": first,
        "year": (item.get("meta", {}).get("parsedDate") or d.get("date", ""))[:4],
        "title": title,
    }


class Library:
    def __init__(self, client: zotero.Zotero, dry_run: bool = False) -> None:
        self.z = client
        self.dry_run = dry_run

    # ---------- read ----------

    def list_items(
        self,
        query: str | None = None,
        tag: str | None = None,
        collection: str | None = None,
        item_type: str | None = None,
        top: bool = True,
        limit: int | None = 50,
        everything_mode: str = "titleCreatorYear",
        sort: str = "dateModified",
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"sort": sort, "direction": "desc"}
        if query:
            params["q"] = query
            params["qmode"] = everything_mode
        if tag:
            params["tag"] = tag
        if item_type:
            params["itemType"] = item_type
        if limit:
            params["limit"] = min(limit, 100)
        if collection:
            fetch = self.z.collection_items_top if top else self.z.collection_items
            first = fetch(collection, **params)
        else:
            fetch = self.z.top if top else self.z.items
            first = fetch(**params)
        if limit is None:
            return self.z.everything(first) if first is not None else []
        items = list(first)
        while len(items) < limit and self._has_next():
            items.extend(self.z.follow())
        return items[:limit]

    def _has_next(self) -> bool:
        links = getattr(self.z, "links", None) or {}
        return "next" in links

    def get_item(self, key: str) -> dict[str, Any]:
        return self.z.item(key)

    def children(self, key: str) -> list[dict[str, Any]]:
        return self.z.children(key)

    def list_collections(self) -> list[dict[str, Any]]:
        return self.z.everything(self.z.collections())

    def list_tags(self) -> list[str]:
        return self.z.everything(self.z.tags())

    def count(self) -> int:
        return self.z.count_items()

    # ---------- attachments ----------

    def pdf_attachments(self, key: str) -> list[dict[str, Any]]:
        """Stored or linked PDF attachments that are children of item `key`."""
        return [
            c
            for c in self.z.children(key)
            if c["data"].get("itemType") == "attachment"
            and c["data"].get("contentType") == "application/pdf"
        ]

    def attach_pdf(self, parent_key: str, path: str, title: str = "Full Text PDF") -> Any:
        """Upload `path` as a stored-file child attachment of `parent_key` (adds only)."""
        payload = {"parentItem": parent_key, "title": title, "filename": path}
        if self.dry_run:
            return {"dry_run": payload}
        return self.z.attachment_both([(title, path)], parentid=parent_key)

    def fill_attachment(self, att_key: str, path: str) -> Any:
        """Upload `path` into an existing attachment item whose file never arrived."""
        payload = {"key": att_key, "filename": path}
        if self.dry_run:
            return {"dry_run": payload}
        return self.z.upload_attachments([payload])

    # ---------- create ----------

    def new_item(
        self,
        item_type: str,
        fields: dict[str, Any] | None = None,
        creators: list[dict[str, str]] | None = None,
        tags: list[str] | None = None,
        collections: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build an item payload from the server template for `item_type`."""
        tpl = dict(self.z.item_template(item_type))
        unknown = set(fields or {}) - set(tpl)
        if unknown:
            raise ValueError(
                f"Field(s) not valid for {item_type}: {', '.join(sorted(unknown))}. "
                f"Run `zotero-tool fields {item_type}` to list them."
            )
        tpl.update(fields or {})
        if creators:
            tpl["creators"] = creators
        elif tpl.get("creators") == [{"creatorType": "author", "firstName": "", "lastName": ""}]:
            tpl["creators"] = []
        if tags:
            tpl["tags"] = [{"tag": t} for t in tags]
        if collections:
            tpl["collections"] = list(collections)
        return tpl

    def create_items(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        """Create items in batches of 50. Returns `{"success": {...}, "failed": {...}}`
        with keys of the created items (or the payloads under `dry_run`)."""
        if self.dry_run:
            return {"dry_run": payloads}
        result: dict[str, Any] = {"success": {}, "failed": {}}
        offset = 0
        for batch in chunks(payloads):
            resp = self.z.create_items(batch)
            for idx, val in resp.get("success", {}).items():
                result["success"][str(int(idx) + offset)] = val
            for idx, val in resp.get("failed", {}).items():
                result["failed"][str(int(idx) + offset)] = val
            offset += len(batch)
        return result

    # ---------- update ----------

    def update_item(
        self,
        key: str,
        fields: dict[str, Any] | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
        add_collections: list[str] | None = None,
        remove_collections: list[str] | None = None,
        creators: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """PATCH only the changed fields, guarded by the item's version."""
        item = self.get_item(key)
        data = item["data"]
        patch: dict[str, Any] = {"key": key, "version": data["version"]}
        if fields:
            unknown = set(fields) - set(data)
            if unknown:
                raise ValueError(
                    f"Field(s) not valid for {data['itemType']}: {', '.join(sorted(unknown))}"
                )
            patch.update(fields)
        if creators is not None:
            patch["creators"] = creators
        if add_tags or remove_tags:
            current = [t["tag"] for t in data.get("tags", [])]
            kept = [t for t in current if t not in set(remove_tags or [])]
            kept += [t for t in (add_tags or []) if t not in kept]
            # keep the tag type (manual/automatic) of existing tags
            types = {t["tag"]: t.get("type") for t in data.get("tags", [])}
            patch["tags"] = [
                {"tag": t, "type": types[t]} if types.get(t) else {"tag": t} for t in kept
            ]
        if add_collections or remove_collections:
            cols = [
                c for c in data.get("collections", []) if c not in set(remove_collections or [])
            ]
            cols += [c for c in (add_collections or []) if c not in cols]
            patch["collections"] = cols
        if self.dry_run:
            return {"dry_run": patch}
        self.z.update_item(patch)
        return {
            "updated": key,
            "changes": {k: v for k, v in patch.items() if k not in ("key", "version")},
        }

    # ---------- delete ----------

    def trash_items(self, keys: list[str]) -> dict[str, Any]:
        """Move items to the library's trash (recoverable from Zotero)."""
        if self.dry_run:
            return {"dry_run": {"trash": keys}}
        done = []
        for key in keys:
            data = self.get_item(key)["data"]
            self.z.update_item({"key": key, "version": data["version"], "deleted": 1})
            done.append(key)
        return {"trashed": done}

    def restore_items(self, keys: list[str]) -> dict[str, Any]:
        if self.dry_run:
            return {"dry_run": {"restore": keys}}
        for key in keys:
            data = self.get_item(key)["data"]
            self.z.update_item({"key": key, "version": data["version"], "deleted": 0})
        return {"restored": keys}

    def delete_items(self, keys: list[str]) -> dict[str, Any]:
        """Permanently delete items (no trash). Each item's version is sent."""
        items = [self.get_item(k) for k in keys]
        if self.dry_run:
            return {"dry_run": {"delete": keys}}
        for batch in chunks(items):
            self.z.delete_item(batch)
        return {"deleted": keys}

    # ---------- collections ----------

    def create_collection(self, name: str, parent: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name}
        if parent:
            payload["parentCollection"] = parent
        if self.dry_run:
            return {"dry_run": payload}
        return self.z.create_collections([payload])

    def update_collection(
        self, key: str, name: str | None = None, parent: str | None = None
    ) -> dict[str, Any]:
        col = self.z.collection(key)
        data = col["data"]
        patch: dict[str, Any] = {"key": key, "version": data["version"]}
        if name is not None:
            patch["name"] = name
        if parent is not None:
            patch["parentCollection"] = parent or False  # "" -> move to top level
        if self.dry_run:
            return {"dry_run": patch}
        self.z.update_collection(patch)
        return {"updated": key}

    def delete_collection(self, key: str) -> dict[str, Any]:
        """Delete a collection. Its items stay in the library."""
        col = self.z.collection(key)
        if self.dry_run:
            return {"dry_run": {"delete_collection": key, "name": col["data"]["name"]}}
        self.z.delete_collection(col)
        return {"deleted": key}

    # ---------- tags ----------

    def delete_tags(self, tags: list[str]) -> dict[str, Any]:
        """Remove tags from every item of the library."""
        if self.dry_run:
            return {"dry_run": {"delete_tags": tags}}
        for batch in chunks(tags):
            self.z.delete_tags(*batch)
        return {"deleted_tags": tags}

    def rename_tag(self, old: str, new: str) -> dict[str, Any]:
        """Rename a tag on every item that carries it (one versioned PATCH per item)."""
        items = self.z.everything(self.z.items(tag=old))
        keys = [i["key"] for i in items]
        if self.dry_run:
            return {"dry_run": {"rename_tag": [old, new], "items": keys}}
        for i in items:
            self.update_item(i["key"], add_tags=[new], remove_tags=[old])
        return {"renamed": [old, new], "items": keys}
