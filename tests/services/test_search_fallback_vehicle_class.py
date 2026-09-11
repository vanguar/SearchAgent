"""Отсечение ключевиков тяжёлого транспорта для профилей с категорией B.

Живой прогон профиля «Доставка / Курьер» (права B) показал, что расширение запроса
подставляло berufskraftfahrer / lkw-fahrer / truck driver: 78 скачанных вакансий, 0 hot —
всё срезал heavy_vehicle_mismatch уже после сети. Признак профиля известен заранее,
поэтому такие ключевики не должны доходить до запроса.
"""

from __future__ import annotations

from app.services.filter_engine import is_b_only_driving_profile
from app.services.role_family import RoleFamily
from app.services.search_fallback import (
    _MAX_FALLBACK_KEYWORDS as MAX_FALLBACK_KEYWORDS_FOR_TEST,
    HEAVY_VEHICLE_KEYWORDS,
    drop_heavy_vehicle_keywords,
    get_fallback_keywords,
    get_intent_fallback_keywords,
    get_profile_fallback_keywords,
)
from app.services.search_models import SearchProfileContext
from app.services.role_intent import normalize_role_intent


def _driver_profile(**overrides) -> SearchProfileContext:
    base = {
        "profile_label": "Доставка / Курьер",
        "profile_source": "saved",
        "driver_license": "B",
        "desired_roles": ("курьер", "водитель"),
    }
    base.update(overrides)
    return SearchProfileContext(**base)  # type: ignore[arg-type]


def test_b_only_driving_profile_is_recognized() -> None:
    assert is_b_only_driving_profile(_driver_profile()) is True


def test_profile_with_ce_licence_is_not_light_vehicle_only() -> None:
    assert is_b_only_driving_profile(_driver_profile(driver_license="B, CE")) is False


def test_non_driving_profile_is_not_light_vehicle_only() -> None:
    warehouse = _driver_profile(desired_roles=("склад",), driver_license=None)
    assert is_b_only_driving_profile(warehouse) is False


def test_drop_heavy_vehicle_keywords_removes_only_heavy_terms() -> None:
    candidates = ("fahrer", "kurier", "kraftfahrer", "lkw-fahrer", "zusteller", "truck driver")

    assert drop_heavy_vehicle_keywords(candidates) == ("fahrer", "kurier", "zusteller")


def test_drop_heavy_vehicle_keywords_is_case_insensitive() -> None:
    assert drop_heavy_vehicle_keywords(("LKW-Fahrer", "  Berufskraftfahrer ")) == ()


def test_profile_broadening_drops_heavy_keywords_for_b_licence() -> None:
    keywords = get_profile_fallback_keywords(
        profile_terms=("zusteller", "kurier"),
        family=RoleFamily.DRIVING,
        role_primary_de="zusteller",
        broaden=True,
        light_vehicle_only=True,
    )

    lowered = {keyword.casefold() for keyword in keywords}
    assert not lowered & HEAVY_VEHICLE_KEYWORDS
    # Лёгкие альтернативы обязаны остаться — иначе мы просто сузили поиск.
    assert {"lieferfahrer", "paketzusteller"} & lowered


def test_profile_broadening_keeps_heavy_keywords_without_the_flag() -> None:
    keywords = get_profile_fallback_keywords(
        profile_terms=("zusteller", "kurier"),
        family=RoleFamily.DRIVING,
        role_primary_de="zusteller",
        broaden=True,
        light_vehicle_only=False,
    )

    assert {keyword.casefold() for keyword in keywords} & HEAVY_VEHICLE_KEYWORDS


def test_explicit_profile_terms_are_never_dropped() -> None:
    """Термин, который пользователь сам вписал в профиль, — его осознанный выбор."""
    keywords = get_profile_fallback_keywords(
        profile_terms=("zusteller", "LKW-Fahrer", "kurier"),
        family=RoleFamily.DRIVING,
        role_primary_de="zusteller",
        broaden=True,
        light_vehicle_only=True,
    )

    assert "LKW-Fahrer" in keywords


def test_intent_fallback_drops_heavy_keywords_for_b_licence() -> None:
    intent = normalize_role_intent("курьер")
    assert intent is not None

    keywords = get_intent_fallback_keywords(
        intent=intent,
        primary_query=intent.primary_de,
        low_language=True,
        light_vehicle_only=True,
    )

    assert not {keyword.casefold() for keyword in keywords} & HEAVY_VEHICLE_KEYWORDS


def test_ru_ladder_fallback_drops_heavy_keywords_for_b_licence() -> None:
    keywords = get_fallback_keywords(
        primary_query="fahrer",
        role="водитель",
        low_language=True,
        light_vehicle_only=True,
    )

    assert not {keyword.casefold() for keyword in keywords} & HEAVY_VEHICLE_KEYWORDS


# --- Лимит попыток не должен выкидывать то, что пользователь сам записал в профиль ---

# Реальные термины профиля «Driver b – fernverkehr» (13 штук) и «AI Automation» (16 штук):
# на них лимит _MAX_FALLBACK_KEYWORDS=12 обрезал план поиска.
_PROFILE_13_TERMS = (
    "Fahrer Klasse B", "Sprinterfahrer", "Transporterfahrer", "Fahrer bis 3,5 t",
    "Fahrer Klasse B Fernverkehr", "Sprinterfahrer Fernverkehr", "Transporterfahrer Fernverkehr",
    "Fernverkehr Fahrer", "Fahrer Klasse B Direktfahrten", "Fahrer Klasse B Sonderfahrten",
    "Fahrer Klasse B Expressfahrten", "Planensprinter Fahrer", "Koffersprinter Fahrer",
)
_PROFILE_16_TERMS = tuple(f"AI Term {index}" for index in range(1, 17))


def test_explicit_profile_terms_are_never_truncated_by_the_attempt_cap() -> None:
    """План профиля из 16 терминов должен отрабатывать целиком, а не первыми 12."""
    keywords = get_profile_fallback_keywords(
        profile_terms=(_PROFILE_16_TERMS[0], *_PROFILE_16_TERMS),
        family=RoleFamily.GENERIC,
        broaden=True,
        light_vehicle_only=False,
    )

    plan = {_PROFILE_16_TERMS[0], *keywords}
    assert set(_PROFILE_16_TERMS) <= plan


def test_a_different_typed_query_does_not_push_a_profile_term_out_of_the_plan() -> None:
    """Регрессия: введённый запрос занимал слот и вытеснял последний термин профиля."""
    keywords = get_profile_fallback_keywords(
        profile_terms=("Lagerhelfer", *_PROFILE_13_TERMS),
        family=RoleFamily.DRIVING,
        broaden=True,
        light_vehicle_only=True,
    )

    assert set(_PROFILE_13_TERMS) <= set(keywords)


def test_generated_synonyms_are_still_capped() -> None:
    """Лимит обязан продолжать ограничивать автоподбор — иначе он бессмыслен."""
    keywords = get_profile_fallback_keywords(
        profile_terms=("lager", "lagermitarbeiter"),
        family=RoleFamily.WAREHOUSE,
        role_primary_de="lager",
        broaden=True,
        light_vehicle_only=False,
    )

    assert len(keywords) <= MAX_FALLBACK_KEYWORDS_FOR_TEST
