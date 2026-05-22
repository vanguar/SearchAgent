# PHASE 16 - freeze / acceptance / no-regression close

Read `AGENTS.md` and `PROJECT_BRIEF.md` first. Follow their product rules strictly.

Do NOT implement PHASE 17.
Do NOT add new product features.
Do NOT widen scope beyond the accepted local-first Germany v1 product.
Do NOT do cosmetic refactors.
Do NOT change accepted business logic unless a deterministic PHASE 16 acceptance failure proves a minimal fix is necessary.

## Phase Lineage Anchor

`AGENTS.md` and `PROJECT_BRIEF.md` contain the stable product rules and an older high-level phase map ending at optional notifications. The accepted current project state is later than that historical map.

For PHASE 16, treat the accepted baseline as:

- PHASE 1-15 implemented and accepted
- accepted PHASE 15.x micro-cleanups included
- current local product already includes the implemented search, profile, source-state, fallback, ATS/CRM, stats, manual email/linking, optional notification, and bounded LLM/degraded-config paths that exist in the codebase

PHASE 16 is not a new product phase. It is a release-freeze / acceptance / no-regression layer over the accepted current baseline. Do not use the older phase map to remove or reopen already accepted implemented behavior.

## Goal

Freeze v1 safely without breaking current working behavior.

This phase exists only to:

- validate the full local product flow end to end
- align and lock regression tests to already accepted behavior
- fix only proven acceptance failures
- close small acceptance/documentation gaps
- produce practical local-run and manual-QA documentation

This phase is not for broad hardening refactors, architecture cleanup by taste, new search logic, new source logic, new ATS/email/stats features, deployment, SaaS, auth, background workers, or browser automation.

## Core Freeze Rule

If something is already working and accepted, do not improve it in PHASE 16. Freeze it.

Acceptance failure discipline:

1. First align acceptance tests with the explicitly accepted current behavior and PHASE 16 rules.
2. Run the updated deterministic regression suite.
3. If an updated regression still fails, treat it as a real production issue until proven otherwise.
4. Production code changes are allowed only as minimal, surgical fixes for a proven acceptance failure.
5. Do not automatically label failing tests as stale. Do not silently bless a real bug.

## Strict Boundaries

Do NOT:

- add new features
- add new source families
- widen country scope beyond Germany
- redesign architecture
- rename modules for style
- rewrite search, fallback, filtering, scoring, role-family, dedup, merge, ATS, CRM, email, stats, or notification flows
- reopen accepted PHASE 15.x fixes unless a regression is proven
- add deployment, SaaS, auth, cloud, background workers, or browser automation
- change optional integration behavior just because credentials are missing locally

## Preserve Accepted Behavior

Preserve unless a deterministic PHASE 16 acceptance test proves otherwise:

- Germany-only scope
- deterministic filtering/scoring as the source of truth
- bounded LLM use only where already allowed
- missing LLM config degrades to deterministic local behavior
- explicit selected-profile search path
- profile binding across search, history, leads, email workspace, and stats where already implemented
- multi-source degraded behavior when one source fails
- visible source-state reporting in UI
- current profession-family anti-junk logic
- guarded fallback behavior for narrow/specific/unknown roles
- suppression of obvious hard rejects from visible hot/maybe output
- current search progress and completion-state flow
- dedup-preview visibility if already implemented
- ATS/CRM lead creation, event tracking, notes, reminders, documents, and lifecycle actions already implemented
- stats and funnel calculations already implemented
- manual email import, email-to-lead/thread/message linking, and degraded Gmail scaffold already implemented
- optional manual notification triggers already implemented

## Allowed Changes

Only these changes are allowed:

- acceptance-test alignment to already accepted behavior
- focused deterministic regression tests for accepted behavior
- tiny production bug fixes only after a corrected acceptance test still fails
- tiny route/template truthfulness fixes where UI contradicts actual pipeline state
- README/local-run/manual-QA documentation fixes
- `.env.example` cleanup only for correct local operation
- tiny test-selection updates to reflect the corrected acceptance lock

No broad refactors. No speculative hardening. No product expansion.

## Mandatory Local Deterministic Acceptance

These checks must pass locally without secrets and without live external-provider dependency.

Use deterministic fixtures, dependency overrides, stubs, or local DB state where practical.

Mandatory coverage:

- migrations and startup: app factory, health route, Alembic upgrade, basic page route smoke
- profile intake and selection: Russian intake, validation, save, selected profile autofill/context
- jobs/search UI: search form, selected profile, progress state, completion state, source states, visible hot/maybe buckets, no visible hard rejects
- search core: normalization, dedup, scoring, filtering, guarded fallback, unknown-role no-helper-poisoning, narrow-role anti-junk, source failure isolation
- dedup preview: current preview appears when present and preserves source count/original link behavior
- ATS/CRM: lead creation from search, source identity uniqueness, viewed/save/open/apply/reply/reject/interview/archive events, notes/reminders, resume/template linking where implemented
- stats: profile-scoped dashboard/funnel/source-quality counts remain consistent with search and lead events
- email/linking: manual import, deterministic matching helpers, thread/message link/unlink, validation notices, lead-detail email summary, Gmail not-configured degraded path
- bounded LLM path: missing `OPENAI_API_KEY` does not block deterministic local flow; configured/provider paths are covered with mocks unless explicitly doing optional live smoke

## Optional Integration Smoke Checks

These are not mandatory for deterministic acceptance and may be skipped when credentials, local services, or network are unavailable.

Optional smoke includes only:

- live BA/Careerjet/EURES/Remotive/Adzuna source checks when enabled and configured
- live OpenAI check when `OPENAI_API_KEY` is set
- live Gmail scaffold check when Gmail credentials are set
- live Telegram digest when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set
- live SMTP email digest when notification SMTP env vars are set
- PostgreSQL migration/startup smoke when a local `DATABASE_URL` for PostgreSQL is configured

Missing optional integration config must produce clear degraded/not-configured behavior and must not fail the mandatory deterministic acceptance suite.

## Database Wording

PostgreSQL remains the preferred target stack from `AGENTS.md` and `PROJECT_BRIEF.md`.

SQLite may be documented and used only as a local-dev convenience fallback for repeatable local startup and deterministic acceptance when PostgreSQL is not configured. Do not present SQLite as replacing PostgreSQL as the preferred target stack. Do not refactor the database layer in PHASE 16 just to change this wording.

## Expected Files To Change

Before editing, list exact files and why each must change.

Likely allowed files:

- focused tests under `tests/`
- `README.md`
- `.env.example`
- `MANUAL_QA.md`
- `PHASE16_FREEZE_PLAN.md`
- a tiny route/template/service file only if a corrected deterministic acceptance test proves a real production issue

If no production code change is strictly necessary, say so and produce only tests/docs/checklists.

## Output Discipline

Before writing code:

1. State the task as `PHASE 16 - freeze / acceptance / no-regression close`.
2. List exact files to create or change.
3. Explain why each file must change.
4. List every proposed production code change and why it is strictly necessary.
5. If no production code changes are necessary, say so.

After changes:

6. Output complete contents of every created or changed supporting doc file.
7. Give exact local commands for mandatory deterministic acceptance.
8. Give exact local commands to start the app.
9. Provide the final manual QA checklist.
10. State the expected local result.
11. Stop.

Do not continue to PHASE 17.
