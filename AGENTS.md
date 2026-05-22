# AGENTS.md

## Mission

Build a local-first personal job-search agent + ATS/CRM for Germany.

This product is for one real user first.
It is not SaaS yet.
It must be genuinely useful in daily job search.

Core product shape:
- job aggregation
- normalization
- deterministic ranking
- Russian-language UI
- ATS / CRM tracking
- notes, reminders, analytics
- email-ready data model

## Non-negotiable rules

- Work strictly phase by phase.
- Keep the app runnable after each phase.
- Do not jump ahead.
- Do not silently add unrelated features.
- Do not overengineer.
- Prefer simple, readable implementations over abstract cleverness.
- No spaghetti.
- No giant god classes or god modules.
- Keep service boundaries clear.

## Current scope boundaries

This repository is local-first only.

For now, do NOT add:
- Railway deployment files
- GitHub Actions
- Docker for production
- SaaS logic
- multi-user auth
- billing
- auto-apply
- browser automation for job applications
- React / Vue / Next.js
- Redis / Celery / Kafka / microservices

If deployment or SaaS concerns appear relevant, stop and do not implement them unless explicitly requested.

## Product architecture

Core logic must be deterministic and rule-based:
- source querying
- normalization
- deduplication
- filtering
- scoring
- tracking
- analytics

LLM may only be used for:
- parsing free-form user profile input into structured fields
- translating short vacancy summaries into Russian
- generating short Russian summaries of vacancies
- explaining in Russian why a vacancy matches or does not match

Do NOT use LLM as the orchestration brain of the system.

## Primary user scenario

The user may describe their situation in simple Russian, for example:
“Я в Германии, по 24 параграфу, немецкого почти не знаю, английский слабый, ищу склад, упаковку, производство, можно по всей Германии, готов к переезду, смены ок.”

The system must:
- parse the profile
- validate it
- ask only for missing critical fields
- confirm the profile in Russian
- search enabled sources
- normalize and deduplicate results
- score them
- show a clear Russian UI
- allow ATS/CRM tracking of all follow-up actions

## Domain rules

Treat Section 24 / temporary protection as:
- work authorization exists
- not a search API parameter
- part of profile interpretation and scoring logic

Jobs with sponsorship ambiguity should often be marked for review, not immediately hard-rejected.

Prioritize low-barrier roles such as:
- warehouse
- logistics
- packaging
- production
- helper roles
- shift work
- immediate start
- low-experience roles

Strong German requirement must be analyzed from vacancy text, not only title.

## Source priority

Adapters must be implemented in this order:
1. BA adapter first
2. Careerjet adapter second
3. EURES adapter as stub or partial adapter
4. Remotive adapter as optional disabled-by-default lane

Each adapter must:
- implement a common interface
- be isolated
- fail safely
- never break the whole search flow
- preserve raw source payloads for traceability

## Data model principles

Keep source raw data and canonical normalized data separate.

Expected core entities:
- UserProfile
- SearchProfile
- VacancySourceRecord
- VacancyCanonical
- VacancyScore
- SearchRun
- ApplicationLead
- LeadEvent
- ManualNote
- ReminderTask
- InterviewEvent
- Resume
- ResumeVersion
- CoverLetterTemplate
- EmailThread
- EmailMessage
- UserActionAudit

Important timestamp fields where relevant:
- first_seen_at
- last_seen_at
- viewed_at
- saved_at
- applied_at
- reply_at
- rejected_at
- interview_at
- archived_at
- next_action_at

## Cache and dedup rules

Caching and dedup are mandatory.

If the same source + same external_id is seen again and nothing materially changed:
- do not recreate the vacancy
- update last_seen_at only

Run translation and AI summarization only AFTER deduplication.

Cross-source dedup should consider:
- normalized title
- company
- location
- text similarity
- posting date proximity

## Russian UI rules

UI language must be Russian by default.

Menu sections should remain human and intuitive:
- Профиль
- Поиск вакансий
- Подходящие
- Отклики
- Переписка
- Собеседования
- Статистика
- Заметки / задачи
- Настройки

The UI must not feel like a developer admin panel.

A user should understand a vacancy in about 5 seconds:
- what the job is
- where it is
- whether it fits
- what the risks are
- what action to take next

## Vacancy card expectations

Vacancy cards should eventually show:
- original title
- Russian title or summary where useful
- company
- location
- salary if present
- short Russian summary
- why it matches
- risks / caveats
- link to original vacancy
- quick ATS actions

## ATS / CRM rules

This is not only a search tool.
It is also a personal ATS / CRM.

For each lead, support tracking of:
- found date
- source
- original link
- viewed status and date
- saved status and date
- applied status and date
- application channel
- reply received and date
- rejection and date
- interview and date
- notes
- next action
- reminder / follow-up date
- archive state
- which resume version was used
- which cover letter template was used

Manual editing is required and must be supported cleanly.

## Email integration rules

Email integration should be designed into the data model from the start.

V1 should be manual-first or semi-manual:
- manual email entry
- manual linking of email thread/message to a lead
- parsing helpers are acceptable
- full sync can come later

Do not force heavy email automation before CRM core is stable.

## Tech stack rules

Preferred stack:
- Python
- FastAPI
- PostgreSQL
- SQLAlchemy
- Alembic
- Jinja2
- HTMX
- minimal vanilla JS only when needed

Keep everything local-first.

Prefer commands and workflows that work on a local machine and are easy to run repeatedly.

## Coding style

- Typed Python
- Clear module boundaries
- Small focused services
- Minimal useful comments
- Explicit names
- Avoid hidden coupling
- Avoid premature abstractions
- Avoid giant utility dumping grounds
- Keep files readable

When patching code:
- output complete files or complete functions
- do not provide fragile partial snippets for critical logic
- keep imports clean
- keep logs structured and useful

## Testing expectations

Core logic must be covered with deterministic tests.

Prioritize tests for:
- health and startup
- migrations
- normalization
- deduplication
- filter engine
- scoring
- lead event tracking
- stats calculations
- email-to-lead matching

Use small deterministic fixtures.
Avoid brittle tests.

## Phase discipline

Always execute work in the current phase only.

Expected phase order:
1. project skeleton
2. database models and migrations
3. Russian UI skeleton
4. intake agent
5. source adapters
6. normalization, cache policy, deduplication
7. filtering, scoring, search orchestration
8. ATS / CRM layer
9. statistics and dashboard
10. email-ready integration layer
11. optional notifications

Do not skip forward.

## Output discipline for coding sessions

For each task:
- state the current phase
- say what files will be created or changed
- keep the app runnable
- summarize what was completed
- provide exact local commands to run
- provide expected local result
- stop after the requested phase

Do not continue to the next phase unless explicitly told.

## Safety / change discipline

Before adding any external dependency, ask whether it is necessary if the value is unclear.

Before adding any secret-dependent integration, prefer scaffolding and local-safe stubs unless explicitly requested.

If a request conflicts with this file, follow the most specific explicit user instruction for the current task.