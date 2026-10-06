# RSS vacancy cards and seniority — 2026-10-06

Phase 16 maintenance: correct source normalization and candidate eligibility within the existing search flow.

## Corrected behavior

- Djinni's employer introduction `Медіабай шукає ...` now supplies the company field. The original RSS payload remains intact; summaries are never used as extraction evidence. Generic introductions and incidental company mentions remain unknown.
- DOU and Djinni publish vacancy descriptions in RSS. A usable body is now marked complete instead of unconditionally incomplete. Empty, short and visibly clipped bodies retain the insufficient-description signal. Excerpt sources such as Careerjet and Adzuna keep their existing policy.
- For the existing personal-project AI candidate policy, an explicit senior title is a seniority requirement even without a numeric commercial-experience clause. Technology matches and positive feedback cannot override it. Explicit senior search targets preserve the previous behavior; mandatory experience gaps remain effective.
- HTML text cleaning was moved unchanged into a shared module so both source adapters and normalization can use it without circular imports.

## Reported vacancies

Source pages checked against the stored RSS description:

- [Python/Go Backend Developer (AI-driven)](https://djinni.co/jobs/850823-python-go-backend-developer-ai-driven/)
- [Senior AI Business Systems Analyst](https://jobs.dou.ua/companies/justanswer/vacancies/371266/)

A read-only replay of the original stored records with saved profiles 5 and 8 reproduced both reported failures before the change. After the change, both profiles show Медіабай, classify the backend role as `maybe` with score 55 and only the commercial-experience review, and hide the Senior analyst with `it_seniority_gap`. Neither body has the insufficient-description signal. The backend review is justified by its explicit requirement for 2+ years of commercial Python experience.

## Verification

- Full deterministic suite: **2011 passed**.
- `ruff check app tests`: passed.
- `git diff --check`: passed.
- Fresh isolated SQLite database: all existing Alembic migrations reached head.
- `mypy app`: the same **60 pre-existing errors in 16 unchanged files** before and after; no new diagnostics. The repository's existing type-check job is non-blocking.
- Tests include minimized public source fixtures, adapter mapping, intact raw payloads, full versus clipped descriptions, company values in rendered cards and ATS form fields, hidden senior results, senior profile preservation, junior mentorship, and the existing exact non-IT baseline comparison.

Run from the project directory:

```powershell
Set-Location -LiteralPath 'C:\Users\tzvan\Desktop\SmartJob_ SearchAgent'
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q
.\.venv\Scripts\python.exe -B -m ruff check app tests
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q tests/services/test_rss_card_regressions.py tests/services/test_it_candidate_eligibility.py
```

Expected results: full tests and lint pass; a new search displays the corrected backend company and description status and hides the senior analyst for the two candidate profiles. Previously completed search runs are historical snapshots and must be rerun to obtain the corrected ranking.
