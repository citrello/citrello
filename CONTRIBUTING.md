# Contributing to Citrello

Thanks for helping make references the sweet part of writing!

## Reporting problems
Open an issue with:
- what you did and what you expected;
- your Windows and Word version;
- for Word problems, the text from the Citrello tab's **Diagnostics** button. Remove anything private
  before posting.

## Code
- Python 3.10+. Install the dependencies with `pip install python-docx citeproc-py pytest`.
- Run `pytest` before opening a pull request. Tests never use the network.
- Match the style of the surrounding code. Keep the Word macro (in `word_addin.py`) plain ASCII.
- Everything runs offline-first. Network access is limited to Crossref, doi.org, the CSL style
  repository and, only when the user connects them, Mendeley and Zotero. Keep it that way.

## Contributor licence agreement
Citrello is licensed under the GNU AGPL v3 or later. So that the project can keep evolving (for
example, offering it under other licences in the future), contributions are accepted on these terms.
**By submitting a pull request you agree that:**

1. You wrote the contribution, or otherwise have the right to submit it under these terms.
2. You keep the copyright in your contribution.
3. You grant the Citrello maintainer a perpetual, worldwide, non-exclusive, royalty-free, irrevocable
   licence to use, copy, modify, distribute and sublicense your contribution, and to license it
   under the AGPL or any other licence, including commercial licences.
4. Your contribution is provided "as is", without warranties.

If you can't agree to this, for example because your employer owns your work, please say so in the
pull request before it's merged.
