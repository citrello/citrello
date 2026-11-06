"""arXiv preparation: the .bbl BibTeX would make, comment removal, leftovers, and the checks that catch what
makes arXiv fail. The last test compiles the result with pdfLaTeX (no BibTeX), as arXiv does, when LaTeX is
installed."""
import io
import os
import shutil
import subprocess
import zipfile

import pytest

import arxiv

BIB = r"""@article{smith2020, author={Smith, John and M{\"u}ller, Anna}, title={Deep learning for {CRISPR} screens},
  journal={Nature Methods}, year={2020}, volume={17}, pages={123--130}}
@book{knuth1984, author={Donald E. Knuth}, title={The {\TeX}book}, publisher={Addison-Wesley}, year=1984}
@inproceedings{lee2019, author={Lee, Kim and Park, Jae}, title={A study of things}, booktitle={NeurIPS}, year={2019}}
@misc{uncited, title={Never cited}, year={2001}}
"""
MAIN = r"""\documentclass{article}
\usepackage[numbers]{natbib}
\usepackage{graphicx,url}
% PRIVATE: reviewer 2 is wrong
\graphicspath{{figures/}}
\begin{document}
\input{sections/intro}
See \url{https://example.com/a%20b}, 50\% of cases.% my note
\includegraphics{plot}
\begin{verbatim}
x = 5 % stays
\end{verbatim}
\bibliographystyle{plainnat}
\bibliography{refs}
\end{document}
"""
INTRO = "% draft v3\nShown by \\citet{lee2019} and \\citep{smith2020,knuth1984}.\n"
def _png():
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"\x00\x1d\x3f\x8f")) + chunk(b"IEND", b""))


PNG = _png()


def _zip(files, folder=""):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in files.items():
            z.writestr(folder + n, b)
    return buf.getvalue()


def _project(**extra):
    files = {"main.tex": MAIN, "sections/intro.tex": INTRO, "refs.bib": BIB, "figures/plot.png": PNG,
             "main.aux": "x", "main.log": "x", "main.synctex.gz": "x"}
    files.update(extra)
    return {n: (b.encode() if isinstance(b, str) else b) for n, b in files.items() if b is not None}


def test_prepare_makes_bbl_and_cleans_up():
    files = arxiv.read_all(_zip(_project()), "paper.zip")
    res = arxiv.prepare(files)
    rep, out = res["report"], res["files"]
    assert rep["ok"], rep
    assert rep["main"] == "main.tex"
    assert rep["bibliography"]["entries"] == 3 and rep["bibliography"]["bst_source"] == "citrello"
    bbl = out["main.bbl"].decode()
    # natbib labels in citation order: lee2019 first (unsrt would number them; plainnat sorts by author)
    assert "\\bibitem[Lee and Park(2019)]{lee2019}" in bbl and "uncited" not in bbl
    assert set(out) == {"main.tex", "sections/intro.tex", "figures/plot.png", "main.bbl"}  # .bib and junk gone
    main = out["main.tex"].decode()
    assert main.startswith("\\pdfoutput=1\n")
    assert "PRIVATE" not in main and "my note" not in main
    assert "a%20b" in main and "50\\%" in main and "x = 5 % stays" in main
    assert "cases.%\n" in main  # the trailing % stays, so the lines still join the same way
    assert "draft" not in out["sections/intro.tex"].decode()


def test_keep_comments_and_flatten_folder():
    files = arxiv.read_all(_zip(_project(), folder="My paper/"), "paper.zip")
    assert "main.tex" in files  # the single top folder is removed
    res = arxiv.prepare(files, strip=False)
    assert "PRIVATE" in res["files"]["main.tex"].decode()


def test_problems_are_reported():
    proj = _project(**{"figures/plot.png": None, "figures/Plot.PNG": PNG})
    proj["sections/intro.tex"] = (INTRO + "And \\cite{ghost2021}.\n\\input{sections/missing}\n").encode()
    proj["my figure.pdf"] = b"%PDF"
    rep = arxiv.prepare(arxiv.read_all(_zip(proj), "p.zip"))["report"]
    text = "\n".join(rep["errors"])
    assert not rep["ok"]
    assert "Plot.PNG" in text and "case-sensitive" in text
    assert "ghost2021" in text and "[?]" in text
    assert "sections/missing" in text
    assert "my figure.pdf" in text


def test_project_bst_and_unknown_style():
    proj = _project()
    proj["main.tex"] = MAIN.replace("{plainnat}", "{myjournal}").encode()
    rep = arxiv.prepare(arxiv.read_all(_zip(proj), "p.zip"))["report"]
    assert any("myjournal" in e and "isn't in the project" in e for e in rep["errors"])
    proj["styles/myjournal.bst"] = open(os.path.join(arxiv._bst_dir(), "unsrt.bst"), "rb").read()
    rep = arxiv.prepare(arxiv.read_all(_zip(proj), "p.zip"))["report"]
    assert rep["ok"] and rep["bibliography"]["bst_source"] == "project"


def test_biblatex_and_nocite_all():
    proj = _project()
    proj["main.tex"] = MAIN.replace("\\usepackage[numbers]{natbib}", "\\usepackage{biblatex}\\addbibresource{refs.bib}").encode()
    rep = arxiv.prepare(arxiv.read_all(_zip(proj), "p.zip"))["report"]
    assert rep["bibliography"]["kind"] == "biblatex" and any("Submit → arXiv" in e for e in rep["errors"])
    proj["main.tex"] = MAIN.replace("\\begin{document}", "\\begin{document}\\nocite{*}").encode()
    res = arxiv.prepare(arxiv.read_all(_zip(proj), "p.zip"))
    assert res["report"]["bibliography"]["entries"] == 4 and "uncited" in res["files"]["main.bbl"].decode()


def test_strip_comments_edge_cases():
    src = "a % c\n% whole\nb\\\\% after a line break\n\\verb|%| d % e\n\\href{http://x/%41}{t} % f\n"
    out, had = arxiv.strip_comments(src)
    assert had
    assert out == "a %\nb\\\\%\n\\verb|%| d %\n\\href{http://x/%41}{t} %\n"


@pytest.mark.skipif(not shutil.which("pdflatex"), reason="needs pdflatex")
def test_arxiv_zip_compiles_without_bibtex(tmp_path):
    res = arxiv.prepare(arxiv.read_all(_zip(_project()), "paper.zip"))
    with zipfile.ZipFile(io.BytesIO(arxiv.zip_bytes(res["files"]))) as z:
        z.extractall(tmp_path)
    for _ in range(2):  # arXiv runs (pdf)latex, never bibtex
        subprocess.run(["pdflatex", "-interaction=nonstopmode", "main.tex"], cwd=tmp_path, capture_output=True,
                       timeout=120)
    log = (tmp_path / "main.log").read_text(errors="replace")
    assert (tmp_path / "main.pdf").exists()
    assert "undefined" not in log.lower(), [ln for ln in log.splitlines() if "undefined" in ln.lower()]
