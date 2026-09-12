from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime

import pytest
from app.services.normalization_models import (
    CanonicalVacancyGroup,
    LanguageSignals,
    NormalizedLocation,
    NormalizedVacancyRecord,
)
from app.services.search_export_service import (
    EXPORT_MODE_DIAGNOSTICS,
    EXPORT_MODE_FULL,
    EXPORT_SCHEMA_VERSION,
    build_export_filename,
    build_search_export,
)
from app.services.search_models import (
    FilterResult,
    HiddenFilteredItem,
    RuleHit,
    ScoreResult,
    SearchAttemptRecord,
    SearchAttemptSummary,
    SearchProfileContext,
    SearchResultItem,
    SearchRunResult,
    SearchSourceState,
    VacancySignalSnapshot,
)
from app.services.source_adapters.models import SourceSearchInput

GENERATED_AT = datetime(2026, 9, 11, 14, 30, tzinfo=UTC)


def _record(
    *,
    source_id: str = "careerjet",
    source_name: str = "Careerjet",
    external_id: str = "cj-1",
    body_text: str = "Ohne Deutsch. 3 Schicht. Ab sofort.",
) -> NormalizedVacancyRecord:
    return NormalizedVacancyRecord(
        source_id=source_id,
        source_name=source_name,
        external_id=external_id,
        source_reference="https://example.org/1",
        source_url="https://example.org/1",
        raw_payload={"description": body_text},
        original_title="Lagerhelfer (m/w/d)",
        original_company="Logistik Nord GmbH",
        original_location="10115 Berlin",
        original_posted_at="2026-09-10",
        posted_date=date(2026, 9, 10),
        normalized_title="lagerhelfer",
        normalized_company="logistik nord",
        normalized_location=NormalizedLocation(
            raw_text="10115 Berlin",
            normalized_text="berlin",
            country_code="DE",
            city="Berlin",
        ),
        body_text=body_text,
        normalized_body_text=body_text.lower(),
        title_tokens=("lagerhelfer",),
        content_tokens=("ohne", "deutsch"),
        title_fingerprint="tf1",
        content_fingerprint="cf1",
        language_signals=LanguageSignals(low_language_signal=True, shift_signal=True),
    )


def _item(*, bucket: str = "hot", score: int = 83) -> SearchResultItem:
    record = _record()
    group = CanonicalVacancyGroup(
        canonical_key="ck-1",
        normalized_title=record.normalized_title,
        company_name=record.normalized_company,
        location_text=record.normalized_location.normalized_text,
        country_code="DE",
        city="Berlin",
        posted_date=record.posted_date,
        language_signals=record.language_signals,
        source_records=(record,),
        provenance=(record.source_record_key,),
    )
    return SearchResultItem(
        canonical_group=group,
        primary_record=record,
        signals=VacancySignalSnapshot(
            combined_text="очень длинный склеенный текст вакансии",
            positive_role_hits=(RuleHit(code="warehouse_role", label_ru="Складская роль", weight=20),),
            low_language_signal=True,
            required_driver_license_categories=("B",),
        ),
        filter_result=FilterResult(
            decision="allow",
            positive_hits=(RuleHit(code="low_language_signal", label_ru="Без немецкого", weight=15),),
        ),
        score_result=ScoreResult(
            score=score,
            positive_hits=(RuleHit(code="shift_signal", label_ru="Сменный график", weight=10),),
            negative_hits=(RuleHit(code="strong_experience_required", label_ru="Нужен опыт", weight=-12),),
        ),
        bucket=bucket,  # type: ignore[arg-type]
        explanation_ru="Подходит: без немецкого, смены.",
        summary_ru="Склад в Берлине, смены, старт сразу.",
        translated_title_ru="Работник склада",
        role_family="warehouse",
        search_query="Lagerhelfer",
    )


def _result() -> SearchRunResult:
    item = _item()
    return SearchRunResult(
        profile=SearchProfileContext(
            profile_label="Основной профиль",
            profile_source="saved",
            german_level="none",
            desired_roles=("склад",),
            no_german_required=True,
        ),
        source_states=(
            SearchSourceState(
                source_id="careerjet",
                source_name="Careerjet",
                status_label="Успех",
                status_kind="success",
                raw_count=20,
                total_count=9054,
                normalized_count=20,
                canonical_count=18,
            ),
            SearchSourceState(
                source_id="hh",
                source_name="HeadHunter API",
                status_label="Нет данных",
                status_kind="warning",
                warnings=("HH: доступ заблокирован API (HTTP 403 forbidden).",),
            ),
        ),
        results=(item,),
        hot_results=(item,),
        total_raw_records=20,
        total_normalized_records=20,
        total_canonical_results=18,
        hidden_filtered_items=(
            HiddenFilteredItem(
                canonical_key="ck-hidden",
                title="LKW-Fahrer CE",
                company_name="Spedition",
                location_text="Hamburg",
                source_name="BA",
                original_url="https://example.org/hidden",
                rejection_reasons=(RuleHit(code="heavy_vehicle_required", label_ru="Тяжёлый транспорт"),),
            ),
        ),
        attempt_summary=SearchAttemptSummary(
            primary_query="склад",
            final_query_used="Lagerhelfer",
            fallback_used=True,
            language_relaxation_used=False,
            user_message_ru="Расширили запрос.",
            attempts=(
                SearchAttemptRecord(
                    attempt_number=0,
                    primary_query="склад",
                    stage_name="primary",
                    query_used="Lager",
                    language_relaxation_applied=False,
                    raw_count=40,
                    normalized_count=40,
                    deduped_count=31,
                    hot_count=0,
                    review_count=0,
                    rejected_count=31,
                    non_rejected_count=0,
                    reason_continued="Недостаточно результатов.",
                    rejection_reason_counts=(("location_mismatch", 31),),
                ),
            ),
        ),
    )


def test_export_has_stable_top_level_shape() -> None:
    export = build_search_export(_result(), task_id="abc123", generated_at=GENERATED_AT)

    assert export["schema_version"] == EXPORT_SCHEMA_VERSION
    assert export["task_id"] == "abc123"
    assert export["generated_at"] == "2026-09-11T14:30:00+00:00"
    assert export["mode"] == EXPORT_MODE_DIAGNOSTICS
    assert set(export) == {
        "schema_version",
        "mode",
        "generated_at",
        "task_id",
        "run",
        "profile",
        "sources",
        "attempts",
        "vacancies",
        "hidden_filtered",
    }


def test_export_records_search_input_and_totals() -> None:
    export = build_search_export(
        _result(),
        task_id="abc123",
        generated_at=GENERATED_AT,
        search_input=SourceSearchInput(query="склад", location="Berlin", radius_km=50, page_size=20),
        source_ids_requested=("ba", "careerjet"),
    )

    run = export["run"]
    assert run["query_submitted"] == "склад"
    assert run["location"] == "Berlin"
    assert run["radius_km"] == 50
    assert run["search_mode"] == "germany_local"
    assert run["source_ids_requested"] == ["ba", "careerjet"]
    assert run["totals"]["raw_records"] == 20
    assert run["totals"]["canonical_results"] == 18
    assert run["totals"]["hot"] == 1
    assert run["totals"]["hidden_by_hard_filter"] == 1


def test_export_keeps_per_source_diagnostics_including_failures() -> None:
    export = build_search_export(_result(), generated_at=GENERATED_AT)

    by_id = {source["source_id"]: source for source in export["sources"]}
    assert by_id["careerjet"]["status_kind"] == "success"
    assert by_id["careerjet"]["total_count"] == 9054
    # Источник, который отработал вхолостую, обязан быть виден — иначе выпадение не заметить.
    assert by_id["hh"]["status_kind"] == "warning"
    assert "403" in by_id["hh"]["warnings"][0]


def test_export_keeps_attempt_chain_with_rejection_reasons() -> None:
    export = build_search_export(_result(), generated_at=GENERATED_AT)

    attempts = export["attempts"]
    assert attempts["primary_query"] == "склад"
    assert attempts["final_query_used"] == "Lagerhelfer"
    assert attempts["fallback_used"] is True

    first = attempts["attempts"][0]
    assert first["query_used"] == "Lager"
    assert first["reason_continued"] == "Недостаточно результатов."
    # Главный диагностический сигнал: по какому коду сыпалась выдача.
    assert first["rejection_reason_counts"] == {"location_mismatch": 31}


def test_export_vacancy_carries_score_breakdown_and_provenance() -> None:
    export = build_search_export(_result(), generated_at=GENERATED_AT)

    vacancy = export["vacancies"][0]
    assert vacancy["bucket"] == "hot"
    assert vacancy["score"] == 83
    assert vacancy["posted_date"] == "2026-09-10"
    assert vacancy["filter"]["decision"] == "allow"
    assert vacancy["score_hits"]["positive"][0] == {"code": "shift_signal", "weight": 10}
    assert vacancy["score_hits"]["negative"][0]["weight"] == -12
    assert vacancy["sources"][0]["source_id"] == "careerjet"
    assert vacancy["sources"][0]["external_id"] == "cj-1"


def test_full_mode_adds_texts_and_rule_labels() -> None:
    export = build_search_export(_result(), generated_at=GENERATED_AT, mode=EXPORT_MODE_FULL)

    vacancy = export["vacancies"][0]
    assert export["mode"] == EXPORT_MODE_FULL
    assert vacancy["title_ru"] == "Работник склада"
    assert vacancy["summary_ru"] == "Склад в Берлине, смены, старт сразу."
    assert vacancy["explanation_ru"]
    assert vacancy["description_excerpt"]
    assert vacancy["score_hits"]["positive"][0] == {
        "code": "shift_signal",
        "label_ru": "Сменный график",
        "weight": 10,
    }


def test_diagnostics_mode_omits_vacancy_texts() -> None:
    """Тексты вакансий — 83% объёма и почти ничего не говорят о работе движка."""
    export = build_search_export(_result(), generated_at=GENERATED_AT)

    vacancy = export["vacancies"][0]
    for field in ("description_excerpt", "summary_ru", "explanation_ru", "title_ru"):
        assert field not in vacancy
    # Но решение движка должно остаться полностью.
    assert vacancy["bucket"] and vacancy["score"] and vacancy["filter"]["decision"]


def test_diagnostics_mode_is_much_smaller_than_full() -> None:
    import json

    result = _result()
    small = json.dumps(build_search_export(result, generated_at=GENERATED_AT), ensure_ascii=False)
    large = json.dumps(
        build_search_export(result, generated_at=GENERATED_AT, mode=EXPORT_MODE_FULL),
        ensure_ascii=False,
    )

    assert len(small) < len(large)


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_search_export(_result(), generated_at=GENERATED_AT, mode="whatever")


def test_export_signals_are_serialized_without_the_bulky_combined_text() -> None:
    export = build_search_export(_result(), generated_at=GENERATED_AT)

    signals = export["vacancies"][0]["signals"]
    assert "combined_text" not in signals
    assert signals["low_language_signal"] is True
    assert signals["required_driver_license_categories"] == ["B"]
    # RuleHit-кортежи разворачиваются в объекты, а не в repr датакласса.
    assert signals["positive_role_hits"][0]["code"] == "warehouse_role"


def test_export_signal_fields_follow_the_dataclass() -> None:
    """Новые поля VacancySignalSnapshot должны попадать в выгрузку без правок сериализатора."""
    export = build_search_export(_result(), generated_at=GENERATED_AT, mode=EXPORT_MODE_FULL)

    exported = set(export["vacancies"][0]["signals"])
    declared = {field.name for field in dataclasses.fields(VacancySignalSnapshot)} - {"combined_text"}
    assert exported == declared


def test_diagnostics_mode_keeps_only_signals_that_fired() -> None:
    """Выключенные сигналы ничего не сообщают — важно то, что сработало."""
    export = build_search_export(_result(), generated_at=GENERATED_AT)

    signals = export["vacancies"][0]["signals"]
    assert signals["low_language_signal"] is True
    assert signals["required_driver_license_categories"] == ["B"]
    # Ни одного выключенного признака остаться не должно.
    assert all(value not in (False, None, [], ()) for value in signals.values())
    assert "strong_german_required" not in signals


def test_export_includes_hard_filtered_items_with_reasons() -> None:
    export = build_search_export(_result(), generated_at=GENERATED_AT)

    hidden = export["hidden_filtered"][0]
    assert hidden["title"] == "LKW-Fahrer CE"
    # Без веса правило сериализуется просто кодом — так компактнее.
    assert hidden["rejection_reasons"] == ["heavy_vehicle_required"]

    full_export = build_search_export(_result(), generated_at=GENERATED_AT, mode=EXPORT_MODE_FULL)
    full_hidden = full_export["hidden_filtered"][0]
    assert full_hidden["rejection_reasons"][0]["code"] == "heavy_vehicle_required"
    assert full_hidden["url"] == "https://example.org/hidden"


def test_export_truncates_long_descriptions() -> None:
    long_body = "Deutschkenntnisse " * 300
    item = _item()
    record = dataclasses.replace(item.primary_record, body_text=long_body)
    item = dataclasses.replace(item, primary_record=record)
    result = dataclasses.replace(_result(), hot_results=(item,), results=(item,))

    export = build_search_export(
        result, generated_at=GENERATED_AT, description_excerpt_chars=100, mode=EXPORT_MODE_FULL
    )

    excerpt = export["vacancies"][0]["description_excerpt"]
    assert len(excerpt) <= 101  # 100 символов + многоточие
    assert excerpt.endswith("…")


def test_export_handles_run_without_attempt_summary() -> None:
    result = dataclasses.replace(_result(), attempt_summary=None)

    export = build_search_export(result, generated_at=GENERATED_AT)

    assert export["attempts"] is None


def test_export_filename_is_sortable_task_scoped_and_names_the_mode() -> None:
    assert (
        build_export_filename("abc123", GENERATED_AT)
        == "smartjob-search-20260911-1430-abc123-diagnostics.json"
    )
    assert (
        build_export_filename("abc123", GENERATED_AT, mode=EXPORT_MODE_FULL)
        == "smartjob-search-20260911-1430-abc123-full.json"
    )
    assert build_export_filename(None, GENERATED_AT) == "smartjob-search-20260911-1430-diagnostics.json"
