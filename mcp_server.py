#!/usr/bin/env python3
"""
Citrello connector for Claude (Model Context Protocol, stdio).

Gives Claude Desktop / Claude Code access to your Citrello library, so the
references you use are the same in every chat and project:
search, add (by DOI or pasted reference), organise into projects, format in any
style, and write or update Word documents with live citations.

Claude Desktop config (Citrello's Settings page can add this for you):
  {"mcpServers": {"citrello": {"command": "C:\\\\path\\\\to\\\\CitrelloMCP.exe"}}}
or, without the .exe:
  {"mcpServers": {"citrello": {"command": "python", "args": ["C:\\\\path\\\\to\\\\mcp_server.py"]}}}
"""
import contextlib
import io
import json
import os
import sys
import threading
import traceback

# The protocol owns stdout; anything else that prints goes to stderr.
_PROTOCOL_OUT = sys.stdout.buffer if hasattr(sys.stdout, "buffer") else None
sys.stdout = sys.stderr

import citeformat  # noqa: E402
import latex  # noqa: E402
import library  # noqa: E402
import citrello_core as core  # noqa: E402

VERSION = core.VERSION
PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]

INSTRUCTIONS = """Citrello is the user's reference manager. Its library is shared across all chats and projects.

How to cite:
- Before citing, call search_references to find the reference and its key. If it is not there, call add_reference
  with the DOI (best) or the full reference text. Never invent keys or reference details.
- In any text you write (chat answers, drafts, documents), cite with markers: [@Key], several as [@Key1; @Key2],
  with a page as [@Key, p. 12], with a prefix as [see @Key], and after the author's name in a sentence as
  Smith et al. [-@Key] (shows only the year).
- To give the user a Word file, call create_word_document with your text containing markers: citations become live
  (Mendeley-compatible) fields with a formatted bibliography. Use update_word_document to restyle or refresh a .docx.
- When editing existing text, keep every citation marker exactly as it is unless asked to change citations.
- Check add_reference's reliability result and tell the user about problems (retracted paper, wrong DOI, ...).
- Use a project name (e.g. the paper's title) to keep references of one paper together.

LaTeX / Overleaf:
- Cite with the library keys: \\cite{Key}, \\citep{Key1,Key2}, \\citet{Key}, or the biblatex commands \\parencite / \\textcite.
  Keys are the same as in [@Key] markers and never change.
- Give the user the bibliography with get_bibtex (a project or keys; format bibtex or biblatex) to save as references.bib.
- check_citations also checks \\cite commands. check_latex_project checks a .tex file, folder or Overleaf .zip on disk:
  cited keys missing from the .bib, unused and duplicate entries, unreliable references, hand-typed bibliographies.
- prepare_arxiv turns a LaTeX project into an arXiv-ready .zip (with the .bbl arXiv needs) and lists what to fix."""


def _email():
    try:
        return json.load(open(os.path.expanduser("~/.citrello_app.json"), encoding="utf-8")).get("email")
    except (OSError, ValueError):
        return None


def _summary(it):
    c = it["csl"]
    out = {"key": it["key"], "citation_marker": f"[@{it['key']}]", "reference": library.short_ref(it),
           "type": c.get("type"), "container": c.get("container-title") or c.get("publisher"),
           "doi": c.get("DOI"), "projects": it.get("projects", [])}
    if it.get("reliability"):
        out["reliability"] = it["reliability"]
    if it.get("checks"):
        out["issues"] = [ch["text"] for ch in it["checks"]]
    return out


# Requests relayed from ChatGPT / Claude on the web (through the Citrello bridge) can only write Word files in
# Citrello's Documents folder and only read Word or reference files, as a safeguard against prompt injection.
_REMOTE = threading.local()


@contextlib.contextmanager
def remote_call():
    _REMOTE.active = True
    try:
        yield
    finally:
        _REMOTE.active = False


def _remote():
    return getattr(_REMOTE, "active", False)


def _docs_folder():
    return os.path.realpath(os.path.join(library.HOME, "Documents"))


def _in_docs(path):
    try:
        return os.path.commonpath([os.path.realpath(path), _docs_folder()]) == _docs_folder()
    except ValueError:  # different drives on Windows
        return False


def _check_remote_path(path, exts, write=False):
    if not _remote():
        return path
    if os.path.splitext(path)[1].lower() not in exts:
        raise ValueError(f"From a web connector, only {', '.join(exts)} files can be used.")
    if write:
        if not _in_docs(path):
            raise ValueError(f"From a web connector, documents can only be saved in {_docs_folder()}. "
                             "Leave output_path empty to save there.")
    return path


def _default_doc_path(title):
    folder = os.path.join(library.HOME, "Documents")
    os.makedirs(folder, exist_ok=True)
    name = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in (title or "Citrello document")).strip()
    return os.path.join(folder, (name or "Citrello document")[:80] + ".docx")


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

def t_search(query="", project=None, limit=25):
    data = library.load()
    hits = library.search(data, query, project)
    return {"total_in_library": len(data["items"]), "matches": len(hits),
            "references": [_summary(it) for it in hits[:max(1, min(int(limit or 25), 200))]]}


def t_get(key, style=None):
    data = library.load()
    it = library.get(data, key)
    if not it:
        raise ValueError(f"No reference with key {key!r}. Use search_references.")
    out = _summary(it)
    out["details"] = it["csl"]
    if style:
        r = citeformat.Renderer(style, {it["key"]: it["csl"]})
        i = r.add([{"key": it["key"]}])
        out["formatted_citation"] = core.re.sub(r"<[^>]+>", "", r.render(i))
        out["formatted_reference"] = core.re.sub(r"<[^>]+>", "", r.bibliography()[0])
    return out


def t_add(reference, project=None):
    it, created, how = library.add_reference(reference, project, email=_email())
    out = _summary(it)
    out["status"] = "added" if created else f"already in the library ({how})"
    return out


def t_add_many(references, project=None):
    results = []
    for ref in references:
        try:
            results.append(t_add(ref, project))
        except Exception as e:  # keep going; report per reference
            results.append({"reference_text": ref[:120], "error": str(e)})
    return {"results": results}


def t_projects():
    data = library.load()
    counts = {}
    for it in data["items"]:
        for p in it.get("projects", []):
            counts[p] = counts.get(p, 0) + 1
    return {"projects": [{"name": p, "references": counts.get(p, 0)} for p in data["projects"]],
            "references_without_project": sum(1 for it in data["items"] if not it.get("projects"))}


def t_add_to_project(keys, project):
    data = library.load()
    done = []
    for k in keys:
        it = library.get(data, k)
        if not it:
            raise ValueError(f"No reference with key {k!r}")
        library.set_projects(it["key"], it.get("projects", []) + [project])
        done.append(it["key"])
    return {"project": project, "added": done}


def t_format(keys=None, project=None, style=None):
    data = library.load()
    if keys:
        items = [library.get(data, k) for k in keys]
        missing = [k for k, it in zip(keys, items) if not it]
        if missing:
            raise ValueError(f"Unknown keys: {missing}")
    elif project:
        items = library.search(data, project=project)
    else:
        raise ValueError("Give keys or a project.")
    r = citeformat.Renderer(style or citeformat.DEFAULT_STYLE, {it["key"]: it["csl"] for it in items})
    cites = {it["key"]: r.add([{"key": it["key"]}]) for it in items}
    strip = lambda s: core.re.sub(r"<[^>]+>", "", s)
    return {"style": r.info["title"],
            "in_text": {k: strip(r.render(i)) for k, i in cites.items()},
            "bibliography": [strip(e) for e in r.bibliography()]}


def t_check_text(text):
    data = library.load()
    keys = {it["key"].lower(): it for it in data["items"]}
    found, unknown = [], []
    for m in citeformat.MARKER_RE.finditer(text):
        group = citeformat.parse_marker(m.group(1)) or []
        for g in group:
            (found if g["key"].lower() in keys else unknown).append(g["key"])
    for k in latex.cited_keys(text):  # LaTeX: \cite{Key}, \citep{...}, \parencite{...}
        if k != "*":
            (found if k.lower() in keys else unknown).append(k)
    return {"markers": len(found) + len(unknown), "known_keys": sorted(set(found)),
            "unknown_keys": sorted(set(unknown)),
            "ok": not unknown}


def t_create_doc(text, output_path=None, style=None, title=None):
    out = os.path.abspath(os.path.expanduser(output_path)) if output_path else _default_doc_path(title)
    _check_remote_path(out, [".docx"], write=True)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    res = citeformat.create_document(text, out, style, title)
    return {"saved_to": out, "citations": res["citations"], "bibliography_entries": res["entries"],
            "style": res["style"]["title"], "problems": res["report"],
            "note": "Open in Word. Citations are live fields; Mendeley Cite can take them over, "
                    "or restyle any time with update_word_document / the Citrello app."}


def t_update_doc(path, style=None, output_path=None):
    src = _check_remote_path(os.path.abspath(os.path.expanduser(path)), [".docx"])
    if not os.path.exists(src):
        raise ValueError(f"File not found: {src}")
    out = os.path.abspath(os.path.expanduser(output_path)) if output_path else src
    if _remote() and not _in_docs(out):
        out = os.path.join(_docs_folder(), os.path.basename(out))  # web connectors save a copy, never overwrite
    _check_remote_path(out, [".docx"], write=True)
    res = citeformat.update_document(src, out, style)
    return {"saved_to": out, "citations": res["citations"], "markers_converted": res["markers"],
            "bibliography_entries": res["entries"], "style": res["style"]["title"], "problems": res["report"]}


def t_bibtex(keys=None, project=None, format="bibtex", output_path=None):
    data = library.load()
    if keys:
        items, missing = [], []
        for k in keys:
            it = library.get(data, k)
            (items.append(it) if it else missing.append(k))
    else:
        items, missing = (library.search(data, project=project) if project else data["items"]), []
    fmt = "biblatex" if str(format).lower() == "biblatex" else "bibtex"
    text = latex.bibtex(items, fmt)
    out = {"entries": len(items), "format": fmt, "unknown_keys": missing}
    if output_path:
        path = os.path.abspath(os.path.expanduser(output_path))
        _check_remote_path(path, [".bib"], write=True)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        out["saved_to"] = path
    else:
        out["bib"] = text
    return out


def t_check_latex(path):
    src = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(src):
        _check_remote_path(src, [".tex", ".zip", ".bib", ".ltx"])
        if not os.path.exists(src):
            raise ValueError(f"File not found: {src}")
    elif _remote():
        raise ValueError("From a web connector, give a .tex or .zip file, not a folder.")
    rep = latex.check_project(latex.read_project(path=src), library.load())
    rep["note"] = ("Missing keys that are in the Citrello library can be added with get_bibtex(keys=[...]). "
                   "A hand-typed bibliography can be converted in the Citrello app (Documents → LaTeX).")
    return rep


def t_prepare_arxiv(path, output_path=None, remove_comments=True):
    import arxiv
    if _remote():
        raise ValueError("Ask the user to use the Citrello app (Documents → Check and fix, then 'Make an "
                         "arXiv-ready .zip'), or Claude Code / Claude Desktop on their computer.")
    src = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(src):
        raise ValueError(f"Not found: {src}")
    res = arxiv.prepare(arxiv.read_all(path=src), strip=remove_comments)
    stem = src.rstrip(os.sep) if os.path.isdir(src) else os.path.splitext(src)[0]
    out = os.path.abspath(os.path.expanduser(output_path)) if output_path else stem + "_arxiv.zip"
    with open(out, "wb") as f:
        f.write(arxiv.zip_bytes(res["files"]))
    return {"output": out, **res["report"],
            "note": "Upload the .zip on arXiv's Files page. The original project is unchanged."}


def t_import_zotero(project=None):
    import zotero_local
    if _remote():
        raise ValueError("Ask the user to import Zotero from the Citrello app (Library → Import).")
    return zotero_local.import_library(project)


def t_styles(query=""):
    if not query.strip():
        return {"styles": citeformat.list_styles(),
                "note": "These are ready offline. Search by a journal or publisher name to find any of the "
                        "10,000+ others; pass its id as `style` and it is downloaded once."}
    hits = [s for s in citeformat.search_styles(query, 25) if s["usable"]]
    return {"styles": [{k: s[k] for k in ("id", "title", "format", "installed")} for s in hits],
            "note": "Pass the id as `style`. Styles not installed are downloaded once (needs internet). "
                    "Footnote styles are not supported yet."}


def t_verify(keys=None, project=None, include_verified=False):
    data = library.load()
    if not keys:
        keys = [it["key"] for it in library.search(data, project=project)] if project else None
    res = library.verify(keys, include_verified, _email())
    res["note"] = ("Crossref is now the source for title, authors, journal, volume, issue, pages, year and DOI of "
                   "the references it found. Tell the user about problems (retracted, wrong DOI).")
    return res


def t_import(file_path, project=None):
    path = _check_remote_path(os.path.abspath(os.path.expanduser(file_path)),
                              [".ris", ".bib", ".bibtex", ".json", ".txt", ".nbib", ".enw"])
    text = open(path, encoding="utf-8-sig", errors="replace").read()
    added, skipped = library.import_items(library.parse_any(text, path), project, "import")
    return {"added": added, "skipped_duplicates_or_invalid": skipped}


S = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}
STR = {"type": "string"}
PROJECT = {"type": "string", "description": "Project name, e.g. the paper's title (optional)"}
STYLE = {"type": "string", "description": "Citation style id, e.g. apa, ieee, nature, vancouver, "
                                          "harvard-cite-them-right, chicago-author-date, or any id from zotero.org/styles. "
                                          "Default: apa"}

TOOLS = {
    "search_references": (t_search, "Search the user's reference library (all chats share it) by author, title, "
                          "year, DOI, key or tag. Empty query lists recent references. Returns keys to cite as [@Key].",
                          S({"query": STR, "project": PROJECT, "limit": {"type": "integer"}})),
    "get_reference": (t_get, "Full details of one reference, optionally formatted in a citation style.",
                      S({"key": STR, "style": STYLE}, ["key"])),
    "add_reference": (t_add, "Add a reference to the library from a DOI, a link to the paper (publisher page, arXiv, "
                      "ScienceDirect, doi.org...) or a web page, or a full typed reference. "
                      "Looks it up on Crossref, avoids duplicates, and returns its key plus a reliability check "
                      "(verified / check / problem, e.g. retracted or wrong DOI).",
                      S({"reference": {"type": "string", "description": "DOI, link, or full reference text"},
                         "project": PROJECT}, ["reference"])),
    "add_references": (t_add_many, "Add several references at once (DOIs or typed references).",
                       S({"references": {"type": "array", "items": STR}, "project": PROJECT}, ["references"])),
    "list_projects": (t_projects, "List the user's projects (groups of references, e.g. one per paper).", S({})),
    "add_to_project": (t_add_to_project, "Put references into a project.",
                       S({"keys": {"type": "array", "items": STR}, "project": STR}, ["keys", "project"])),
    "format_references": (t_format, "Format references (by keys or a whole project) in a citation style: "
                          "in-text citations and the bibliography as plain text.",
                          S({"keys": {"type": "array", "items": STR}, "project": PROJECT, "style": STYLE})),
    "check_citations": (t_check_text, "Check that every [@Key] marker in a text exists in the library. "
                        "Use before delivering text or documents.", S({"text": STR}, ["text"])),
    "create_word_document": (t_create_doc, "Create a Word (.docx) file from text with [@Key] markers. Supports "
                             "# headings, paragraphs, - bullets, **bold**, *italic*. Citations become live fields and "
                             "a bibliography is added in the chosen style. Saved to ~/Citrello/Documents unless a path is given.",
                             S({"text": STR, "title": STR, "style": STYLE,
                                "output_path": {"type": "string", "description": "Absolute .docx path (optional)"}},
                               ["text"])),
    "update_word_document": (t_update_doc, "Update a .docx: turn [@Key] markers and typed-in markers into live citations "
                             "and re-render every citation and the bibliography in a style. Overwrites the file unless "
                             "output_path is given.",
                             S({"path": STR, "style": STYLE, "output_path": STR}, ["path"])),
    "get_bibtex": (t_bibtex, "BibTeX or biblatex entries for references (by keys, a project, or the whole library), "
                   "with the same stable keys as [@Key] markers, for LaTeX / Overleaf (\\cite{Key}). Returns the .bib "
                   "text, or saves it when output_path is given.",
                   S({"keys": {"type": "array", "items": STR}, "project": PROJECT,
                      "format": {"type": "string", "enum": ["bibtex", "biblatex"]},
                      "output_path": {"type": "string", "description": "Absolute .bib path (optional)"}})),
    "check_latex_project": (t_check_latex, "Check a LaTeX project on disk (a .tex file, a folder, or an Overleaf "
                            "source .zip): cited keys missing from the .bib (and whether they are in the library), "
                            "unused and duplicate entries, unreliable references (retracted, wrong DOI, no DOI...), "
                            "and hand-typed bibliographies.",
                            S({"path": STR}, ["path"])),
    "prepare_arxiv": (t_prepare_arxiv, "Make an arXiv-ready .zip from a LaTeX project on disk (folder, Overleaf "
                      "source .zip or main .tex): adds the .bbl arXiv needs (it never runs BibTeX), removes comments "
                      "and build files, adds \\pdfoutput=1, and reports what would make arXiv fail (missing figures, "
                      "case mismatches, biblatex, citations missing from the .bib, bad file names, >50 MB). Writes "
                      "<name>_arxiv.zip next to the project unless output_path is given; the project is not changed.",
                      S({"path": STR, "output_path": STR, "remove_comments": {"type": "boolean"}}, ["path"])),
    "import_zotero_library": (t_import_zotero, "Import the user's Zotero library from Zotero on this computer (no account "
                              "or key needed). Zotero collections become projects; duplicates are skipped.",
                              S({"project": {"type": "string", "description": "Also put everything into this project (optional)"}})),
    "list_styles": (t_styles, "Find a citation style: with no query, the styles ready offline; with a journal, publisher or style name (e.g. 'Journal of Cleaner Production', 'IEEE'), matching styles from the full set of 10,000+.", S({"query": STR})),
    "verify_references": (t_verify, "Check references online against Crossref (all not-yet-verified ones, or given keys "
                          "or a project): fixes their details from the publisher record and re-rates them as verified / "
                          "check / problem (retracted, wrong DOI...). Takes about a second per reference.",
                          S({"keys": {"type": "array", "items": STR}, "project": PROJECT,
                             "include_verified": {"type": "boolean"}})),
    "import_references": (t_import, "Import a RIS, BibTeX or CSL-JSON file (exported from Zotero, Mendeley, "
                          "EndNote...) into the library, skipping duplicates.",
                          S({"file_path": STR, "project": PROJECT}, ["file_path"])),
}


# What each tool does to the user's things, for ChatGPT and Claude (they ask before tools that change things, and their
# directories require these): title, read-only, destructive (overwrites something), open world (goes online).
ANNOTATIONS = {
    "search_references": ("Search the library", True, False, False),
    "get_reference": ("Show a reference", True, False, True),            # formatting may download a style
    "add_reference": ("Add a reference", False, False, True),            # looked up on Crossref / doi.org
    "add_references": ("Add several references", False, False, True),
    "list_projects": ("List projects", True, False, False),
    "add_to_project": ("Put references into a project", False, False, False),
    "format_references": ("Format references", True, False, True),
    "check_citations": ("Check citation markers", True, False, False),
    "create_word_document": ("Create a Word document", False, False, True),
    "update_word_document": ("Update a Word document", False, True, True),  # rewrites the file unless told not to
    "get_bibtex": ("Get BibTeX", False, False, False),                      # can save a .bib file
    "check_latex_project": ("Check a LaTeX project", True, False, True),
    "prepare_arxiv": ("Make an arXiv-ready .zip", False, False, False),
    "import_zotero_library": ("Import from Zotero", False, False, False),
    "list_styles": ("Find a citation style", True, False, True),
    "verify_references": ("Verify references online", False, False, True),  # corrects details from Crossref
    "import_references": ("Import a reference file", False, False, False),
}


def tool_list():
    out = []
    for n, (_, d, s) in TOOLS.items():
        title, read_only, destructive, online = ANNOTATIONS[n]
        out.append({"name": n, "title": title, "description": d, "inputSchema": s,
                    "annotations": {"title": title, "readOnlyHint": read_only, "destructiveHint": destructive,
                                    "idempotentHint": read_only, "openWorldHint": online}})
    return out


# --------------------------------------------------------------------------
# JSON-RPC over stdio
# --------------------------------------------------------------------------

def handle(msg):
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:
        return None  # notification (e.g. notifications/initialized)
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "citrello", "title": "Citrello reference manager", "version": VERSION},
            "instructions": INSTRUCTIONS}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tool_list()}}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool {name}"}}
        try:
            result = TOOLS[name][0](**args)
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=1)}],
                "isError": False}}
        except BaseException as e:  # tool errors go back to Claude as text, the server keeps running
            traceback.print_exc()
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}}
    if method in ("resources/list", "prompts/list"):
        return {"jsonrpc": "2.0", "id": mid, "result": {method.split("/")[0]: []}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main():
    library.migrate_legacy_data()
    out = _PROTOCOL_OUT or sys.__stdout__.buffer
    stdin = sys.stdin.buffer
    while True:
        line = stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            batch = msg if isinstance(msg, list) else [msg]
            replies = [r for r in (handle(m) for m in batch) if r]
            reply = (replies if isinstance(msg, list) else replies[0]) if replies else None
        if reply is not None:
            out.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            out.flush()


if __name__ == "__main__":
    main()
