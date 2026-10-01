# Three live-search follow-up regressions

Starting HEAD: `0e0287ac4d52a03a01745ede56a5356ecc56dc22`; clean working tree.
Scope: phases 6–7 only; no UI, database, profile, configuration or API changes.

## Exact BA reference identity

BA external IDs `12016-10005358982-S` and `12016-10005373885-S` now participate
in matching explicit employer references from other sources. Matching requires
the exact reference, compatible employer names, title overlap and no conflicting
city. Arbitrary aggregator external IDs are not employer references. Existing
fuzzy thresholds are unchanged. Reference evidence survives incremental merges;
all source records and URLs remain attached to the canonical vacancy.

Tests cover both reported titles in every three-source arrival order, conflicting
references, employers, cities and roles, non-BA IDs and unlabelled references.

## Actual stop reason

Once all explicit queries have run and canonical sufficiency is reached, export
reports `sufficient_canonical_results`, even if the final successful query also
uses the last budget slot or Jooble is blocked. Cancellation remains first;
budget exhaustion still applies when the explicit plan could not finish.
Tests cover three successful queries with both available and exhausted budgets.

## Salary conflict and provenance

Existing scoring policy chooses the lowest textual amount, preferring amounts
with explicit periods over inferred periods. There was no title-over-body
priority; adapter salary metadata did not override text. That policy is retained.
The old summary independently used body text and could therefore assert 25.54.

For `Auslieferungsfahrer / Überführer m/w/d 18,23 € / Stunde`, scoring still uses
18.23 EUR/hour. Summary now explicitly reports title 18.23 versus body 25.54,
identifies 18.23 as the calculation rate and states that pay needs confirmation.
Neither an LLM response nor its cache can silently pick the higher body amount.
The original title/body/payload remain unchanged. Export includes the conflict
flag and evidence with source-record key, URL, field, amount, period and net flag.
Metadata disagreements are retained as evidence without changing scoring.

Jooble: German default `https://de.jooble.org/api`, no local URL override.
403 remains a credential/configuration issue. No credential requests or bypasses.

Final validation: targeted **198 passed**, full pytest **1690 passed**, ruff clean;
mypy **60 pre-existing errors in 16 files**, **0 new diagnostics**.
The resumed checkout includes separate UI commits `2a6fada` and `931bb04` and
their eight additional tests. They are preserved and outside this commit's diff.

Working database SHA-256 at the start of this follow-up:
`3E538766C155BCB4062B6A5621BE7FC15CCA3439B080AEC4C8CBDF3F5C78D885`.
At final validation the database had changed between sessions; its last write
and stored search were at 2026-10-01 00:11:21 UTC, before the resumed validation.
Repeated final hashes are stable:
`A5879256A70A55CFEEED2C1CCB15075ACD2A55259018DB576B3A33493856DE36`.
Neither matches the earlier September audit hash `16D9F4C6...03E0F`.
This task made no database writes; tests used `DATABASE_URL=sqlite://`.
No push.
