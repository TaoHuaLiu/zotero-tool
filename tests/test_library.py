"""Library logic against an in-memory stand-in for pyzotero (no network)."""

import pytest

from zotero_tool.library import Library, parse_assignments, parse_creator, summarize


class FakeZotero:
    def __init__(self, items=None, collections=None):
        self.store = {i["key"]: i for i in (items or [])}
        self.cols = {c["key"]: c for c in (collections or [])}
        self.calls = []
        self.links = {}

    # reads
    def item(self, key):
        return {"key": key, "data": self.store[key]}

    def item_template(self, item_type):
        return {
            "itemType": item_type,
            "title": "",
            "date": "",
            "tags": [],
            "creators": [{"creatorType": "author", "firstName": "", "lastName": ""}],
            "collections": [],
            "relations": {},
        }

    def collection(self, key):
        return {"key": key, "data": self.cols[key]}

    def items(self, **kw):
        tag = kw.get("tag")
        return [
            {"key": k, "data": d}
            for k, d in self.store.items()
            if not tag or tag in [t["tag"] for t in d.get("tags", [])]
        ]

    def everything(self, q):
        return list(q)

    # writes
    def update_item(self, patch):
        self.calls.append(("update_item", patch))
        d = self.store[patch["key"]]
        d.update({k: v for k, v in patch.items() if k != "key"})
        d["version"] += 1

    def create_items(self, batch):
        self.calls.append(("create_items", len(batch)))
        return {"success": {str(i): f"NEW{i}" for i in range(len(batch))}, "failed": {}}

    def delete_item(self, batch):
        self.calls.append(("delete_item", [b["key"] for b in batch]))

    def delete_tags(self, *tags):
        self.calls.append(("delete_tags", tags))

    def update_collection(self, patch):
        self.calls.append(("update_collection", patch))


def make_item(key, **data):
    base = {
        "key": key,
        "version": 5,
        "itemType": "journalArticle",
        "title": "T",
        "tags": [],
        "collections": [],
        "creators": [],
    }
    base.update(data)
    return base


def test_parse_assignments_keeps_equals_in_value():
    assert parse_assignments(["url=https://x?a=b", "title=A"]) == {
        "url": "https://x?a=b",
        "title": "A",
    }
    with pytest.raises(ValueError):
        parse_assignments(["oops"])


def test_parse_creator_variants():
    assert parse_creator("Doe, Jane") == {
        "creatorType": "author",
        "lastName": "Doe",
        "firstName": "Jane",
    }
    assert parse_creator("editor:ACME") == {"creatorType": "editor", "name": "ACME"}


def test_summarize_first_author_et_al():
    s = summarize(
        {"data": make_item("K", creators=[{"lastName": "A"}, {"lastName": "B"}], date="2021-03")}
    )
    assert s["creator"] == "A et al." and s["year"] == "2021"


def test_update_sends_only_changes_with_version():
    z = FakeZotero([make_item("K", tags=[{"tag": "old", "type": 1}, {"tag": "keep"}])])
    Library(z).update_item("K", fields={"title": "New"}, add_tags=["new"], remove_tags=["old"])
    ((_, patch),) = z.calls
    assert patch["version"] == 5
    assert patch["title"] == "New"
    assert patch["tags"] == [{"tag": "keep"}, {"tag": "new"}]
    assert "creators" not in patch  # untouched fields are not sent


def test_update_rejects_unknown_field():
    z = FakeZotero([make_item("K")])
    with pytest.raises(ValueError, match="nope"):
        Library(z).update_item("K", fields={"nope": "x"})


def test_dry_run_writes_nothing():
    z = FakeZotero([make_item("K")])
    lib = Library(z, dry_run=True)
    assert "dry_run" in lib.update_item("K", fields={"title": "X"})
    assert "dry_run" in lib.trash_items(["K"])
    assert "dry_run" in lib.delete_items(["K"])
    assert "dry_run" in lib.create_items([{"itemType": "book"}])
    assert z.calls == []


def test_trash_sets_deleted_flag():
    z = FakeZotero([make_item("K")])
    Library(z).trash_items(["K"])
    assert z.calls == [("update_item", {"key": "K", "version": 5, "deleted": 1})]


def test_create_batches_by_50_and_offsets_indexes():
    z = FakeZotero()
    res = Library(z).create_items([{"itemType": "book"}] * 120)
    assert [c[1] for c in z.calls] == [50, 50, 20]
    assert len(res["success"]) == 120 and "119" in res["success"]


def test_new_item_validates_fields_and_drops_empty_creator():
    lib = Library(FakeZotero())
    item = lib.new_item("book", {"title": "B"}, tags=["t"])
    assert item["creators"] == [] and item["tags"] == [{"tag": "t"}]
    with pytest.raises(ValueError, match="volume"):
        lib.new_item("book", {"volume": "1"})


def test_rename_tag_touches_only_tagged_items():
    z = FakeZotero([make_item("A", tags=[{"tag": "x"}]), make_item("B")])
    Library(z).rename_tag("x", "y")
    assert [p["key"] for _, p in z.calls] == ["A"]
    assert z.store["A"]["tags"] == [{"tag": "y"}]


def test_move_collection_to_top_level():
    z = FakeZotero(collections=[{"key": "C", "version": 3, "name": "n", "parentCollection": "P"}])
    Library(z).update_collection("C", parent="")
    assert z.calls == [("update_collection", {"key": "C", "version": 3, "parentCollection": False})]
