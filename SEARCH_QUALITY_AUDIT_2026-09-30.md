# Live search quality audit — 2026-09-30

Scope: filtering/scoring/search quality on the real «Перегон автомобилей» export.
No UI, migration, profile, user database, API key or production configuration changes.
No live API requests or push.

## Starting checkpoint

- Branch: `fix/search-quality-and-scoring`.
- HEAD: `d968043894467d16c4ab20a3a929dc25a8ac1783`; working tree clean.
- Baseline: **1574 passed**, ruff clean, mypy **60 existing errors in 16 files**.
- Database SHA-256 before:
  `16D9F4C62FEA6E32FE101294CB02EB543F35144F7ABC460B893F2AAF15D03E0F`.
- Read-only inventory: warehouse (id 2), delivery/courier (4), Python/AI developer
  (5), Driver B Fernverkehr (7), AI automation (8), vehicle transfer (9).
- Profile 9 really stores `employment_types=["full_time"]`, B, A1,
  `self_employment_ok=false`; these values were not inferred from the prompt.

## Findings and root causes

| Case | Status | Finding and change |
|---|---|---|
| A — Fahrzeugaufbereiter | confirmed defect | The taxonomy, role-intent dictionary and positive-role regex explicitly treated vehicle care as transfer. Care/detailing now belongs to the existing cleaning family; explicit detailing profiles still work. It is removed from the general transfer fallback pool. Bare `Fahrzeug` remains insufficient. |
| B — Elektrogeräte | confirmed defect | `Fahrzeugführer` was not recognized as driving. Body-only `Fahrzeugüberführer` then created transfer matches. The title now identifies driving; a specific contrary title prevents incidental body text from earning transfer bonuses. Generic `fahrer` aliases are also filtered against the profile's requested families. Mixed transfer/delivery titles remain eligible. |
| C — generic driver words | confirmed defect; source/data limitation for complete hidden records | `Berufskraftfahrer` was a strong heavy-vehicle signal; `Kraftfahrer` became a hard rejection when no light vehicle was named. Both now remain contextual evidence only. Explicit C/CE, LKW, heavy tonnage, specialized trucks and mandatory driver qualifications remain effective. Negations are preserved. `Überführer / Fahrer` now classifies as transfer. |
| D — Werkstudent | confirmed defect | The extractor had no working-student pattern. `working_student` is extracted without creating a new profile option. Existing policy applies review and a −10 employment mismatch penalty against full time, not an employment hard rejection. |
| E — PerZukunft duplicate | confirmed defect | Company normalization retained `Arbeitsvermittlung` on one source; the other used `perZukunft`. A conservative alias match now requires almost identical title, same location, nearby dates, plus a substantial exact description prefix or matching employer reference. Conflicting references prevent merging. A key collision can no longer overwrite a separate position. Cross-query/city results retain source evidence and are deduplicated and rescored together. |
| F — incomplete HOT | product-policy issue; requested conservative improvement applied | A score above 70 previously sufficed without checking description completeness. A B-profile transfer job with incomplete description and unknown licence, relevant German requirement or employment contract (when self-employment is disallowed) is capped at 69. Known requirements from title/resolved signals, including structured permanent-contract metadata, allow HOT; missing description alone never causes hard rejection. Scope does not change the confidence policy of unrelated IT or Driver B long-distance roles. |
| G — stale postings | product-policy issue; requested improvement applied | All ages above 60 days had the same −12 penalty. It is now −12 for 61–90 days, −18 for 91–180, −26 for 181–365, −36 after 365. Postings older than 180 days cannot be HOT. Missing/future dates remain distinct from old dates. No age hard filter or new bucket was introduced. |
| H — fallback | intentional old behavior; new policy explicitly approved on continuation | Previously `exhaustive_search=True` deliberately traversed the full plan, so this was not a broken active stop-condition. All saved primary and additional terms now run first, subject to cancellation and the existing adapter budgets. Automatic terms and the last-resort LLM stage run only while fewer than three unique, visible, relevant non-rejected canonical jobs have accumulated. MAYBE counts; duplicates, raw counts, hard rejects and contrary families do not. `reason_continued` identifies explicit-plan or insufficient-results continuation, and `stop_reason` distinguishes sufficiency, exhaustion, cancellation and unavailable source budget. Limits 48 overall / 24 per source and 403/429 blocking are unchanged. |
| I — Jooble zero | configuration issue; incorrect German default fixed | No explicit URL override was present in the local `.env`; the default was the US endpoint. Official Jooble documentation requires the German domain and its own key. The default and example now use `https://de.jooble.org/api`; explicit overrides remain respected. Diagnostic wording no longer claims that the US host always returns zero. The key's country was not tested, so successful German access is not claimed. |

Jooble contract:
[regional endpoint and key restrictions](https://help.jooble.org/en/support/solutions/articles/60000922689-how-to-connect-to-the-jooble-rest-api-after-receiving-the-key),
[German connection example](https://help.jooble.org/de/support/solutions/articles/60000922689-so-verbinden-sie-sich-mit-der-jooble-rest-api).

## Offline replay and its limits

The export contains 12 visible results and 16 hidden summaries. Read-only lookup
by source ID/external ID and URL recovered the original source payloads for all
12 visible results. The 16 hidden payloads are not stored in this database.
The 85 raw fetch occurrences therefore cannot be reconstructed exactly.

The pre-change offline replay reproduced **all 12 exported scores and buckets
exactly**, using the original source parsers, normalizer, filter, scorer, saved
profile fields and posting-age date 2026-09-30. The same recovered corpus was then
replayed on the updated code without network access.

| Real record | Before | After |
|---|---|---|
| Kraftfahrzeugführer – Fahrzeugüberführung, 25,54 Euro | HOT, 80; incomplete | MAYBE, 69; still prominent, requirements unconfirmed |
| Fahrzeugüberführer … hochwertigen Pkw`s | Two canonical records, Careerjet 89 / Adzuna 77, both MAYBE | One canonical record, two sources, fuller Careerjet text, 89 / MAYBE; immediate-start review retained |
| Fahrzeugführer – Elektrogeräte, Careerjet | Generic title + transfer body matches, 53 / MAYBE | Driving/delivery, 27, hidden by profession-family mismatch for the transfer profile; remains eligible for a delivery profile |
| Fahrzeugaufbereiter – Spandau | vehicle_logistics, 60 / MAYBE | cleaning, 10; hidden for the transfer profile |
| Fahrzeugaufbereiter – Wittenberge | vehicle_logistics, 54 / MAYBE | cleaning, 0; hidden for the transfer profile |
| Werkstudent Überführungsfahrer, Adzuna | employment unknown, 57 / MAYBE | working_student, 47 / MAYBE, employment review |
| Fahrer/in für Fahrzeugüberführungen, old posting | 57 / MAYBE | 43 / score-rejected, no age hard rejection |
| Überführungsfahrer – FS B | 61 / MAYBE | 61 / MAYBE |
| Fahrzeuglogistik / Fahrzeugüberführung, Adzuna | 57 / MAYBE | 51 / MAYBE due to age |
| Fahrzeuglogistik / Fahrzeugüberführung, BA | 51 / MAYBE | 51 / MAYBE |
| Hausmeister | 19 / rejected | 19 / rejected |
| Exported generic `Berufskraftfahrer` / `Kraftfahrer` reason on transfer titles | Generic wording could cause heavy-vehicle hard rejection | No heavy rejection from that wording alone; exact exported titles tested, missing bodies remain unverified |
| Fallback with sufficient recovered corpus | Export shows 13 query stages, including the automatic tail | Offline orchestration executes all 8 saved terms, then stops with `sufficient_canonical_results`; 6 eligible canonical jobs |

Recovered corpus: **12 → 11 canonical**, including one confirmed pair **2 → 1**.
Updated recovered corpus: **0 HOT / 6 MAYBE / 2 score-rejected / 3 hidden**.
This is not a claim of a complete 28-result replay or a new live API result.
The orchestration replay supplies the recovered corpus as an offline source response;
it verifies the query plan and stop policy, not when live APIs would discover each job.

The two hidden C examples are covered using their exact exported titles and the
generic signals identified in the export. Those isolated reasons no longer reject;
other requirements in their unavailable bodies remain unverified. The hidden
Careerjet Werkstudent self-employment decision is likewise not declared incorrect
without its body.

## Changes and verification

Production files:

- `role_family.py`, `_role_intent_lexicon.py`, `rule_catalog.py`,
  `vehicle_class_signal_extractor.py`, `filter_engine.py`: role evidence and heavy-vehicle proof.
- `employment_signal_extractor.py`: working-student extraction.
- `deduper.py`, `normalization_models.py`, `source_merge.py`, `search_models.py`,
  `search_service.py`: conservative identity evidence, source preservation,
  canonical rescoring and sufficient-results stopping.
- `search_export_service.py`: truthful continuation and stop metadata.
- `scorer.py`, `vacancy_quality_signals.py`: confidence cap and progressive aging.
- `search_fallback.py`: remove detailing from the transfer family pool; require three
  relevant canonical results rather than a HOT result for automatic stopping.
- `app/core/config.py`, `.env.example`, `source_adapters/jooble_adapter.py`: German Jooble default and accurate diagnostics.

New suite: `tests/services/test_live_vehicle_logistics_regressions.py` with **65**
cases, eight additional orchestration regressions, plus minimal real text fixtures
in `tests/fixtures/live_vehicle_logistics.json`.
Contacts and tracking URLs are omitted/replaced in the committed fixture; actual
job wording, employers, cities, dates, source/external IDs and employer reference
needed for the regressions are retained. The user's full JSON is not committed.

Counterexamples cover real heavy requirements and negations, delivery versus
transfer, mixed jobs, explicit detailing profiles, unknown employment preference,
distinct employer references/cities/descriptions, source-order-independent scoring,
three-source URLs, cross-query merging, conflicting snippets, known requirements
in incomplete descriptions, and age boundaries. Orchestration tests cover explicit
additional terms, accumulated canonical sufficiency, duplicates/rejects, contrary
families, export reasons, source/overall budgets and blocked sources. Existing
assertions encoding confirmed errors or superseded policies were updated explicitly.

The existing warehouse, courier, Python/AI, Driver B Fernverkehr, AI automation,
vehicle-transfer and profession-neutrality matrices remain in the full suite.
No stored profiles were changed to make the tests pass.

Final validation in the actual working repository (2026-10-01):

- Targeted orchestration/export/live-corpus/search-QA suite: **204 passed**.
- Full pytest: **1647 passed** (baseline 1574; 73 added cases).
- Ruff: **all checks passed**.
- Mypy: **60 existing errors in 16 files, 0 new diagnostics**; diagnostic multisets
  match the baseline after ignoring line-number movement.
- `git diff --check`: clean. Reviewed scope: 26 project files, including the report
  and minimal fixtures; no working database, `.env`, external helpers or local paths.
- Working database SHA-256 after validation equals the starting hash:
  `16D9F4C62FEA6E32FE101294CB02EB543F35144F7ABC460B893F2AAF15D03E0F`.

Commands, from the repository directory (mypy cache was redirected to scratch storage):

```powershell
$env:DATABASE_URL = 'sqlite://'
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check --no-cache app tests alembic
.\.venv\Scripts\python.exe -m mypy app
Remove-Item Env:DATABASE_URL
Get-FileHash .\smartjob.local.sqlite3 -Algorithm SHA256
git status --short
```

Consciously unresolved: full hidden-record replay without the missing source
payloads and the user's Jooble key/domain compatibility. No unsupported conclusion
is drawn about these points.
