# BibTeX styles used for "Make arXiv-ready"

arXiv doesn't run BibTeX, so an arXiv upload needs the `.bbl` file BibTeX would make. Citrello makes it with
pybtex (a Python copy of BibTeX) and the project's `\bibliographystyle`. Most journal templates include their own
`.bst`; these common ones are here for projects that use a style from TeX Live.

The files are unmodified copies from TeX Live 2023. Each keeps its own licence, stated at the top of the file
(mostly the LaTeX Project Public License; `plain`, `unsrt`, `alpha`, `abbrv` and relatives allow unlimited copying
of unmodified files; `ACM-Reference-Format` is public domain). Do not edit them: any change requires renaming the
file under those licences.
