"""
LaTeX and Overleaf support.

- bibtex(items, fmt): clean BibTeX or biblatex with Citrello's stable keys (brace-protected acronyms, escaped
  special characters, en-dash page ranges).
- find_cites / replace_cite_keys: \\cite, \\citep, \\citet, \\parencite, \\textcite, \\autocite, \\footcite,
  \\nocite, \\cites ... (comments are skipped).
- read_project / check_project: a .tex file, a folder or an Overleaf .zip -> cited keys missing from the .bib,
  unused entries, duplicates, unreliable references (retracted, wrong DOI ...), and hand-typed bibliographies.
- convert_thebibliography: a hand-typed \\begin{thebibliography} -> library references + references.bib + \\cite keys.
- Automatic export: keep a .bib file up to date with a project (for local LaTeX, Dropbox or Git sync).
"""
import io
import json
import os
import re
import threading
import zipfile

import citrello_core as core

# ---------------------------------------------------------------------------
# BibTeX / biblatex writer
# ---------------------------------------------------------------------------

_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
            "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
_SPECIAL_RE = re.compile(r"[\\&%$#_{}~^]")
_TAG_RE = re.compile(r"<(/?)(i|em|b|strong|sub|sup|scp|span|p|br)\b[^>]*>", re.I)
_TAG_CMD = {"i": r"\textit{", "em": r"\textit{", "b": r"\textbf{", "strong": r"\textbf{",
            "sub": r"\textsubscript{", "sup": r"\textsuperscript{", "scp": r"\textsc{"}
_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&apos;": "'", "&nbsp;": " "}


def escape(s):
    return _SPECIAL_RE.sub(lambda m: _SPECIAL[m.group(0)], s)


def _protect_word(w):
    """{LiDAR}, {CO2}, {mRNA}: words whose case a BibTeX style must not change."""
    letters = re.sub(r"[^A-Za-z\u00C0-\u024F]", "", w)
    if not letters:
        return w
    if any(ch.isupper() for ch in letters[1:]) or (re.search(r"\d", w) and any(ch.isupper() for ch in letters)):
        return "{" + w + "}"
    return w


def _text(s, protect=False):
    """Plain or lightly tagged (Crossref <i>, <sub> ...) text -> LaTeX."""
    s = str(s or "")
    for k, v in _ENTITIES.items():
        s = s.replace(k, v)
    out, stack, pos = [], [], 0

    def plain(t):
        t = escape(t)
        if protect:
            # protect word by word, never inside the commands we just added
            t = re.sub(r"(?<![\\{\w])([A-Za-z\u00C0-\u024F][\w\u00C0-\u024F\-]*)", lambda m: _protect_word(m.group(1)), t)
        return t

    for m in _TAG_RE.finditer(s):
        out.append(plain(s[pos:m.start()]))
        pos = m.end()
        closing, tag = m.group(1) == "/", m.group(2).lower()
        if tag in _TAG_CMD:
            if closing:
                if tag in stack:
                    stack.remove(tag)
                    out.append("}")
            else:
                stack.append(tag)
                out.append(_TAG_CMD[tag])
    out.append(plain(s[pos:]))
    out.append("}" * len(stack))
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _names(people):
    out = []
    for p in people or []:
        if p.get("literal"):
            out.append("{" + _text(p["literal"]) + "}")  # an organisation: keep as written
            continue
        family = _text(p.get("family", ""))
        if p.get("non-dropping-particle"):
            family = _text(p["non-dropping-particle"]) + " " + family
        given = _text(p.get("given", ""))
        if p.get("dropping-particle"):
            given = (given + " " + _text(p["dropping-particle"])).strip()
        if p.get("suffix"):
            out.append(f"{family}, {_text(p['suffix'])}, {given}".strip(", "))
        else:
            out.append(f"{family}, {given}".strip(", ") if given else ("{" + family + "}" if " " in family else family))
    return " and ".join(n for n in out if n)


def _date(c, key="issued"):
    parts = ((c.get(key) or {}).get("date-parts") or [[]])[0]
    parts = [p for p in parts if p not in (None, "")]
    try:
        return [int(p) for p in parts]
    except (TypeError, ValueError):
        return []


_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_BIBTEX_TYPES = {"article-journal": "article", "article-magazine": "article", "article-newspaper": "article",
                 "article": "article", "book": "book", "chapter": "incollection", "paper-conference": "inproceedings",
                 "thesis": "phdthesis", "report": "techreport", "webpage": "misc", "dataset": "misc",
                 "manuscript": "unpublished", "post-weblog": "misc", "software": "misc"}
_BIBLATEX_TYPES = {"article-journal": "article", "article-magazine": "article", "article-newspaper": "article",
                   "article": "article", "book": "book", "chapter": "incollection",
                   "paper-conference": "inproceedings", "thesis": "thesis", "report": "report",
                   "webpage": "online", "post-weblog": "online", "dataset": "dataset", "manuscript": "unpublished",
                   "software": "software"}


def entry(key, c, fmt="bibtex"):
    """One CSL item -> a BibTeX (fmt="bibtex") or biblatex (fmt="biblatex") entry."""
    t = c.get("type") or "article"
    big = fmt == "biblatex"
    etype = (_BIBLATEX_TYPES if big else _BIBTEX_TYPES).get(t, "misc")
    if etype == "article" and not c.get("container-title"):
        etype = "misc"
    f = []
    add = lambda k, v: v and f.append((k, v))  # noqa: E731
    add("author", _names(c.get("author")))
    add("editor", _names(c.get("editor")))
    add("title", _text(c.get("title"), protect=True))
    cont = _text(c.get("container-title"), protect=etype in ("incollection", "inproceedings"))
    if etype == "article":
        add("journaltitle" if big else "journal", cont)
    elif etype in ("incollection", "inproceedings"):
        add("booktitle", cont)
    elif etype == "online" or (etype == "misc" and t == "webpage"):
        add("organization" if big else "howpublished", cont)
    d = _date(c)
    if big:
        add("date", "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(d)))
    else:
        add("year", str(d[0]) if d else "")
        if len(d) > 1 and 1 <= d[1] <= 12:
            f.append(("month", _MONTHS[d[1] - 1]))  # a BibTeX macro, written without braces
    add("volume", _text(c.get("volume")))
    add("number", _text(c.get("issue")))
    pages = str(c.get("page") or "").replace("–", "-").replace("—", "-")
    add("pages", escape(re.sub(r"\s*-+\s*", "--", pages)))
    if c.get("number") and t == "report":
        add("number", _text(c.get("number")))
    publisher = _text(c.get("publisher"))
    if etype in ("phdthesis", "thesis"):
        add("institution" if big else "school", publisher)
        if big:
            add("type", "phdthesis")
    elif etype in ("techreport", "report"):
        add("institution", publisher)
        if big:
            add("type", "techreport")
    elif etype not in ("article", "online"):
        add("publisher", publisher)
    add("location" if big else "address", _text(c.get("publisher-place")))
    add("edition", _text(c.get("edition")))
    add("isbn", _text(c.get("ISBN")))
    add("issn", _text(c.get("ISSN")))
    add("doi", c.get("DOI"))
    if c.get("URL") and (not c.get("DOI") or t == "webpage"):
        if big or t != "webpage":
            add("url", c["URL"])
        else:
            add("note", r"\url{" + c["URL"] + "}" + (f", accessed {_iso(_date(c, 'accessed'))}" if _date(c, "accessed") else ""))
    if big and _date(c, "accessed"):
        add("urldate", _iso(_date(c, "accessed")))
    body = ",\n".join(f"  {k} = {v}" if k == "month" else f"  {k} = {{{_bare(k, v)}}}" for k, v in f)
    return f"@{etype}{{{key},\n{body}\n}}\n"


def _iso(d):
    return "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(d))


def _bare(k, v):
    # DOIs and URLs are verbatim fields: only braces need care
    return v.replace("{", "%7B").replace("}", "%7D") if k in ("url", "doi") else v


HEADER = "% {what} written by Citrello (https://citrello.app). Keys are stable; manage references in Citrello.\n\n"


def bibtex(items, fmt="bibtex", header=True):
    """Library items -> .bib text, sorted by key (stable diffs for Git and Overleaf)."""
    out = [HEADER.format(what="biblatex file" if fmt == "biblatex" else "BibTeX file")] if header else []
    for it in sorted(items, key=lambda it: it["key"].lower()):
        out.append(entry(it["key"], it["csl"], fmt))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# \cite commands
# ---------------------------------------------------------------------------

_CITE_RE = re.compile(
    r"\\(?P<cmd>[A-Za-z]*cite[A-Za-z]*)\*?(?P<args>(?:\s*\[[^\]]*\]){0,2})\s*\{(?P<keys>[^{}]*)\}"
    r"(?P<more>(?:(?:\s*\[[^\]]*\]){0,2}\s*\{[^{}]*\})*)")
_MORE_RE = re.compile(r"((?:\s*\[[^\]]*\]){0,2}\s*\{)([^{}]*)(\})")
_NOT_CITES = {"citestyle", "citetitlefont", "citeauthorfont"}


def _code_spans(text):
    """(start, end) of each line's code part (before an unescaped %)."""
    pos = 0
    for line in text.splitlines(keepends=True):
        m = re.search(r"(?<!\\)%", line)
        yield pos, pos + (m.start() if m else len(line))
        pos += len(line)


def _cite_matches(text):
    for a, b in _code_spans(text):
        for m in _CITE_RE.finditer(text, a, b):
            if m.group("cmd").lower() in _NOT_CITES:
                continue
            yield m


def _keys_of(m):
    groups = [m.group("keys")]
    if m.group("cmd").endswith("cites"):  # \cites{a}{b}, \parencites[see][]{a}{b}
        groups += [g.group(2) for g in _MORE_RE.finditer(m.group("more"))]
    return [k.strip() for g in groups for k in g.split(",") if k.strip()]


def find_cites(text):
    """[(command, [keys])] for every citation command outside comments."""
    return [(m.group("cmd"), _keys_of(m)) for m in _cite_matches(text)]


def cited_keys(text):
    keys = []
    for _, ks in find_cites(text):
        keys += ks
    return keys


def replace_cite_keys(text, mapping):
    """Rename keys inside citation commands (mapping: old -> new)."""
    def keys(group):
        parts = group.split(",")
        return ",".join(re.sub(r"\S+", lambda k: mapping.get(k.group(0), k.group(0)), p, count=1) for p in parts)

    def fix(m):
        s = m.group(0)
        start = m.start("keys") - m.start()
        s = s[:start] + keys(m.group("keys")) + s[start + len(m.group("keys")):]
        if m.group("cmd").endswith("cites") and m.group("more"):
            ms = m.start("more") - m.start()
            more = _MORE_RE.sub(lambda g: g.group(1) + keys(g.group(2)) + g.group(3), m.group("more"))
            s = s[:ms] + more + s[ms + len(m.group("more")):]
        return s

    out, pos = [], 0
    for m in _cite_matches(text):
        out.append(text[pos:m.start()])
        out.append(fix(m))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


# ---------------------------------------------------------------------------
# Reading a project
# ---------------------------------------------------------------------------

MAX_FILE = 20 * 1024 * 1024


def _decode(data):
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def read_project(data=None, filename="", path=None):
    """A .tex / .bib file, an Overleaf .zip, or a folder -> {"tex": {name: text}, "bib": {name: text}}."""
    files = {"tex": {}, "bib": {}}

    def take(name, raw):
        ext = os.path.splitext(name)[1].lower()
        if ext in (".tex", ".ltx") and len(raw) <= MAX_FILE:
            files["tex"][name] = _decode(raw)
        elif ext == ".bib" and len(raw) <= MAX_FILE:
            files["bib"][name] = _decode(raw)

    if path and os.path.isdir(path):
        for root, dirs, names in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
            for n in names:
                if os.path.splitext(n)[1].lower() in (".tex", ".ltx", ".bib"):
                    full = os.path.join(root, n)
                    with open(full, "rb") as f:
                        take(os.path.relpath(full, path).replace(os.sep, "/"), f.read())
        return files
    if path:
        filename = filename or os.path.basename(path)
        with open(path, "rb") as f:
            data = f.read()
    if data[:2] == b"PK" or filename.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                if info.is_dir() or info.file_size > MAX_FILE or "__MACOSX" in info.filename:
                    continue
                take(info.filename, z.read(info))
    else:
        take(filename or "main.tex", data)
    if not files["tex"] and not files["bib"]:
        raise ValueError("No .tex or .bib files found. Choose a .tex file, a .bib file, or an Overleaf project .zip "
                         "(Overleaf: Menu → Download → Source).")
    return files


def bib_entries(files):
    """[(file, key, csl, raw)] from the project's .bib files."""
    import library
    out = []
    for name, text in files["bib"].items():
        for c in library.parse_bibtex(text):
            raw, key = c.pop("_raw", ""), c.pop("_key", "")
            out.append((name, key, c, raw))
    return out


def bib_files_used(files):
    """The .bib names a document asks for (\\bibliography{a,b} / \\addbibresource{a.bib})."""
    names = []
    for text in files["tex"].values():
        for a, b in _code_spans(text):
            for m in re.finditer(r"\\(?:bibliography|addbibresource)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", text[a:b]):
                for n in m.group(1).split(","):
                    n = n.strip()
                    if n:
                        names.append(n if n.lower().endswith(".bib") else n + ".bib")
    return names


_THEBIB_RE = re.compile(r"\\begin\{thebibliography\}\s*\{[^}]*\}(.*?)\\end\{thebibliography\}", re.S)
_BIBITEM_RE = re.compile(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def typed_bibliography(text):
    """[(key, plain text)] of a hand-typed thebibliography environment."""
    import library
    m = _THEBIB_RE.search(text)
    if not m:
        return []
    body = "\n".join(line[:mm.start()] if (mm := re.search(r"(?<!\\)%", line)) else line
                     for line in m.group(1).splitlines())
    parts = _BIBITEM_RE.split(body)
    out = []
    for i in range(1, len(parts) - 1, 2):
        t = parts[i + 1]
        t = re.sub(r"\\(newblock|emph|textit|textbf|textsc|textrm|em|it|bf|sc|url|path|doi)\b\s*", " ", t)
        t = re.sub(r"\\href\s*\{([^}]*)\}\s*\{([^}]*)\}", r"\2 \1", t)
        t = re.sub(r"\\(?:penalty\d*|allowbreak|relax|/)", "", t)
        t = t.replace("``", "\u201c").replace("''", "\u201d")  # LaTeX quotes, so IEEE-style titles are found
        out.append((parts[i].strip(), library._delatex(t).strip()))
    return out


# ---------------------------------------------------------------------------
# Checking a project
# ---------------------------------------------------------------------------

def check_project(files, data):
    """Report on a LaTeX project's citations and bibliography."""
    import library
    cites, nocite_all = {}, False
    for name, text in files["tex"].items():
        for cmd, keys in find_cites(text):
            for k in keys:
                if k == "*" and cmd == "nocite":
                    nocite_all = True
                    continue
                cites[k] = cites.get(k, 0) + 1
    entries = bib_entries(files)
    bib_keys = {}
    for name, key, c, raw in entries:
        bib_keys.setdefault(key, (name, c))
    lib_keys = {it["key"]: it for it in data["items"]}
    lib_lower = {k.lower(): it for k, it in lib_keys.items()}

    typed_keys = {k for text in files["tex"].values() for k, _ in typed_bibliography(text)}
    missing = []
    for k in sorted(cites):
        if k in bib_keys or k in typed_keys:
            continue
        it = lib_keys.get(k) or lib_lower.get(k.lower())
        missing.append({"key": k, "uses": cites[k], "in_library": bool(it),
                        "library_key": it["key"] if it else None, "reference": library.short_ref(it) if it else None})

    unused = [] if nocite_all else sorted(k for k in bib_keys if k not in cites)

    groups = {}
    for name, key, c, raw in entries:
        sig = ("doi", core.norm_doi(c.get("DOI"))) if c.get("DOI") else ("title", core.norm(c.get("title") or ""))
        if sig[1]:
            groups.setdefault(sig, [])
            if key not in groups[sig]:
                groups[sig].append(key)
    duplicates = [ks for ks in groups.values() if len(ks) > 1]
    dup_keys = {}
    for name, key, c, raw in entries:
        dup_keys[key] = dup_keys.get(key, 0) + 1
    same_key = sorted(k for k, n in dup_keys.items() if n > 1)

    issues = []
    for key, (name, c) in bib_keys.items():
        it, _ = library.find_duplicate(data, c) if c.get("title") else (None, None)
        if it and it.get("reliability") in ("problem", "check") and it.get("checks"):
            checks, rel, src = it["checks"], it["reliability"], "library"
        else:
            rec = {"number": 0, "raw": "", "source": "import", "csl": c, "typed": {}}
            core.assess_references([rec])
            checks, rel, src = rec["checks"], rec["reliability"], "offline"
            if it and it.get("reliability") == "verified":
                continue
        if rel in ("problem", "check") and checks:
            issues.append({"key": key, "reliability": rel, "title": c.get("title", ""), "checked": src,
                           "in_library": bool(it), "checks": [ch["text"] for ch in checks]})
    issues.sort(key=lambda x: (x["reliability"] != "problem", x["key"].lower()))

    typed = []
    for name, text in files["tex"].items():
        items = typed_bibliography(text)
        if items:
            typed.append({"file": name, "entries": len(items)})

    used = bib_files_used(files)
    return {
        "tex_files": sorted(files["tex"]), "bib_files": sorted(files["bib"]), "bib_files_used": used,
        "missing_bib_files": [n for n in used if not any(b == n or b.endswith("/" + n) for b in files["bib"])],
        "citations": sum(cites.values()), "cited_keys": len(cites), "bib_entries": len(bib_keys),
        "missing": missing, "unused": unused, "duplicates": duplicates, "same_key_twice": same_key,
        "issues": issues, "typed_bibliography": typed,
        "ok": not (missing or duplicates or same_key or any(i["reliability"] == "problem" for i in issues)),
    }


def main_bib_name(files):
    used = bib_files_used(files)
    for n in used:
        for b in files["bib"]:
            if b == n or b.endswith("/" + n):
                return b
    if files["bib"]:
        return sorted(files["bib"])[0]
    return used[0] if used else "references.bib"


def fixed_bib(files, report, data, fmt="bibtex"):
    """The main .bib with the cited references that were missing from it added from the Citrello library.
    Existing entries are kept exactly as they are."""
    name = main_bib_name(files)
    lib = {it["key"]: it for it in data["items"]}
    add = [lib[m["library_key"]] for m in report["missing"] if m["in_library"]]
    renamed = {m["library_key"]: m["key"] for m in report["missing"] if m["in_library"] and m["library_key"] != m["key"]}
    text = files["bib"].get(name, "")
    extra = []
    for it in add:
        extra.append(entry(renamed.get(it["key"], it["key"]), it["csl"], fmt))
    if extra:
        text = text.rstrip() + ("\n\n" if text.strip() else "") + "% Added by Citrello from your library\n\n" + "\n".join(extra)
    return name, text, len(extra)


# ---------------------------------------------------------------------------
# Converting a hand-typed bibliography
# ---------------------------------------------------------------------------

def convert_thebibliography(files, project=None, offline=False, email=None, progress=None, fmt="bibtex"):
    """Every \\begin{thebibliography} becomes \\bibliography{references}; its entries are looked up, added to the
    library, written to references.bib, and the \\cite keys are renamed to Citrello keys.
    Returns {"files": {name: new text}, "bib_name", "entries", "added", "mapping", "unresolved"}."""
    import library
    todo = [(name, key, text) for name, tex in files["tex"].items() for key, text in typed_bibliography(tex)]
    if not todo:
        raise ValueError("No hand-typed bibliography (\\begin{thebibliography}) found.")
    mapping, keys, unresolved, added = {}, [], [], 0
    for i, (name, old, text) in enumerate(todo):
        if progress:
            progress(i, len(todo), old)
        rec = library.resolve(text, offline, email) if text else None
        csl = rec["csl"] if rec else {}
        extra = {"checks": rec.get("checks", []), "reliability": rec.get("reliability", "check")} if rec else {}
        if not csl.get("title"):
            csl = {"type": "article", "title": text or old}
            extra = {"checks": [{"level": "check", "text": "Couldn't read this reference; check its details"}],
                     "reliability": "check"}
            unresolved.append(old)
        with library.editing() as data:
            it, created, _ = library.add_csl(data, csl, project, "latex", raw=text[:2000], extra=extra)
            new = it["key"]
        added += created
        mapping[old] = new
        if new not in keys:
            keys.append(new)
    if progress:
        progress(len(todo), len(todo), "")

    bib_name = "references.bib"
    n = 1
    while bib_name in files["bib"]:
        n += 1
        bib_name = f"references{n}.bib"
    out = {}
    for name, tex in files["tex"].items():
        new = replace_cite_keys(tex, mapping)
        if _THEBIB_RE.search(new):
            natbib = re.search(r"\\usepackage(\[[^\]]*\])?\{[^}]*natbib", new)
            style = "unsrtnat" if natbib else "unsrt"
            repl = (f"% Bibliography converted by Citrello: entries are in {bib_name}.\n"
                    f"% Change the style if you like, e.g. IEEEtran, elsarticle-num, plainnat or apalike.\n"
                    f"\\bibliographystyle{{{style}}}\n\\bibliography{{{bib_name[:-4]}}}")
            new = _THEBIB_RE.sub(lambda m: repl, new, count=1)
        if new != tex:
            out[name] = new
    data = library.load()
    items = [library.get(data, k) for k in keys]
    out[bib_name] = bibtex([it for it in items if it], fmt)
    return {"files": out, "bib_name": bib_name, "entries": len(todo), "added": added, "mapping": mapping,
            "unresolved": unresolved}


def zip_files(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Automatic .bib export
# ---------------------------------------------------------------------------

EXPORTS_FILE = os.environ.get("CITRELLO_BIB_EXPORTS") or os.path.join(os.path.expanduser("~"), ".citrello_bib_exports.json")
_EXPORT_LOCK = threading.Lock()
_EXPORT_ERRORS = {}


def list_exports():
    try:
        with open(EXPORTS_FILE, encoding="utf-8") as f:
            exports = json.load(f)
    except (OSError, ValueError):
        exports = []
    for e in exports:
        e["error"] = _EXPORT_ERRORS.get(e["path"])
    return exports


def _save_exports(exports):
    with open(EXPORTS_FILE, "w", encoding="utf-8") as f:
        json.dump([{k: e.get(k) for k in ("project", "path", "format")} for e in exports], f, indent=1)


def add_export(project, path, fmt="bibtex"):
    """Keep `path` (.bib) up to date with a project (None = the whole library)."""
    if fmt not in ("bibtex", "biblatex"):
        raise ValueError("Format must be bibtex or biblatex")
    path = os.path.abspath(os.path.expanduser(path.strip().strip('"')))
    if os.path.isdir(path):
        path = os.path.join(path, "references.bib")
    if not path.lower().endswith(".bib"):
        raise ValueError("Choose a file name ending in .bib")
    if not os.path.isdir(os.path.dirname(path)):
        raise ValueError(f"Folder not found: {os.path.dirname(path)}")
    if os.path.exists(path):
        head = open(path, encoding="utf-8", errors="replace").read(300)
        if "written by Citrello" not in head and os.path.getsize(path) > 0:
            raise ValueError(f"{os.path.basename(path)} already exists and wasn't made by Citrello. "
                             "Choose another name so it isn't overwritten (e.g. citrello.bib).")
    with _EXPORT_LOCK:
        exports = [e for e in list_exports() if os.path.normcase(e["path"]) != os.path.normcase(path)]
        exports.append({"project": project or None, "path": path, "format": fmt})
        _save_exports(exports)
    import library
    run_exports(library.load())
    return list_exports()


def remove_export(path):
    with _EXPORT_LOCK:
        _save_exports([e for e in list_exports() if os.path.normcase(e["path"]) != os.path.normcase(path)])
    return list_exports()


def run_exports(data):
    """Rewrite each automatic .bib whose content changed. Called after every library save."""
    exports = list_exports()
    if not exports:
        return
    import library
    for e in exports:
        try:
            items = library.search(data, project=e["project"]) if e.get("project") else data["items"]
            text = bibtex(items, e.get("format") or "bibtex")
            try:
                with open(e["path"], encoding="utf-8") as f:
                    if f.read() == text:
                        _EXPORT_ERRORS.pop(e["path"], None)
                        continue
            except OSError:
                pass
            tmp = e["path"] + ".citrello-tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.replace(tmp, e["path"])
            _EXPORT_ERRORS.pop(e["path"], None)
        except Exception as ex:  # never break a library save
            _EXPORT_ERRORS[e["path"]] = str(ex)
