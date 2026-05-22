from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.search import SearchRun, VacancyScore
from app.db.models.vacancies import VacancyCanonical, VacancySourceRecord
from app.services.filter_engine import FilterResult
from app.services.normalization_models import CanonicalVacancyGroup, LanguageSignals, NormalizedLocation, NormalizedVacancyRecord
from app.services.search_history_service import SearchHistoryService
from app.services.search_models import RuleHit, ScoreResult, SearchProfileContext, SearchResultItem, SearchRunResult, VacancySignalSnapshot


def _normalized_record(*, source_id: str, source_name: str, external_id: str, title: str, city: str) -> NormalizedVacancyRecord:
    return NormalizedVacancyRecord(
        source_id=source_id,
        source_name=source_name,
        external_id=external_id,
        source_reference=external_id,
        source_url=f"https://example.org/jobs/{external_id}",
        raw_payload={"salary": "2400 EUR"},
        original_title=title,
        original_company="Logistik Nord GmbH",
        original_location=f"{city}, Deutschland",
        original_posted_at="2026-04-15",
        posted_date=date(2026, 4, 15),
        normalized_title=title.casefold(),
        normalized_company="Logistik Nord GmbH",
        normalized_location=NormalizedLocation(
            raw_text=city,
            normalized_text=city,
            country_code="DE",
            city=city,
        ),
        body_text="Warehouse role with shifts and immediate start.",
        normalized_body_text="warehouse role with shifts and immediate start",
        title_tokens=("warehouse",),
        content_tokens=("warehouse", "shift"),
        title_fingerprint=f"title-{external_id}",
        content_fingerprint=f"body-{external_id}",
        language_signals=LanguageSignals(low_language_signal=True, shift_signal=True),
    )


def _result_item(*, canonical_key: str, record: NormalizedVacancyRecord, bucket: str, score: int) -> SearchResultItem:
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
        signals=VacancySignalSnapshot(combined_text="warehouse shift"),
        filter_result=FilterResult(decision="allow", positive_hits=(RuleHit(code="warehouse", label_ru="склад"),)),
        score_result=ScoreResult(score=score, positive_hits=(RuleHit(code="fit", label_ru="подходит", weight=score),)),
        bucket=bucket,  # type: ignore[arg-type]
        explanation_ru="Подходит для быстрого отклика.",
        summary_ru="Короткая сводка по вакансии.",
        translated_title_ru="Складская вакансия",
    )


def test_search_history_service_persists_search_run_and_latest_scores(
    db_session: Session,
    owner_records: dict[str, object],
) -> None:
    _ = owner_records
    fixed_now = datetime(2026, 4, 17, 10, 0, tzinfo=UTC)
    service = SearchHistoryService(now_provider=lambda: fixed_now)

    ba_record = _normalized_record(
        source_id="ba",
        source_name="BA",
        external_id="ba-1",
        title="Lagermitarbeiter/in",
        city="Berlin",
    )
    cj_record = _normalized_record(
        source_id="careerjet",
        source_name="Careerjet",
        external_id="cj-1",
        title="Verpacker/in",
        city="Hamburg",
    )
    result = SearchRunResult(
        profile=SearchProfileContext(profile_label="Основной поиск", profile_source="saved"),
        source_states=(),
        results=(
            _result_item(canonical_key="canonical-1", record=ba_record, bucket="hot", score=91),
            _result_item(canonical_key="canonical-2", record=cj_record, bucket="maybe", score=64),
        ),
        hot_results=(_result_item(canonical_key="canonical-1", record=ba_record, bucket="hot", score=91),),
        maybe_results=(_result_item(canonical_key="canonical-2", record=cj_record, bucket="maybe", score=64),),
        rejected_results=(),
        total_raw_records=2,
        total_normalized_records=2,
        total_canonical_results=2,
    )

    search_run = service.persist_search_result(db_session, result=result)
    db_session.commit()

    assert search_run is not None
    assert search_run.total_fetched == 2
    assert search_run.total_new == 2
    assert search_run.total_scored == 2

    stored_runs = tuple(db_session.execute(select(SearchRun)).scalars())
    stored_canonicals = tuple(db_session.execute(select(VacancyCanonical)).scalars())
    stored_sources = tuple(db_session.execute(select(VacancySourceRecord)).scalars())
    stored_scores = tuple(db_session.execute(select(VacancyScore)).scalars())

    assert len(stored_runs) == 1
    assert len(stored_canonicals) == 2
    assert len(stored_sources) == 2
    assert len(stored_scores) == 2
    assert {score.bucket for score in stored_scores} == {"hot", "maybe"}


def test_search_history_service_updates_last_seen_without_duplicate_source_records(
    db_session: Session,
    owner_records: dict[str, object],
) -> None:
    _ = owner_records
    first_now = datetime(2026, 4, 17, 10, 0, tzinfo=UTC)
    second_now = datetime(2026, 4, 18, 10, 0, tzinfo=UTC)

    record = _normalized_record(
        source_id="ba",
        source_name="BA",
        external_id="ba-1",
        title="Lagermitarbeiter/in",
        city="Berlin",
    )
    first_result = SearchRunResult(
        profile=SearchProfileContext(profile_label="Основной поиск", profile_source="saved"),
        source_states=(),
        results=(_result_item(canonical_key="canonical-1", record=record, bucket="hot", score=91),),
        hot_results=(_result_item(canonical_key="canonical-1", record=record, bucket="hot", score=91),),
        maybe_results=(),
        rejected_results=(),
        total_raw_records=1,
        total_normalized_records=1,
        total_canonical_results=1,
    )
    second_result = SearchRunResult(
        profile=SearchProfileContext(profile_label="Основной поиск", profile_source="saved"),
        source_states=(),
        results=(_result_item(canonical_key="canonical-1", record=record, bucket="hot", score=93),),
        hot_results=(_result_item(canonical_key="canonical-1", record=record, bucket="hot", score=93),),
        maybe_results=(),
        rejected_results=(),
        total_raw_records=1,
        total_normalized_records=1,
        total_canonical_results=1,
    )

    first_service = SearchHistoryService(now_provider=lambda: first_now)
    second_service = SearchHistoryService(now_provider=lambda: second_now)

    first_search_run = first_service.persist_search_result(db_session, result=first_result)
    second_search_run = second_service.persist_search_result(db_session, result=second_result)
    db_session.commit()

    stored_sources = tuple(db_session.execute(select(VacancySourceRecord)).scalars())
    stored_canonicals = tuple(db_session.execute(select(VacancyCanonical)).scalars())

    assert first_search_run is not None
    assert second_search_run is not None
    assert second_search_run.total_new == 0
    assert second_search_run.total_updated == 1
    assert len(stored_sources) == 1
    assert len(stored_canonicals) == 1
    assert stored_sources[0].first_seen_at == first_now.replace(tzinfo=None)
    assert stored_sources[0].last_seen_at == second_now.replace(tzinfo=None)
    assert stored_canonicals[0].first_seen_at == first_now.replace(tzinfo=None)
    assert stored_canonicals[0].last_seen_at == second_now.replace(tzinfo=None)


def test_search_history_service_skips_persistence_for_degraded_profile(
    caplog,
) -> None:
    result = SearchRunResult(
        profile=SearchProfileContext.degraded(
            note_ru="Сохраненный профиль временно недоступен из-за ошибки базы данных."
        ),
        source_states=(),
        results=(),
    )
    calls = {"count": 0}

    def _session_factory() -> Session:
        calls["count"] += 1
        raise AssertionError("session_factory should not be called for degraded profile persistence")

    service = SearchHistoryService(session_factory=_session_factory)

    with caplog.at_level(logging.INFO):
        service.record_search_result(result=result)

    assert calls["count"] == 0
    assert "search_history_persistence_skipped reason=profile_unavailable profile_source=degraded" in caplog.text


def test_search_history_service_fallback_profile_attempts_persistence_and_logs_missing_saved_profile(
    db_session: Session,
    caplog,
) -> None:
    session_factory = sessionmaker(bind=db_session.get_bind())
    result = SearchRunResult(
        profile=SearchProfileContext(
            profile_label="Временный локальный профиль",
            profile_source="fallback",
            note_ru="Сохраненный профиль не найден, используется локальный fallback.",
        ),
        source_states=(),
        results=(),
    )
    service = SearchHistoryService(session_factory=session_factory)

    with caplog.at_level(logging.INFO):
        service.record_search_result(result=result)

    assert "search_history_persistence_skipped reason=no_active_search_profile profile_source=fallback" in caplog.text
