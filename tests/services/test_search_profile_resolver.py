from __future__ import annotations

from types import SimpleNamespace

import app.services.search_profile_resolver as search_profile_resolver_module
from app.services.search_models import SearchProfileContext
from app.services.search_profile_resolver import DatabaseSearchProfileResolver, interpret_legal_status
from sqlalchemy.exc import OperationalError


def _build_profile(**overrides: object) -> SearchProfileContext:
    payload = {
        "profile_label": "Основной поиск",
        "profile_source": "saved",
        "note_ru": "Используется сохраненный профиль поиска.",
        "work_authorized": True,
        "desired_roles": ("склад", "упаковка", "производство"),
        "relocation_ready": True,
        "shift_ok": True,
        "physical_work_ok": True,
    }
    payload.update(overrides)
    return SearchProfileContext(**payload)


class _FakeQuery:
    def __init__(self, result: object) -> None:
        self._result = result

    def order_by(self, *args: object) -> _FakeQuery:
        return self

    def filter(self, *args: object) -> _FakeQuery:
        return self

    def first(self) -> object:
        return self._result


class _FakeSession:
    def __init__(self, *, search_profile: object, user_profile: object) -> None:
        self._search_profile = search_profile
        self._user_profile = user_profile

    def query(self, model: object) -> _FakeQuery:
        model_name = getattr(model, "__name__", "")
        if model_name == "SearchProfile":
            return _FakeQuery(self._search_profile)
        return _FakeQuery(self._user_profile)

    def get(self, model: object, pk: object) -> object:
        model_name = getattr(model, "__name__", "")
        if model_name == "SearchProfile":
            sp = self._search_profile
            if sp is not None and getattr(sp, "id", None) == pk:
                return sp
            return None
        if model_name == "UserProfile":
            up = self._user_profile
            if up is not None and getattr(up, "id", None) == pk:
                return up
            return None
        return None

    def close(self) -> None:
        return None


def test_search_profile_context_low_german_is_false_for_missing_or_unknown_values() -> None:
    assert _build_profile(german_level=None).low_german is False
    assert _build_profile(german_level="unknown").low_german is False
    assert _build_profile(german_level="не указано").low_german is False
    assert _build_profile(german_level="A1").low_german is True


def test_interpret_legal_status_maps_explicit_section24_variants() -> None:
    for raw_value in ("Section 24", "temporary protection", "temporary_protection", "по 24 параграфу", "§ 24"):
        interpretation = interpret_legal_status(raw_value)
        assert interpretation.code == "section_24"
        assert interpretation.section24 is True
        assert interpretation.work_authorized is True


def test_interpret_legal_status_maps_canonical_section24_forms() -> None:
    for raw_value in ("section_24", "section24", "section-24"):
        interpretation = interpret_legal_status(raw_value)
        assert interpretation.code == "section_24"
        assert interpretation.section24 is True
        assert interpretation.work_authorized is True


def test_interpret_legal_status_does_not_match_unrelated_number_24() -> None:
    interpretation = interpret_legal_status("Worker 24 shift schedule")

    assert interpretation.code is None
    assert interpretation.section24 is False
    assert interpretation.work_authorized is False


def test_database_search_profile_resolver_uses_section24_interpretation_for_work_authorization() -> None:
    search_profile = SimpleNamespace(
        id=1,
        user_profile_id=7,
        name="Основной поиск",
        desired_roles=["склад", "упаковка"],
        excluded_roles=[],
        preferred_locations=["Deutschland"],
        relocation_ready=True,
        shift_ok=True,
        physical_work_ok=True,
        housing_needed=False,
        start_availability_text=None,
        driver_license="B",
        no_german_required=False,
        search_query_terms=None,
    )
    user_profile = SimpleNamespace(
        id=7,
        display_name="Профиль",
        city="Tribsees",
        legal_status="temporary protection",
        work_authorized=False,
        german_level=None,
        english_level=None,
    )
    resolver = DatabaseSearchProfileResolver(
        session_factory=lambda: _FakeSession(search_profile=search_profile, user_profile=user_profile)
    )

    result = resolver.resolve()

    assert result.work_authorized is True
    assert result.section24_interpreted is True
    assert result.low_german is False
    assert result.driver_license == "B"


def test_database_search_profile_resolver_opens_session_without_db_reachability_precheck() -> None:
    assert not hasattr(search_profile_resolver_module, "_database_server_is_reachable")

    search_profile = SimpleNamespace(
        id=2,
        user_profile_id=8,
        name="Резервный поиск",
        desired_roles=["склад"],
        excluded_roles=[],
        preferred_locations=["Deutschland"],
        relocation_ready=True,
        shift_ok=True,
        physical_work_ok=True,
        housing_needed=False,
        start_availability_text=None,
        no_german_required=False,
        search_query_terms=None,
    )
    user_profile = SimpleNamespace(
        id=8,
        display_name="Профиль 2",
        city="Tribsees",
        legal_status="section24",
        work_authorized=False,
        german_level="A1",
        english_level=None,
    )
    calls = {"count": 0}

    def _session_factory() -> _FakeSession:
        calls["count"] += 1
        return _FakeSession(search_profile=search_profile, user_profile=user_profile)

    resolver = DatabaseSearchProfileResolver(session_factory=_session_factory)

    result = resolver.resolve()

    assert calls["count"] == 1
    assert result.profile_source == "saved"
    assert result.section24_interpreted is True
    assert result.work_authorized is True


def test_database_search_profile_resolver_returns_degraded_profile_when_db_is_unreachable() -> None:
    def _session_factory() -> _FakeSession:
        raise OperationalError(
            statement="select 1",
            params={},
            orig=RuntimeError("connection timeout expired"),
        )

    resolver = DatabaseSearchProfileResolver(session_factory=_session_factory)

    result = resolver.resolve()

    assert result.profile_source == "degraded"
    assert result.profile_label == "Временный локальный профиль"
    assert "нет соединения с базой данных" in (result.note_ru or "")


def test_database_search_profile_resolver_explicit_profile_id_loads_correct_profile() -> None:
    search_profile = SimpleNamespace(
        id=42,
        user_profile_id=7,
        name="Конкретный профиль",
        desired_roles=["склад"],
        excluded_roles=[],
        preferred_locations=[],
        relocation_ready=None,
        shift_ok=None,
        physical_work_ok=None,
        housing_needed=None,
        start_availability_text=None,
        no_german_required=False,
        search_query_terms=None,
    )
    user_profile = SimpleNamespace(
        id=7,
        display_name="Профиль",
        city="Tribsees",
        legal_status=None,
        work_authorized=True,
        german_level=None,
        english_level=None,
    )
    resolver = DatabaseSearchProfileResolver(
        session_factory=lambda: _FakeSession(search_profile=search_profile, user_profile=user_profile)
    )

    result = resolver.resolve(profile_id=42)

    assert result.profile_source == "saved"
    assert result.profile_label == "Конкретный профиль"


def test_database_search_profile_resolver_explicit_profile_id_not_found_returns_fallback() -> None:
    resolver = DatabaseSearchProfileResolver(
        session_factory=lambda: _FakeSession(search_profile=None, user_profile=None)
    )
    result = resolver.resolve(profile_id=999)
    assert result.profile_source == "fallback"
