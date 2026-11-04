"""
Import the Zotero library on this computer, straight from Zotero's own database (zotero.sqlite).

No Zotero account or API key. Zotero may be open: the database is copied first (or its automatic backup,
zotero.sqlite.bak, is used if the database is locked). Collections become Citrello projects; items in the
trash, notes and attachments are skipped.
"""
import glob
import os
import re
import shutil
import sqlite3
import sys
import tempfile

TYPES = {
    "journalArticle": "article-journal", "book": "book", "bookSection": "chapter", "conferencePaper": "paper-conference",
    "thesis": "thesis", "report": "report", "webpage": "webpage", "preprint": "article", "magazineArticle": "article-magazine",
    "newspaperArticle": "article-newspaper", "dataset": "dataset", "document": "article", "manuscript": "manuscript",
    "patent": "patent", "blogPost": "post-weblog", "encyclopediaArticle": "entry-encyclopedia",
    "dictionaryEntry": "entry-dictionary", "presentation": "speech", "computerProgram": "software",
    "statute": "legislation", "case": "legal_case", "letter": "personal_communication", "interview": "interview",
    "film": "motion_picture", "standard": "standard", "forumPost": "post",
}
SKIP_TYPES = {"attachment", "note", "annotation"}
CONTAINER = ("publicationTitle", "bookTitle", "proceedingsTitle", "websiteTitle", "blogTitle", "encyclopediaTitle",
             "dictionaryTitle", "forumTitle", "programTitle")
FIELDS = {"title": "title", "volume": "volume", "issue": "issue", "pages": "page", "DOI": "DOI", "url": "URL",
          "ISBN": "ISBN", "ISSN": "ISSN", "publisher": "publisher", "place": "publisher-place", "edition": "edition",
          "abstractNote": "abstract", "university": "publisher", "institution": "publisher", "reportNumber": "number",
          "numPages": "number-of-pages", "language": "language", "series": "collection-title",
          "conferenceName": "event-title", "shortTitle": "title-short", "archiveID": "number"}
ROLES = {"author": "author", "editor": "editor", "bookAuthor": "container-author", "seriesEditor": "collection-editor",
         "translator": "translator", "director": "director", "inventor": "author", "programmer": "author",
         "presenter": "author", "contributor": "contributor"}


class ZoteroNotFound(Exception):
    pass


def data_dirs():
    """Where Zotero keeps its library: CITRELLO_ZOTERO_DIR, the dataDir set in Zotero's settings, or ~/Zotero."""
    out = []
    if os.environ.get("CITRELLO_ZOTERO_DIR"):
        out.append(os.environ["CITRELLO_ZOTERO_DIR"])
    home = os.path.expanduser("~")
    if sys.platform.startswith("win"):
        profiles = os.path.join(os.environ.get("APPDATA", home), "Zotero", "Zotero", "Profiles")
    elif sys.platform == "darwin":
        profiles = os.path.join(home, "Library", "Application Support", "Zotero", "Profiles")
    else:
        profiles = os.path.join(home, ".zotero", "zotero")
    for prefs in glob.glob(os.path.join(profiles, "*", "prefs.js")):
        try:
            text = open(prefs, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        m = re.search(r'user_pref\("extensions\.zotero\.dataDir",\s*"((?:[^"\\]|\\.)*)"\)', text)
        if m:
            out.append(m.group(1).encode().decode("unicode_escape"))
    out.append(os.path.join(home, "Zotero"))
    seen, result = set(), []
    for d in out:
        if d and d not in seen:
            seen.add(d)
            result.append(d)
    return result


def find_database():
    for d in data_dirs():
        for name in ("zotero.sqlite", "zotero.sqlite.bak"):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return os.path.join(d, "zotero.sqlite") if os.path.isfile(os.path.join(d, "zotero.sqlite")) else p
    raise ZoteroNotFound("Zotero's library wasn't found on this computer. If Zotero keeps its data somewhere else "
                         "(Zotero → Settings → Advanced → Files and Folders → Data Directory), export a file from "
                         "Zotero instead (File → Export Library → BibTeX or RIS) and import it.")


def _open_copy(path):
    """Copy the database (and its journal) so a running Zotero is not disturbed; fall back to the backup."""
    tmp = tempfile.mkdtemp(prefix="citrello_zotero_")
    tries = [path] + ([path + ".bak"] if not path.endswith(".bak") else [])
    last = None
    for src in tries:
        if not os.path.isfile(src):
            continue
        try:
            dst = os.path.join(tmp, "zotero.sqlite")
            shutil.copy2(src, dst)
            for suffix in ("-wal", "-journal"):
                if os.path.isfile(src + suffix):
                    shutil.copy2(src + suffix, dst + suffix)
            con = sqlite3.connect(dst)
            con.execute("SELECT 1 FROM items LIMIT 1").fetchall()
            return con, tmp, src
        except (OSError, sqlite3.Error) as e:
            last = e
    shutil.rmtree(tmp, ignore_errors=True)
    raise ZoteroNotFound(f"Zotero's library couldn't be read ({last}). Close Zotero and try again.")


def _table(con, *names):
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    return next((n for n in names if n in have), names[-1])


def _date(value):
    """Zotero stores dates as 'YYYY-MM-DD original text' (zeros for unknown parts)."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value or "")
    if not m:
        y = re.search(r"\b(1[5-9]|20)\d{2}\b", value or "")
        return {"date-parts": [[int(y.group(0))]]} if y else None
    y, mo, d = (int(x) for x in m.groups())
    if not y:
        return None
    parts = [y] + ([mo] if mo else []) + ([d] if mo and d else [])
    return {"date-parts": [parts]}


def read_library(db_path=None):
    """-> (list of CSL items with "_projects" and "_id", info dict)"""
    path = db_path or find_database()
    con, tmp, used = _open_copy(path)
    try:
        types_t, fields_t = _table(con, "itemTypesCombined", "itemTypes"), _table(con, "fieldsCombined", "fields")
        ctypes_t = _table(con, "creatorTypes")
        rows = con.execute(f"""SELECT i.itemID, i.key, t.typeName FROM items i JOIN {types_t} t ON t.itemTypeID = i.itemTypeID
                               WHERE i.itemID NOT IN (SELECT itemID FROM deletedItems)""").fetchall()
        items = {iid: {"_id": key, "_type": tname} for iid, key, tname in rows if tname not in SKIP_TYPES}
        for iid, fname, value in con.execute(f"""SELECT d.itemID, f.fieldName, v.value FROM itemData d
                JOIN {fields_t} f ON f.fieldID = d.fieldID JOIN itemDataValues v ON v.valueID = d.valueID"""):
            it = items.get(iid)
            if it is not None:
                it.setdefault("_fields", {})[fname] = value
        for iid, first, last, mode, role in con.execute(f"""SELECT ic.itemID, c.firstName, c.lastName, c.fieldMode,
                ct.creatorType FROM itemCreators ic JOIN creators c ON c.creatorID = ic.creatorID
                JOIN {ctypes_t} ct ON ct.creatorTypeID = ic.creatorTypeID ORDER BY ic.itemID, ic.orderIndex"""):
            it = items.get(iid)
            if it is None:
                continue
            person = {"literal": last} if mode == 1 else {"family": last or "", "given": first or ""}
            it.setdefault("_creators", []).append((ROLES.get(role, "author"), person))
        names = {cid: (name, parent) for cid, name, parent in
                 con.execute("SELECT collectionID, collectionName, parentCollectionID FROM collections")}

        def path_of(cid, depth=0):
            name, parent = names.get(cid, ("", None))
            return name if not parent or depth > 10 else f"{path_of(parent, depth + 1)} / {name}"
        for cid, iid in con.execute("SELECT collectionID, itemID FROM collectionItems"):
            if iid in items and cid in names:
                items[iid].setdefault("_projects", []).append(path_of(cid))
    finally:
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)

    out = []
    for it in items.values():
        f = it.get("_fields", {})
        c = {"type": TYPES.get(it["_type"], "article")}
        for zf, cf in FIELDS.items():
            if f.get(zf) and cf not in c:
                c[cf] = f[zf]
        cont = next((f[k] for k in CONTAINER if f.get(k)), None)
        if cont:
            c["container-title"] = cont
        if f.get("date") and _date(f["date"]):
            c["issued"] = _date(f["date"])
        if f.get("accessDate") and _date(f["accessDate"]):
            c["accessed"] = _date(f["accessDate"])
        if not c.get("DOI") and f.get("extra"):
            m = re.search(r"^DOI:\s*(10\.\S+)", f["extra"], re.M | re.I)
            if m:
                c["DOI"] = m.group(1)
        for role, person in it.get("_creators", []):
            c.setdefault(role, []).append(person)
        c["_id"] = it["_id"]
        if it.get("_projects"):
            c["_projects"] = sorted(set(it["_projects"]))
        if c.get("title"):
            out.append(c)
    return out, {"database": used, "collections": len(names)}


def import_library(project=None, db_path=None):
    """Read Zotero's library and add it to Citrello. Returns a summary."""
    import library
    items, info = read_library(db_path)
    added, skipped = library.import_items(items, project, "zotero", extra_key="zotero_key")
    return {"added": added, "skipped": skipped, "found": len(items), "collections": info["collections"],
            "from_backup": info["database"].endswith(".bak"), "database": info["database"]}
