from __future__ import annotations

import dataclasses

from app.main import create_app
from app.services.normalization_models import CanonicalVacancyGroup, NormalizedVacancyRecord
from app.services.normalizer import VacancyNormalizer
from app.services.search_models import (
    DedupPreviewItem,
    FilterResult,
    RuleHit,
    ScoreResult,
    SearchProfileContext,
    SearchQueryResultGroup,
    SearchResultItem,
    SearchRunResult,
    SearchSourceState,
    VacancySignalSnapshot,
)
from app.services.search_service import SearchService
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.models import (
    AdapterSearchResponse,
    SourceAdapterDescriptor,
    SourceRecordPreview,
    SourceSearchInput,
)
from app.services.source_adapters.registry import SourceAdapterRegistry
from app.web.routes.jobs import _SearchTask, get_profile_catalog_service, get_search_history_service, get_search_service
from fastapi.testclient import TestClient


def _normalize_record(
    *,
    external_id: str,
    title: str,
    detail_url: str | None,
    source_id: str = "ba",
    source_name: str = "BA (Bundesagentur fur Arbeit)",
    company: str = "Logistik Nord GmbH",
    location: str = "10115 Berlin, Deutschland",
    description: str = "Ohne Deutsch. 3 Schicht. Unterkunft vorhanden. Ab sofort.",
) -> NormalizedVacancyRecord:
    return VacancyNormalizer().normalize_source_record(
        SourceRecordPreview(
            source_id=source_id,
            source_name=source_name,
            external_id=external_id,
            source_reference=external_id,
            title=title,
            company=company,
            location=location,
            posted_at="2026-04-15",
            detail_url=detail_url,
            raw_payload={"description": description},
        )
    )


def _build_item(
    *,
    canonical_key: str,
    primary_record: NormalizedVacancyRecord,
    source_records: tuple[NormalizedVacancyRecord, ...],
    explanation_ru: str,
    summary_ru: str,
    translated_title_ru: str,
) -> SearchResultItem:
    canonical = CanonicalVacancyGroup(
        canonical_key=canonical_key,
        normalized_title=primary_record.normalized_title,
        company_name=primary_record.normalized_company,
        location_text=primary_record.normalized_location.normalized_text,
        country_code=primary_record.normalized_location.country_code,
        city=primary_record.normalized_location.city,
        posted_date=primary_record.posted_date,
        language_signals=primary_record.language_signals,
        source_records=source_records,
        provenance=tuple(record.source_record_key for record in source_records),
    )
    return SearchResultItem(
        canonical_group=canonical,
        primary_record=primary_record,
        signals=VacancySignalSnapshot(
            combined_text="lager ohne deutsch schicht unterkunft ab sofort",
            low_language_signal=True,
            shift_signal=True,
            relocation_signal=True,
            immediate_start_signal=True,
        ),
        filter_result=FilterResult(
            decision="allow",
            positive_hits=(
                RuleHit(code="warehouse_family", label_ru="складская роль"),
                RuleHit(code="shift_fit", label_ru="смены допустимы"),
            ),
        ),
        score_result=ScoreResult(
            score=92,
            positive_hits=(RuleHit(code="priority_role", label_ru="целевая складская или производственная роль", weight=26),),
        ),
        bucket="hot",
        explanation_ru=explanation_ru,
        summary_ru=summary_ru,
        translated_title_ru=translated_title_ru,
    )


def _build_result(
    *,
    profile_source: str = "saved",
    note_ru: str = "Используется сохраненный профиль поиска.",
) -> SearchRunResult:
    direct_record = _normalize_record(
        external_id="10000-1234567890-S",
        title="Lagermitarbeiter/in",
        detail_url="https://example.org/jobs/10000-1234567890-S",
    )
    fallback_primary_record = _normalize_record(
        external_id="10000-2234567890-S",
        title="Produktionshelfer/in",
        detail_url="   ",
    )
    fallback_secondary_record = _normalize_record(
        external_id="cj-2234567890",
        title="Produktionshelfer/in",
        detail_url="https://example.org/jobs/cj-2234567890",
        source_id="careerjet",
        source_name="Careerjet",
    )
    missing_link_record = _normalize_record(
        external_id="10000-3234567890-S",
        title="Verpacker/in",
        detail_url=None,
    )

    direct_item = _build_item(
        canonical_key="canonical-1",
        primary_record=direct_record,
        source_records=(direct_record,),
        explanation_ru="Подходит: складская роль, смены допустимы, низкий языковой барьер.",
        summary_ru="Сотрудник склада. Языковой барьер невысокий, есть смены.",
        translated_title_ru="Сотрудник склада",
    )
    fallback_item = _build_item(
        canonical_key="canonical-2",
        primary_record=fallback_primary_record,
        source_records=(fallback_primary_record, fallback_secondary_record),
        explanation_ru="Подходит: производственная роль, смены допустимы, можно быстро начать.",
        summary_ru="Производственный помощник. Есть альтернативная ссылка на источник.",
        translated_title_ru="Производственный помощник",
    )
    missing_link_item = _build_item(
        canonical_key="canonical-3",
        primary_record=missing_link_record,
        source_records=(missing_link_record,),
        explanation_ru="Подходит: упаковка и физическая работа без сложных требований.",
        summary_ru="Упаковщик. Источник не отдал рабочую ссылку на оригинал.",
        translated_title_ru="Упаковщик",
    )

    profile = SearchProfileContext(
        profile_label="Основной поиск" if profile_source == "saved" else "Временный локальный профиль",
        profile_source=profile_source,  # type: ignore[arg-type]
        note_ru=note_ru,
        legal_status="Section 24",
        work_authorized=True,
        german_level="basic",
        desired_roles=("склад", "логистика", "упаковка", "производство"),
        relocation_ready=True,
        shift_ok=True,
        physical_work_ok=True,
    )
    return SearchRunResult(
        profile=profile,
        source_states=(
            SearchSourceState(
                source_id="ba",
                source_name="BA (Bundesagentur fur Arbeit)",
                status_label="Включен",
                raw_count=3,
                total_count=3,
                normalized_count=3,
                canonical_count=3,
            ),
            SearchSourceState(
                source_id="careerjet",
                source_name="Careerjet",
                status_label="Включен",
                raw_count=1,
                total_count=1,
                normalized_count=1,
                canonical_count=1,
            ),
        ),
        results=(direct_item, fallback_item, missing_link_item),
        hot_results=(direct_item, fallback_item, missing_link_item),
        deduped_preview_items=(
            DedupPreviewItem(
                canonical_key="canonical-1",
                title="Lagermitarbeiter/in",
                company_name="Logistik Nord GmbH",
                location_text="10115 Berlin, Deutschland",
                source_name="BA (Bundesagentur fur Arbeit)",
                source_count=1,
                original_url="https://example.org/jobs/10000-1234567890-S",
            ),
            DedupPreviewItem(
                canonical_key="canonical-2",
                title="Produktionshelfer/in",
                company_name="Logistik Nord GmbH",
                location_text="10115 Berlin, Deutschland",
                source_name="Careerjet",
                source_count=2,
                original_url="https://example.org/jobs/cj-2234567890",
            ),
            DedupPreviewItem(
                canonical_key="canonical-3",
                title="Verpacker/in",
                company_name="Logistik Nord GmbH",
                location_text="10115 Berlin, Deutschland",
                source_name="BA (Bundesagentur fur Arbeit)",
                source_count=1,
                original_url=None,
            ),
        ),
        total_raw_records=4,
        total_normalized_records=4,
        total_canonical_results=3,
    )


class FakeSearchService:
    def __init__(
        self,
        *,
        raise_on_profile_context: bool = False,
        profile_context: SearchProfileContext | None = None,
        search_result: SearchRunResult | None = None,
    ) -> None:
        self.last_search_input: SourceSearchInput | None = None
        self.last_source_ids: tuple[str, ...] | None = None
        self.profile_context_calls = 0
        self.raise_on_profile_context = raise_on_profile_context
        self.profile_context = profile_context or _build_result().profile
        self.search_result = search_result or _build_result()

    def list_sources(self) -> tuple[SourceAdapterDescriptor, ...]:
        return (
            SourceAdapterDescriptor(
                source_id="ba",
                display_name="BA (Bundesagentur fur Arbeit)",
                enabled=True,
                status_label="Включен",
            ),
            SourceAdapterDescriptor(
                source_id="careerjet",
                display_name="Careerjet",
                enabled=True,
                status_label="Не работает",
                status_kind="error",
                status_detail="HTTP 403 Unauthorized access from IP.",
            ),
            SourceAdapterDescriptor(
                source_id="remotive",
                display_name="Remotive",
                enabled=True,
                status_label="Готов",
                status_kind="success",
                global_remote=True,
            ),
            SourceAdapterDescriptor(
                source_id="hh",
                display_name="HeadHunter API",
                enabled=True,
                status_label="Готов",
                status_kind="success",
                global_remote=True,
            ),
            SourceAdapterDescriptor(
                source_id="dou_rss",
                display_name="DOU Jobs RSS",
                enabled=True,
                status_label="Готов",
                status_kind="success",
                global_remote=True,
            ),
            SourceAdapterDescriptor(
                source_id="djinni_rss",
                display_name="Djinni Jobs RSS",
                enabled=True,
                status_label="Готов",
                status_kind="success",
                global_remote=True,
            ),
        )

    def resolve_source_ids_for_search(
        self,
        *,
        source_ids: tuple[str, ...] = (),
        search_mode: str = "germany_local",
        source_scope: str = "western",
    ) -> tuple[str, ...]:
        if source_ids:
            return source_ids
        russian_source_ids = {"hh", "dou_rss", "djinni_rss"}
        if source_scope == "russian":
            return tuple(
                source.source_id
                for source in self.list_sources()
                if source.enabled
                and source.status_kind != "error"
                and source.source_id in russian_source_ids
            )
        return tuple(
            source.source_id
            for source in self.list_sources()
            if source.enabled
            and source.status_kind != "error"
            and source.source_id not in russian_source_ids
            and (
                (search_mode == "remote_worldwide" and source.global_remote)
                or (search_mode != "remote_worldwide" and not source.global_remote)
            )
        )

    def get_profile_context(self, *, profile_id: int | None = None) -> SearchProfileContext:
        self.profile_context_calls += 1
        if self.raise_on_profile_context:
            raise AssertionError("get_profile_context should not be called during initial /jobs render")
        return self.profile_context

    def search(
        self,
        *,
        search_input: SourceSearchInput,
        source_ids: tuple[str, ...] = (),
        profile: SearchProfileContext | None = None,
        profile_id: int | None = None,
    ) -> SearchRunResult:
        _ = profile
        self.last_search_input = search_input
        self.last_source_ids = source_ids
        return self.search_result

    def orchestrated_search(
        self,
        *,
        search_input: SourceSearchInput,
        source_ids: tuple[str, ...] = (),
        profile_id: int | None = None,
        feedback_memory: object | None = None,
    ) -> SearchRunResult:
        _ = profile_id, feedback_memory
        self.last_search_input = search_input
        self.last_source_ids = source_ids
        return self.search_result


class FakeSearchHistoryService:
    def __init__(self) -> None:
        self.calls = 0
        self.last_result: SearchRunResult | None = None

    def record_search_result(self, *, result: SearchRunResult, profile_id: int | None = None) -> None:
        self.calls += 1
        self.last_result = result


class FakeProfileCatalogService:
    """Stub catalog service that returns no profiles (safe for DB-less tests)."""

    def list_profiles(self, db: object) -> list:
        return []

    def resolve_default_profile_id(self, db: object) -> int | None:
        return None

    def backfill_search_fields(self, db: object) -> int:
        return 0


class _ProfileEntry:
    def __init__(self, profile: object, *, is_default: bool = True) -> None:
        self.profile = profile
        self.is_default = is_default


class RemoteProfileCatalogService:
    def __init__(self) -> None:
        self.profile = type(
            "Profile",
            (),
            {
                "id": 77,
                "name": "Remote Python",
                "desired_roles": ["Python Developer"],
                "preferred_locations": ["worldwide remote", "Germany", "EU", "USA"],
                "search_query_de": "Python",
                "search_location_de": "Deutschland",
            },
        )()

    def list_profiles(self, db: object) -> list:
        return [_ProfileEntry(self.profile)]

    def resolve_default_profile_id(self, db: object) -> int | None:
        return 77

    def backfill_search_fields(self, db: object) -> int:
        self.profile.search_location_de = "remote"
        return 1

    def get_profile(self, db: object, *, profile_id: int) -> object | None:
        return self.profile if profile_id == 77 else None


class RouteDeliveryProfileResolver:
    def resolve(self, *, profile_id: int | None = None) -> SearchProfileContext:
        return SearchProfileContext(
            profile_label="Доставка / Курьер",
            profile_source="saved",
            note_ru="Используется сохраненный профиль поиска.",
            legal_status="Section 24",
            work_authorized=True,
            german_level="basic",
            desired_roles=("Доставка", "Курьер", "Водитель"),
            preferred_locations=("Rostock",),
            relocation_ready=True,
            shift_ok=True,
            physical_work_ok=True,
        )


class RouteDeliveryAdapter(BaseSourceAdapter):
    source_id = "adzuna"
    display_name = "Adzuna"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=(
                SourceRecordPreview(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    external_id="verkaufsfahrer-rostock",
                    source_reference="verkaufsfahrer-rostock",
                    title="Verkaufsfahrer (m/w/d)",
                    company="bofrost* Dienstleistungs GmbH & Co. KG",
                    location="Rostock, Deutschland",
                    posted_at="2026-04-18",
                    detail_url="https://example.org/jobs/verkaufsfahrer",
                    raw_payload={
                        "description": (
                            "Fahrer im Aussendienst mit Lieferung. Basic German. "
                            "Flexible Schichten. Ab sofort."
                        )
                    },
                ),
            ),
            total_count=1,
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload={"source": "adzuna"},
        )


def _delivery_search_service() -> SearchService:
    return SearchService(
        registry=SourceAdapterRegistry(adapters=(RouteDeliveryAdapter(),)),
        profile_resolver=RouteDeliveryProfileResolver(),
    )


def _delivery_search_input() -> SourceSearchInput:
    return SourceSearchInput(query="Lieferfahrer", location="Rostock", page=1, page_size=8)


def test_jobs_page_renders_search_form_without_synchronous_profile_resolution() -> None:
    service = FakeSearchService(raise_on_profile_context=True)
    catalog = FakeProfileCatalogService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    app.dependency_overrides[get_profile_catalog_service] = lambda: catalog
    client = TestClient(app)

    response = client.get("/jobs")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Поиск вакансий" in response.text
    assert "Активный профиль" in response.text
    assert "Все готовые источники" in response.text
    assert 'method="post"' in response.text
    assert 'action="/jobs/search/start"' in response.text
    assert 'hx-post="/jobs/search/start"' in response.text
    assert 'data-async-form' in response.text
    assert 'hx-get="/jobs/profile-context"' in response.text
    assert 'hx-trigger="load"' in response.text
    assert "Загружаем профиль поиска..." in response.text
    assert service.profile_context_calls == 0


def test_jobs_page_prefills_remote_for_remote_worldwide_profile() -> None:
    service = FakeSearchService(raise_on_profile_context=True)
    catalog = RemoteProfileCatalogService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    app.dependency_overrides[get_profile_catalog_service] = lambda: catalog
    client = TestClient(app)

    response = client.get("/jobs")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert 'id="jobs-query"' in response.text
    assert 'value="Python"' in response.text
    assert 'id="jobs-location"' in response.text
    assert 'value="remote"' in response.text
    assert 'id="jobs-search-mode"' in response.text
    assert 'value="remote_worldwide" selected' in response.text


def test_jobs_profile_context_route_renders_profile_block() -> None:
    service = FakeSearchService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    client = TestClient(app)

    response = client.get("/jobs/profile-context")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "<html" not in response.text.lower()
    assert "Используется сохраненный профиль поиска." in response.text
    assert "Немецкий" in response.text
    assert "Право на работу" in response.text
    assert service.profile_context_calls == 1


def test_jobs_profile_context_route_renders_explicit_degraded_warning() -> None:
    degraded_profile = SearchProfileContext.degraded(
        note_ru=(
            "Сохраненный профиль временно недоступен: нет соединения с базой данных. "
            "Поиск все равно можно запускать по полям формы; будет использован безопасный базовый профиль."
        )
    )
    service = FakeSearchService(profile_context=degraded_profile)
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    client = TestClient(app)

    response = client.get("/jobs/profile-context")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "notice warning" in response.text
    assert "нет соединения с базой данных" in response.text
    assert "Загружаем профиль поиска..." not in response.text


def test_jobs_search_results_route_renders_phase7_partial() -> None:
    service = FakeSearchService()
    history_service = FakeSearchHistoryService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    app.dependency_overrides[get_search_history_service] = lambda: history_service
    client = TestClient(app)

    response = client.post(
        "/jobs/search-results",
        data={"source": "ba", "query": "lager", "location": "Berlin", "radius_km": "25"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "<html" not in response.text.lower()
    assert "Горячие вакансии" in response.text
    assert "Сотрудник склада" in response.text
    assert "Производственный помощник" in response.text
    assert "Упаковщик" in response.text
    assert "Подходит: складская роль, смены допустимы, низкий языковой барьер." in response.text
    assert response.text.count("Сохранить в отклики") == 3
    assert response.text.count("Отметить просмотр") == 3
    assert response.text.count('hx-post="/leads/from-search"') == 6
    assert response.text.count('action="/leads/from-search"') == 6
    assert response.text.count('data-async-form') == 6
    assert response.text.count('action="/leads/open-original"') == 2
    assert 'value="https://example.org/jobs/10000-1234567890-S"' in response.text
    assert 'value="https://example.org/jobs/cj-2234567890"' in response.text
    assert "Оригинальная ссылка недоступна" in response.text
    assert "Показать вакансии после дедупликации (3)" in response.text
    assert "до фильтров и bucket-ов" in response.text
    assert "Скрыто жёсткими фильтрами: 0" in response.text
    assert "Источники в текущем запуске" in response.text
    assert service.last_source_ids == ("ba",)
    assert service.last_search_input is not None
    assert service.last_search_input.query == "lager"
    assert service.last_search_input.location == "Berlin"
    assert service.last_search_input.search_mode == "germany_local"
    assert service.last_search_input.radius_km == 25
    assert service.last_search_input.page_size == 8
    assert history_service.calls == 1


def test_jobs_search_start_passes_remote_worldwide_mode() -> None:
    service = FakeSearchService()
    history_service = FakeSearchHistoryService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    app.dependency_overrides[get_search_history_service] = lambda: history_service
    client = TestClient(app)

    response = client.post(
        "/jobs/search-results",
        data={
            "source": "ba",
            "search_mode": "remote_worldwide",
            "query": "Python Developer",
            "location": "",
            "radius_km": "25",
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert service.last_search_input is not None
    assert service.last_search_input.search_mode == "remote_worldwide"
    assert service.last_search_input.location == "remote"


def test_jobs_search_results_germany_mode_replaces_stale_remote_location() -> None:
    service = FakeSearchService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/jobs/search-results",
        data={
            "source": "__enabled__",
            "search_mode": "germany_local",
            "query": "python developer",
            "location": "remote",
            "radius_km": "25",
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert service.last_search_input is not None
    assert service.last_search_input.search_mode == "germany_local"
    assert service.last_search_input.location == "Deutschland"
    assert service.last_source_ids == ("ba",)


def test_jobs_search_results_remote_worldwide_all_sources_uses_global_remote_only() -> None:
    service = FakeSearchService()
    history_service = FakeSearchHistoryService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    app.dependency_overrides[get_search_history_service] = lambda: history_service
    client = TestClient(app)

    response = client.post(
        "/jobs/search-results",
        data={
            "source": "__enabled__",
            "search_mode": "remote_worldwide",
            "query": "Python Developer",
            "location": "",
            "radius_km": "25",
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert service.last_source_ids == ("remotive",)


def test_jobs_search_results_germany_local_all_sources_excludes_global_remote_only() -> None:
    service = FakeSearchService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/jobs/search-results",
        data={
            "source": "__enabled__",
            "search_mode": "germany_local",
            "query": "lager",
            "location": "Berlin",
            "radius_km": "25",
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert service.last_source_ids == ("ba",)


def test_jobs_search_results_russian_scope_uses_only_russian_language_sources() -> None:
    service = FakeSearchService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/jobs/search-results",
        data={
            "source": "__enabled__",
            "source_scope": "russian",
            "search_mode": "remote_worldwide",
            "query": "python developer",
            "location": "",
            "radius_km": "25",
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert service.last_source_ids == ("hh", "dou_rss", "djinni_rss")
    assert service.last_search_input is not None
    assert service.last_search_input.search_mode == "remote_worldwide"


def test_jobs_search_results_russian_scope_forces_exhaustive_query_mode() -> None:
    service = FakeSearchService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/jobs/search-results",
        data={
            "source": "__enabled__",
            "source_scope": "russian",
            "search_mode": "germany_local",
            "query": "softwareentwickler",
            "location": "Berlin",
            "radius_km": "25",
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert service.last_source_ids == ("hh", "dou_rss", "djinni_rss")
    assert service.last_search_input is not None
    assert service.last_search_input.search_mode == "remote_worldwide"
    assert service.last_search_input.location == "remote"


def test_jobs_page_renders_separate_russian_language_search_button() -> None:
    service = FakeSearchService()
    catalog = FakeProfileCatalogService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    app.dependency_overrides[get_profile_catalog_service] = lambda: catalog
    client = TestClient(app)

    response = client.get("/jobs")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert 'name="source_scope" value="russian"' in response.text
    assert "Искать по русскоязычным ресурсам" in response.text
    assert "HeadHunter API, DOU Jobs RSS, Djinni Jobs RSS" in response.text


def test_jobs_search_results_route_hides_explicit_irrelevant_feedback_from_visible_buckets(
    client: TestClient,
    owner_records: dict,
) -> None:
    profile_id = owner_records["search_profile"].id
    service = _delivery_search_service()
    target = service.search(search_input=_delivery_search_input()).hot_results[0]
    client.app.dependency_overrides[get_search_service] = lambda: service
    client.app.dependency_overrides[get_search_history_service] = lambda: FakeSearchHistoryService()

    feedback = client.post(
        "/feedback/record",
        data={
            "profile_id": str(profile_id),
            "canonical_key": target.canonical_group.canonical_key,
            "source_id": target.primary_record.source_id,
            "source_name": target.primary_record.source_name,
            "normalized_title": target.canonical_group.normalized_title,
            "company_name": target.canonical_group.company_name or "",
            "location_text": target.canonical_group.location_text or "",
            "role_family": target.role_family or "",
            "current_query": "Lieferfahrer",
            "feedback_label": "irrelevant",
        },
    )
    response = client.post(
        "/jobs/search-results",
        data={
            "profile_id": str(profile_id),
            "source": "adzuna",
            "query": "Lieferfahrer",
            "location": "Rostock",
            "radius_km": "25",
        },
    )
    client.app.dependency_overrides.clear()

    assert feedback.status_code == 200
    assert "Не подходит" in feedback.text
    assert response.status_code == 200
    assert "Горячие вакансии" not in response.text
    assert "Можно проверить" not in response.text
    assert "Сохранить в отклики" not in response.text
    assert "Показано пользователю: 0" in response.text
    assert "Отклонены: 1" in response.text
    assert "Показать вакансии после дедупликации (1)" in response.text
    assert "Verkaufsfahrer (m/w/d)" in response.text


def test_jobs_search_results_route_moves_explicit_weak_feedback_to_review_bucket(
    client: TestClient,
    owner_records: dict,
) -> None:
    profile_id = owner_records["search_profile"].id
    service = _delivery_search_service()
    target = service.search(search_input=_delivery_search_input()).hot_results[0]
    client.app.dependency_overrides[get_search_service] = lambda: service
    client.app.dependency_overrides[get_search_history_service] = lambda: FakeSearchHistoryService()

    feedback = client.post(
        "/feedback/record",
        data={
            "profile_id": str(profile_id),
            "canonical_key": target.canonical_group.canonical_key,
            "source_id": target.primary_record.source_id,
            "source_name": target.primary_record.source_name,
            "normalized_title": target.canonical_group.normalized_title,
            "company_name": target.canonical_group.company_name or "",
            "location_text": target.canonical_group.location_text or "",
            "role_family": target.role_family or "",
            "current_query": "Lieferfahrer",
            "feedback_label": "weak",
        },
    )
    response = client.post(
        "/jobs/search-results",
        data={
            "profile_id": str(profile_id),
            "source": "adzuna",
            "query": "Lieferfahrer",
            "location": "Rostock",
            "radius_km": "25",
        },
    )
    client.app.dependency_overrides.clear()

    assert feedback.status_code == 200
    assert "Слабо" in feedback.text
    assert response.status_code == 200
    assert "Горячие вакансии" not in response.text
    assert "Можно проверить" in response.text
    assert "Категория:" in response.text
    assert "на проверку" in response.text
    assert "Сохранить в отклики" in response.text
    assert "Показано пользователю: 1" in response.text
    assert "Отклонены: 0" in response.text


def test_jobs_search_results_route_stays_usable_in_degraded_profile_mode() -> None:
    degraded_note = (
        "Сохраненный профиль временно недоступен: нет соединения с базой данных. "
        "Поиск все равно можно запускать по полям формы; будет использован безопасный базовый профиль."
    )
    service = FakeSearchService(
        search_result=_build_result(profile_source="degraded", note_ru=degraded_note)
    )
    history_service = FakeSearchHistoryService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    app.dependency_overrides[get_search_history_service] = lambda: history_service
    client = TestClient(app)

    response = client.post(
        "/jobs/search-results",
        data={"source": "ba", "query": "lager", "location": "Berlin", "radius_km": "25"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "notice warning" in response.text
    assert "нет соединения с базой данных" in response.text
    assert "Горячие вакансии" in response.text
    assert "Сотрудник склада" in response.text
    assert history_service.calls == 1


def test_search_task_snapshot_reports_progress_state_truthfully() -> None:
    task = _SearchTask(task_id="phase16", profile_id=42)

    task.on_attempt_started("lager")
    attempt = task.snapshot()
    assert attempt["status"] == "running"
    assert attempt["phase_code"] == "attempt"
    assert attempt["phase_label"] == "Новая попытка поиска"
    assert attempt["current_query"] == "lager"
    assert attempt["current_count"] == 0
    assert attempt["total_count"] == 0
    assert attempt["profile_id"] == 42

    task.on_source_done(3, "BA", query="lager")
    task.mark_partial(_build_result())
    fetch = task.snapshot()
    assert fetch["phase_code"] == "fetch"
    assert fetch["phase_label"] == "Поиск по источникам"
    assert fetch["current_count"] == 3
    assert fetch["total_count"] == 3
    assert fetch["best_count"] == 3
    assert fetch["last_source"] == "BA"
    assert fetch["partial_result"] is not None

    task.on_postprocess(2, query="lager")
    postprocess = task.snapshot()
    assert postprocess["phase_code"] == "postprocess"
    assert postprocess["phase_label"] == "Дедупликация и фильтрация"
    assert postprocess["current_query"] == "lager"
    assert postprocess["current_count"] == 2
    assert postprocess["total_count"] == 3
    assert postprocess["best_count"] == 3
    assert postprocess["last_source"] is None

    task.on_attempt_started("lagerhelfer")
    next_attempt = task.snapshot()
    assert next_attempt["current_query"] == "lagerhelfer"
    assert next_attempt["current_count"] == 0
    assert next_attempt["total_count"] == 3

    task.on_source_done(4, "BA", query="lagerhelfer")
    next_fetch = task.snapshot()
    assert next_fetch["current_count"] == 4
    assert next_fetch["total_count"] == 7

    task.request_stop()
    stopping = task.snapshot()
    assert stopping["status"] == "stopping"


def test_jobs_search_progress_renders_labeled_partial_results_while_running() -> None:
    service = FakeSearchService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    client = TestClient(app)

    task = _SearchTask(task_id="partial1", profile_id=None)
    task.on_attempt_started("lager")
    task.mark_partial(_build_result())
    from app.web.routes.jobs import _register_task
    _register_task(task)

    response = client.get("/jobs/search/progress/partial1")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Промежуточная выдача, поиск ещё продолжается" in response.text
    assert "Горячие вакансии" in response.text


def test_jobs_search_progress_confirms_stop_request_while_processing() -> None:
    service = FakeSearchService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    client = TestClient(app)

    task = _SearchTask(task_id="stop1", profile_id=None)
    task.on_attempt_started("Middle Python Developer")
    task.request_stop()
    from app.web.routes.jobs import _register_task
    _register_task(task)

    response = client.get("/jobs/search/progress/stop1")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Остановка принята" in response.text
    assert "Дождитесь завершения обработки уже найденных вакансий" in response.text
    assert "Остановить и показать лучший промежуточный результат" not in response.text


def test_jobs_legacy_source_preview_route_is_not_exposed_anymore() -> None:
    service = FakeSearchService()
    app = create_app()
    app.dependency_overrides[get_search_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/jobs/source-preview",
        data={"source": "ba", "query": "lager", "location": "Berlin", "radius_km": "25"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 404


def test_jobs_export_renders_all_vacancies_for_completed_task() -> None:
    app = create_app()
    client = TestClient(app)
    from app.web.routes.jobs import _register_task

    task = _SearchTask(task_id="export1", profile_id=None)
    task.mark_done(_build_result())
    _register_task(task)

    response = client.get("/jobs/export/export1")

    assert response.status_code == 200
    assert "Lagermitarbeiter/in" in response.text  # vacancy title present
    assert "Сотрудник склада" in response.text  # translated title / summary present
    assert "Источник:" in response.text
    assert "https://example.org/jobs/10000-1234567890-S" in response.text  # clickable link
    assert "Сохранить как PDF" in response.text
    assert "window.print()" in response.text  # auto-print for one-click PDF


def test_jobs_export_groups_vacancies_by_keyword() -> None:
    base = _build_result()
    item1, item2, _item3 = base.results
    grouped = dataclasses.replace(
        base,
        query_result_groups=(
            SearchQueryResultGroup(query="Python Developer", hot_results=(item1,), maybe_results=()),
            SearchQueryResultGroup(query="FastAPI", hot_results=(item2,), maybe_results=()),
        ),
    )

    app = create_app()
    client = TestClient(app)
    from app.web.routes.jobs import _register_task

    task = _SearchTask(task_id="exportkw", profile_id=None)
    task.mark_done(grouped)
    _register_task(task)

    response = client.get("/jobs/export/exportkw")

    assert response.status_code == 200
    assert "Ключевик: «Python Developer»" in response.text
    assert "Ключевик: «FastAPI»" in response.text


def test_jobs_export_unknown_task_shows_friendly_message() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.get("/jobs/export/does-not-exist")

    assert response.status_code == 200
    assert "Нет данных для экспорта" in response.text


def test_jobs_search_progress_done_shows_pdf_export_button() -> None:
    app = create_app()
    client = TestClient(app)
    from app.web.routes.jobs import _register_task

    task = _SearchTask(task_id="exportbtn", profile_id=None)
    task.mark_done(_build_result())
    _register_task(task)

    response = client.get("/jobs/search/progress/exportbtn")

    assert response.status_code == 200
    assert "Скачать всё в ПДФ" in response.text
    assert "/jobs/export/exportbtn" in response.text
