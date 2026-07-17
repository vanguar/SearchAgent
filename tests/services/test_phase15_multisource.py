"""PHASE 15 — Multi-source Germany search completion + source hardening.

Acceptance tests covering:
  Part 1 — multi-source fan-out, source subset selection, source state reporting
  Part 2 — cross-source dedup/merge semantics
  Part 3 — explicit profile binding end-to-end
  Part 4 — source hardening (partial failure isolation, disabled adapters)
  Part 6 — BA-only regression, progressive fallback regression
"""
from __future__ import annotations

import itertools
from collections.abc import Generator
from unittest.mock import MagicMock

import app.db.models  # noqa: F401
import pytest
from app.db.base import Base
from app.db.models.profiles import SearchProfile, UserProfile
from app.services.search_history_service import SearchHistoryService
from app.services.search_models import (
    SearchProfileContext,
    SearchRunResult,
    SearchSourceState,
)
from app.services.search_service import SearchService
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.errors import AdapterDisabledError, AdapterRequestError
from app.services.source_adapters.models import (
    AdapterSearchResponse,
    SourceRecordPreview,
    SourceSearchInput,
)
from app.services.source_adapters.registry import SourceAdapterRegistry
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Заглушки адаптеров
# ---------------------------------------------------------------------------

def _source_record(
    *,
    source_id: str,
    source_name: str,
    external_id: str,
    title: str,
    company: str = "Logistik GmbH",
    location: str = "Berlin, Deutschland",
    description: str = "Без немецкого. Смены. Сразу.",
) -> SourceRecordPreview:
    return SourceRecordPreview(
        source_id=source_id,
        source_name=source_name,
        external_id=external_id,
        source_reference=external_id,
        title=title,
        company=company,
        location=location,
        posted_at="2026-04-15",
        detail_url=f"https://example.org/jobs/{external_id}",
        raw_payload={"description": description},
    )


class StubBAAdapter(BaseSourceAdapter):
    """BA адаптер-заглушка — возвращает 2 складские вакансии."""
    source_id = "ba"
    display_name = "BA (Bundesagentur fur Arbeit)"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=(
                _source_record(source_id=self.source_id, source_name=self.display_name,
                               external_id="ba-1", title="Lagermitarbeiter/in",
                               description="Ohne Deutsch. 3 Schicht. Unterkunft. Ab sofort. Keine Erfahrung."),
                _source_record(source_id=self.source_id, source_name=self.display_name,
                               external_id="ba-2", title="Verpacker/in",
                               description="Verpackung von Lebensmitteln. Keine Vorkenntnisse."),
            ),
            total_count=2, page=search_input.page, page_size=search_input.page_size,
            raw_payload={"source": "ba"},
        )


class StubCareerjetAdapter(BaseSourceAdapter):
    """Careerjet адаптер-заглушка — возвращает 2 вакансии (одна дублирует BA)."""
    source_id = "careerjet"
    display_name = "Careerjet"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=(
                # та же вакансия, что ba-1 (дубль для проверки dedup)
                _source_record(source_id=self.source_id, source_name=self.display_name,
                               external_id="cj-1", title="Lagermitarbeiter (m/w/d)",
                               company="Logistik GmbH",
                               description="Ohne Deutsch. 3 Schicht. Unterkunft. Ab sofort. Keine Erfahrung."),
                # уникальная вакансия только в Careerjet
                _source_record(source_id=self.source_id, source_name=self.display_name,
                               external_id="cj-2", title="Produktionshelfer/in",
                               location="Hamburg, Deutschland",
                               description="Produktion. Schichtarbeit. Kein Deutsch erforderlich."),
            ),
            total_count=2, page=search_input.page, page_size=search_input.page_size,
            raw_payload={"source": "careerjet"},
        )


class StubEmptyAdapter(BaseSourceAdapter):
    """Адаптер, возвращающий 0 вакансий (нет результатов)."""
    source_id = "empty_source"
    display_name = "Empty Source"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=(),
            total_count=0, page=search_input.page, page_size=search_input.page_size,
            raw_payload={},
        )


class StubBrokenAdapter(BaseSourceAdapter):
    """Адаптер, всегда бросающий ошибку сети."""
    source_id = "broken"
    display_name = "Broken"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        raise AdapterRequestError(
            source_id=self.source_id,
            source_name=self.display_name,
            message="Временный сбой сети.",
        )


class StubDisabledAdapter(BaseSourceAdapter):
    """Адаптер, отключённый в конфиге."""
    source_id = "disabled_source"
    display_name = "Disabled Source"

    def is_enabled(self) -> bool:
        return False

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()  # бросит AdapterDisabledError


class StubProfileResolver:
    """Резолвер профиля, не обращающийся к БД."""
    def __init__(self, *, profile_id_for_check: int | None = None) -> None:
        self.resolved_profile_id: int | None = None
        self._profile_id_for_check = profile_id_for_check

    def resolve(self, *, profile_id: int | None = None) -> SearchProfileContext:
        self.resolved_profile_id = profile_id
        return SearchProfileContext(
            profile_label="Основной поиск",
            profile_source="saved",
            note_ru="Используется сохраненный профиль поиска.",
            legal_status="Section 24",
            work_authorized=True,
            german_level="basic",
            desired_roles=("склад", "логистика", "упаковка", "производство"),
            relocation_ready=True,
            shift_ok=True,
            physical_work_ok=True,
        )


def _make_service(*adapters: BaseSourceAdapter) -> SearchService:
    registry = SourceAdapterRegistry(adapters=list(adapters))
    return SearchService(registry=registry, profile_resolver=StubProfileResolver())


def _make_input(query: str = "lager") -> SourceSearchInput:
    return SourceSearchInput(query=query, location="Berlin", radius_km=25, page=1, page_size=10)


# ---------------------------------------------------------------------------
# PART 1 — Multi-source fan-out
# ---------------------------------------------------------------------------

def test_multisource_fanout_queries_all_enabled_adapters() -> None:
    """Поиск по всем включённым адаптерам: BA + Careerjet. Собираем все сырые записи."""
    svc = _make_service(StubBAAdapter(), StubCareerjetAdapter())

    result = svc.search(search_input=_make_input())

    # BA=2, Careerjet=2 → 4 сырых записей до дедупликации
    assert result.total_raw_records == 4
    assert len(result.source_states) == 2
    source_ids = {s.source_id for s in result.source_states}
    assert "ba" in source_ids
    assert "careerjet" in source_ids


def test_multisource_partial_failure_still_returns_results() -> None:
    """Если один источник упал, остальные результаты всё равно возвращаются."""
    svc = _make_service(StubBAAdapter(), StubBrokenAdapter())

    result = svc.search(search_input=_make_input())

    # BA-результаты должны быть
    assert result.total_raw_records == 2
    # Broken зафиксирован как ошибка
    broken_state = next(s for s in result.source_states if s.source_id == "broken")
    assert broken_state.status_kind == "error"
    assert broken_state.error_message is not None
    # BA зафиксирован как успех
    ba_state = next(s for s in result.source_states if s.source_id == "ba")
    assert ba_state.status_kind == "success"


def test_all_sources_failed_returns_empty_result() -> None:
    """Все источники упали → пустой результат с состояниями ошибок."""
    svc = _make_service(StubBrokenAdapter())

    result = svc.search(search_input=_make_input())

    assert result.total_raw_records == 0
    assert result.total_canonical_results == 0
    assert not result.has_results
    assert len(result.source_states) >= 1
    assert all(s.status_kind == "error" for s in result.source_states if s.error_message)


# ---------------------------------------------------------------------------
# PART 1 — Выбор подмножества источников
# ---------------------------------------------------------------------------

def test_source_subset_restricts_search_to_specified_source() -> None:
    """source_ids=("ba",) → опрашивается только BA, Careerjet не вызывается."""
    svc = _make_service(StubBAAdapter(), StubCareerjetAdapter())

    result = svc.search(search_input=_make_input(), source_ids=("ba",))

    # Только BA: 2 записи
    assert result.total_raw_records == 2
    source_ids = {s.source_id for s in result.source_states}
    assert source_ids == {"ba"}


def test_source_subset_single_source_with_partial_fanout_broken() -> None:
    """source_ids=("careerjet",) при сломанном BA → только Careerjet в результатах."""
    svc = _make_service(StubBrokenAdapter(), StubCareerjetAdapter())

    result = svc.search(search_input=_make_input(), source_ids=("careerjet",))

    assert result.total_raw_records == 2
    assert all(s.source_id == "careerjet" for s in result.source_states)


# ---------------------------------------------------------------------------
# PART 1 — Состояние источников (status_kind)
# ---------------------------------------------------------------------------

def test_source_state_success_when_adapter_returns_records() -> None:
    """status_kind='success' когда адаптер вернул записи."""
    svc = _make_service(StubBAAdapter())

    result = svc.search(search_input=_make_input())

    ba_state = next(s for s in result.source_states if s.source_id == "ba")
    assert ba_state.status_kind == "success"
    assert ba_state.status_label == "Успех"
    assert ba_state.raw_count == 2


def test_source_state_warning_when_adapter_returns_zero_records() -> None:
    """status_kind='warning' когда адаптер вернул 0 записей."""
    svc = _make_service(StubEmptyAdapter())

    result = svc.search(search_input=_make_input())

    empty_state = next(s for s in result.source_states if s.source_id == "empty_source")
    assert empty_state.status_kind == "warning"
    assert empty_state.status_label == "Нет результатов"
    assert empty_state.raw_count == 0


def test_source_state_error_on_adapter_failure() -> None:
    """status_kind='error' когда адаптер бросил исключение."""
    svc = _make_service(StubBrokenAdapter())

    result = svc.search(search_input=_make_input())

    broken_state = next(s for s in result.source_states if s.source_id == "broken")
    assert broken_state.status_kind == "error"
    assert broken_state.status_label == "Ошибка"
    assert "Временный сбой" in (broken_state.error_message or "")


def test_source_state_mixed_success_and_error() -> None:
    """BA успех + Careerjet сломан: разные status_kind в одном результате."""
    svc = _make_service(StubBAAdapter(), StubBrokenAdapter())

    result = svc.search(search_input=_make_input())

    by_id = {s.source_id: s for s in result.source_states}
    assert by_id["ba"].status_kind == "success"
    assert by_id["broken"].status_kind == "error"


# ---------------------------------------------------------------------------
# PART 2 — Cross-source dedup / слияние
# ---------------------------------------------------------------------------

def test_crosssource_dedup_merges_same_vacancy_from_two_sources() -> None:
    """Одна и та же вакансия (склад, Logistik GmbH, Berlin) из BA и Careerjet
    схлопывается в один canonical group — нет дублей в выдаче."""
    svc = _make_service(StubBAAdapter(), StubCareerjetAdapter())

    result = svc.search(search_input=_make_input())

    # BA=2, Careerjet=2 сырых → после dedup < 4 canonical
    # ba-1 и cj-1 — один и тот же лагермитарбайтер → должны слиться
    assert result.total_raw_records == 4
    assert result.total_canonical_results < 4  # как минимум один дубль схлопнулся


def test_crosssource_dedup_preserves_source_provenance() -> None:
    """Слитый canonical group хранит записи из обоих источников (provenance)."""
    svc = _make_service(StubBAAdapter(), StubCareerjetAdapter())

    result = svc.search(search_input=_make_input())

    # Ищем canonical с несколькими source_records (слитый)
    merged = [item for item in result.results if len(item.canonical_group.source_records) > 1]
    assert len(merged) >= 1, "Должен быть хотя бы один слитый canonical (BA + Careerjet)"

    merged_item = merged[0]
    source_ids_in_group = {r.source_id for r in merged_item.canonical_group.source_records}
    assert len(source_ids_in_group) == 2, "Слитый canonical должен содержать записи из обоих источников"


def test_dedup_does_not_merge_different_vacancies() -> None:
    """Разные вакансии (Lager Berlin vs Produktion Hamburg) не схлопываются."""
    svc = _make_service(StubBAAdapter(), StubCareerjetAdapter())

    result = svc.search(search_input=_make_input())

    # Produktionshelfer в Hamburg — уникальная, не должна слиться с Berlin вакансиями
    unique_items = [item for item in result.results if len(item.canonical_group.source_records) == 1]
    assert len(unique_items) >= 1


def test_translation_runs_after_dedup() -> None:
    """После дедупликации для каждого canonical group есть summary_ru/explanation_ru."""
    svc = _make_service(StubBAAdapter())

    result = svc.search(search_input=_make_input())

    for item in result.results:
        # explanation_ru всегда генерируется (не требует LLM)
        assert item.explanation_ru, f"explanation_ru пуст для {item.canonical_group.canonical_key}"


# ---------------------------------------------------------------------------
# PART 3 — Явная привязка профиля (profile binding)
# ---------------------------------------------------------------------------

def test_explicit_profile_id_passed_to_resolver() -> None:
    """profile_id=7 передаётся в profile_resolver.resolve()."""
    resolver = StubProfileResolver()
    registry = SourceAdapterRegistry(adapters=[StubBAAdapter()])
    svc = SearchService(registry=registry, profile_resolver=resolver)

    svc.search(search_input=_make_input(), profile_id=7)

    assert resolver.resolved_profile_id == 7


def test_profile_id_none_passed_as_none_to_resolver() -> None:
    """profile_id=None → resolver получает None (выбирает профиль по умолчанию)."""
    resolver = StubProfileResolver()
    registry = SourceAdapterRegistry(adapters=[StubBAAdapter()])
    svc = SearchService(registry=registry, profile_resolver=resolver)

    svc.search(search_input=_make_input(), profile_id=None)

    assert resolver.resolved_profile_id is None


def test_orchestrated_search_passes_profile_id_correctly() -> None:
    """orchestrated_search передаёт profile_id в get_profile_context без подмены."""
    resolver = StubProfileResolver()
    registry = SourceAdapterRegistry(adapters=[StubBAAdapter()])
    svc = SearchService(registry=registry, profile_resolver=resolver)
    # profile_id=42 через orchestrated_search
    svc.orchestrated_search(search_input=_make_input(), profile_id=42)

    assert resolver.resolved_profile_id == 42


# ---------------------------------------------------------------------------
# PART 3 — Привязка профиля в SearchHistoryService
# ---------------------------------------------------------------------------

@pytest.fixture()
def history_db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _make_search_run_result(*, profile_source: str = "saved") -> SearchRunResult:
    """Минимальный SearchRunResult для тестирования history-сервиса."""
    profile = SearchProfileContext(
        profile_label="Тест",
        profile_source=profile_source,  # type: ignore[arg-type]
        work_authorized=True,
        desired_roles=("склад",),
    )
    return SearchRunResult(
        profile=profile,
        source_states=(SearchSourceState(
            source_id="ba", source_name="BA", status_label="Успех", status_kind="success",
        ),),
        results=(),
        hot_results=(),
        maybe_results=(),
        rejected_results=(),
        total_raw_records=0,
        total_normalized_records=0,
        total_canonical_results=0,
    )


def test_search_history_saved_to_explicit_profile_id(history_db: Session) -> None:
    """record_search_result с явным profile_id сохраняет SearchRun к правильному профилю."""
    from app.db.models.search import SearchRun

    user = UserProfile(
        display_name="Тест", country_code="DE", city="Berlin",
        work_authorized=True, raw_profile_text="склад",
    )
    history_db.add(user)
    history_db.flush()

    profile_a = SearchProfile(user_profile_id=user.id, name="Профиль A", is_active=True)
    profile_b = SearchProfile(user_profile_id=user.id, name="Профиль B", is_active=True, is_default=True)
    history_db.add_all([profile_a, profile_b])
    history_db.commit()

    svc = SearchHistoryService(session_factory=lambda: history_db)
    result = _make_search_run_result()

    # явно указываем profile_a → история должна привязаться к нему, не к default profile_b
    svc.persist_search_result(history_db, result=result, profile_id=profile_a.id)
    history_db.commit()

    runs = history_db.query(SearchRun).all()
    assert len(runs) == 1
    assert runs[0].search_profile_id == profile_a.id, (
        "SearchRun должен ссылаться на явно выбранный profile_a, не на default profile_b"
    )


def test_search_history_skipped_for_degraded_profile() -> None:
    """record_search_result не записывает ничего, если профиль degraded (нет БД)."""

    def _fail_factory() -> Session:
        raise AssertionError("БД не должна вызываться при degraded-профиле")

    svc = SearchHistoryService(session_factory=_fail_factory)  # type: ignore[arg-type]
    result = _make_search_run_result(profile_source="degraded")

    # Не должно бросить исключение и не должно обратиться к БД
    svc.record_search_result(result=result, profile_id=None)


# ---------------------------------------------------------------------------
# PART 4 — Source hardening
# ---------------------------------------------------------------------------

def test_disabled_adapter_raises_disabled_error_when_searched_directly() -> None:
    """Отключённый адаптер при вызове ensure_enabled() бросает AdapterDisabledError."""
    adapter = StubDisabledAdapter()

    with pytest.raises(AdapterDisabledError):
        adapter.search(_make_input())


def test_disabled_adapter_excluded_from_default_fanout() -> None:
    """Отключённый адаптер не включается в поиск по умолчанию (source_ids=())."""
    svc = _make_service(StubBAAdapter(), StubDisabledAdapter())

    # source_ids=() → только enabled; Disabled не должен вызываться вообще
    result = svc.search(search_input=_make_input(), source_ids=())

    source_ids_in_result = {s.source_id for s in result.source_states}
    assert "disabled_source" not in source_ids_in_result


def test_unexpected_non_adapter_exception_does_not_crash_search() -> None:
    """Неожиданное исключение (не SourceAdapterError) в адаптере изолируется."""

    class UnexpectedBrokenAdapter(BaseSourceAdapter):
        source_id = "unexpected_broken"
        display_name = "Unexpected Broken"

        def is_enabled(self) -> bool:
            return True

        def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
            raise RuntimeError("Совершенно неожиданная ошибка")

    svc = _make_service(StubBAAdapter(), UnexpectedBrokenAdapter())

    result = svc.search(search_input=_make_input())

    # BA должен вернуть результаты
    assert result.total_raw_records == 2
    # Сломанный адаптер должен быть в error-состоянии
    broken = next((s for s in result.source_states if s.source_id == "unexpected_broken"), None)
    assert broken is not None
    assert broken.status_kind == "error"


# ---------------------------------------------------------------------------
# PART 6 — Регрессия: BA-only search
# ---------------------------------------------------------------------------

def test_ba_only_search_returns_correctly_bucketed_results() -> None:
    """BA-only поиск — базовый регрессионный тест без изменений."""
    svc = _make_service(StubBAAdapter())

    result = svc.search(search_input=_make_input())

    assert result.total_raw_records == 2
    assert result.total_canonical_results == 2
    # Хотя бы одна hot или maybe вакансия
    assert len(result.hot_results) + len(result.maybe_results) >= 1
    # Все результаты имеют explanation_ru
    for item in result.results:
        assert item.explanation_ru.startswith(("Подходит", "С осторожностью", "Скорее не подходит"))


# ---------------------------------------------------------------------------
# PART 6 — Регрессия: progressive fallback не сломан после multi-source
# ---------------------------------------------------------------------------

def test_progressive_fallback_still_works_multisource() -> None:
    """orchestrated_search запускает fallback-попытки при пустом primary — multi-source не сломал."""
    good = SearchRunResult(
        profile=SearchProfileContext(
            profile_label="Тест", profile_source="saved",
            work_authorized=True, desired_roles=("склад",),
        ),
        source_states=(SearchSourceState(
            source_id="ba", source_name="BA", status_label="Успех", status_kind="success",
        ),),
        results=tuple(MagicMock(bucket="hot") for _ in range(2))
            + tuple(MagicMock(bucket="maybe") for _ in range(3)),
        hot_results=tuple(MagicMock(bucket="hot") for _ in range(2)),
        maybe_results=tuple(MagicMock(bucket="maybe") for _ in range(3)),
        rejected_results=(),
        total_raw_records=5,
        total_normalized_records=5,
        total_canonical_results=5,
    )
    empty = SearchRunResult(
        profile=good.profile,
        source_states=good.source_states,
        results=(), hot_results=(), maybe_results=(), rejected_results=(),
        total_raw_records=0, total_normalized_records=0, total_canonical_results=0,
    )

    svc = SearchService()
    svc.get_profile_context = MagicMock(return_value=SearchProfileContext(
        profile_label="Тест", profile_source="saved",
        work_authorized=True, desired_roles=("склад",),
    ))
    svc._resolve_source_ids = MagicMock(return_value=("ba",))
    svc.search = MagicMock(side_effect=itertools.chain([empty, good], itertools.repeat(good)))

    result = svc.orchestrated_search(search_input=_make_input("unknown_role"))

    assert svc.search.call_count >= 2, "Fallback должен был запуститься"
    assert result.attempt_summary is not None
    assert result.attempt_summary.fallback_used
    assert len(result.hot_results) == 2
    assert len(result.maybe_results) == 3


# ---------------------------------------------------------------------------
# PART 6 — Регрессия: stats/email flows не задеты (smoke)
# ---------------------------------------------------------------------------

def test_search_result_has_expected_fields_for_stats_and_history() -> None:
    """SearchRunResult содержит все поля, нужные stats и history сервисам."""
    svc = _make_service(StubBAAdapter())

    result = svc.search(search_input=_make_input())

    # Поля, которые использует SearchHistoryService
    assert hasattr(result, "total_raw_records")
    assert hasattr(result, "total_canonical_results")
    assert hasattr(result, "hot_results")
    assert hasattr(result, "maybe_results")
    assert hasattr(result, "rejected_results")
    assert hasattr(result, "profile")
    assert result.profile.profile_source in ("saved", "fallback", "degraded")
