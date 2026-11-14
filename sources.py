"""
"Check my sources": every sentence of a document that cites a paper, next to the passages of that paper's full text
that match it best, so the writer can see whether the paper supports what they wrote. No AI: the passages are found
by word matching (BM25, fulltext.passages) in the texts Citrello keeps; everything stays on the computer.

Citations are read from [@Key] markers, from Zotero / Mendeley / Citrello / EndNote / Word fields, from Mendeley
Cite content controls, and from typed citations ([1], (Smith, 2020)) matched to the typed reference list.
"""
import re

import docx
from docx.oxml.ns import qn

import citeformat
import citrello_core as core
import doccheck
import fulltext
import library

MAX_SENTENCES = 200
TOKEN = "{}"
TOKEN_RE = re.compile("(\\d+)")
ABBREV_END = re.compile(r"\b(?:et al|e\.g|i\.e|cf|vs|fig|figs|eq|eqs|ref|refs|no|vol|approx|ca|resp)\.$", re.I)
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"“(\[A-Z0-9])")


# ---------------------------------------------------------------------------
# Reading a paragraph with its citations in place
# ---------------------------------------------------------------------------

def _field_items(instr, data, word_sources):
    """The CSL items a citation field holds, or None when the field isn't a citation."""
    s, up = instr.strip(), instr.strip().upper()
    if "CSL_CITATION" in up:
        return [c for _, c in doccheck._csl_items(s)]
    if up.startswith("ADDIN EN.CITE"):
        return [c for _, c in doccheck._endnote_items(data or s)]
    if re.match(r"CITATION\s", s):
        return [word_sources[t] for t in re.findall(r"(?:^CITATION|\\m)\s+(\S+)", s) if t in word_sources]
    return None


def _paragraph(p_el, groups, cc_payloads, word_sources):
    """The paragraph's text, each citation field / content control replaced by a token (its items in `groups`)."""
    out = []
    state = {"depth": 0, "instr": "", "data": "", "cite": None, "shown": ""}

    def fld_data(fc):
        import base64
        fd = fc.find(qn("w:fldData"))
        if fd is None or not fd.text:
            return ""
        raw = base64.b64decode(re.sub(r"\s+", "", fd.text) + "==")
        for enc in ("utf-8", "utf-16"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return ""

    def text(t):
        if state["depth"] and state["cite"] is not None:
            state["shown"] += t  # what the citation looks like in the text
        elif not state["depth"] or state.get("result"):
            out.append(t)

    def walk(el):
        tag = el.tag
        if tag == qn("w:sdt") and id(el) in cc_payloads:  # Mendeley Cite: the whole control is one citation
            shown = "".join(t.text or "" for t in el.iter(qn("w:t")))
            groups.append({"items": cc_payloads[id(el)], "shown": shown})
            out.append(TOKEN.format(len(groups) - 1))
            return
        if tag == qn("w:fldChar"):
            kind = el.get(qn("w:fldCharType"))
            if kind == "begin":
                state["depth"] += 1
                if state["depth"] == 1:
                    state.update(instr="", data=fld_data(el), cite=None, shown="", result=False)
            elif kind == "separate" and state["depth"] == 1:
                items = _field_items(state["instr"], state["data"], word_sources)
                state["cite"] = items
                state["result"] = True
            elif kind == "end":
                if state["depth"] == 1:
                    if state["cite"]:
                        groups.append({"items": state["cite"], "shown": state["shown"]})
                        out.append(TOKEN.format(len(groups) - 1))
                    state.update(cite=None, result=False)
                state["depth"] = max(0, state["depth"] - 1)
            return
        if tag == qn("w:instrText"):
            if state["depth"] == 1:
                state["instr"] += el.text or ""
            return
        if tag == qn("w:t"):
            text(el.text or "")
            return
        if tag in (qn("w:tab"), qn("w:br"), qn("w:cr")):
            text(" ")
            return
        if tag == qn("w:fldSimple"):
            items = _field_items(el.get(qn("w:instr")) or "", "", word_sources)
            shown = "".join(t.text or "" for t in el.iter(qn("w:t")))
            if items:
                groups.append({"items": items, "shown": shown})
                out.append(TOKEN.format(len(groups) - 1))
            else:
                out.append(shown)
            return
        for child in el:
            walk(child)

    for child in p_el:
        walk(child)
    return "".join(out)


def sentences(text):
    """Split a paragraph into sentences, keeping "et al." and the like inside them."""
    parts, out = SENT_SPLIT.split(text.strip()), []
    for part in parts:
        if out and ABBREV_END.search(TOKEN_RE.sub("", out[-1]).rstrip()):
            out[-1] += " " + part
        else:
            out.append(part)
    return [p for p in out if p.strip()]


# ---------------------------------------------------------------------------
# Finding the cited papers in the library
# ---------------------------------------------------------------------------

class _Library:
    def __init__(self, data):
        self.items = data["items"]
        self.by_key = {it["key"]: it for it in self.items}
        self.by_doi, self.by_title = {}, {}
        for it in self.items:
            c = it.get("csl") or {}
            if c.get("DOI"):
                self.by_doi.setdefault(core.norm_doi(c["DOI"]), it)
            t = core.norm(c.get("title", ""))
            if len(t) > 12:
                self.by_title.setdefault(t, it)
        self.texts = {}

    def find(self, csl):
        if not csl:
            return None
        if csl.get("DOI") and core.norm_doi(csl["DOI"]) in self.by_doi:
            return self.by_doi[core.norm_doi(csl["DOI"])]
        return self.by_title.get(core.norm(csl.get("title", "")))

    def text(self, it):
        if it["key"] not in self.texts:
            self.texts[it["key"]] = fulltext.read(it)
        return self.texts[it["key"]]


def _label(csl):
    names = [a.get("family") or a.get("literal") or "" for a in csl.get("author") or []]
    who = names[0] if len(names) == 1 else f"{names[0]} & {names[1]}" if len(names) == 2 else f"{names[0]} et al." if names else ""
    year = core.year_of(csl) or "n.d."
    return f"{who} {year}".strip() if who else (csl.get("title") or "")[:60]


# ---------------------------------------------------------------------------
# The passages that match a sentence
# ---------------------------------------------------------------------------

def _qwords(text):
    return {w for w in fulltext._words(text) if w not in fulltext.STOP and len(w) > 2}


def best_window(piece, query_words, width=480):
    """The sentence(s) of a passage that share most words with the query (about `width` characters)."""
    sents = [s for s in re.split(r"(?<=[.!?])\s+", piece.replace("\n", " ")) if s.strip()]
    if not sents:
        return piece[:width], 0
    scores = [len(_qwords(s) & query_words) for s in sents]
    i = max(range(len(sents)), key=lambda k: scores[k])
    lo, hi, text = i, i, sents[i]
    while len(text) < width:
        grew = False
        if hi + 1 < len(sents) and len(text) + len(sents[hi + 1]) <= width:
            hi += 1
            grew = True
        if lo > 0 and len(text) + len(sents[lo - 1]) <= width:
            lo -= 1
            grew = True
        text = " ".join(sents[lo:hi + 1])
        if not grew:
            break
    return text[:width + 200], len(_qwords(text) & query_words)


def evidence(paper_text, sentence, n=2):
    """Up to n passages of the paper that match the sentence, best first: [{"text", "at", "words"}]."""
    q = _qwords(sentence)
    if not q:
        return []
    found = []
    for p in fulltext.passages(paper_text, sentence, n=4):
        text, words = best_window(p["text"], q)
        if words >= 2 or (words and len(q) <= 3):
            found.append({"text": text, "at": p["at"], "words": words})
    return sorted(found, key=lambda f: -f["words"])[:n]


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def check(path):
    """-> {"sentences": [...], "counts": {...}} for a .docx."""
    if not path.lower().endswith(".docx"):
        raise ValueError("Checking sources works on Word documents (.docx) for now.")
    document = docx.Document(path)
    body = document.element.body
    lib = _Library(library.load())
    word_sources = None
    try:
        heading, entries = core.find_bibliography(document)
    except SystemExit:
        heading, entries = None, []
    bib_paras = {id(e[1]._p) for e in entries}
    cc_payloads = {id(sdt): [c for _, c, _ in doccheck._payload_csl(pl)]
                   for sdt, kind, pl in doccheck.content_controls(body) if kind == "citation"}
    if any(re.match(r"\s*CITATION\s", instr) for instr, _ in doccheck._fields(document)):
        word_sources = doccheck._word_sources(path)

    paras, groups, els = [], [], []
    for p in core.body_paragraphs(document):
        if heading is not None and p._p is heading._p:
            break  # citations live before the reference list
        if id(p._p) in bib_paras:
            continue
        els.append(p._p)
        paras.append(_paragraph(p._p, groups, cc_payloads, word_sources or {}))

    has_markers = any(citeformat.MARKER_RE.search(t) for t in paras)
    numbers = {}  # numbered typed lists: each reference's number, to show the citation as [12]
    if not groups and not has_markers and entries:  # typed citations: match them to the typed list
        typed_refs = [{"number": num, "raw": text, "csl": core.parse_reference(text), "source": "parsed"}
                      for num, _, text in entries]
        numeric = core.detect_mode(entries) == "numeric"
        finder = (core.numeric_finder({r["number"]: r for r in typed_refs}, {"bracket", "superscript"})
                  if numeric else core.author_year_finder(typed_refs))
        if numeric:
            numbers = {id(r["csl"]): r["number"] for r in typed_refs}
        for n, p_el in enumerate(els):
            segs = core.segments(p_el)
            full = "".join(sg[3] for sg in segs)
            sup = "".join(("1" if sg[4] else "0") * len(sg[3]) for sg in segs)
            for st, en, items in sorted(finder(p_el, full, sup, []), key=lambda f: -f[0]):
                groups.append({"items": [it["ref"]["csl"] for it in items], "shown": full[st:en]})
                full = full[:st] + TOKEN.format(len(groups) - 1) + full[en:]
            paras[n] = full.replace(core.OPAQUE, "")

    out, counts = [], {"sentences": 0, "with_text": 0, "no_text": 0, "not_in_library": 0, "papers": 0}
    for para in paras:
        for sent in sentences(para):
            cites, shown_sent = [], sent
            for m in TOKEN_RE.finditer(sent):  # fields and content controls
                g = groups[int(m.group(1))]
                cites += [("csl", c) for c in g["items"]]
                shown_sent = shown_sent.replace(m.group(0), g["shown"] or "(citation)")
            for m in citeformat.MARKER_RE.finditer(sent):  # [@Key] markers
                cites += [("key", g["key"]) for g in citeformat.parse_marker(m.group(1)) or []]
            if not cites:
                continue
            query = citeformat.MARKER_RE.sub(" ", TOKEN_RE.sub(" ", sent))
            query = re.sub(r"\[\d+(?:[,–\-]\s*\d+)*\]|\([^()]*\d{4}[a-z]?\)", " ", query)
            papers, seen = [], set()
            for kind, val in cites:
                it = lib.by_key.get(val) if kind == "key" else lib.find(val)
                csl = (it or {}).get("csl") or (val if kind == "csl" else {"title": f"[@{val}]"})
                ident = it["key"] if it else core.norm(csl.get("title", "")) or str(val)
                if ident in seen:
                    continue
                seen.add(ident)
                entry = {"label": _label(csl), "title": csl.get("title", ""), "key": it["key"] if it else None,
                         # how the citation looks in the paper: [12] for a numbered list, the marker, or author and year
                         "cite": f"[{numbers[id(val)]}]" if kind == "csl" and id(val) in numbers
                                 else f"[@{val}]" if kind == "key" else _label(csl)}
                if it:  # what the browser extension needs to fetch the paper
                    entry.update(doi=it["csl"].get("DOI", ""), url=it["csl"].get("URL", ""), type=it["csl"].get("type", ""))
                if not it:
                    entry["status"] = "not_in_library"
                else:
                    text = lib.text(it)
                    if not text:
                        entry["status"] = "no_text"
                    else:
                        entry["passages"] = evidence(text, query)
                        entry["status"] = "found" if entry["passages"] else "no_match"
                counts["with_text" if entry["status"] in ("found", "no_match") else entry["status"]] += 1
                papers.append(entry)
            counts["sentences"] += 1
            out.append({"sentence": shown_sent.strip(), "papers": papers})
            if len(out) >= MAX_SENTENCES:
                break
        if len(out) >= MAX_SENTENCES:
            break
    counts["papers"] = len({p["key"] or p["title"] for s in out for p in s["papers"]})
    return {"sentences": out, "counts": counts, "limited": len(out) >= MAX_SENTENCES}


REFS_HEAD = re.compile(r"\n\s*(?:\d+\.?\s*)?(references|bibliography|literature cited|works cited|reference list)\s*\n", re.I)


def body_text(text):
    """The paper without its own reference list (its last "References" heading in the second half)."""
    heads = [m for m in REFS_HEAD.finditer(text) if m.start() > len(text) * 0.5]
    return text[:heads[-1].start()] if heads else text


# ---------------------------------------------------------------------------
# For Claude's verdicts (a pro feature; the page sends these to Citrello's server, which asks Claude)
# ---------------------------------------------------------------------------

def _passages_for(it, text, claim, n=6):
    """The paper's passages most likely to matter for the claim: its opening (abstract) and the best matches."""
    body = body_text(text)
    found = [p["text"] for p in fulltext.passages(body, claim, n=n)]
    opening = body[:1200].strip()
    if opening and not any(opening[:200] in f for f in found):
        found = [opening] + found[:n - 1]
    return found


def ai_items(report):
    """One item per cited sentence: the claim and, for each cited paper with text, its best passages."""
    lib, items = _Library(library.load()), []
    for i, x in enumerate(report["sentences"]):
        claim = re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s+", " ", re.sub(
            r"\[@[^\]]*\]|\[\d+(?:[,\u2013\-]\s*\d+)*\]|\([^()]*\d{4}[a-z]?[^()]*\)", " ", x["sentence"]))).strip()
        papers = []
        for j, p in enumerate(x["papers"]):
            it = lib.by_key.get(p.get("key") or "")
            text = it and lib.text(it)
            if text:
                papers.append({"ref": f"{j}", "key": it["key"], "title": it["csl"].get("title", ""),
                               "passages": _passages_for(it, text, claim)})
        if papers and claim:
            items.append({"id": str(i), "claim": claim, "papers": papers})
    return items


def library_candidates(claim, exclude=(), n=6):
    """Papers in the library that may support the claim: their best passages (full text, else the abstract)."""
    data, exclude = library.load(), set(exclude)
    q = [w for w in fulltext._words(claim) if w not in fulltext.STOP]
    if not q:
        return []
    scored = []
    for it in data["items"]:
        if it["key"] in exclude:
            continue
        text = fulltext.read(it) or it["csl"].get("abstract") or ""
        if len(text) < 200:
            continue
        best = fulltext.passages(body_text(text), claim, n=3)
        if not best:
            continue
        words = set(fulltext._words(" ".join(b["text"] for b in best)))
        score = sum(1 for w in set(q) if w in words) / len(set(q))
        scored.append((score, it, [b["text"] for b in best], bool(it.get("fulltext"))))
    scored.sort(key=lambda t: -t[0])
    return [{"ref": it["key"], "key": it["key"], "title": it["csl"].get("title", ""), "label": _label(it["csl"]),
             "passages": passages, "full_text": ft} for score, it, passages, ft in scored[:n] if score >= 0.3]


# ---------------------------------------------------------------------------
# The Word add-in: checking one citation where the writer clicked
# ---------------------------------------------------------------------------

CITE_RE = re.compile(r"\[@[^\]]*\]|\[\d+(?:[,–\-]\s*\d+)*\]|\([^()]*\d{4}[a-z]?[^()]*\)")


def clean_claim(sentence):
    """The sentence without its citation markers."""
    return re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s+", " ", CITE_RE.sub(" ", sentence))).strip()


def sentence_at(paragraph, at):
    """The sentence of `paragraph` that holds character `at` (where the citation starts)."""
    pos, best = 0, paragraph
    for part in sentences(paragraph):
        start = paragraph.find(part, pos)
        if start < 0:
            continue
        end = start + len(part)
        if start <= at <= end:
            return part
        if start <= at:
            best = part
        pos = end
    return best


def word_paper_text(key, csl, search=True):
    """A cited paper's text for checking: from the library, else a free copy found online (kept in the library;
    the paper is added to it first if needed). -> {"key", "status": "text"|"abstract"|"none", "changed", "doi"}"""
    data = library.load()
    lib = _Library(data)
    it = lib.by_key.get(key) or lib.find(csl)
    changed = False
    if not it:
        if not isinstance(csl, dict) or not csl.get("title"):
            return {"key": key, "status": "none", "changed": False, "doi": ""}
        with library.editing() as d:
            it, _, _ = library.add_csl(d, dict(csl), source="word", key=key)
            it = dict(it)
        changed = True
    if search and not it.get("fulltext") and it["csl"].get("DOI"):
        res = fulltext.find_free([it["key"]])
        if res["found"]:
            changed = True
            it = library.get(library.load(), it["key"])
    if it.get("fulltext") and fulltext.read(it):
        status = "text"
    elif len(it["csl"].get("abstract") or "") >= 200:
        status = "abstract"
    else:
        status = "none"
    return {"key": it["key"], "status": status, "changed": changed, "doi": it["csl"].get("DOI", ""),
            "words": (it.get("fulltext") or {}).get("words", 0)}


def word_claim_item(paragraph, at, keys):
    """What Claude reads to judge one citation: the sentence, and each cited paper's best passages (full text, else
    its abstract). Papers with neither are left out. -> {"item": {...} | None, "sentence", "claim"}"""
    sentence = sentence_at(paragraph or "", int(at or 0))
    claim = clean_claim(sentence)
    lib, papers = _Library(library.load()), []
    for j, key in enumerate(keys):
        it = lib.by_key.get(key)
        if not it:
            continue
        text = lib.text(it)
        if text:
            passages = _passages_for(it, text, claim)
        elif len(it["csl"].get("abstract") or "") >= 200:
            passages = [it["csl"]["abstract"]]
        else:
            continue
        papers.append({"ref": key, "key": key, "title": it["csl"].get("title", ""), "passages": passages})
    item = {"id": "0", "claim": claim, "papers": papers} if papers and claim else None
    return {"item": item, "sentence": sentence, "claim": claim}

