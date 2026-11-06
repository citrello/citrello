"""Make a LaTeX project ready to upload to arXiv.

arXiv compiles the source itself but never runs BibTeX, so an upload without the .bbl file prints every citation
as [?]. This module takes an Overleaf .zip (or a folder) and returns an upload-ready .zip:

- the .bbl, made with pybtex (a Python copy of BibTeX) and the project's own \\bibliographystyle, so no LaTeX
  installation is needed (the .bst comes from the project, or from the common ones in styles/bst);
- comments removed (arXiv shows the source files to anyone), build leftovers (.aux, .log...) and .bib removed;
- \\pdfoutput=1 added when the figures need pdfLaTeX;
- a report of what would make arXiv fail: missing figures or \\input files, name case mismatches, biblatex,
  unknown styles, citations missing from the .bib, unsupported file names, size over 50 MB, XeLaTeX-only packages.
"""
import contextlib
import io
import os
import posixpath
import re
import sys
import tempfile
import zipfile

import latex

MAX_UPLOAD = 50 * 1024 * 1024  # arXiv's limit for a submission
MAX_FILE = 100 * 1024 * 1024
JUNK = {".aux", ".log", ".out", ".toc", ".lof", ".lot", ".blg", ".fls", ".fdb_latexmk", ".synctex", ".bcf",
        ".xdv", ".nav", ".snm", ".vrb", ".idx", ".ilg", ".ind", ".brf", ".dvi", ".run.xml", ".synctex.gz",
        ".synctex(busy)", ".bak", ".swp"}
GRAPHIC_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".ps", ".mps", ".jbig2", ".jb2", ".svg")
VERBATIM_ENVS = ("verbatim", "verbatim*", "Verbatim", "lstlisting", "minted", "filecontents", "filecontents*",
                 "comment")
NAME_OK = re.compile(r"^[A-Za-z0-9_+\-.,=/]+$")


def _bst_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "styles", "bst")


def _ext(name):
    low = name.lower()
    for e in (".synctex.gz", ".synctex(busy)", ".run.xml"):
        if low.endswith(e):
            return e
    return os.path.splitext(low)[1]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def read_all(data=None, filename="", path=None):
    """Every file of a project: {relative name: bytes}. A zip whose files sit in one folder is flattened."""
    files = {}
    if path and os.path.isdir(path):
        for root, dirs, names in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
            for n in names:
                full = os.path.join(root, n)
                if os.path.getsize(full) <= MAX_FILE:
                    with open(full, "rb") as f:
                        files[os.path.relpath(full, path).replace(os.sep, "/")] = f.read()
    else:
        if path:
            filename = filename or os.path.basename(path)
            with open(path, "rb") as f:
                data = f.read()
        if data[:2] == b"PK" or filename.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    name = info.filename.replace("\\", "/")
                    if info.is_dir() or info.file_size > MAX_FILE or "__MACOSX/" in name or \
                            posixpath.basename(name) in (".DS_Store", "Thumbs.db"):
                        continue
                    files[name.lstrip("/")] = z.read(info)
        else:
            files[posixpath.basename(filename or "main.tex")] = data
    tops = {n.split("/", 1)[0] if "/" in n else "" for n in files}
    if len(tops) == 1 and "" not in tops:
        cut = len(tops.pop()) + 1
        files = {n[cut:]: b for n, b in files.items()}
    if not any(n.lower().endswith(".tex") for n in files):
        raise ValueError("No .tex file found. Choose your LaTeX project as a .zip (Overleaf: Menu → Download → "
                         "Source) or its main .tex file.")
    return files


def _text(b):
    return latex._decode(b)


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

def _strip_line(line):
    """(line without its comment, keeping the % so no space appears; True if there was a comment)."""
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c == "\\":
            m = re.match(r"\\verb\*?([^a-zA-Z\s*])", line[i:])
            if m:
                j = line.find(m.group(1), i + len(m.group(0)))
                i = n if j < 0 else j + 1
                continue
            m = re.match(r"\\(?:url|href|path|nolinkurl)\s*\{", line[i:])
            if m:  # % is allowed inside URLs
                j, depth = i + len(m.group(0)), 1
                while j < n and depth:
                    if line[j] == "\\":
                        j += 2
                        continue
                    depth += {"{": 1, "}": -1}.get(line[j], 0)
                    j += 1
                i = j
                continue
            i += 2
            continue
        if c == "%":
            return line[:i + 1], True
        i += 1
    return line, False


def strip_comments(text):
    """Remove LaTeX comments. Whole-line comments go entirely; a comment after text keeps its % (so the line
    still joins the next one exactly as before). Verbatim-like environments are left alone."""
    out, env, removed = [], None, False
    for line in text.split("\n"):
        if env:
            out.append(line)
            if re.search(r"\\end\s*\{" + re.escape(env) + r"\}", line):
                env = None
            continue
        new, had = _strip_line(line)
        m = re.search(r"\\begin\s*\{(" + "|".join(re.escape(e) for e in VERBATIM_ENVS) + r")\}", new)
        if m and not re.search(r"\\end\s*\{" + re.escape(m.group(1)) + r"\}", new[m.end():]):
            env = m.group(1)
            new, had = line, False  # keep the line as it is
        if had:
            removed = True
            if new.strip() == "%":
                continue
        out.append(new)
    return "\n".join(out), removed


# ---------------------------------------------------------------------------
# Project structure
# ---------------------------------------------------------------------------

def _uncommented(text):
    return strip_comments(text)[0]


def find_main(files):
    """The .tex file that holds \\documentclass and \\begin{document}; others that do too are returned as well."""
    mains = []
    for n, b in files.items():
        if n.lower().endswith(".tex"):
            t = _uncommented(_text(b))
            if re.search(r"\\document(class|style)\b", t) and "\\begin{document}" in t:
                mains.append(n)
    if not mains:
        raise ValueError("No main .tex file found (one with \\documentclass and \\begin{document}).")

    def rank(n):
        base = posixpath.basename(n).lower()
        return (n.count("/"), base not in ("main.tex", "ms.tex", "paper.tex", "manuscript.tex", "article.tex"), n)
    mains.sort(key=rank)
    return mains[0], mains[1:]


def _resolve(files, name, base_dir, exts=("", ".tex")):
    """A file name as LaTeX would find it: relative to the main file's folder. Returns (name or None, case_hint)."""
    name = name.strip().strip('"')
    lower = {n.lower(): n for n in files}
    for e in exts:
        cand = posixpath.normpath(posixpath.join(base_dir, name + e)) if base_dir else posixpath.normpath(name + e)
        if cand in files:
            return cand, None
        if cand.lower() in lower:
            return None, lower[cand.lower()]
    return None, None


def expand(files, main, warnings=None, missing=None):
    """The main file with every \\input / \\include / \\subfile pasted in, comments removed: the text LaTeX reads,
    in reading order (so citation numbers come out in the right order)."""
    base = posixpath.dirname(main)
    seen = set()

    def go(name, depth):
        if depth > 20 or name in seen:
            return ""
        seen.add(name)
        text = _uncommented(_text(files[name]))

        def sub(m):
            target = m.group(2)
            if target.startswith("/") or re.match(r"[A-Za-z]:", target):
                if warnings is not None:
                    warnings.append(f"{name} uses a full path ({target}); arXiv can't see your computer. "
                                    "Use a path inside the project.")
                return ""
            found, case = _resolve(files, target, base)
            if found:
                return go(found, depth + 1)
            if missing is not None:
                missing.append((target, case, name))
            return ""
        return re.sub(r"\\(input|include|subfile)\s*\{([^}]+)\}", sub, text)
    return go(main, 0)


def _figures(files, text, main):
    """[(as written, found name or None, case hint)] for every \\includegraphics."""
    base = posixpath.dirname(main)
    paths = [""]
    m = re.search(r"\\graphicspath\s*\{((?:\s*\{[^}]*\})+)\s*\}", text)
    if m:
        paths += re.findall(r"\{([^}]*)\}", m.group(1))
    out = []
    for g in re.finditer(r"\\includegraphics\*?\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}", text):
        name = g.group(1).strip()
        found = case = None
        exts = ("",) if os.path.splitext(name)[1].lower() in GRAPHIC_EXTS else ("",) + GRAPHIC_EXTS
        for p in paths:
            f, c = _resolve(files, posixpath.join(p, name) if p else name, base, exts)
            if f:
                found = f
                break
            case = case or c
        out.append((name, found, None if found else case))
    return out


# ---------------------------------------------------------------------------
# The bibliography
# ---------------------------------------------------------------------------

def bibliography_kind(text):
    if re.search(r"\\usepackage\s*(\[[^\]]*\])?\s*\{[^}]*\bbiblatex\b", text) or "\\addbibresource" in text:
        return "biblatex"
    if re.search(r"\\bibliography\s*\{", text):
        return "bibtex"
    if "\\begin{thebibliography}" in text:
        return "thebibliography"
    return "none"


def _bib_keys(text):
    return {m.group(1).strip().lower() for m in re.finditer(r"@\s*(?!comment|string|preamble)\w+\s*[{(]\s*([^,\s]+)\s*,",
                                                           text, re.I)}


def make_bbl(files, main, text):
    """Run BibTeX (pybtex) for the project. Returns (bbl text, info dict). Raises ValueError with a plain reason."""
    from pybtex import errors as pyerrors
    from pybtex.bibtex import BibTeXEngine

    base = posixpath.dirname(main)
    sm = re.findall(r"\\bibliographystyle\s*\{([^}]+)\}", text)
    if not sm:
        raise ValueError("The project has \\bibliography but no \\bibliographystyle, so BibTeX doesn't know "
                         "how to format the references. Add one, e.g. \\bibliographystyle{plainnat}.")
    style = sm[-1].strip()
    bst_name = style if style.lower().endswith(".bst") else style + ".bst"
    bst, source = None, "project"
    found, _ = _resolve(files, bst_name, base, ("",))
    if not found:
        found = next((n for n in files if posixpath.basename(n).lower() == posixpath.basename(bst_name).lower()), None)
    if found:
        bst = files[found]
    else:
        p = os.path.join(_bst_dir(), posixpath.basename(bst_name))
        if os.path.exists(p):
            with open(p, "rb") as f:
                bst = f.read()
            source = "citrello"
    if bst is None:
        raise ValueError(f"The bibliography style “{style}” isn't in the project, and Citrello doesn't have it. "
                         f"Add {posixpath.basename(bst_name)} to the project (journal templates come with it) and "
                         "try again.")
    bib_names = []
    for group in re.findall(r"\\bibliography\s*\{([^}]+)\}", text):
        for n in group.split(","):
            n = n.strip()
            if n:
                bib_names.append(n if n.lower().endswith(".bib") else n + ".bib")
    bibs, missing_bibs = [], []
    for n in bib_names:
        f, case = _resolve(files, n, base, ("",))
        if not f:
            f = next((x for x in files if posixpath.basename(x).lower() == posixpath.basename(n).lower()), None)
        (bibs.append(f) if f else missing_bibs.append(n))
    if not bibs:
        raise ValueError(f"The bibliography file {', '.join(bib_names) or '(none)'} isn't in the project. "
                         "Add it and try again.")

    cites, seen = [], set()
    for cmd, keys in latex.find_cites(text):
        for k in keys:
            if k and k.lower() not in seen:
                seen.add(k.lower())
                cites.append(k)
    known = set()
    for b in bibs:
        known |= _bib_keys(_text(files[b]))
    undefined = [k for k in cites if k != "*" and k.lower() not in known]

    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i, b in enumerate(bibs):
            p = os.path.join(tmp, f"bib{i}.bib")
            with open(p, "w", encoding="utf-8") as f:
                f.write(_text(files[b]))
            paths.append(p)
        stem = os.path.join(tmp, "style")
        with open(stem + ".bst", "w", encoding="utf-8") as f:
            f.write(_text(bst))
        out = io.StringIO()
        try:
            with pyerrors.capture() as problems, contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                bbl = BibTeXEngine().format_from_files(paths, style=stem, citations=cites, bib_encoding="utf-8",
                                                       bst_encoding="utf-8")
        except Exception as e:
            raise ValueError(f"BibTeX stopped with an error: {e}. Check the .bib file around that entry.")
    notes = [str(p) for p in problems if "missing database entry" not in str(p)]
    return bbl, {"style": style, "bst_source": source, "bib_files": bibs, "missing_bib_files": missing_bibs,
                 "entries": bbl.count("\\bibitem"), "undefined": undefined, "notes": notes[:10]}


# ---------------------------------------------------------------------------
# Preparing the upload
# ---------------------------------------------------------------------------

def prepare(files, strip=True):
    """files: {name: bytes} from read_all. Returns {"files": {name: bytes}, "report": {...}}."""
    files = dict(files)
    changes, warnings, errors = [], [], []
    main, other_mains = find_main(files)
    base = posixpath.dirname(main)
    stem = posixpath.splitext(main)[0]
    if other_mains:
        warnings.append("Several files start a document (" + ", ".join([main] + other_mains) + "). arXiv may "
                        f"pick the wrong one: remove the others, or choose {main} when arXiv asks.")

    missing = []
    text = expand(files, main, warnings, missing)
    for target, case, where in missing:
        if case:
            errors.append(f"{where} asks for “{target}” but the file is called {case}. arXiv is case-sensitive: "
                          "make the names match.")
        else:
            errors.append(f"{where} asks for “{target}”, which isn't in the project.")

    # bibliography
    kind = bibliography_kind(text)
    bib = {"kind": kind}
    bbl_name = stem + ".bbl"
    if kind == "bibtex":
        try:
            bbl, info = make_bbl(files, main, text)
            bib.update(info)
            had = bbl_name in files
            files[bbl_name] = bbl.encode("utf-8")
            changes.append(f"{'Replaced' if had else 'Added'} {bbl_name}: the bibliography arXiv needs "
                           f"({info['entries']} reference{'s' if info['entries'] != 1 else ''}, style "
                           f"{info['style']}). arXiv doesn't run BibTeX itself.")
            if info["undefined"]:
                errors.append(f"{len(info['undefined'])} citation{'s are' if len(info['undefined']) != 1 else ' is'} "
                              "not in the .bib and would print as [?]: " + ", ".join(info["undefined"][:15]) +
                              ("…" if len(info["undefined"]) > 15 else ""))
            for n in info["missing_bib_files"]:
                warnings.append(f"\\bibliography asks for {n}, which isn't in the project.")
            removed = [b for b in info["bib_files"] if b in files]
            for b in removed:
                del files[b]
            if removed:
                changes.append("Removed " + ", ".join(removed) + ": not needed once the .bbl is there, and arXiv "
                               "would publish it.")
        except ValueError as e:
            errors.append(str(e))
    elif kind == "biblatex":
        if bbl_name in files:
            warnings.append("This project uses biblatex and already has a .bbl. arXiv accepts it only if it was "
                            "made with the same Biber version arXiv uses; if arXiv complains, use Overleaf's "
                            "Submit → arXiv, or switch to natbib.")
        else:
            errors.append("This project uses biblatex. arXiv needs a .bbl made by the same Biber version arXiv "
                          "uses, which Citrello can't make. In Overleaf, use Submit → arXiv (it adds a matching "
                          ".bbl), or switch to \\usepackage{natbib} with \\bibliographystyle and \\bibliography.")

    # figures
    figs = _figures(files, text, main)
    for name, found, case in figs:
        if not found:
            errors.append(f"Figure “{name}”: " + (f"the file is called {case}. arXiv is case-sensitive: make the "
                                                  "names match." if case else "the file isn't in the project."))
    fig_exts = {os.path.splitext(f)[1].lower() for _, f, _ in figs if f}
    eps = bool(fig_exts & {".eps", ".ps"})
    raster = bool(fig_exts & {".png", ".jpg", ".jpeg", ".pdf"})
    if eps and raster:
        warnings.append("The figures mix .eps with .png/.jpg/.pdf. arXiv compiles with either LaTeX (needs .eps) "
                        "or pdfLaTeX (needs .png/.jpg/.pdf), not both: convert the .eps figures to .pdf.")
    if re.search(r"\\usepackage\s*(\[[^\]]*\])?\s*\{[^}]*\b(fontspec|unicode-math|polyglossia|xeCJK)\b", text):
        warnings.append("The project loads a XeLaTeX/LuaLaTeX package (fontspec, unicode-math, polyglossia or "
                        "xeCJK). arXiv compiles with pdfLaTeX, so it may fail.")

    # \pdfoutput=1 tells arXiv to use pdfLaTeX
    main_text = _text(files[main])
    if not eps and "\\pdfoutput" not in "\n".join(main_text.split("\n")[:5]):
        main_text = "\\pdfoutput=1\n" + main_text
        files[main] = main_text.encode("utf-8")
        changes.append(f"Added \\pdfoutput=1 at the top of {main}, so arXiv uses pdfLaTeX.")

    # comments
    if strip:
        stripped = []
        for n in list(files):
            if n.lower().endswith((".tex", ".ltx")):
                new, had = strip_comments(_text(files[n]))
                if had:
                    files[n] = new.encode("utf-8")
                    stripped.append(n)
        if stripped:
            changes.append(f"Removed comments from {len(stripped)} .tex file{'s' if len(stripped) != 1 else ''}. "
                           "arXiv lets anyone download your source files.")

    # leftovers
    junk = [n for n in files if _ext(n) in JUNK or (n.lower() == stem.lower() + ".pdf")]
    for n in junk:
        del files[n]
    if junk:
        changes.append(f"Removed {len(junk)} build file{'s' if len(junk) != 1 else ''} arXiv doesn't need "
                       f"({', '.join(sorted({_ext(n) for n in junk}))}).")

    bad = [n for n in files if not NAME_OK.match(n)]
    if bad:
        errors.append("arXiv only accepts file names with letters, digits and _ + - . , = (no spaces or accents): "
                      + ", ".join(bad[:10]) + ("…" if len(bad) > 10 else ""))
    size = sum(len(b) for b in files.values())
    if size > MAX_UPLOAD:
        errors.append(f"The project is {size / 1048576:.0f} MB; arXiv accepts up to 50 MB. Make large figures "
                      "smaller (e.g. save photos as .jpg) or remove files that aren't used.")
    return {"files": files, "report": {
        "main": main, "bibliography": bib, "changes": changes, "warnings": warnings, "errors": errors,
        "files": len(files), "size": size, "ok": not errors}}


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            z.writestr(name, files[name])
    return buf.getvalue()
