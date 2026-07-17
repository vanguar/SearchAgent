"""Deterministic tests for multilingual role-intent normalization."""
from __future__ import annotations

import pytest
from app.services.role_family import RoleFamily
from app.services.role_intent import normalize_role_intent

# ---------------------------------------------------------------------------
# DRIVING family
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected_family,expected_de", [
    ("курьер", RoleFamily.DRIVING, "kurier"),
    ("водитель", RoleFamily.DRIVING, "fahrer"),
    ("водитель-курьер", RoleFamily.DRIVING, "lieferfahrer"),
    ("доставщик", RoleFamily.DRIVING, "zusteller"),
    ("доставка", RoleFamily.DRIVING, "zusteller"),
    ("courier", RoleFamily.DRIVING, "kurier"),
    ("driver", RoleFamily.DRIVING, "fahrer"),
    ("delivery driver", RoleFamily.DRIVING, "lieferfahrer"),
    ("delivery", RoleFamily.DRIVING, "zusteller"),
    ("lieferfahrer", RoleFamily.DRIVING, "lieferfahrer"),
    ("zusteller", RoleFamily.DRIVING, "zusteller"),
    ("fahrer", RoleFamily.DRIVING, "fahrer"),
    ("kurier", RoleFamily.DRIVING, "kurier"),
])
def test_normalize_role_intent_driving(query: str, expected_family: RoleFamily, expected_de: str) -> None:
    result = normalize_role_intent(query)
    assert result is not None, f"Expected intent for {query!r}, got None"
    assert result.family == expected_family
    assert result.primary_de == expected_de


def test_driving_intent_has_synonyms() -> None:
    result = normalize_role_intent("курьер")
    assert result is not None
    assert len(result.synonyms_de) > 0
    assert "fahrer" in result.synonyms_de or "zusteller" in result.synonyms_de


# ---------------------------------------------------------------------------
# WAREHOUSE family
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected_family,expected_de", [
    ("склад", RoleFamily.WAREHOUSE, "lager"),
    ("логистика", RoleFamily.WAREHOUSE, "logistik"),
    ("упаковка", RoleFamily.WAREHOUSE, "verpacker"),
    ("комплектовщик", RoleFamily.WAREHOUSE, "kommissionierer"),
    ("грузчик", RoleFamily.WAREHOUSE, "lagerhelfer"),
    ("кладовщик", RoleFamily.WAREHOUSE, "lagerist"),
    ("warehouse", RoleFamily.WAREHOUSE, "lager"),
    ("packer", RoleFamily.WAREHOUSE, "verpacker"),
    ("logistics", RoleFamily.WAREHOUSE, "logistik"),
    ("lager", RoleFamily.WAREHOUSE, "lager"),
    ("verpacker", RoleFamily.WAREHOUSE, "verpacker"),
    ("kommissionierer", RoleFamily.WAREHOUSE, "kommissionierer"),
])
def test_normalize_role_intent_warehouse(query: str, expected_family: RoleFamily, expected_de: str) -> None:
    result = normalize_role_intent(query)
    assert result is not None
    assert result.family == expected_family
    assert result.primary_de == expected_de


# ---------------------------------------------------------------------------
# PRODUCTION family
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected_family,expected_de", [
    ("производство", RoleFamily.PRODUCTION, "produktion"),
    ("сборщик", RoleFamily.PRODUCTION, "montagemitarbeiter"),
    ("оператор", RoleFamily.PRODUCTION, "maschinenführer"),
    ("production", RoleFamily.PRODUCTION, "produktion"),
    ("produktion", RoleFamily.PRODUCTION, "produktion"),
    ("produktionshelfer", RoleFamily.PRODUCTION, "produktionshelfer"),
])
def test_normalize_role_intent_production(query: str, expected_family: RoleFamily, expected_de: str) -> None:
    result = normalize_role_intent(query)
    assert result is not None
    assert result.family == expected_family
    assert result.primary_de == expected_de


# ---------------------------------------------------------------------------
# IT family
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected_family,expected_de", [
    ("программист", RoleFamily.IT, "softwareentwickler"),
    ("разработчик", RoleFamily.IT, "softwareentwickler"),
    ("developer", RoleFamily.IT, "softwareentwickler"),
    ("software developer", RoleFamily.IT, "softwareentwickler"),
    ("devops", RoleFamily.IT, "devops"),
    ("it support", RoleFamily.IT, "it-support"),
    ("softwareentwickler", RoleFamily.IT, "softwareentwickler"),
])
def test_normalize_role_intent_it(query: str, expected_family: RoleFamily, expected_de: str) -> None:
    result = normalize_role_intent(query)
    assert result is not None
    assert result.family == expected_family
    assert result.primary_de == expected_de


def test_normalize_role_intent_it_word_boundary() -> None:
    """'it' as standalone word → IT, but 'it' inside longer word → no match on IT."""
    assert normalize_role_intent("it") is not None
    assert normalize_role_intent("it")is not None
    assert normalize_role_intent("it").family == RoleFamily.IT  # type: ignore[union-attr]
    # "quality" contains "it" as substring but should NOT match IT
    # quality doesn't appear in the map at all → None
    assert normalize_role_intent("quality control") is None


# ---------------------------------------------------------------------------
# Greedy longest-match priority
# ---------------------------------------------------------------------------

def test_longer_key_wins_over_shorter() -> None:
    """'водитель-курьер' must match before 'водитель' or 'курьер'."""
    result = normalize_role_intent("водитель-курьер")
    assert result is not None
    assert result.primary_de == "lieferfahrer"  # longer key, not fahrer or kurier


def test_delivery_driver_beats_delivery() -> None:
    result = normalize_role_intent("delivery driver")
    assert result is not None
    assert result.primary_de == "lieferfahrer"


def test_software_developer_beats_developer() -> None:
    result = normalize_role_intent("software developer")
    assert result is not None
    assert result.primary_de == "softwareentwickler"
    assert result.family == RoleFamily.IT


# ---------------------------------------------------------------------------
# None for unknown queries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "",
    "   ",
    "неизвестная профессия",
    "xyz123",
    "quality control",
])
def test_normalize_role_intent_returns_none_for_unknown(query: str) -> None:
    assert normalize_role_intent(query) is None


# ---------------------------------------------------------------------------
# CLEANING, KITCHEN, HEALTHCARE, CONSTRUCTION, OFFICE, SALES, SECURITY
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected_family", [
    ("уборщик", RoleFamily.CLEANING),
    ("cleaner", RoleFamily.CLEANING),
    ("reinigungskraft", RoleFamily.CLEANING),
    ("повар", RoleFamily.KITCHEN),
    ("cook", RoleFamily.KITCHEN),
    ("официант", RoleFamily.KITCHEN),
    ("медсестра", RoleFamily.HEALTHCARE),
    ("nurse", RoleFamily.HEALTHCARE),
    ("pflegekraft", RoleFamily.HEALTHCARE),
    ("строитель", RoleFamily.CONSTRUCTION),
    ("электрик", RoleFamily.CONSTRUCTION),
    ("electrician", RoleFamily.CONSTRUCTION),
    ("бухгалтер", RoleFamily.OFFICE),
    ("accountant", RoleFamily.OFFICE),
    ("продавец", RoleFamily.SALES),
    ("cashier", RoleFamily.SALES),
    ("охранник", RoleFamily.SECURITY),
    ("security guard", RoleFamily.SECURITY),
])
def test_normalize_role_intent_other_families(query: str, expected_family: RoleFamily) -> None:
    result = normalize_role_intent(query)
    assert result is not None
    assert result.family == expected_family


# ---------------------------------------------------------------------------
# RoleIntent dataclass invariants
# ---------------------------------------------------------------------------

def test_role_intent_primary_de_not_in_synonyms() -> None:
    """primary_de should not also appear in synonyms_de (would waste a fallback slot)."""
    result = normalize_role_intent("склад")
    assert result is not None
    assert result.primary_de not in result.synonyms_de


def test_role_intent_is_frozen() -> None:
    intent = normalize_role_intent("курьер")
    assert intent is not None
    with pytest.raises(Exception):
        intent.family = RoleFamily.GENERIC  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Added rarer roles (RU/UA/EN -> DE) — verified German keywords
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected_family,expected_de", [
    ("таксист", RoleFamily.DRIVING, "taxifahrer"),
    ("дальнобойщик", RoleFamily.DRIVING, "berufskraftfahrer"),
    ("водитель погрузчика", RoleFamily.WAREHOUSE, "staplerfahrer"),
    ("бармен", RoleFamily.KITCHEN, "barkeeper"),
    ("бариста", RoleFamily.KITCHEN, "barista"),
    ("пекарь", RoleFamily.KITCHEN, "bäcker"),
    ("кондитер", RoleFamily.KITCHEN, "konditor"),
    ("мойщик посуды", RoleFamily.KITCHEN, "spülkraft"),
    ("мясник", RoleFamily.KITCHEN, "metzger"),
    ("врач", RoleFamily.HEALTHCARE, "arzt"),
    ("физиотерапевт", RoleFamily.HEALTHCARE, "physiotherapeut"),
    ("стоматолог", RoleFamily.HEALTHCARE, "zahnarzt"),
    ("маляр", RoleFamily.CONSTRUCTION, "maler"),
    ("плиточник", RoleFamily.CONSTRUCTION, "fliesenleger"),
    ("штукатур", RoleFamily.CONSTRUCTION, "stuckateur"),
    ("столяр", RoleFamily.CONSTRUCTION, "tischler"),
    ("каменщик", RoleFamily.CONSTRUCTION, "maurer"),
    ("кровельщик", RoleFamily.CONSTRUCTION, "dachdecker"),
    ("сантехник", RoleFamily.CONSTRUCTION, "anlagenmechaniker"),
    ("автомеханик", RoleFamily.CONSTRUCTION, "kfz-mechaniker"),
    ("швея", RoleFamily.PRODUCTION, "näherin"),
    ("сборщик урожая", RoleFamily.AGRICULTURE, "erntehelfer"),
    ("оператор колл-центра", RoleFamily.OFFICE, "callcenter"),
    ("сторож", RoleFamily.SECURITY, "sicherheitsdienst"),
    ("разнорабочий", RoleFamily.GENERIC, "helfer"),
    ("plumber", RoleFamily.CONSTRUCTION, "anlagenmechaniker"),
    ("painter", RoleFamily.CONSTRUCTION, "maler"),
    ("baker", RoleFamily.KITCHEN, "bäcker"),
])
def test_normalize_role_intent_added_rare_roles(query: str, expected_family: RoleFamily, expected_de: str) -> None:
    result = normalize_role_intent(query)
    assert result is not None, f"Expected intent for {query!r}, got None"
    assert result.family == expected_family
    assert result.primary_de == expected_de


@pytest.mark.parametrize("query,expected_de", [
    # Longer added keys must not hijack these existing single-word queries.
    ("курьер", "kurier"),
    ("водитель", "fahrer"),
    ("оператор", "maschinenführer"),
    ("сборщик", "montagemitarbeiter"),
    ("грузчик", "lagerhelfer"),
])
def test_added_roles_do_not_regress_existing_queries(query: str, expected_de: str) -> None:
    result = normalize_role_intent(query)
    assert result is not None
    assert result.primary_de == expected_de
