**SmartJob SearchAgent: перевозка грузов на Sprinter / Transporter B**

Реализовано в изолированном worktree `smartjob-light-goods-transport`, ветка `codex/light-goods-transport`, база `c59a82ec7ae5b69ad689099ae2c982e59c7128c0`. Перед созданием worktree выполнен fetch `origin/fix/search-quality-and-scoring`; remote совпадал с этой базой. Основной checkout не редактировался. Merge и push не выполнялись. Saved profile и пользовательский полноценный поиск не создавались; диагностические параметры существовали только в памяти.

**1–4. Семантика и немецкие названия.** Новое семейство — `LIGHT_GOODS_TRANSPORT = "light_goods_transport"`. Понятное название будущего профиля: **Перевозка грузов — Sprinter / Transporter B**. Это отдельная специализация с проверкой light vehicle + goods transport. Generic `Fahrer B` остаётся общим водительским направлением. Совместимость с `driving` сохраняет существующие профили, а дополнительный semantic gate ограничивает новую специализацию.

Реальное употребление подтверждено не только агрегаторами: [Maibach](https://www.maibach-logistik.de/sprinterfahrer-in-m-w-d-bis-35t) ищет Sprinterfahrer/-in bis 3,5t для перевозки товаров A→B и упоминает Sonderfahrten; [Melcher](https://www.melcher-transporte.de/jobs/) отдельно предлагает трудоустройство и сотрудничество с Subunternehmer. Формулировка Werksverkehr неоднозначна: [DEKRA](https://www.dekra-arbeit.de/stellenmarkt/fahrerim-werksverkehr-job-in-bremen/4379063222) использует её для перегона самих автомобилей. Поэтому это не самостоятельное доказательство goods transport.

Полный словарь новой специализации в `FAMILY_SYNONYMS`:

- Sprinterfahrer; Sprinter-Fahrer; Sprinter Fahrer; Sprinterfahrer Klasse B; Sprinter-Fahrer Klasse B; Sprinter Fahrer Klasse B.
- Transporterfahrer; Transporter-Fahrer; Transporter Fahrer; Fahrer Transporter; Fahrer für Transporter; Fahrer Klasse B Transporter; Fahrer Klasse B Sprinter.
- Fahrer bis 3,5 t; Fahrer bis 3,5t; Fahrer 3,5 t; Fahrer 3,5t; Sprinterfahrer bis 3,5 t; Transporterfahrer bis 3,5 t; Fahrer 3,5-Tonner; Fahrer 3,5 Tonner.
- Sprinterfahrer Nahverkehr; Transporterfahrer Nahverkehr; Fahrer Nahverkehr Klasse B; Fahrer im Nahverkehr Klasse B.
- Fahrer Werksverkehr mit Sprinter; Fahrer Direktfahrten; Fahrer Direktfahrten Klasse B; Fahrer für Direktfahrten Klasse B; Fahrer Sonderfahrten; Fahrer Sonderfahrten Transporter; Kurierfahrer Sonderfahrten Klasse B.
- Kleintransporter Fahrer; Kastenwagen Fahrer; Shuttlefahrer Warenverkehr Klasse B.

Это словарь написаний и контекстных поисковых выражений, а не безусловный allowlist вакансий. Например, Fahrer Nahverkehr Klasse B требует дополнительного goods evidence; Fahrer Direktfahrten без класса/лёгкого автомобиля также не получает strong match. Дефисные/раздельные написания распознаются нормализацией. Не каждое написание отдельно проверялось сетевым запросом: для этого и нужны query families.

Не используются как самостоятельные основные запросы: Fahrer, Transport, Logistik, Nahverkehr, Shuttlefahrer, Servicefahrer, Regionalverkehr. Fahrer Klasse B / Fahrer Führerschein Klasse B сами по себе не задают новое семейство. Depotfahrer найден в [австрийской почтовой классификации](https://www.ris.bka.gv.at/Dokumente/BgblAuth/BGBLA_2014_II_56/BGBLA_2014_II_56.pdfsig), но надёжная современная немецкая вакансия нужного типа не подтверждена: основной запрос не добавлен. Werksverkehr, Depotverkehr, Tagestouren, feste Touren, Abholfahrten и Transportfahrten — контекст, а не доказательство класса автомобиля и груза. Конкретные модели не обязательны.

**5–6. Измерение запросов.** Диагностический охват: Berlin, радиус источников 50 км, page=1, page_size=25, BA + Adzuna + Careerjet, десять кандидатов × три источника = 30 adapter calls. 117 RAW → 97 canonical. Это ограниченная первая страница, не оценка всего рынка. Jooble не вызывался; Arbeitnow не включался.

RELEVANT — независимо размеченный TARGET; UNIQUE RELEVANT — TARGET, не найденный остальными девятью запросами. NOISE* включает всё, что не подтверждено как TARGET: явный шум, ADJACENT и UNKNOWN. Такой подсчёт не выдаёт короткие BA-заголовки за подтверждённые вакансии. Canonical рассчитан через существующий merge, идентичность между запросами — по source-record keys. Возможные остаточные дубли с разным написанием работодателя не исправлялись в этой задаче.

| Query family | RAW | CANONICAL | RELEVANT | UNIQUE RELEVANT | NOISE* | В плане |
|---|---:|---:|---:|---:|---:|---|
| Sprinterfahrer | 1 | 1 | 1 | 0 | 0 | Да, первая |
| Fahrer Klasse B Transporter | 24 | 19 | 7 | 6 | 12 | Да, вторая |
| Fahrer bis 3,5 t | 25 | 25 | 1 | 1 | 24 | Да: единственный дополнительный TARGET |
| Transporterfahrer | 1 | 1 | 0 | 0 | 1 | Да: самостоятельное точное название, результат без body |
| Fahrer Direktfahrten | 0 | 0 | 0 | 0 | 0 | Да: ограниченный слот поиска отдельного типа рейсов |
| Fahrer Sonderfahrten | 1 | 1 | 0 | 0 | 1 | Да: ограниченный слот поиска отдельного типа рейсов |
| Fahrer Nahverkehr Klasse B | 25 | 20 | 0 | 0 | 20 | Нет |
| Fahrer Werksverkehr | 26 | 19 | 0 | 0 | 19 | Нет |
| Kleintransporter Fahrer | 7 | 7 | 0 | 0 | 7 | Нет |
| Gütertransport Fahrer Klasse B | 7 | 7 | 0 | 0 | 7 | Нет |

Итог — **6 families**. Нулевые Direkt-/Sonderfahrten сохранены ради различия обязанностей и подтверждённой терминологии работодателей; их продуктивность для Berlin пока не доказана. Transporterfahrer тоже пока не дал подтверждённого уникального TARGET. Рост RAW не считается основанием добавлять запрос.

BA завершил ошибкой Sprinterfahrer и Fahrer Nahverkehr Klasse B. Нули BA для этих запросов — отсутствие полученных данных, не отсутствие вакансий. Полная разбивка RAW/status по источникам находится в `diagnostics/light_transport_live_after.json` → `query_metrics`.

Фактические Settings с production `.env`: page_size=25, max_pages=4. Общий бюджет кода — 48 adapter calls, на источник — 24. Для нового направления используется одна страница каждого запроса. HTTP retries внутри адаптера не являются дополнительными adapter calls.

Anchor определяется существующим `place_weight` по крупнейшему requested city, без hardcode Berlin. Сначала реально выполняются все шесть запросов в anchor, затем Sprinterfahrer и Fahrer Klasse B Transporter в меньших городах. Для четырёх городов и трёх работающих источников это 36 вызовов до возможного early-stop. Остаток бюджета может обслужить другие сочетания. Очень большой список городов всё равно ограничен общим бюджетом. Ответ с ошибкой не засчитывается как успешно выполненная family; достаточный RAW первого запроса не останавливает начальное покрытие. Unit tests проверяют также Rostock как anchor, отмену, отсутствие LLM-fallback и лимиты.

**7–13. Сигналы и защиты.** Strong match начинается с водительского TITLE и названия Sprinter / Transporter / Kleintransporter / Kastenwagen / массы 3,5 т; либо требует класса B/PKW вместе с явной перевозкой товаров или прямыми/специальными рейсами. Название автомобиля само по себе без водительской роли не даёт target. Klasse B, Führerschein B, Führerscheinklasse B, варианты запятой и пробелов в 3,5 t распознаются. Модель автомобиля не является обязательной.

C/CE/C1/C1E, если обязательны, перекрывают лёгкий транспорт. Сохраняется защита от LKW, Sattelzug, Sattelauflieger, 7,5/12/40 t и тяжёлых квалификаций. Bare Kraftfahrer/Berufskraftfahrer по-прежнему скрываются для B-only, фикс c59a82e сохранён. Kraftfahrer/Berufskraftfahrer 3,5 t Klasse B проходят. Для новой специализации явно необязательные категории/модули и отрицания не становятся жёстким запретом. Отдельно учтён реальный заголовок «3,5t LKW»: только LKW при явно лёгкой массе и отсутствии обязательных тяжёлых условий не скрывает вакансию. Просто «Transporter + CE обязательно» остаётся reject.

Müll-/Abfallfahrer, Bagger-/Stapler-/Radlader-/Kran-/Maschinenfahrer, Bus-/Taxifahrer, Lokführer, Triebfahrzeugführer, охрана, редакторы, основной складской труд и продажи не становятся целевыми из-за Fahrer в тексте. Courier/parcel — отдельное направление: Paket-Zustellung/Amazon DSP не повышают confidence новой специализации. Auslieferungsfahrer допускается с light + goods evidence. Vehicle transfer перевозкой товаров не считается. Personenbeförderung/пациенты исключаются; Shuttle/Service без доказательства перевозки товаров не допускаются.

Узкое body-правило разрешает `Mitarbeiter Logistik` с регулярной перевозкой товаров на лёгком автомобиле в одной фразе обязанностей, но ставит MAYBE/review, без title-бонуса. `Lagermitarbeiter — gelegentlich Transporter fahren` скрывается. Чужое специфичное семейство заголовка не спасается описанием. Для нового профиля случайные body matches удаляются из положительных сигналов.

Самозанятость переиспользует существующий extractor/filter. Только для нового направления исключены ложные признаки вроде selbstständige Arbeitsweise / arbeitest selbstständig. Gewerbe/Subunternehmer/Freelance остаются запретом при self_employment_ok=false. Общая политика других профилей не изменена. Немецкий A1 не конфликтует с Grundkenntnisse / einfache Deutschkenntnisse / Deutsch von Vorteil или отсутствием языкового требования; отсутствие полноценного описания не доказывает отсутствия требований.

**14–16. Frozen BEFORE → AFTER.** BEFORE получен из неизменённого c59a82e; AFTER — из feature branch. Один и тот же in-memory профиль, без географии, чтобы измерять семантику профессии. Сравнение не основано на двух разных live runs. Visible = HOT + MAYBE. Precision = GOOD/TARGET среди всех visible; Recall = visible GOOD/TARGET / все GOOD/TARGET.

Synthetic frozen corpus: 32 примера (13 GOOD, 15 BAD, 4 AMBIG), сохранён до production edits.

| Группа | BEFORE HOT / MAYBE / REJECTED / HIDDEN | AFTER HOT / MAYBE / REJECTED / HIDDEN |
|---|---|---|
| GOOD, 13 | 3 / 10 / 0 / 0 | 11 / 2 / 0 / 0 |
| BAD, 15 | 0 / 4 / 3 / 8 | 0 / 0 / 0 / 15 |
| AMBIG, 4 | 0 / 4 / 0 / 0 | 0 / 0 / 0 / 4 |

Strict precision: **13/21 = 61,9% → 13/13 = 100%**. Recall: **13/13 = 100% → 100%**. Если считать только GOOD+BAD и исключить AMBIG из знаменателя: precision 76,5% → 100%. Это приёмочный набор, не статистическая гарантия на рынке.

Независимо размеченный live frozen corpus: 97 canonical, 8 TARGET / 5 ADJACENT / 59 NOISE / 25 UNKNOWN. Разметка выполнялась по заголовкам и полученным описаниям; UNKNOWN означает недостаток доказательств, а не доказанно плохую вакансию. Результаты последующей доработки на этом корпусе являются диагностикой на исследованном наборе, а не независимым holdout.

| Группа | BEFORE HOT / MAYBE / REJECTED / HIDDEN | AFTER HOT / MAYBE / REJECTED / HIDDEN |
|---|---|---|
| TARGET, 8 | 1 / 3 / 0 / 4 | 3 / 5 / 0 / 0 |
| ADJACENT, 5 | 1 / 2 / 0 / 2 | 0 / 2 / 0 / 3 |
| NOISE, 59 | 0 / 1 / 3 / 55 | 0 / 0 / 0 / 59 |
| UNKNOWN, 25 | 2 / 7 / 3 / 13 | 0 / 0 / 0 / 25 |

Strict TARGET precision: **4/17 = 23,5% → 8/10 = 80%**. TARGET recall: **4/8 = 50% → 8/8 = 100%**. Две ADJACENT остаются MAYBE для ручной проверки; считать их подтверждёнными TARGET было бы некорректно. 100% visible относится к TARGET+ADJACENT вместе, а не к strict precision.

RAW frozen-файл: `tests/fixtures/light_transport_live_probes.json`. SHA-256 (UTF-8, LF): `1d41f32f234ff3d1b3d28aa101db60addff5ecbcfe0abc9840432a35f94d27e8`. Этот hash совпадает в BEFORE/AFTER. Файл содержит публичные записи вакансий; credential-shaped поля проверены и не обнаружены. Разметка — `diagnostics/light_transport_probe_labels.json`; решения/баллы каждого профиля — `diagnostics/light_transport_live_before.json` и `..._after.json`.

**17–20. Регрессии.** На тех же 97 canonical для каждого из Courier, Fahrer B, VEHICLE_LOGISTICS, Lager: **0 изменений итоговой категории и балла**. Дополнительно существующие тесты этих направлений проходят. Глобальная регистрация точных лёгких водительских заголовков в новом семействе намеренная; числовой ranking и видимость существующих профилей на frozen-корпусе сохранены.

**21. Реальные примеры и последняя live validation.** В исходном probe-корпусе:

- TARGET: EVAZ — Fahrer/in Transporter, Klasse B, Renault Master mit Planenaufbau; BA ID `10001-1003739371-S`.
- TARGET: Uwe Steingross Feinmechanik — Fahrer/Transportfahrer 3,5T, Nahverkehr & Warenlogistik; BA ID `10001-1003758665-S`.
- TARGET: Good Bank — Fahrer/Driver Kühltransport (трудоустройство); его отдельная freelance-вакансия — NOISE для данного пользователя.
- TARGET: BieneGold — Fahrer für Baustellenlogistik, Transporter; BonFry — Auslieferungsfahrer 3,5t LKW с необязательной тяжёлой квалификацией.
- ADJACENT/MAYBE: Hauptstadt Möblerei — Möbelpacker/Fahrer, существенная погрузка и языковые вопросы; nextbike — Fahrer/Auslieferungsfahrer, совмещённый сервис велосипедов.
- NOISE: REMONDIS C/CE, Tesla Busfahrer, Sicherheitsmitarbeiter Werkschutz, Paketfahrer и обязательные freelance/Gewerbe.

После offline-проверок выполнены ещё 12 диагностических adapter calls: Berlin — Sprinterfahrer; Neustrelitz — Sprinterfahrer, Transporterfahrer, Fahrer Klasse B Transporter. Careerjet снова вернул одну целевую Sprinter-вакансию в Friesack. **При реальной географии Berlin + 50 км она корректно HIDDEN (outside_requested_cities)**, хотя профессионально TARGET. Нельзя представить её как прошедший географический фильтр результат Berlin. В Neustrelitz Adzuna/Careerjet вернули 0; BA во всех четырёх завершающих probes завершился ошибкой. Поэтому живая проверка малого города частичная, а не доказательство пустого рынка. Подробности — `diagnostics/light_transport_validation.json`. Полноценный orchestration с настоящими пользовательскими профилями не запускался.

**22–25. Проверки и Git.** Финальный полный pytest: **1867 passed** (50,12 с), включая 94 новые проверки; Ruff: **All checks passed**; `git diff --check`: без ошибок. Команды запускаются из isolated worktree существующим Python проекта; новые зависимости не установлены. Hash feature commit приведён в сообщении сдачи. Commit содержит реализацию, targeted tests, frozen fixtures, диагностические скрипты и этот отчёт. Мержить в основную ветку до согласования с параллельным аудитом не следует.

```powershell
$py = 'C:/Users/tzvan/Desktop/SmartJob_ SearchAgent/.venv/Scripts/python.exe'
& $py -m pytest -q
& $py -m ruff check .
git diff --check
$env:PYTHONPATH = (Get-Location).Path
& $py scripts/diagnose_light_transport.py score --input tests/fixtures/light_transport_frozen.json --output .scratch/recheck-synthetic.json
& $py scripts/summarize_light_transport.py --input tests/fixtures/light_transport_live_probes.json --labels docs/diagnostics/light_transport_probe_labels.json --output .scratch/recheck-live.json
```

Ожидается: тесты без сети проходят; Ruff и diff-check без ошибок; повторное офлайн-сравнение использует тот же hash corpus. Баллы freshness зависят от даты, поэтому regression tests фиксируют дату захвата. Для воспроизведения BEFORE экспортировать c59a82e через git archive в отдельный каталог и задать его как PYTHONPATH перед запуском того же диагностического скрипта.

**26. Будущий saved profile — только предложение.** Точная конфигурация находится в `light_goods_transport_profile_proposal.json`. desired_roles = [Transporter / Sprinter Klasse B]; search_query_terms = шесть families из плана выше; driver_license=B; german_level=A1; english_level=нет; no_german_required=false; full_time / part_time / mini_job / temporary; shift_ok=true; self_employment_ok=false; §24 и разрешение на работу. Locations: Berlin, Neustrelitz, Neubrandenburg, Rostock. Радиус 50 км предложен как стартовая настройка, а не принят безусловно. Домашний город, готовность к переезду и физические ограничения оставлены неизвестными.

Excluded roles содержат только явно посторонние профессии и parcel titles. Голые Kraftfahrer/Berufskraftfahrer, LKW и Verkaufsfahrer не добавлены в пользовательский exclusion-list: они могли бы скрыть соответственно реальные 3,5-тонные вакансии или формулировку «nicht Verkaufsfahrer»; это решают контекстные правила. Опыт с 2006 года и опыт доставки сохранены в предложении notes. **Ни эта конфигурация, ни пользовательский saved profile в БД не записывались. Создание и первый полноценный поиск требуют отдельной команды пользователя.**
