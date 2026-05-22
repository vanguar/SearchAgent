# PROJECT_BRIEF.md

## Product name

Local-first Personal Job Agent + ATS/CRM for Germany

## One-line description

A local web app that helps one real user in Germany find relevant jobs, understand them quickly in Russian, and track the full application process in a personal ATS/CRM workflow.

## Core mission

Build a genuinely useful daily tool for job search in Germany.

This is not just a vacancy scraper.
This is not just a chatbot.
This is not SaaS yet.

This product combines:
- vacancy search
- normalization
- deterministic relevance ranking
- Russian summaries
- ATS / CRM tracking
- reminders and notes
- analytics and funnel tracking

## Primary target user

A Russian-speaking / Ukrainian-speaking user in Germany who:
- may be under Section 24 temporary protection
- may have weak or no German
- may have weak English
- needs practical low-barrier jobs
- wants to search across Germany
- wants one place for search + tracking + statistics

## Primary job-search focus

The first target categories are:
- warehouse
- logistics
- packaging
- production
- helper roles
- shift-based low-barrier jobs
- entry-level jobs
- quick-start jobs

The product should strongly prioritize realistic jobs over “nice-looking but unreachable” jobs.

## Product principles

### 1. Deterministic core

Search, normalization, deduplication, filtering, scoring, tracking, and analytics must be deterministic and rule-based.

### 2. LLM as convenience layer only

LLM may only be used for:
- parsing free-form user input into structured profile fields
- translating short vacancy summaries into Russian
- generating short Russian summaries
- explaining why a vacancy matches or does not match

LLM must not orchestrate the entire system.

### 3. Local-first development

Build and test locally first.
No deployment, SaaS, cloud ops, or CI/CD in the first development stages.

### 4. Human UI

The interface must feel like a practical product, not a developer admin panel.

The user should understand a vacancy in about 5 seconds:
- what the job is
- where it is
- whether it is worth attention
- what the main risks are
- what to do next

## Russian UI requirement

The product UI must be Russian by default.

Main menu sections:
- Профиль
- Поиск вакансий
- Подходящие
- Отклики
- Переписка
- Собеседования
- Статистика
- Заметки / задачи
- Настройки

Vacancy cards should show:
- original title
- short Russian summary
- company
- location
- salary if available
- why it matches
- risks / caveats
- original link
- quick actions

## User profile intake

The user must be able to describe their situation in simple natural language, for example:

“Я в Германии, по 24 параграфу, немецкого почти не знаю, английский слабый, ищу склад, упаковку, производство, можно по всей Германии, готов к переезду, смены ок.”

The system must:
- parse this text
- build a structured search profile
- validate it
- ask only for missing critical fields
- confirm the profile in Russian before search

Important profile fields include:
- current country and city
- legal status
- work authorization
- German level
- English level
- desired roles
- excluded roles
- preferred geography
- relocation readiness
- shift acceptance
- physical work acceptance
- housing need
- start availability
- driving license / car status if relevant

## Important domain rule: Section 24

Section 24 is not a search API parameter.
It is part of profile interpretation.

Interpret it as:
- work authorization exists
- no visa sponsorship assumption is needed by default
- uncertain “sponsorship” cases may need review rather than hard rejection

## Source strategy for v1

Source adapter priority:
1. BA adapter first
2. Careerjet adapter second
3. EURES adapter as stub or partial adapter
4. Remotive adapter as optional disabled-by-default lane

Each source must:
- use a common adapter interface
- fail safely
- keep raw payloads for traceability
- not break the whole search pipeline if one source fails

## Core data model concept

The system must keep raw source data and canonical normalized data separate.

Main entities:
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

Important timestamps where relevant:
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

## ATS / CRM requirement

This product must be useful not only for finding vacancies, but also for managing the real application process.

For each lead, the user should be able to track:
- when the vacancy was found
- source
- original link
- whether it was viewed
- when it was opened
- whether it was saved
- whether an application was sent
- when the application was sent
- application channel
- whether a reply was received
- when the reply was received
- whether it was rejected
- whether an interview was scheduled
- notes
- next action
- reminder / follow-up date
- which resume version was used
- which cover letter template was used

Manual editing must be supported well.

## Resume and templates

The product must support:
- storing multiple resumes
- storing multiple resume versions
- storing reusable cover letter templates
- linking the used resume version and cover letter template to each application

## Email-ready design

Email is part of the CRM model from the beginning.

V1 should be manual-first:
- manual email entry or import
- manual linking of an email thread/message to a lead
- parsing helpers are acceptable
- full Gmail sync can come later

## Cache and dedup requirements

Caching and deduplication are mandatory.

If the same source + same external_id is seen again and nothing materially changed:
- do not recreate the vacancy
- update last_seen_at only

Translation and AI summaries must run only after deduplication.

Cross-source dedup should consider:
- normalized title
- company
- location
- text similarity
- posting date proximity

## Scoring philosophy

Scoring must be deterministic and practical.

Positive signals:
- warehouse / logistics / packaging / production / helper
- low language barrier
- shift work accepted
- immediate start
- relocation-friendly
- low experience requirement
- no mandatory degree or vocational qualification

Negative signals:
- strong or fluent German required
- mandatory degree
- mandatory vocational qualification
- clearly mismatched profession
- strong prior experience required where user lacks it

Output buckets:
- hot
- maybe
- rejected

## Statistics and funnel

The product must provide useful analytics such as:
- vacancies found by day/week
- strong matches by source
- viewed/opened counts
- applications sent
- replies
- rejections
- interviews
- response rate
- funnel metrics
- source quality indicators

## Technical direction

Preferred stack:
- Python
- FastAPI
- PostgreSQL
- SQLAlchemy
- Alembic
- Jinja2
- HTMX
- minimal vanilla JS only when needed

Avoid:
- React / Vue / Next.js
- Redis
- Celery
- Kafka
- microservices
- premature abstractions

## Phase order

Work strictly in this order:
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

Do not skip phases.
Do not jump ahead.

## Definition of success for v1

A successful v1 is a local app that:
- runs reliably on a local machine
- lets the user describe their profile naturally
- fetches real vacancies from at least one working source
- normalizes and deduplicates them
- ranks them meaningfully
- shows a clear Russian UI
- allows real ATS/CRM tracking of job applications
- gives useful statistics

That is enough.
Do not expand scope beyond this unless explicitly requested.