"""Import from Zotero's own database (a small copy of its real schema)."""
import os
import sqlite3

import library
import zotero_local

SCHEMA = """
CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INT, libraryID INT, key TEXT);
CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value);
CREATE TABLE itemData (itemID INT, fieldID INT, valueID INT);
CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT, fieldMode INT);
CREATE TABLE creatorTypes (creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT);
CREATE TABLE itemCreators (itemID INT, creatorID INT, creatorTypeID INT, orderIndex INT);
CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY);
CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT, parentCollectionID INT, libraryID INT);
CREATE TABLE collectionItems (collectionID INT, itemID INT, orderIndex INT);
"""


def make_db(path):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO itemTypes VALUES (?,?)", [(1, "journalArticle"), (2, "book"), (3, "attachment"), (4, "webpage")])
    fields = ["title", "publicationTitle", "volume", "pages", "date", "DOI", "publisher", "url", "extra", "websiteTitle"]
    con.executemany("INSERT INTO fields VALUES (?,?)", list(enumerate(fields, 1)))
    con.executemany("INSERT INTO creatorTypes VALUES (?,?)", [(1, "author"), (2, "editor")])
    items = [(1, 1, "AAAA1111"), (2, 2, "BBBB2222"), (3, 3, "CCCC3333"), (4, 1, "DDDD4444"), (5, 4, "EEEE5555")]
    con.executemany("INSERT INTO items VALUES (?,?,1,?)", items)
    vals, data = [], []

    def put(item, field, value):
        vals.append((len(vals) + 1, value))
        data.append((item, fields.index(field) + 1, len(vals)))
    put(1, "title", "Sustainable decommissioning of an offshore wind farm"); put(1, "publicationTitle", "Renewable Energy")
    put(1, "volume", "102"); put(1, "pages", "470-480"); put(1, "date", "2017-03-00 March 2017")
    put(1, "DOI", "10.1016/j.renene.2016.10.066")
    put(2, "title", "Wind Energy Handbook"); put(2, "publisher", "Wiley"); put(2, "date", "2011-00-00 2011")
    put(3, "title", "Full text PDF")
    put(4, "title", "A deleted paper"); put(4, "date", "2020-00-00 2020")
    put(5, "title", "Our offshore wind farms"); put(5, "websiteTitle", "Ørsted"); put(5, "url", "https://orsted.com/x")
    put(5, "extra", "DOI: 10.9999/not-real")
    con.executemany("INSERT INTO itemDataValues VALUES (?,?)", vals)
    con.executemany("INSERT INTO itemData VALUES (?,?,?)", data)
    con.executemany("INSERT INTO creators VALUES (?,?,?,?)", [(1, "Eva", "Topham", 0), (2, "David", "McMillan", 0),
                                                              (3, "", "IEA Wind", 1), (4, "Tony", "Burton", 0)])
    con.executemany("INSERT INTO itemCreators VALUES (?,?,?,?)", [(1, 1, 1, 0), (1, 2, 1, 1), (2, 4, 1, 0), (2, 3, 2, 1)])
    con.execute("INSERT INTO deletedItems VALUES (4)")
    con.executemany("INSERT INTO collections VALUES (?,?,?,1)", [(1, "Thesis", None), (2, "Chapter 2", 1)])
    con.executemany("INSERT INTO collectionItems VALUES (?,?,0)", [(1, 1), (2, 1), (2, 2)])
    con.commit()
    con.close()


def test_read_zotero_database(tmp_path):
    db = str(tmp_path / "zotero.sqlite")
    make_db(db)
    items, info = zotero_local.read_library(db)
    by = {c["title"]: c for c in items}
    assert set(by) == {"Sustainable decommissioning of an offshore wind farm", "Wind Energy Handbook",
                       "Our offshore wind farms"}  # no attachment, nothing from the trash
    t = by["Sustainable decommissioning of an offshore wind farm"]
    assert t["type"] == "article-journal" and t["container-title"] == "Renewable Energy"
    assert t["issued"] == {"date-parts": [[2017, 3]]} and t["page"] == "470-480"
    assert [a["family"] for a in t["author"]] == ["Topham", "McMillan"]
    assert t["_projects"] == ["Thesis", "Thesis / Chapter 2"]
    b = by["Wind Energy Handbook"]
    assert b["editor"] == [{"literal": "IEA Wind"}] and b["issued"] == {"date-parts": [[2011]]}
    w = by["Our offshore wind farms"]
    assert w["type"] == "webpage" and w["container-title"] == "Ørsted" and w["DOI"] == "10.9999/not-real"
    assert info["collections"] == 2


def test_import_keeps_collections_as_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "HOME", str(tmp_path / "library"))  # its own library, not the shared test one
    db = str(tmp_path / "zotero.sqlite")
    make_db(db)
    r = zotero_local.import_library(db_path=db)
    assert r["found"] == 3
    data = library.load()
    it = next(i for i in data["items"] if i["csl"]["title"] == "Wind Energy Handbook")
    assert "Thesis / Chapter 2" in it["projects"] and it.get("zotero_key") == "BBBB2222"
    assert {"Thesis", "Thesis / Chapter 2"} <= set(data["projects"])
    again = zotero_local.import_library(db_path=db)
    assert again["added"] == 0  # no duplicates the second time


def test_not_found_message(tmp_path, monkeypatch):
    monkeypatch.setenv("CITRELLO_ZOTERO_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    try:
        zotero_local.find_database()
        raise AssertionError("expected ZoteroNotFound")
    except zotero_local.ZoteroNotFound as e:
        assert "Export" in str(e)
