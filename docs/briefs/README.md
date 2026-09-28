# Briefs for delegated cloud runs

Each file is the full instruction set for one cloud routine (a Claude Code session
in Anthropic's cloud with this repository cloned). The routine's own prompt is one
line pointing here, because the routine API truncates long prompts. A brief is
self-contained: the branch to work on, what to read, what to build or review, the
rules, and what to return.
- `review-pr16-m1.md`: adversarial pre-merge review of pull request 16 (M1, the source side).
- `rebuild-pr15-m3-no-private-data.md`: rebuild the M3 branch from master without the companion-derived test data.
- `rebuild-pr16-m1-fixes.md`: rebuild the M1 branch without the private documents, derived truth tables, and the review fixes.
