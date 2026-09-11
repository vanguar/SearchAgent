"""Deterministic staged keyword fallback ladder for Germany job search.

Expands query candidates only — never touches scoring or filtering.
"""
from __future__ import annotations

from app.services._role_intent_lexicon import RoleIntent
from app.services.role_family import (
    RoleFamily,
    classify_query_ru,
    is_specific_family,
    prohibits_broad_fallback,
)
from app.services.search_models import is_low_german_level

# role_key (lowercase Russian) → ordered German keyword stages
# Index 0 = primary; indices 1+ = deterministic fallback stages
FALLBACK_LADDER: dict[str, tuple[str, ...]] = {
    "склад":            ("lager", "lagermitarbeiter", "lagerhelfer", "helfer"),
    "логистика":        ("logistik", "lagerlogistik", "disponent", "helfer"),
    "упаковка":         ("verpacker", "verpackung", "lagerhelfer", "helfer"),
    "производство":     ("produktion", "produktionshelfer", "maschinenbediener", "helfer"),
    "комплектовщик":    ("kommissionierer", "lagerhelfer", "lager", "helfer"),
    "грузчик":          ("lagerhelfer", "lager", "helfer"),
    "рабочий помощник": ("helfer", "lagerhelfer", "produktionshelfer"),
    "курьер":           ("kurier", "fahrer", "zusteller", "lieferfahrer"),
    "водитель":         ("fahrer", "lkw-fahrer", "kraftfahrer", "kurier"),
    "уборщик":          ("reinigungskraft", "reinigung", "housekeeping", "helfer"),
    "повар":            ("koch", "küchenhelfer", "gastro", "helfer"),
    "официант":         ("kellner", "servicekraft", "gastro", "helfer"),
    "охранник":         ("sicherheitsdienst", "security", "bewachung"),
    "строитель":        ("bauhelfer", "bau", "montage", "helfer"),
    "it":               ("softwareentwickler", "developer", "it-support", "administrator"),
    "программист":      ("softwareentwickler", "developer", "programmierer"),
    "монтажник":        ("monteur", "montage", "techniker", "helfer"),
    "электрик":         ("elektriker", "elektrotechniker", "helfer"),
    "сварщик":          ("schweißer", "metallbau", "schlosser"),
    "слесарь":          ("schlosser", "mechaniker", "monteur"),
    "оператор":         ("maschinenführer", "maschinenbediener", "bediener", "helfer"),
    "кладовщик":        ("lagerist", "lagerverwaltung", "lager"),
    "экспедитор":       ("disponent", "spedition", "logistik"),
    "медсестра":        ("pflegekraft", "pflege", "pflegehelferin"),
    "бухгалтер":        ("buchhalter", "buchhaltung"),
    "менеджер":         ("manager", "teamleiter"),
    "продавец":         ("verkäufer", "einzelhandel", "kassierer"),
    "кассир":           ("kassierer", "verkäufer"),
}

# Low-barrier keywords appended when profile has no usable German/English
# AND the query family allows broad cross-family fallback.
LOW_LANGUAGE_FALLBACK: tuple[str, ...] = (
    "helfer",
    "lagerhelfer",
    "produktionshelfer",
    "reinigungskraft",
)

# Alternative German + English job titles per ROLE FAMILY, used to broaden coverage so a
# vacancy is not missed just because the listing used a different (but equivalent) title.
# IMPORTANT: only defined for HOMOGENEOUS low-barrier families whose roles are genuinely
# interchangeable (a warehouse seeker accepts Lagerhelfer OR Kommissionierer OR Verpacker).
# Skilled-trade / heterogeneous families (construction, kitchen, healthcare, sales, IT, …)
# are intentionally NOT pooled here — they rely on per-role synonyms_de to avoid mixing
# distinct trades (an electrician search must not pull bricklayer/painter jobs).
# All German terms were verified to return live results on the BA jobs API.
FAMILY_SYNONYMS: dict[RoleFamily, tuple[str, ...]] = {
    RoleFamily.WAREHOUSE: (
        "lager", "lagermitarbeiter", "lagerhelfer", "kommissionierer", "fachlagerist",
        "verpacker", "staplerfahrer", "transportarbeiter", "versandmitarbeiter", "wareneingang",
        "warehouse associate", "warehouse worker", "picker",
    ),
    RoleFamily.DRIVING: (
        # Лёгкий транспорт (до 3,5 т) и тяжёлый перемешаны намеренно: порядок попыток
        # сохраняется как был. Для профиля с категорией B тяжёлая часть отсекается
        # через HEAVY_VEHICLE_KEYWORDS — см. drop_heavy_vehicle_keywords.
        "fahrer", "kurier", "zusteller", "lieferfahrer", "kraftfahrer", "berufskraftfahrer",
        "auslieferungsfahrer", "paketzusteller", "lkw-fahrer", "delivery driver", "truck driver",
    ),
    RoleFamily.CLEANING: (
        "reinigungskraft", "gebäudereiniger", "raumpfleger", "reinigung", "unterhaltsreinigung",
        "reinigungshelfer", "housekeeping", "cleaner", "cleaning",
    ),
    RoleFamily.PRODUCTION: (
        "produktionshelfer", "produktionsmitarbeiter", "fertigungsmitarbeiter", "maschinenbediener",
        "maschinenführer", "anlagenführer", "montagehelfer", "production worker",
    ),
    RoleFamily.GENERIC: (
        "helfer", "aushilfe", "hilfskraft", "produktionshelfer", "lagerhelfer",
    ),
}

# Ключевики, обозначающие работу на тяжёлом транспорте (C/CE, от 7,5 т, дальнобой).
# Профилю с категорией B они дают только отказы: живой прогон по профилю «Доставка/Курьер»
# показал, что berufskraftfahrer / lkw-fahrer / truck driver вернули 78 вакансий и 0 hot —
# всё срезал heavy_vehicle_mismatch уже ПОСЛЕ скачивания. Отсекаем их до запроса.
HEAVY_VEHICLE_KEYWORDS: frozenset[str] = frozenset({
    "kraftfahrer",
    "berufskraftfahrer",
    "fernfahrer",
    "lkw-fahrer",
    "lkw fahrer",
    "lkwfahrer",
    "truck driver",
    "hgv driver",
})


def drop_heavy_vehicle_keywords(candidates: tuple[str, ...]) -> tuple[str, ...]:
    """Убирает ключевики тяжёлого транспорта из автоматически расширенного списка.

    Применяется ТОЛЬКО к синонимам, которые подобрала сама система. Термины, явно
    записанные пользователем в профиль, не трогаются — это его осознанный выбор.
    """
    return tuple(
        keyword for keyword in candidates
        if keyword.strip().casefold() not in HEAVY_VEHICLE_KEYWORDS
    )


# Per-ROLE alternative job titles (German + English), keyed by the German primary keyword
# used in ROLE_INTENT_MAP (RoleIntent.primary_de). Unlike FAMILY_SYNONYMS this is safe for
# heterogeneous/skilled families because each entry stays within ONE specific occupation
# (an electrician expands only to electrician variants, never to mason/painter).
# German terms verified to return live results on the BA jobs API; a few rarer or
# English terms are kept because they appear on English-language boards (Remotive, Arbeitnow).
ROLE_SYNONYMS_DE: dict[str, tuple[str, ...]] = {
    # --- Construction & skilled trades ---
    "elektriker": ("elektroniker", "elektroinstallateur", "elektrotechniker", "elektromonteur"),
    "schweißer": ("metallbauer", "schlosser", "industriemechaniker"),
    "schlosser": ("metallbauer", "industriemechaniker", "monteur"),
    "monteur": ("servicetechniker", "industriemechaniker", "mechaniker"),
    "bauhelfer": ("baufacharbeiter", "hochbaufacharbeiter", "bau"),
    "maler": ("lackierer", "anstreicher"),
    "fliesenleger": ("fliesen", "bau"),
    "stuckateur": ("trockenbauer", "trockenbaumonteur", "verputzer"),
    "tischler": ("schreiner", "holzmechaniker"),
    "schreiner": ("tischler", "holzmechaniker"),
    "zimmermann": ("zimmerer", "dachdecker"),
    "maurer": ("hochbaufacharbeiter", "betonbauer"),
    "dachdecker": ("bauklempner", "zimmerer"),
    "installateur": ("anlagenmechaniker", "klempner", "heizungsbauer"),
    "anlagenmechaniker": ("installateur", "klempner", "heizungsbauer"),
    "kfz-mechaniker": ("kfz-mechatroniker", "automechaniker", "servicetechniker"),
    # --- Kitchen & gastronomy ---
    "koch": ("beikoch", "jungkoch"),
    "küchenhelfer": ("küchenhilfe", "beikoch", "spülkraft"),
    "küchenhilfe": ("küchenhelfer", "beikoch", "spülkraft"),
    "kellner": ("servicekraft", "servicemitarbeiter", "restaurantfachmann"),
    "servicekraft": ("kellner", "servicemitarbeiter", "restaurantfachmann"),
    "barkeeper": ("barmann", "barista"),
    "barista": ("barkeeper", "barmann"),
    "bäcker": ("konditor",),
    "konditor": ("bäcker",),
    "metzger": ("fleischer",),
    "spülkraft": ("küchenhilfe", "küchenhelfer"),
    # --- Healthcare ---
    "pflegekraft": ("pflegehelfer", "pflegehilfskraft", "altenpfleger", "betreuungskraft"),
    "pflegefachkraft": ("altenpfleger", "krankenpfleger", "gesundheits- und krankenpfleger"),
    "pflegehelferin": ("pflegehelfer", "pflegehilfskraft", "betreuungskraft", "alltagsbegleiter"),
    "arzt": ("facharzt", "assistenzarzt", "mediziner"),
    "physiotherapeut": ("physiotherapie", "krankengymnast"),
    # --- Sales & office ---
    "verkäufer": ("verkaufsberater", "fachverkäufer", "verkaufsmitarbeiter", "einzelhandelskaufmann"),
    "vertriebsmitarbeiter": ("außendienstmitarbeiter", "kundenberater", "account manager"),
    "buchhalter": ("finanzbuchhalter", "bilanzbuchhalter", "buchhaltung"),
    "sachbearbeiter": ("bürokaufmann", "kaufmännischer mitarbeiter"),
    "manager": ("teamleiter", "projektmanager", "abteilungsleiter"),
    # --- IT (English titles are common in German listings) ---
    "softwareentwickler": ("software engineer", "developer", "programmierer", "fullstack", "backend", "frontend"),
    "systemadministrator": ("sysadmin", "it-administrator", "system engineer"),
    "it-support": ("helpdesk", "fachinformatiker", "service desk", "1st level support"),
    "devops": ("devops engineer", "cloud engineer", "site reliability engineer"),
    # --- Security ---
    "sicherheitsdienst": ("sicherheitsmitarbeiter", "sicherheitskraft", "wachmann", "objektschutz", "werkschutz"),
    # --- Agriculture ---
    "gärtner": ("landschaftsgärtner", "galabau", "gartenhelfer"),
    "landwirtschaft": ("erntehelfer", "saisonarbeiter"),
    "erntehelfer": ("ernte", "saisonarbeiter"),
    # --- Driving specifics (family pool also applies) ---
    "taxifahrer": ("personenbeförderung", "mietwagenfahrer"),
    "berufskraftfahrer": ("lkw-fahrer", "kraftfahrer", "fernfahrer"),
    "kurier": ("fahrradkurier", "paketzusteller"),
    "staplerfahrer": ("gabelstaplerfahrer", "kommissionierer"),
}

# Upper bound on total fallback queries per search — each becomes one fetch round, so this
# bounds runtime while still covering the realistic synonym space for a profession.
_MAX_FALLBACK_KEYWORDS: int = 12

MAX_DETERMINISTIC_STAGES: int = 2
MAX_LLM_STAGES: int = 1

# Stop condition: at least N non-rejected AND at least M hot
ENOUGH_NON_REJECTED: int = 3
ENOUGH_HOT: int = 1


def get_fallback_keywords(
    *,
    primary_query: str,
    role: str | None,
    low_language: bool,
    query_family: RoleFamily | None = None,
    light_vehicle_only: bool = False,
) -> tuple[str, ...]:
    """Return ordered fallback keywords for a role, excluding the primary query.

    IMPORTANT:
    - Unknown roles must NOT automatically degrade into broad low-barrier helper queries.
    - Broad low-language fallback is allowed only for known, compatible low-barrier families.
    """
    role_key = (role or "").strip().lower()
    ladder = FALLBACK_LADDER.get(role_key, ())
    candidates = tuple(kw for kw in ladder if kw != primary_query)

    resolved_family = query_family or (classify_query_ru(role_key) if role_key else RoleFamily.GENERIC)

    # Universal bug fix:
    # if we do not know the role well enough to build a deterministic ladder,
    # do NOT inject generic helper fallback. Better to return few/zero results
    # than to poison the output with irrelevant jobs.
    if not candidates:
        return ()

    # Broaden with per-role alternative titles first, then same-family alternatives.
    combined = (
        candidates
        + ROLE_SYNONYMS_DE.get(primary_query.strip().lower(), ())
        + FAMILY_SYNONYMS.get(resolved_family, ())
    )

    broad_fallback_allowed = (
        low_language
        and is_specific_family(resolved_family)
        and not prohibits_broad_fallback(resolved_family)
    )
    if broad_fallback_allowed:
        combined = combined + LOW_LANGUAGE_FALLBACK

    if light_vehicle_only:
        combined = drop_heavy_vehicle_keywords(combined)

    return _finalize_fallback_keywords(combined, primary_query=primary_query)


def get_intent_fallback_keywords(
    *,
    intent: RoleIntent,
    primary_query: str,
    low_language: bool,
    light_vehicle_only: bool = False,
) -> tuple[str, ...]:
    """Return ordered fallback keywords for a known RoleIntent, excluding primary_query."""
    # Role-specific synonyms first (most relevant), then same-family alternative titles.
    combined = (
        tuple(intent.synonyms_de)
        + ROLE_SYNONYMS_DE.get(intent.primary_de, ())
        + FAMILY_SYNONYMS.get(intent.family, ())
    )
    if low_language and not prohibits_broad_fallback(intent.family):
        combined = combined + LOW_LANGUAGE_FALLBACK
    if light_vehicle_only:
        combined = drop_heavy_vehicle_keywords(combined)
    return _finalize_fallback_keywords(combined, primary_query=primary_query)


def get_profile_fallback_keywords(
    *,
    profile_terms: tuple[str, ...],
    family: RoleFamily,
    role_primary_de: str = "",
    broaden: bool,
    light_vehicle_only: bool = False,
) -> tuple[str, ...]:
    """Fallback keywords for a saved multi-term profile, excluding the primary term.

    Always includes the profile's other terms. When `broaden` is True (western/German
    sources), also appends per-role then same-family alternative job titles so equivalent
    listings are not missed. For Russian-only source runs the raw profile terms are kept.
    """
    if not profile_terms:
        return ()
    primary = profile_terms[0]

    # Явные термины профиля — это план, заданный пользователем: они НЕ обрезаются лимитом.
    # Лимит существует, чтобы ограничить автоподбор синонимов, а не чтобы выкидывать то,
    # что человек сам записал в профиль.
    explicit = _finalize_fallback_keywords(
        tuple(profile_terms[1:]), primary_query=primary, limit=None
    )
    if not broaden:
        return explicit

    broadened = (
        ROLE_SYNONYMS_DE.get(role_primary_de.strip().lower(), ())
        + FAMILY_SYNONYMS.get(family, ())
    )
    if light_vehicle_only:
        # Режем только автоподбор: явные термины профиля — осознанный выбор пользователя.
        broadened = drop_heavy_vehicle_keywords(broadened)

    remaining = _MAX_FALLBACK_KEYWORDS - len(explicit)
    if remaining <= 0:
        return explicit
    extra = _finalize_fallback_keywords(
        broadened, primary_query=primary, limit=remaining, already_seen=explicit
    )
    return explicit + extra


def _finalize_fallback_keywords(
    candidates: tuple[str, ...],
    *,
    primary_query: str,
    limit: int | None = _MAX_FALLBACK_KEYWORDS,
    already_seen: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Drop the primary query, dedupe, and cap the number of attempts.

    Dedup and primary exclusion are CASE-INSENSITIVE: job-board APIs are case-insensitive,
    so e.g. a profile term "Lagermitarbeiter" and a pool term "lagermitarbeiter" are the same
    query — keeping both wastes a fetch round and a cap slot. First-seen casing is preserved.

    `limit=None` disables the cap (used for explicit profile terms, which are a user-defined
    plan rather than generated synonyms). `already_seen` lets a second call continue the
    dedup of an earlier one instead of re-emitting its keywords.
    """
    seen: set[str] = {keyword.strip().casefold() for keyword in already_seen}
    primary_norm = primary_query.strip().casefold()
    result: list[str] = []
    for keyword in candidates:
        normalized = keyword.strip().casefold()
        if not normalized or normalized == primary_norm or normalized in seen:
            continue
        seen.add(normalized)
        result.append(keyword)
        if limit is not None and len(result) >= limit:
            break
    return tuple(result)


def is_low_language_profile(*, german_level: str | None, english_level: str | None) -> bool:
    """True when both German and English are weak or absent."""
    return is_low_german_level(german_level) and is_low_german_level(english_level)
