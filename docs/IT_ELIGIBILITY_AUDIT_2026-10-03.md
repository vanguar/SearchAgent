# IT eligibility audit — 2026-10-03

1. **Область и исходная версия.** База: `f4e4dd9`. Только целевые AI Automation / AI Tools и Python + AI Automation профили. Изменения БД, UI, адаптеров, схем, настроек или данных профилей отсутствуют. Работа выполнена в изолированной копии; production diff — новый IT-модуль и два места подключения.

2. **Root cause.** `ai_tools_profile.match_ai_tools_signals` собирал технологические совпадения, `VacancyScorer.score` давал до +40 за AI и +12 за отсутствие обнаруженного языкового требования. `STRONG_EXPERIENCE_RULE` распознавал узкий набор фраз, а обычный IT-штраф составлял лишь −10. Senior AI/DS/Applied Scientist не всегда попадали под `has_classic_engineering_title`. Общая taxonomy объединяла DS и интеграции в IT. Флаг entry-level мог отключить старый штраф за опыт. Обязательный английский, наоборот, жёстко отклонял AI-профиль.

3. **Прослеженный data flow.** `DatabaseSearchProfileResolver.resolve` → `SearchService.orchestrated_search/search` → `_resolve_profile_search_terms` → существующие `DouRssAdapter`, `DjinniRssAdapter`, `HHAdapter` → `SourceRecordPreview` → `VacancyNormalizer.normalize_source_record` (HTML, title, location, languages) → `VacancyProcessingService` (normalization/dedup) → `rule_catalog.inspect_vacancy` → `FilterEngine.evaluate` → `VacancyScorer.score` → `_assign_bucket` → `MatchExplainer` / `SearchResultItem` → существующая карточка. Жёсткие отказы пропускаются `_build_result_item` до выдачи. Пороги остались HOT ≥70 / MAYBE ≥45; review принудительно MAYBE. `role_family`, `language_signal_extractor`, `search_normalizer`, `location_normalizer`, `rule_catalog`, профили и источники не изменены.

4. **Новые правила.** `it_candidate_eligibility.py` применяется только при явном AI/Python Automation намерении профиля. Рабочая модель кандидата: 2 года личных проектов, 0 коммерческого IT-опыта, Германия; языковой уровень читается из профиля.

| Сигнал | Политика |
|---|---|
| 1–2 года commercial/professional с нижней границей 1 | −10, потолок 69, review |
| 2+ commercial/professional | −22, потолок 55, review |
| 3+ commercial/production, extensive/several years | −40, потолок 44, reject; существующий hard-reject cap ≤35 |
| 3–4 года без слова commercial | review ≤55; до 2 лет практики допустимы |
| 5+ лет практики | reject |
| Senior/Lead/Staff/Principal/Architect + обнаруженный разрыв опыта | дополнительное понижение и reject; одно слово senior не отклоняет |
| commercial **OR** substantial personal projects | коммерческий опыт не предполагается; +8 за путь через проекты/обучение |
| DS/Applied Scientist/research; ML Engineer с ML-core | mismatch для automation-профиля |
| Spoken B1/B2, fluent/advanced, общение с клиентами | penalty/cap и review; язык сам по себе не hard reject |
| Written/reading/basic/A2, optional language | без нового языкового запрета |
| Явная глубина SQL, production cloud или обязательный React/Go опыт | review ≤69 |
| Worldwide/EU/Europe/Germany remote | +6; просто Remote — нейтрально |
| Ukraine remote без обязательного проживания | review ≤69 |
| Must reside in Ukraine / mandatory Kyiv office/hybrid | reject только в remote-поиске целевого IT-профиля |

Ограничение применяется **после** технологических бонусов и feedback adjustment. Повторение ключевых слов и feedback не пробивают потолок. Требования анализируются по предложениям и блокам; nice-to-have/Significant Advantage не становятся обязательными, история компании и professional development как бонус не считаются стажем. Никаких проверок конкретных работодателей или названий вакансий в production нет.

5. **Regression-first.** До production-правок полный pytest: **1920 passed in 50.13s**. Новые первые 45 проверок на старом коде: **40 failed, 5 passed**. Финально добавлено **65 новых тестов**: synthetic boundary/negation/interaction cases, 7 реальных минимизированных golden-вакансий и 5 точных non-IT baseline snapshots. Фикстуры хранят категорию, диапазон баллов и ожидаемые причины/положительные сигналы.

Три прежних IT-теста по английскому изменены **по прямому требованию задания**, а не для маскировки регрессии: hard reject заменён точными проверками MAYBE, review reason и score ceiling. Senior Java продолжает быть hard reject по инженерным требованиям. Ни один тест не удалён; остальные старые assertions сохранены.

6. **Итоговые проверки.** Полный pytest: **1985 passed in 34.73s**. Targeted IT + profession neutrality: **119 passed**. Ruff: **All checks passed**. Mypy до/после: **60 ошибок в тех же 16 файлах**, диагностические сообщения совпадают после нормализации номеров строк; новый модуль ошибок не добавил. Старые ошибки не исправлялись. `git diff --check` чистый. Templates/UI не менялись, существующие web/template tests прошли в полном наборе.

7. **Реальные данные и метод сравнения.** Прочитана локальная БД в SQLite `mode=ro`, зафиксированы 3453 вакансии и 7 профилей. Replay — 3701 существующая пара «профиль × вакансия», одна дата оценки 2026-10-03, одинаковые входы до/после, без записи в БД. Для IT-профилей 5 и 8 — `remote_worldwide`; остальные — `germany_local`.

Сделан живой read-only запрос существующих адаптеров: DOU **25**, Djinni **162** записи, всего **187**. Запрос `AI Automation Specialist`, профиль 8, remote_worldwide; DOU использует AI/ML, Djinni — Data Science + Python. Для честного сравнения один и тот же полученный корпус повторно оценён старым и новым кодом. Это проверка adapter→normalize→filter→score→bucket без сохранения и без LLM/CRM feedback, а не запуск UI orchestration со всеми поисковыми попытками. Новые источники не добавлялись. HH запрос по AI Automation возвращает **HTTP 403** во всех настроенных странах; его живой результат сравнить невозможно.

Ниже top 15 доступных результатов по score (затем source key при равенстве), с сохранением категории. Hard-hidden исключены. Это диагностический порядок; UI дополнительно группирует по категориям, языковому приоритету и дате.

**BEFORE — живой DOU/Djinni корпус**

| # | Вакансия | Балл | Категория | Причины проверки / отказа |
|---:|---|---:|---|---|
| 1 | ai engineer ai automation specialist llm python ai agents | 78 | MAYBE | english_requirement_unknown |
| 2 | junior ai developer | 76 | MAYBE | english_requirement_unknown |
| 3 | technical data automation specialist | 76 | HOT | — |
| 4 | middle applied scientist 5884 | 72 | HOT | — |
| 5 | ai solution architect | 72 | MAYBE | self_employment_review |
| 6 | senior ai engineer | 72 | HOT | — |
| 7 | middle ds ai engineer | 72 | HOT | — |
| 8 | ai backend engineer python agentic ai | 70 | HOT | — |
| 9 | ai and software solutions architect contract | 70 | MAYBE | english_requirement_unknown |
| 10 | python integration engineer | 68 | MAYBE | english_requirement_unknown |
| 11 | python go backend developer ai driven | 68 | MAYBE | english_requirement_unknown |
| 12 | ai integration specialist llm engineer gemini websockets | 66 | MAYBE | english_requirement_unknown |
| 13 | expert generative ai engineer python llm agents | 66 | MAYBE | english_requirement_unknown |
| 14 | principal backend engineer python ai | 64 | MAYBE | english_requirement_unknown |
| 15 | senior data architect azure genai ready data platform | 64 | MAYBE | — |

**AFTER — тот же корпус**

| # | Вакансия | Балл | Категория | Причины проверки / отказа |
|---:|---|---:|---|---|
| 1 | junior python full stack developer | 68 | MAYBE | it_english_review |
| 2 | technical data automation specialist | 66 | MAYBE | it_stack_depth_review |
| 3 | ai integration specialist llm engineer gemini websockets | 66 | MAYBE | english_requirement_unknown |
| 4 | external data and media platform architect irc304870 | 64 | MAYBE | — |
| 5 | junior ai developer | 59 | MAYBE | it_english_review |
| 6 | ai engineer ai automation specialist llm python ai agents | 55 | MAYBE | it_experience_gap, english_requirement_unknown |
| 7 | strong junior middle ai engineer | 55 | MAYBE | it_experience_gap, it_english_review |
| 8 | python go backend developer ai driven | 46 | MAYBE | it_experience_gap, english_requirement_unknown |
| 9 | simulation engineer python game math | 44 | MAYBE | english_requirement_unknown |
| 10 | computer vision deep learning engineer | 42 | REJECTED | — |
| 11 | ai engineer trustpilot or reddit automation | 42 | REJECTED | — |
| 12 | algorithm research engineer | 42 | REJECTED | — |
| 13 | python data engineer | 42 | MAYBE | it_experience_gap, english_requirement_unknown |
| 14 | junior software developer | 41 | MAYBE | it_experience_gap, it_english_review, degree_mismatch |
| 15 | python backend engineer | 38 | MAYBE | english_requirement_unknown |

8. **Движение вакансий.** На сохранённой исходной выдаче:

| Вакансия | BEFORE | AFTER | Причина |
|---|---|---|---|
| junior ai developer | 76 MAYBE | 59 MAYBE | it_english_review |
| technical data automation specialist | 76 HOT | 66 MAYBE | it_stack_depth_review |
| middle ds ai engineer | 72 HOT | 0 REJECTED | it_english_review, it_experience_gap, it_research_mismatch |
| middle applied scientist 5884 | 72 HOT | 0 REJECTED | it_english_review, it_experience_gap, it_research_mismatch |
| senior ai engineer | 72 HOT | 0 REJECTED | it_english_review, it_experience_gap, it_seniority_gap |
| eu only full stack genai engineer python node js js ts gcp azure aws claude code | 74 MAYBE | 32 REJECTED | it_experience_gap, english_requirement_unknown, it_seniority_gap |
| ai solution architect | 72 MAYBE | 12 REJECTED | self_employment_review, it_experience_gap, it_seniority_gap |
| ai engineer | 76 HOT | 21 REJECTED | it_english_review, it_experience_gap |

Junior/entry-level поднимаются **относительно недоступных senior-ролей**, а не обещанием HOT при обязательном языке. Движение в диагностическом списке живого корпуса:

- junior ai developer: место 2 → 5, 59 MAYBE.
- junior python engineer: место 54 → 19, 36 MAYBE.
- strong junior or middle data analyst ai: место 56 → 37, 19 MAYBE.
- junior strong junior python developer: место 112 → 67, 0 MAYBE.

У Technical Data Automation Specialist в реальном тексте английский **не указан**, а production experience находится в Significant Advantage. MAYBE обоснован явным High command of SQL, а не выдуманным B2 или обязательным production-опытом. Полные RSS описания могут быть помечены адаптером как incomplete; прежняя неопределённость английского сохраняется, когда новое правило не обнаружило явного требования.

9. **Non-IT неизменность.** Все **2356** пар вне профилей 5/8 совпали полностью: snapshot signals, FilterResult, ScoreResult (включая веса и причины) и bucket. Проверены склад, доставка/курьер, Driver B Fernverkehr, перегон и Sprinter/Transporter. Дополнительно пять сохранённых exact-result snapshots выполняются при каждом pytest. Это проверяемое доказательство для данного корпуса и suite, а не обещание отсутствия любых будущих ошибок на произвольном тексте.

10. **Изменённые файлы.**

- `app/services/it_candidate_eligibility.py` — изолированные IT-правила.
- `app/services/filter_engine.py` — подключение только для целевого профиля; язык review, eligibility reasons.
- `app/services/scorer.py` — IT penalties/positives и итоговый потолок.
- `tests/services/test_it_candidate_eligibility.py` — 65 новых тестов.
- `tests/services/test_ai_tools_profile.py` — три согласованные с заданием изменения English-policy tests.
- `tests/fixtures/it_eligibility_golden.json` — семь минимизированных реальных IT-вакансий.
- `tests/fixtures/it_non_target_baseline.json` — пять результатов, снятых со старого кода.
- `docs/IT_ELIGIBILITY_AUDIT_2026-10-03.md` — этот отчёт.

11. **Вне scope / ограничения.** Mypy имеет прежние 60 ошибок; HH блокирует запросы; RSS не гарантирует полноту требований; отсутствует структурированное поле коммерческого стажа. Поэтому новая политика намеренно привязана к двум automation-намерениям текущего кандидата, а не распространяется на произвольные IT-профили. Значения профилей и существующие сохранённые scores в БД не переписывались. Повторный поиск использует новые правила. Автоматический деплой/публикация и изменения других веток не входят в патч.

12. **Повторение проверок** (из корня репозитория, Python проекта):

```powershell
python -m pytest -q
python -m pytest tests/services/test_it_candidate_eligibility.py tests/services/test_ai_tools_profile.py tests/services/test_profession_neutrality.py -q
python -m ruff check .
python -m mypy app
git diff --check
```

Полные локальные evidence/logs находятся в соседних `it-audit-evidence/`, `it-pytest-before.txt`, `it-new-tests-before.txt`, `it-pytest-final.txt`, `it-mypy-before.txt`, `it-mypy-after.txt`. Read-only replay harness: `../it_audit.py`; команды `replay before`, `replay after`, `live`, `live-replay live-before`, `live-replay live-after`. До/после снимки уже сохранены; повторный `live` заменяет корпус, поэтому исходные снимки следует сохранять для сравнения. Эти артефакты намеренно не добавлены в репозиторий: содержат полный локальный корпус и профили. Commit создаётся только после зелёного полного regression suite; hash и финальные git status/stat сообщаются отдельно.
