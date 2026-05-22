# SmartJob SearchAgent

Local-first personal job-search agent + ATS/CRM for Germany.

PHASE 16 is the v1 freeze / acceptance / no-regression close. The accepted baseline includes the implemented local search flow, profile intake and selection, deterministic filtering/scoring, multi-source degraded behavior, ATS/CRM tracking, stats, manual email/linking, optional notification triggers, and bounded LLM/degraded-config paths that already exist in this repository.

Do not use PHASE 16 to add features, widen country scope, rewrite business logic, or start PHASE 17.

## Stack

- Python 3.11+
- FastAPI
- SQLAlchemy
- Alembic
- Jinja2 / HTMX
- PostgreSQL as the preferred target stack

SQLite is supported here only as a local-dev convenience fallback for repeatable startup and deterministic acceptance when PostgreSQL is not configured. It does not replace PostgreSQL as the preferred project target.

## Local Environment

Create a virtual environment and install the project:

```powershell
Set-Location -LiteralPath 'C:\Users\tzvan\Desktop\SmartJob_ SearchAgent'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .[dev]
```

Create `.env` from `.env.example` and fill only the optional integrations you want to smoke-test.

For deterministic local acceptance without secrets:

```powershell
$env:DATABASE_URL = 'sqlite:///smartjob.local.sqlite3'
```

For PostgreSQL local smoke, set `DATABASE_URL` to a local PostgreSQL URL and run the same migration/start commands.

## Mandatory Deterministic Acceptance

These commands must pass without external provider secrets and without live network-dependent source checks:

```powershell
Set-Location -LiteralPath 'C:\Users\tzvan\Desktop\SmartJob_ SearchAgent'
$env:DATABASE_URL = 'sqlite:///smartjob.local.sqlite3'

.\.venv\Scripts\python.exe -B -m alembic upgrade head

.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider `
  tests\services\test_role_family.py `
  tests\services\test_search_orchestrator.py `
  tests\services\test_search_service.py `
  tests\test_jobs_routes.py `
  tests\services\test_lead_service.py `
  tests\test_leads_routes.py `
  tests\services\test_stats_service.py `
  tests\test_stats_routes.py `
  tests\services\test_email_workspace_service.py `
  tests\services\test_manual_email_ingest.py `
  tests\services\test_thread_matcher.py `
  tests\test_email_routes.py

.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider
```

## Start The App

```powershell
Set-Location -LiteralPath 'C:\Users\tzvan\Desktop\SmartJob_ SearchAgent'
$env:DATABASE_URL = 'sqlite:///smartjob.local.sqlite3'
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/dashboard`.

## Optional Integration Smoke

These checks are optional and may be skipped when credentials, local services, or network are unavailable:

- live source checks for BA/Careerjet/EURES/Remotive/Adzuna when enabled and configured
- OpenAI calls when `OPENAI_API_KEY` is set
- Gmail scaffold check when Gmail env vars are set
- Telegram digest when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set
- SMTP email digest when notification SMTP env vars are set
- PostgreSQL migration/startup smoke when a local PostgreSQL `DATABASE_URL` is configured

Missing optional config should produce clear not-configured/degraded behavior and must not fail mandatory deterministic acceptance.

## Manual QA

Use `MANUAL_QA.md` for the final local operator checklist.
