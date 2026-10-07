"""Настройки релевантности: ключевые слова, лимиты и пороги.

Всё, что меняет решение «показать / понизить / скрыть» и при этом является
вопросом вкуса или рынка, а не логики, лежит здесь, а не в коде правил.

Значения по умолчанию рассчитаны на поиск простой работы в Германии. Числа
переопределяются переменными окружения (RELEVANCE_*), списки — JSON-файлом,
путь к которому задаёт RELEVANCE_CONFIG_FILE: ключи файла совпадают с именами
полей, значение заменяет список целиком.

Шаблоны — регулярные выражения по тексту, свёрнутому normalize_text_for_fingerprint
(нижний регистр, без умляутов и пунктуации, «Führerschein» → «fuhrerschein»).
"""
from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.logging import logger


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class RelevanceConfig:
    # --- 1. Права и допуски к перевозке ---
    # Заголовки и требования, при которых нужен P-Schein (Fahrerlaubnis zur
    # Fahrgastbeförderung). Без него в профиле вакансия скрывается.
    p_schein_requirement_patterns: tuple[str, ...] = (
        r"\bp\s*schein\w*",
        r"\bpersonenbeforderungsschein\w*",
        r"\bfahrerlaubnis\s+zur\s+fahrgastbeforderung\b",
        r"\bfzf\b",
    )
    p_schein_title_patterns: tuple[str, ...] = (
        r"\bpersonenbeforderung\w*",
        r"\bshuttle\w*",
        r"\bmietwagen\s+mit\s+fahrer\b",
        r"\btaxi\w*",
        r"\bfahrgastbeforderung\w*",
    )
    # Квалифицированная перевозка больных: нужен Rettungssanitäter/Rettungshelfer.
    medical_transport_title_patterns: tuple[str, ...] = (r"\bkrankentransport\w*",)
    medical_transport_requirement_patterns: tuple[str, ...] = (
        r"\bqualifiziert\w*\s+krankentransport\w*",
        r"\brettungssanitater\w*",
        r"\brettungshelfer\w*",
        r"\bnotfallsanitater\w*",
    )
    # Не повод скрыть, но повод предупредить: (код, шаблон, подпись).
    license_risk_markers: tuple[tuple[str, str, str], ...] = (
        ("long_haul", r"\bfernverkehr\w*", "дальние рейсы (Fernverkehr): ночёвки вне дома"),
        ("forklift", r"\b(?:gabel)?stapler(?:schein|fahrer|fuhrerschein)?\w*",
         "нужен погрузчик (Stapler): проверить, требуется ли Staplerschein"),
        ("loading_crane", r"\bladekran\w*", "машина с краном-манипулятором (Ladekran): нужен допуск"),
    )

    # --- 2. Способ передвижения ---
    # Вакансии, где работа делается не на автомобиле: (режим, шаблон).
    # Голое «Rad» проверяется только в заголовке: в тексте оно слишком часто
    # значит колесо («Rad- und Reifenwechsel»).
    transport_mode_title_patterns: tuple[tuple[str, str], ...] = (
        # Велосипед как СПОСОБ работы, а не как товар: «mit e-Bike», «Rad»,
        # «Cargo Bike Kurier», «Rider». «Fahrer für die … E-Bike Stores» возит
        # велосипеды на машине и под правило не попадает.
        ("bike", r"\b(?:mit|per|auf)\s+(?:dem\s+|einem\s+)?(?:e\s*bike|fahrrad|rad|lastenrad|cargo\s*bike|velo)\b"
                 r"|\b(?:e\s*bike|fahrrad|cargo\s*bike|lastenrad|velo|rad)\s*(?:kurier|courier|zusteller|rider|lieferant)\w*"
                 r"|\b(?:fahrrad|velo|lastenrad)kurier\w*|\bradkurier\w*|\brad\b|\brider\b"),
        ("foot", r"\bzu\s+fuss\b"),
    )
    transport_mode_body_patterns: tuple[tuple[str, str], ...] = (
        ("bike", r"\b(?:mit|per|auf)\s+(?:dem\s+|einem\s+|eigenem\s+|deinem\s+|ihrem\s+)?(?:e\s*bike|fahrrad|lastenrad|cargo\s*bike|velo)\b"),
        ("bike", r"\b(?:e\s*bike|fahrrad|lastenrad|cargobike)\s*kurier\w*"),
        ("foot", r"\bzu\s+fuss\b"),
    )
    # Признаки того, что работа всё же на машине: тогда велосипед — вариант, а не условие.
    car_mode_patterns: tuple[str, ...] = (
        r"\bpkw\b", r"\bauto\b", r"\bmit\s+(?:dem\s+|eigenem\s+)?(?:auto|pkw|wagen)\b",
        r"\btransporter\w*", r"\bsprinter\w*", r"\bfirmenwagen\b", r"\bdienstfahrzeug\w*",
        r"\bvan\b", r"\bkfz\b",
    )
    # Разноска газет пешком/рано утром — нецелевая роль для профиля на машине.
    press_distribution_patterns: tuple[str, ...] = (
        r"\bzeitung\w*\s*(?:zusteller|austrag|bote|verteil)\w*",
        r"\bzeitungszustell\w*",
        r"\btageszeitung\w*",
        r"\bfruhaufsteher\w*",
        r"\bzeitung\w*\s+austragen\b",
    )
    press_distribution_penalty: int = -20

    # --- 3. Подтверждение роли ---
    # Сколько раз ключевое слово роли должно встретиться в описании, чтобы
    # считаться подтверждённым без совпадения в заголовке.
    role_body_confirmation_min_mentions: int = 2
    apprenticeship_title_patterns: tuple[str, ...] = (
        r"^ausbildung\b",
        r"\bausbildung\s+(?:zum|zur|als)\b",
        r"\bausbildung\s+(?:fachkraft|berufskraftfahrer)\w*",
        r"\bauszubildende\w*",
        r"\bazubi\w*",
        r"\bduales?\s+studium\b",
    )

    # --- 4. Кластеры работодателя ---
    employer_cluster_min_size: int = 5
    max_cards_per_employer_in_top: int = 3

    # --- 5. Шаблонный текст работодателя ---
    # Доля вакансий одного работодателя, в которых абзац должен повториться,
    # чтобы считаться шаблонным; и минимальное число вакансий для такого вывода.
    boilerplate_min_share: float = 0.5
    boilerplate_min_vacancies: int = 3
    boilerplate_min_segment_chars: int = 40
    # Абзацы, которые всегда считаются служебными: сертификаты, гутшайны, согласия.
    boilerplate_marker_patterns: tuple[str, ...] = (
        r"\bazav\b",
        r"\bdekra\b",
        r"\bvermittlungsgutschein\w*",
        r"\baktivierungs\s*und\s*vermittlungsgutschein\w*",
        r"\bavgs\b",
        r"\bdatenschutz\w*",
        r"\beinwilligung\w*",
        r"\bdsgvo\b",
        r"\bpersonenbezogene\w*\s+daten\b",
    )
    # Абзацы с жёсткими требованиями не вырезаются никогда, даже если повторяются
    # у работодателя в каждой вакансии: требование остаётся требованием.
    boilerplate_protected_patterns: tuple[str, ...] = (
        r"\bfuhrerschein\w*",
        r"\bfahrerlaubnis\w*",
        r"\bklasse\s+[a-d]\w*",
        r"\blkw\b",
        r"\bp\s*schein\w*",
        r"\b(?:schlusselzahl|ziffer|code)\s+95\b",
        r"\bberufskraftfahrer\w*",
        r"\bstaplerschein\w*",
    )

    # --- 6. Дедупликация ---
    # Варианты написания одного работодателя → каноническое имя.
    employer_aliases: tuple[tuple[str, str], ...] = (
        (r"\bdeutsche\s+post\b|\bdhl\b", "deutsche post dhl"),
    )
    # Юридические формы и хвосты, которые не различают работодателей.
    employer_legal_suffix_patterns: tuple[str, ...] = (
        r"\bgmbh\b", r"\bag\b", r"\bkg\b", r"\bco\b", r"\bse\b", r"\bug\b", r"\bmbh\b",
        r"\be\s*v\b", r"\be\s*k\b", r"\bggmbh\b", r"\bohg\b", r"\bgbr\b", r"\bltd\b", r"\binc\b",
        r"\bniederlassung\b.*$", r"\bfiliale\b.*$", r"\bbetrieb\b.*$",
        r"\barbeitsvermittlung\b", r"\bpersonalvermittlung\b", r"\bpersonalservice\b",
        r"\bpersonaldienstleistung\w*", r"\bpersonalmanagement\b", r"\bholding\b",
    )
    # Описания, совпадающие на эту долю токенов, — одна вакансия под разными заголовками.
    duplicate_body_similarity: float = 0.9
    duplicate_body_min_chars: int = 200

    # --- 7. Допуск к работе ---
    # (вид требования, шаблон, подпись, закрывается ли обычным правом на работу).
    # Право на работу (например § 24 AufenthG) закрывает Arbeitserlaubnis и визу;
    # гражданство и проверку безопасности оно не закрывает.
    work_authorization_requirements: tuple[tuple[str, str, str, bool], ...] = (
        ("work_permit", r"\b(?:arbeitserlaubnis|arbeitsgenehmigung|aufenthaltstitel|aufenthaltserlaubnis)\w*",
         "нужно разрешение на работу (Arbeitserlaubnis)", True),
        ("work_permit", r"\b(?:work|residence)\s+permit\b|\bright\s+to\s+work\b",
         "нужно разрешение на работу (work permit)", True),
        ("visa", r"\bvisa\b|\bvisum\b|\b(?:visa\s+)?sponsorship\b",
         "вопрос визы или спонсорства", True),
        ("eu_citizenship", r"\beu\s*(?:staats)?burger\w*|\bstaatsangehorigkeit\s+(?:eines\s+)?eu\b|\beu\s+citizenship\b",
         "требуют гражданство ЕС", False),
        ("german_citizenship", r"\bdeutsche\w*\s+staatsangehorigkeit\b|\bgerman\s+citizenship\b",
         "требуют немецкое гражданство", False),
        ("security_clearance", r"\bsicherheitsuberprufung\w*|\bsecurity\s+clearance\b",
         "нужна проверка безопасности (Sicherheitsüberprüfung)", False),
    )
    # Слова рядом с упоминанием, которые делают его информацией, а не требованием:
    # «keine Einschränkungen innerhalb der Arbeitserlaubnis».
    work_authorization_informational_patterns: tuple[str, ...] = (
        r"\b(?:keine|ohne)\s+einschrankung\w*",
        r"\bno\s+restrictions?\b",
        r"\bunterstutz\w*\s+(?:dich|sie|bei)\b",
        r"\bhelfen\s+(?:dir|ihnen)\b",
    )

    # --- 8. Обратная связь ---
    feedback_max_penalty: int = 10
    feedback_max_bonus: int = 8
    feedback_source_penalty: int = 2
    # Доля общих слов заголовка (Жаккар), с которой вакансия «похожа» на отмеченную.
    feedback_title_similarity: float = 0.6
    # Сильные позитивные сигналы, при которых штраф обратной связи не применяется.
    feedback_strong_hourly_eur: float = 15.0

    # --- 9. Правдоподобие зарплаты ---
    salary_hourly_min: float = 12.0
    salary_hourly_max: float = 30.0
    salary_monthly_min: float = 1000.0
    salary_monthly_max: float = 5000.0
    salary_yearly_min: float = 15000.0
    salary_yearly_max: float = 60000.0

    # --- 10. Перевод заголовков и сводок ---
    # Глоссарий частых терминов: подставляется в промпты перевода и пересказа.
    translation_glossary: tuple[tuple[str, str], ...] = (
        ("Botenfahrer", "курьер-водитель"),
        ("Stückgut", "сборные грузы (не «тяжёлые»)"),
        ("Kommissioniertätigkeit", "комплектация заказов (не «комиссионные»)"),
        ("Kommissionierer", "комплектовщик заказов"),
        ("Überführung", "перегон автомобиля"),
        ("Fahrzeugüberführer", "перегонщик автомобилей"),
        ("Quereinsteiger", "без профильного опыта"),
        ("Abrufkraft", "работа по вызову"),
        ("Liliengewächse", "цветы и растения (не «выращивание лилий»)"),
        ("Zusteller", "доставщик"),
        ("Paketzusteller", "доставщик посылок"),
        ("Nahverkehr", "местные перевозки"),
        ("Fernverkehr", "дальние перевозки"),
    )
    # Сколько раз повторить перевод, если ответ модели не прошёл проверку.
    translation_retries: int = 1

    # --- 11. Дорога и начало смены ---
    commute_average_speed_kmh: float = 70.0
    commute_max_minutes_for_early_shift: int = 60
    early_shift_latest_hour: int = 6
    early_shift_patterns: tuple[str, ...] = (
        r"\bnachtschicht\w*",
        r"\bnachtzustellung\w*",
        r"\bfruhmorgens\b",
        r"\bfruhschicht\w*\s+ab\s+0?[0-5]\b",
        r"\bab\s+0?[0-5](?:\s+[0-5]\d)?\s*uhr\b",
        r"\b0?[0-5](?:\s+[0-5]\d)?\s*uhr\s+morgens\b",
        r"\b(?:arbeitsbeginn|schichtbeginn|beginn|start)\s+(?:um\s+|ab\s+)?0?[0-5](?:\s+[0-5]\d)?\s*uhr\b",
        # «zwischen 02:00 und 06:00», «02:00–06:00» (после нормализации «02 00 06 00»).
        r"\b(?:zwischen\s+)?0[0-5]\s+[0-5]\d\s+(?:und\s+|bis\s+)?0?[0-9]\s+[0-5]\d\b",
        r"\bfruhaufsteher\w*",
    )
    # «Дом с даты»: «2026-11-01=Neustrelitz; 2027-03-01=Rostock».
    home_city_schedule: str = ""

    # --- Кадровые агентства ---
    # Что делать с вакансиями кадровых агентств:
    #   demote  — в самый конец выдачи («на проверку»), от агентства одна, самая свежая;
    #   exclude — скрывать целиком;
    #   keep    — как обычные вакансии (только пометка на карточке).
    staffing_agency_policy: str = "demote"
    staffing_agency_company_patterns: tuple[str, ...] = (
        r"\barbeitsvermittlung\w*",
        r"\bpersonalvermittlung\w*",
        r"\bpersonaldienst\w*",
        r"\bpersonalservice\w*",
        r"\bpersonalmanagement\w*",
        r"\bpersonalleasing\w*",
        r"\bzeitarbeit\w*",
        r"\bzeitpersonal\w*",
        r"\barbeitnehmeruberlassung\w*",
        r"\bstaffing\b",
        r"\brecruiting\b",
        r"\bpersonalberatung\w*",
        r"\bjobvermittlung\w*",
    )
    staffing_agency_body_patterns: tuple[str, ...] = (
        r"\barbeitnehmeruberlassung\w*",
        r"\bpersonaldienstleister\w*",
        r"\bzeitarbeit\w*",
        r"\barbeitsvermittlung\s+gmbh\w*",
        r"\bprivate\w*\s+arbeitsvermittlung\w*",
        r"\bpersonalvermittlung\w*",
        r"\bim\s+auftrag\s+(?:unseres|eines)\s+kunden\b",
        # «für unseren Kunden» — дательный падеж единственного числа: «для нашего
        # клиента». Общее «unsere Kunden» (покупатели) признаком не является.
        r"\bfur\s+(?:unseren|einen)\s+(?:namhaften\s+|renommierten\s+|langjahrigen\s+)?kunden\b",
        r"\bunser\s+kunde\b",
        r"\bvermittlungsgutschein\w*",
    )
    # Сколько разных признаков агентства в тексте нужно, чтобы считать
    # вакансию агентской без признака в названии компании.
    staffing_agency_body_min_markers: int = 2


_ENV_NUMBERS: dict[str, str] = {
    "press_distribution_penalty": "RELEVANCE_PRESS_DISTRIBUTION_PENALTY",
    "role_body_confirmation_min_mentions": "RELEVANCE_ROLE_BODY_MIN_MENTIONS",
    "employer_cluster_min_size": "RELEVANCE_EMPLOYER_CLUSTER_MIN_SIZE",
    "max_cards_per_employer_in_top": "RELEVANCE_MAX_CARDS_PER_EMPLOYER",
    "boilerplate_min_share": "RELEVANCE_BOILERPLATE_MIN_SHARE",
    "boilerplate_min_vacancies": "RELEVANCE_BOILERPLATE_MIN_VACANCIES",
    "boilerplate_min_segment_chars": "RELEVANCE_BOILERPLATE_MIN_SEGMENT_CHARS",
    "duplicate_body_similarity": "RELEVANCE_DUPLICATE_BODY_SIMILARITY",
    "duplicate_body_min_chars": "RELEVANCE_DUPLICATE_BODY_MIN_CHARS",
    "feedback_max_penalty": "RELEVANCE_FEEDBACK_MAX_PENALTY",
    "feedback_max_bonus": "RELEVANCE_FEEDBACK_MAX_BONUS",
    "feedback_source_penalty": "RELEVANCE_FEEDBACK_SOURCE_PENALTY",
    "feedback_title_similarity": "RELEVANCE_FEEDBACK_TITLE_SIMILARITY",
    "feedback_strong_hourly_eur": "RELEVANCE_FEEDBACK_STRONG_HOURLY_EUR",
    "salary_hourly_min": "RELEVANCE_SALARY_HOURLY_MIN",
    "salary_hourly_max": "RELEVANCE_SALARY_HOURLY_MAX",
    "salary_monthly_min": "RELEVANCE_SALARY_MONTHLY_MIN",
    "salary_monthly_max": "RELEVANCE_SALARY_MONTHLY_MAX",
    "salary_yearly_min": "RELEVANCE_SALARY_YEARLY_MIN",
    "salary_yearly_max": "RELEVANCE_SALARY_YEARLY_MAX",
    "commute_average_speed_kmh": "RELEVANCE_COMMUTE_SPEED_KMH",
    "commute_max_minutes_for_early_shift": "RELEVANCE_COMMUTE_MAX_MINUTES_EARLY_SHIFT",
    "early_shift_latest_hour": "RELEVANCE_EARLY_SHIFT_LATEST_HOUR",
    "staffing_agency_body_min_markers": "RELEVANCE_STAFFING_AGENCY_BODY_MIN_MARKERS",
}
_ENV_BOOLS: dict[str, str] = {}
_ENV_STRINGS: dict[str, str] = {
    "home_city_schedule": "HOME_CITY_SCHEDULE",
    "staffing_agency_policy": "RELEVANCE_STAFFING_AGENCY_POLICY",
}


def load_relevance_config() -> RelevanceConfig:
    """Значения по умолчанию, поверх них — JSON-файл, поверх него — переменные окружения."""
    defaults = RelevanceConfig()
    overrides: dict[str, Any] = {}
    fields = {field.name: field for field in dataclasses.fields(RelevanceConfig)}

    config_file = os.getenv("RELEVANCE_CONFIG_FILE")
    if config_file:
        try:
            payload = json.loads(Path(config_file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            logger.warning("relevance_config_file_unreadable path=%s error=%s", config_file, error)
            payload = {}
        if isinstance(payload, dict):
            for name, value in payload.items():
                if name not in fields:
                    logger.warning("relevance_config_unknown_key key=%s", name)
                    continue
                default = getattr(defaults, name)
                if isinstance(default, tuple) and isinstance(value, list):
                    overrides[name] = tuple(tuple(item) if isinstance(item, list) else item for item in value)
                elif not isinstance(default, tuple):
                    overrides[name] = value

    for name, env_name in _ENV_NUMBERS.items():
        current = overrides.get(name, getattr(defaults, name))
        if isinstance(current, int) and not isinstance(current, bool):
            overrides[name] = _env_int(env_name, current)
        elif isinstance(current, float):
            overrides[name] = _env_float(env_name, current)
    for name, env_name in _ENV_BOOLS.items():
        current = overrides.get(name, getattr(defaults, name))
        overrides[name] = _env_bool(env_name, bool(current))
    for name, env_name in _ENV_STRINGS.items():
        raw = os.getenv(env_name)
        if raw is not None:
            overrides[name] = raw
    return dataclasses.replace(defaults, **overrides)


@lru_cache(maxsize=1)
def get_relevance_config() -> RelevanceConfig:
    return load_relevance_config()
