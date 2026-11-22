"""tools/reference_scan.py: reading a paper's reference list from Europe PMC's XML, and the report."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import reference_scan as rs  # noqa: E402


def test_references_from_jats_xml():
    xml = ('<ref-list><ref id="r1"><label>1</label><mixed-citation>Topham E, McMillan D. Sustainable decommissioning '
           'of an offshore wind farm. <source>Renew Energy</source>. 2017;102:470-80. <pub-id pub-id-type="doi">'
           '10.1016/j.renene.2016.10.066</pub-id> <pub-id pub-id-type="pmid">123</pub-id></mixed-citation></ref>'
           '<ref id="r2"><mixed-citation>Short.</mixed-citation></ref></ref-list>')
    refs = rs.references(xml)
    assert refs == ["Topham E, McMillan D. Sustainable decommissioning of an offshore wind farm. Renew Energy. "
                    "2017;102:470-80. https://doi.org/10.1016/j.renene.2016.10.066"]


def test_report_counts_papers_with_two_or_more():
    paper = {"pmcid": "PMC1", "doi": "10.1/x", "journal": "J", "year": "2026", "title": "A paper", "refs": 40,
             "failed_lookups": 0}
    results = [dict(paper, maybe_nonexistent=[{"n": 3, "ref": "Fake A.", "why": "may not exist"},
                                              {"n": 9, "ref": "Fake B.", "why": "may not exist"}]),
               dict(paper, pmcid="PMC2", maybe_nonexistent=[{"n": 1, "ref": "Book.", "why": "may not exist"}]),
               dict(paper, pmcid="PMC3", failed_lookups=9, maybe_nonexistent=[]),
               {"pmcid": "PMC4", "error": "full text"}]
    md = rs.report(results, SimpleNamespace(seed=1, date_from="2026-06-01", date_to="2026-11-15"))
    assert "Papers checked fully: **2** of 4" in md
    assert "2 or more references that may not exist: **1**" in md and "## 2 of 40: A paper" in md


def test_crossref_reference_lists():
    """References as Crossref lists them: the ones whose DOI Crossref matched itself are skipped (real papers); the
    others become reference text, with the DOI the publisher gave."""
    assert rs.crossref_reference_text({"doi-asserted-by": "crossref", "DOI": "10.1/x", "article-title": "T"}) is None
    t = rs.crossref_reference_text({"doi-asserted-by": "publisher", "DOI": "10.1016/j.x.2020.1", "author": "Lee",
                                    "year": "2020", "article-title": "A study of things", "journal-title": "J Eng",
                                    "volume": "5", "first-page": "12"})
    assert t == "Lee et al. (2020). A study of things. J Eng, 5, 12. doi:10.1016/j.x.2020.1"
    assert rs.crossref_reference_text({"unstructured": "Smith J. Title. J. 2020.", "DOI": "10.2/y"}).endswith("doi:10.2/y")
    assert rs.crossref_reference_text({"author": "Org", "year": "2020"}) is None
