"""Страница результатов отвечает человеку, а не пересказывает диагностику.

Регресс живого прогона 01.10.2026: блок источников выводил `Сырых: 61 из 61 ·
Нормализовано: 61 · Дедублицировано: 61` по каждому источнику, служебные
предупреждения адаптеров и — для Jooble с HTTP 403 — целиком тело ответа,
начиная с `<!DOCTYPE html>`. Эти тесты закрепляют обратное: в разметке страницы
тела ответа нет вовсе, человек читает короткое «HTTP 403», а счётчики остаются
доступны внутри раскрываемого блока.
"""
from __future__ import annotations

from app.services.normalization_models import CanonicalVacancyGroup, NormalizedVacancyRecord
from app.services.normalizer import VacancyNormalizer
from app.services.search_models import (
    FilterResult,
    RuleHit,
    ScoreResult,
    SearchProfileContext,
    SearchResultItem,
    SearchRunResult,
    SearchSourceState,
    VacancySignalSnapshot,
)
from app.services.source_adapters.models import SourceRecordPreview
from app.web.routes.jobs import get_search_history_service, get_search_service
from app.web.search_presentation import search_overview, source_error_sentence
from fastapi.testclient import TestClient

# Тело ответа Cloudflare, которое Jooble отдаёт вместе с 403: ровно то, что
# раньше попадало на страницу результатов.
JOOBLE_HTML_BODY = (
    "<!DOCTYPE html>\n"
    '<html lang="en-US">\n'
    "<head><title>Access denied</title></head>\n"
    "<body>Attention Required! Cloudflare Ray ID: 8f2c1d</body>\n"
    "</html>"
)
JOOBLE_ERROR_MESSAGE = f"Не удалось получить ответ от Jooble. HTTP 403. {JOOBLE_HTML_BODY}"

ARBEITNOW_STOP_WARNING = "Дальнейшие запросы остановлены: лимит запросов или ограничение источника."


def _record(*, external_id: str, title: str, source_id: str, source_name: str) -> NormalizedVacancyRecord:
    return VacancyNormalizer().normalize_source_record(
        SourceRecordPreview(
            source_id=source_id,
            source_name=source_name,
            external_id=external_id,
            source_reference=external_id,
            title=title,
            company="Logistik Nord GmbH",
            location="10115 Berlin, Deutschland",
            posted_at="2026-09-28",
            detail_url=f"https://example.org/jobs/{external_id}",
            raw_payload={"description": "Ohne Deutsch. 3 Schicht. Ab sofort."},
        )
    )


def _item(record: NormalizedVacancyRecord, *, canonical_key: str, bucket: str) -> SearchResultItem:
    canonical = CanonicalVacancyGroup(
        canonical_key=canonical_key,
        normalized_title=record.normalized_title,
        company_name=record.normalized_company,
        location_text=record.normalized_location.normalized_text,
        country_code=record.normalized_location.country_code,
        city=record.normalized_location.city,
        posted_date=record.posted_date,
        language_signals=record.language_signals,
        source_records=(record,),
        provenance=(record.source_record_key,),
    )
    return SearchResultItem(
        canonical_group=canonical,
        primary_record=record,
        signals=VacancySignalSnapshot(combined_text="lager ohne deutsch schicht", low_language_signal=True),
        filter_result=FilterResult(
            decision="allow",
            positive_hits=(RuleHit(code="warehouse_family", label_ru="складская роль"),),
        ),
        score_result=ScoreResult(score=71),
        bucket=bucket,  # type: ignore[arg-type]
        explanation_ru="Подходит: складская роль, низкий языковой барьер.",
        summary_ru="Сотрудник склада в Берлине.",
        translated_title_ru="Сотрудник склада",
    )


def _mixed_run_result() -> SearchRunResult:
    """Живая картинка прогона: три источника работают, один пуст, Jooble отказал."""
    adzuna_record = _record(
        external_id="adzuna-1",
        title="Lagermitarbeiter/in",
        source_id="adzuna",
        source_name="Adzuna",
    )
    ba_record = _record(
        external_id="10000-1234567890-S",
        title="Produktionshelfer/in",
        source_id="ba",
        source_name="BA",
    )
    hot_item = _item(adzuna_record, canonical_key="canonical-adzuna-1", bucket="hot")
    maybe_item = _item(ba_record, canonical_key="canonical-ba-1", bucket="maybe")

    profile = SearchProfileContext(
        profile_label="Основной поиск",
        profile_source="saved",
        note_ru="Используется сохраненный профиль поиска.",
        legal_status="Section 24",
        work_authorized=True,
        german_level="basic",
        desired_roles=("склад", "производство"),
    )
    return SearchRunResult(
        profile=profile,
        source_states=(
            SearchSourceState(
                source_id="adzuna",
                source_name="Adzuna",
                status_label="Успех",
                status_kind="success",
                raw_count=61,
                total_count=61,
                normalized_count=61,
                canonical_count=61,
            ),
            SearchSourceState(
                source_id="arbeitnow",
                source_name="Arbeitnow",
                status_label="Нет результатов",
                status_kind="warning",
                raw_count=0,
                normalized_count=0,
                canonical_count=0,
                warnings=(ARBEITNOW_STOP_WARNING,),
            ),
            SearchSourceState(
                source_id="ba",
                source_name="BA",
                status_label="Успех",
                status_kind="success",
                raw_count=20,
                total_count=20,
                normalized_count=20,
                canonical_count=19,
            ),
            SearchSourceState(
                source_id="careerjet",
                source_name="Careerjet",
                status_label="Успех",
                status_kind="success",
                raw_count=22,
                total_count=22,
                normalized_count=22,
                canonical_count=20,
            ),
            SearchSourceState(
                source_id="jooble",
                source_name="Jooble",
                status_label="Ошибка",
                status_kind="error",
                error_message=JOOBLE_ERROR_MESSAGE,
            ),
        ),
        results=(hot_item, maybe_item),
        hot_results=(hot_item,),
        maybe_results=(maybe_item,),
        total_raw_records=103,
        total_normalized_records=103,
        total_canonical_results=30,
    )


class _StubSearchService:
    def __init__(self, result: SearchRunResult) -> None:
        self._result = result

    def list_sources(self) -> tuple[object, ...]:
        return ()

    def orchestrated_search(self, **_: object) -> SearchRunResult:
        return self._result

    def search(self, **_: object) -> SearchRunResult:
        return self._result


class _StubHistoryService:
    def record_search_result(self, **_: object) -> None:
        return None


def _render_results_page(client: TestClient, result: SearchRunResult) -> str:
    app = client.app
    app.dependency_overrides[get_search_service] = lambda: _StubSearchService(result)
    app.dependency_overrides[get_search_history_service] = lambda: _StubHistoryService()
    try:
        response = client.post(
            "/jobs/search-results",
            data={"source": "all", "query": "lager", "location": "Berlin", "radius_km": "25"},
        )
    finally:
        app.dependency_overrides.pop(get_search_service, None)
        app.dependency_overrides.pop(get_search_history_service, None)
    assert response.status_code == 200
    return response.text


def test_jooble_response_body_never_reaches_the_page(client: TestClient) -> None:
    """Тело HTTP-ответа источника не попадает в разметку страницы результатов."""
    page = _render_results_page(client, _mixed_run_result())

    assert "DOCTYPE" not in page
    assert "Cloudflare" not in page
    assert "Access denied" not in page
    assert "Attention Required" not in page
    assert "Ray ID" not in page
    # Ни одна часть тела ответа не просочилась даже в экранированном виде.
    assert "&lt;html" not in page


def test_user_sees_short_http_403_sentence(client: TestClient) -> None:
    """Об отказе Jooble человек читает одну короткую фразу с кодом состояния."""
    page = _render_results_page(client, _mixed_run_result())

    assert "Jooble не ответил на запрос: HTTP 403." in page
    assert "Остальные источники продолжили поиск." in page


def test_detailed_counts_live_behind_the_disclosure(client: TestClient) -> None:
    """Счётчики источников остаются доступны — но внутри раскрываемого блока."""
    page = _render_results_page(client, _mixed_run_result())

    assert "Подробнее об источниках" in page
    assert "<details" in page
    details_start = page.index("Подробнее об источниках")
    # Технические счётчики стоят после кнопки раскрытия, а не перед ней.
    assert page.index("raw 61 из 61 · normalized 61 · canonical 61") > details_start
    assert "Итого по прогону: raw 103 · normalized 103 · canonical 30" in page
    assert page.index("Показано пользователю: 2") > details_start
    # Служебное предупреждение адаптера тоже спрятано под кнопку.
    assert page.index(ARBEITNOW_STOP_WARNING) > details_start


def test_working_sources_read_as_working(client: TestClient) -> None:
    """Успешный источник называется «Работает», а не «Успех»."""
    page = _render_results_page(client, _mixed_run_result())

    assert "3 источника работают" in page
    assert page.count("Работает") >= 3
    assert "Успех" not in page
    assert "Получено: 20 · после обработки: 19" in page


def test_empty_source_reads_as_no_results(client: TestClient) -> None:
    """Источник без вакансий показан как «Нет результатов», а не как сбой."""
    page = _render_results_page(client, _mixed_run_result())

    assert "1 без результатов" in page
    assert "Нет результатов" in page
    assert "По этому запросу вакансий не найдено." in page


def test_one_failed_source_does_not_replace_the_other_results(client: TestClient) -> None:
    """Отказ одного источника не выглядит провалом всего поиска."""
    page = _render_results_page(client, _mixed_run_result())

    assert "Найдено 30 уникальных вакансий" in page
    assert "2 подходят для просмотра" in page
    assert "1 с ошибкой" in page
    # Вакансии остальных источников на месте.
    assert "Сотрудник склада" in page
    assert "Горячие вакансии" in page
    # Блок источников остаётся нейтральным, пока есть что показать.
    assert "search-sources--ok" in page
    assert "search-sources--warning" not in page


def test_overview_counts_and_wording_without_rendering() -> None:
    """Сводка считает статусы по тем же счётчикам, что уходят в экспорт."""
    overview = search_overview(_mixed_run_result())

    assert overview.sources_headline_ru == "3 источника работают · 1 без результатов · 1 с ошибкой"
    assert overview.volume_ru == "Получено 103 записи · после обработки 30 вакансий"
    assert overview.found_ru == "Найдено 30 уникальных вакансий"
    assert overview.visible_ru == "2 подходят для просмотра"
    assert overview.error_notes_ru == ("Jooble не ответил на запрос: HTTP 403.",)
    assert overview.others_kept_working is True
    assert overview.tone == "ok"
    assert [row.status_label_ru for row in overview.sources] == [
        "Работает",
        "Нет результатов",
        "Работает",
        "Работает",
        "Ошибка источника",
    ]


def test_source_error_sentence_drops_any_response_payload() -> None:
    """Из сообщения об ошибке в UI уходит максимум код состояния."""
    assert source_error_sentence("Jooble", JOOBLE_ERROR_MESSAGE) == "Jooble не ответил на запрос: HTTP 403."
    # Без кода состояния берём свою первую фразу — но только если она наша.
    assert (
        source_error_sentence("Jooble", "Для Jooble не задан API-ключ.") == "Для Jooble не задан API-ключ."
    )
    assert source_error_sentence("Jooble", "<html><body>nope</body></html>") == "Jooble не ответил на запрос."
    assert source_error_sentence("Jooble", None) == "Jooble не ответил на запрос."

