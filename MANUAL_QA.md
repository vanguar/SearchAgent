# SmartJob SearchAgent MANUAL QA

PHASE 16 manual QA is a freeze checklist for the accepted local v1 product. Do not use it to add new features or reopen accepted business logic.

## Mandatory Local Deterministic QA

Run the app locally after migrations:

```powershell
Set-Location -LiteralPath 'C:\Users\tzvan\Desktop\SmartJob_ SearchAgent'
$env:DATABASE_URL = 'sqlite:///smartjob.local.sqlite3'
.\.venv\Scripts\python.exe -B -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

SQLite is used here only as a local-dev convenience fallback. PostgreSQL remains the preferred target stack for the project.

## Profile Intake And Selection

Open `http://127.0.0.1:8000/profile/intake`.

Use this profile text:

```text
Я в Германии, по 24 параграфу, немецкого почти не знаю, английский слабый, ищу склад, упаковку, производство, можно по всей Германии, готов к переезду, смены ок.
```

Expected:

- Russian confirmation appears.
- Section 24 is interpreted as work authorization, not as a search API parameter.
- Missing critical fields are requested only if needed.
- Saving creates a profile/search profile.
- `/profile` shows the saved profile.
- `/jobs` can select the profile and prefill search fields.

## Jobs Search And Source State

Open `/jobs`.

Run:

```text
query: lager
location: Berlin
radius: 25
source: Все включенные источники
```

Expected:

- Progress panel appears during async search.
- Completion replaces progress with results or a clear no-results/degraded message.
- Source state block shows success/warning/error per source.
- Hot/maybe cards are visible when matching jobs remain.
- Hard rejects are not visible as hot/maybe cards.
- Dedup preview appears when present and is labeled as pre-filter visibility.
- Vacancy cards include source, score, explanation, summary/title where available, original-link action when available, and save/view actions.

Run:

```text
query: xyz-no-such-job-acceptance
location: Berlin
radius: 25
```

Expected:

- Unknown/narrow query does not poison output with generic helper results.
- If no results remain, the UI says so clearly.
- Fallback attempt details appear only when fallback actually ran.

Run with the warehouse profile:

```text
query: softwareentwickler
location: Berlin
```

Expected:

- IT vacancies are not accepted as visible warehouse hot/maybe matches.

Run with the warehouse profile:

```text
query: fahrer
location: Berlin
```

Expected:

- Driving/delivery results are not treated as warehouse-compatible hot/maybe matches unless the selected profile actually targets driving.

## ATS / CRM

From a visible search result:

- Click `Сохранить в отклики`.
- Click `Отметить просмотр`.
- Open the original link action when available.
- Open `/leads`.
- Open the lead detail page.
- Update manual details, application channel, contact email, next action, resume version, and cover letter template if present.
- Mark applied, reply received, rejected, interview scheduled, archive, add note, and add reminder.

Expected:

- Duplicate source identity does not create duplicate leads.
- Lead timeline records the accepted events.
- Manual edits persist after refresh.
- Reminder and note are visible on the lead detail page.
- Resume/template links persist where selected.

## Email And Linking

Open `/email`.

Manual import sample:

```text
subject: Antwort auf Bewerbung Lagermitarbeiter
from_email: HR Team <hr@example.de>
to_emails: me@example.com
body_text: Guten Tag, vielen Dank fuer Ihre Bewerbung. Bitte melden Sie sich fuer ein Gespraech.
```

Expected:

- Manual email import creates or updates a thread/message.
- Matching panel suggests relevant lead candidates when available.
- Thread link/unlink and message link/unlink work.
- Invalid lead IDs show validation notices instead of crashing.
- Lead detail shows linked email summary.
- Gmail scaffold without credentials shows not-configured/degraded state.

## Stats

Open `/stats?window=7d`.

Expected:

- Dashboard renders summary cards, source quality, activity digest, and funnel.
- Counts reflect saved/viewed/applied/reply/rejection/interview activity created during QA.
- Profile selector/default profile behavior remains consistent with search/leads.
- Empty-state messaging is clear when there is no data.

## Settings, LLM, And Notifications

Open `/settings`.

Expected without `OPENAI_API_KEY`:

- LLM status says deterministic mode / missing config.
- Search, profile, ATS, email, and stats remain usable.

Open `/profile` and manually trigger Telegram and Email digest buttons.

Expected without notification credentials:

- Clear not-configured messages.
- No crash and no mandatory acceptance failure.

## Optional Integration Smoke

Skip these when credentials, local services, or network are unavailable.

- Enable and configure live BA/Careerjet/EURES/Remotive/Adzuna source lanes, then run `lager Berlin`.
- Set `OPENAI_API_KEY`, restart, and confirm `/settings` reports configured or provider-error truthfully.
- Set Gmail credentials and run the Gmail scaffold check.
- Set Telegram credentials and send the digest.
- Set SMTP credentials and send the email digest.
- Set a local PostgreSQL `DATABASE_URL`, run `alembic upgrade head`, and start the app.

Expected:

- Optional integration failures are reported as degraded/not-configured/provider errors.
- Optional failures do not invalidate mandatory deterministic acceptance.
