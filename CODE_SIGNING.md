# Code signing policy

Windows releases of Citrello are signed so Windows can verify where they come from.

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by
[SignPath Foundation](https://signpath.org).

## What is signed
Only files built from this repository by GitHub Actions (`.github/workflows/build.yml`) on a version tag:
`Citrello.exe` and `CitrelloMCP.exe`. Nothing built on a personal computer is signed.

## Roles
- **Committers and reviewers:** [members of the citrello organization](https://github.com/orgs/citrello/people).
  Every change reaches `main` through this repository.
- **Approvers:** Armin Hosseinghorbani approves each signing request for a release.

## Privacy
Citrello sends no data anywhere unless you use a feature that needs it. The
[privacy policy](https://citrello.app/privacy) lists every case.
