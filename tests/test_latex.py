"""LaTeX and Overleaf: BibTeX output, \\cite parsing, project checks, conversion, automatic export."""
import io
import os
import zipfile

import latex
import library
import mcp_server

TEX = r"""\documentclass{article}\usepackage{natbib}
\begin{document}
Wind \citep[p.~4]{Topham2017, smith2020}. Also \citet{jones2019}, \cite{dupA,dupB}. % \cite{commented}
\parencites[see][]{jones2019}{dupA}
\bibliographystyle{plainnat}\bibliography{refs}
\end{document}"""
BIB = r"""@article{jones2019, title={A study of {LiDAR}}, author={Jones, A.}, year={2019}, journal={J}}
@article{dupA, title={Offshore wind costs}, author={Lee, K.}, year={2020}, journal={Energy}, doi={10.1/abc}}
@article{dupB, title={Offshore Wind Costs}, author={Lee, K.}, year={2020}, journal={Energy}, doi={10.1/ABC}}
@misc{unused1, title={Never cited}, year={2001}}
"""
TYPED = r"""\documentclass{article}\begin{document}
As shown \cite{b1,b2}.
\begin{thebibliography}{9}
\bibitem{b1} E.~Topham and D.~McMillan, ``Sustainable decommissioning of an offshore wind farm,'' \emph{Renewable Energy}, vol.~102, pp. 470--480, 2017.
% \bibitem{old} commented out
\bibitem[Smith(2020)]{b2} Smith, J. (2020). \newblock Wind turbine blade recycling. \newblock {\em Waste Management}, 105.
\end{thebibliography}
\end{document}"""


def _seed():
    with library.editing() as d:
        library.add_csl(d, {"type": "article-journal", "title": "Sustainable decommissioning of an offshore wind farm",
                            "author": [{"family": "Topham", "given": "Eva"}], "issued": {"date-parts": [[2017]]},
                            "container-title": "Renewable Energy", "DOI": "10.1016/j.renene.2016.10.066"}, "LaTeX test")


def _zip(**files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, t in files.items():
            z.writestr(n.replace("_", "."), t)
    return buf.getvalue()


def test_bibtex_entry():
    c = {"type": "article-journal", "title": "LiDAR-based <i>in situ</i> monitoring of CO2 & NOx: 50% gains_x",
         "author": [{"family": "Van Passel", "given": "Steven"}, {"literal": "IEA Wind TCP"}],
         "container-title": "Energy & Environment", "issued": {"date-parts": [[2024, 3, 5]]}, "page": "10-20",
         "DOI": "10.1/x_y"}
    e = latex.entry("VanPassel2024", c)
    assert e.startswith("@article{VanPassel2024,")
    assert r"title = {{LiDAR-based} \textit{in situ} monitoring of {CO2} \& {NOx}: 50\% gains\_x}" in e
    assert "author = {Van Passel, Steven and {IEA Wind TCP}}" in e
    assert r"journal = {Energy \& Environment}" in e and "month = mar," in e and "pages = {10--20}" in e
    assert "doi = {10.1/x_y}" in e
    b = latex.entry("VanPassel2024", c, "biblatex")
    assert "journaltitle = " in b and "date = {2024-03-05}" in b
    w = latex.entry("Site2025", {"type": "webpage", "title": "Page", "URL": "https://a.org/x", "issued": {"date-parts": [[2025]]}}, "biblatex")
    assert w.startswith("@online{") and "url = {https://a.org/x}" in w


def test_cite_parsing_and_renaming():
    assert latex.cited_keys(TEX) == ["Topham2017", "smith2020", "jones2019", "dupA", "dupB", "jones2019", "dupA"]
    out = latex.replace_cite_keys(TEX, {"smith2020": "Smith2020", "dupA": "Lee2020", "commented": "X"})
    assert r"\citep[p.~4]{Topham2017, Smith2020}" in out and r"\cite{Lee2020,dupB}" in out
    assert r"{jones2019}{Lee2020}" in out and r"% \cite{commented}" in out


def test_check_project_and_fixed_bib():
    _seed()
    files = latex.read_project(_zip(main_tex=TEX, refs_bib=BIB), "paper.zip")
    rep = latex.check_project(files, library.load())
    missing = {m["key"]: m for m in rep["missing"]}
    assert set(missing) == {"Topham2017", "smith2020"} and missing["Topham2017"]["in_library"]
    assert rep["unused"] == ["unused1"] and rep["duplicates"] == [["dupA", "dupB"]]
    assert not rep["ok"] and rep["bib_files_used"] == ["refs.bib"]
    name, text, n = latex.fixed_bib(files, rep, library.load())
    assert name == "refs.bib" and n == 1 and text.startswith("@article{jones2019") and "@article{Topham2017," in text


def test_convert_thebibliography():
    _seed()
    files = latex.read_project(TYPED.encode(), "main.tex")
    assert len(latex.typed_bibliography(TYPED)) == 2
    assert latex.check_project(files, library.load())["missing"] == []  # bibitem keys aren't "missing"
    res = latex.convert_thebibliography(files, "Typed", offline=True)
    assert res["mapping"]["b1"] == "Topham2017"   # matched the library copy
    tex = res["files"]["main.tex"]
    assert r"\cite{Topham2017," in tex and "thebibliography" not in tex and r"\bibliography{references}" in tex
    assert "@article{Topham2017," in res["files"]["references.bib"]


def test_bib_import_keeps_keys():
    keymap = {}
    library.import_items(library.parse_any(BIB, "refs.bib"), "Keys", keymap=keymap)
    keys = {it["key"] for it in library.load()["items"]}
    assert {"jones2019", "dupA", "unused1"} <= keys
    assert "dupB" not in keys and keymap.get("dupB") == "dupA"  # same paper: merged, and reported


def test_automatic_export(tmp_path, monkeypatch):
    monkeypatch.setattr(latex, "EXPORTS_FILE", str(tmp_path / "exports.json"))
    _seed()
    latex.add_export("LaTeX test", str(tmp_path))
    bib = tmp_path / "references.bib"
    assert "@article{Topham2017," in bib.read_text()
    with library.editing() as d:
        library.add_csl(d, {"type": "book", "title": "Wind energy handbook", "author": [{"family": "Burton", "given": "T"}],
                            "issued": {"date-parts": [[2011]]}, "publisher": "Wiley"}, "LaTeX test")
    assert "@book{Burton2011," in bib.read_text()  # rewritten on save
    other = tmp_path / "mine.bib"
    other.write_text("@article{x, title={Mine}}")
    try:
        latex.add_export(None, str(other))
        raise AssertionError("must not overwrite a .bib Citrello didn't write")
    except ValueError:
        pass
    latex.remove_export(str(bib))
    assert latex.list_exports() == []


def test_mcp_latex_tools(tmp_path):
    _seed()
    r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "check_citations", "arguments": {"text": r"See \citep{Topham2017,Nobody1999}."}}})
    assert '"Nobody1999"' in r["result"]["content"][0]["text"]
    r = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "get_bibtex", "arguments": {"keys": ["Topham2017"], "format": "biblatex"}}})
    assert "@article{Topham2017," in r["result"]["content"][0]["text"]
    p = tmp_path / "paper.zip"
    p.write_bytes(_zip(main_tex=TEX, refs_bib=BIB))
    r = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "check_latex_project", "arguments": {"path": str(p)}}})
    assert not r["result"]["isError"] and "smith2020" in r["result"]["content"][0]["text"]
