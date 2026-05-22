from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models.crm import ApplicationLead, LeadEvent, ReminderTask
from app.db.models.profiles import SearchProfile
from app.db.models.search import SearchRun, VacancyScore
from app.db.models.vacancies import VacancyCanonical, VacancySourceRecord
from app.services.stats_service import StatsService


def _seed_stats_dataset(db_session: Session, owner_records: dict[str, object]) -> None:
    user_profile = owner_records["user_profile"]
    search_profile = owner_records["search_profile"]

    canonical_hot = VacancyCanonical(
        normalized_title="lagermitarbeiter",
        company_name="Logistik Nord GmbH",
        location_text="Berlin",
        country_code="DE",
        city="Berlin",
        first_seen_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
    )
    canonical_maybe = VacancyCanonical(
        normalized_title="verpacker",
        company_name="Pack Team GmbH",
        location_text="Hamburg",
        country_code="DE",
        city="Hamburg",
        first_seen_at=datetime(2026, 4, 12, 9, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 4, 12, 9, 0, tzinfo=UTC),
    )
    canonical_old = VacancyCanonical(
        normalized_title="pflegekraft",
        company_name="Care Team GmbH",
        location_text="Leipzig",
        country_code="DE",
        city="Leipzig",
        first_seen_at=datetime(2026, 3, 25, 10, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 3, 25, 10, 0, tzinfo=UTC),
    )
    db_session.add_all((canonical_hot, canonical_maybe, canonical_old))
    db_session.flush()

    db_session.add_all(
        (
            VacancySourceRecord(
                canonical_id=canonical_hot.id,
                source_name="BA",
                external_id="ba-1",
                first_seen_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
                last_seen_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
            ),
            VacancySourceRecord(
                canonical_id=canonical_maybe.id,
                source_name="Careerjet",
                external_id="cj-1",
                first_seen_at=datetime(2026, 4, 12, 9, 0, tzinfo=UTC),
                last_seen_at=datetime(2026, 4, 12, 9, 0, tzinfo=UTC),
            ),
            VacancySourceRecord(
                canonical_id=canonical_old.id,
                source_name="BA",
                external_id="ba-2",
                first_seen_at=datetime(2026, 3, 25, 10, 0, tzinfo=UTC),
                last_seen_at=datetime(2026, 3, 25, 10, 0, tzinfo=UTC),
            ),
        )
    )
    db_session.add_all(
        (
            VacancyScore(
                vacancy_canonical_id=canonical_hot.id,
                search_profile_id=search_profile.id,
                score_total=91,
                bucket="hot",
                explanation="Strong fit",
                scored_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
            ),
            VacancyScore(
                vacancy_canonical_id=canonical_maybe.id,
                search_profile_id=search_profile.id,
                score_total=65,
                bucket="maybe",
                explanation="Needs review",
                scored_at=datetime(2026, 4, 12, 9, 0, tzinfo=UTC),
            ),
            VacancyScore(
                vacancy_canonical_id=canonical_old.id,
                search_profile_id=search_profile.id,
                score_total=32,
                bucket="rejected",
                explanation="Mismatch",
                scored_at=datetime(2026, 3, 25, 10, 0, tzinfo=UTC),
            ),
        )
    )
    db_session.add(
        SearchRun(
            search_profile_id=search_profile.id,
            started_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
            finished_at=datetime(2026, 4, 15, 8, 5, tzinfo=UTC),
            status="completed",
            total_fetched=2,
            total_new=2,
            total_updated=0,
            total_scored=2,
        )
    )

    lead_hot = ApplicationLead(
        user_profile_id=user_profile.id,
        search_profile_id=search_profile.id,
        source_id="ba",
        source_name="BA",
        source_external_id="ba-1",
        vacancy_title="Lagermitarbeiter/in",
        translated_title_ru="Сотрудник склада",
        found_at=datetime(2026, 4, 15, 8, 10, tzinfo=UTC),
        viewed_at=datetime(2026, 4, 15, 8, 15, tzinfo=UTC),
        opened_original_at=datetime(2026, 4, 15, 8, 20, tzinfo=UTC),
        saved_at=datetime(2026, 4, 15, 8, 30, tzinfo=UTC),
        applied_at=datetime(2026, 4, 16, 10, 0, tzinfo=UTC),
        reply_at=datetime(2026, 4, 17, 9, 0, tzinfo=UTC),
        interview_at=datetime(2026, 4, 17, 11, 0, tzinfo=UTC),
        next_action_at=datetime(2026, 4, 18, 9, 0, tzinfo=UTC),
        status="interview_scheduled",
    )
    lead_careerjet = ApplicationLead(
        user_profile_id=user_profile.id,
        search_profile_id=search_profile.id,
        source_id="careerjet",
        source_name="Careerjet",
        source_external_id="cj-1",
        vacancy_title="Verpacker/in",
        translated_title_ru="Упаковщик",
        found_at=datetime(2026, 4, 12, 9, 30, tzinfo=UTC),
        viewed_at=datetime(2026, 4, 12, 10, 0, tzinfo=UTC),
        saved_at=datetime(2026, 4, 12, 11, 0, tzinfo=UTC),
        applied_at=datetime(2026, 4, 14, 12, 0, tzinfo=UTC),
        rejected_at=datetime(2026, 4, 16, 14, 0, tzinfo=UTC),
        status="rejected",
    )
    lead_followup = ApplicationLead(
        user_profile_id=user_profile.id,
        search_profile_id=search_profile.id,
        source_id="ba",
        source_name="BA",
        source_external_id="ba-3",
        vacancy_title="Kommissionierer/in",
        translated_title_ru="Комплектовщик",
        found_at=datetime(2026, 4, 16, 7, 0, tzinfo=UTC),
        viewed_at=datetime(2026, 4, 16, 7, 5, tzinfo=UTC),
        follow_up_due_at=datetime(2026, 4, 16, 8, 0, tzinfo=UTC),
        next_action_at=datetime(2026, 4, 16, 8, 0, tzinfo=UTC),
        status="viewed",
    )
    lead_archived = ApplicationLead(
        user_profile_id=user_profile.id,
        search_profile_id=search_profile.id,
        source_id="ba",
        source_name="BA",
        source_external_id="ba-2",
        vacancy_title="Pflegekraft",
        translated_title_ru="Уход за пациентами",
        found_at=datetime(2026, 3, 25, 10, 10, tzinfo=UTC),
        saved_at=datetime(2026, 3, 26, 8, 0, tzinfo=UTC),
        archived_at=datetime(2026, 4, 14, 9, 0, tzinfo=UTC),
        status="archived",
    )
    db_session.add_all((lead_hot, lead_careerjet, lead_followup, lead_archived))
    db_session.flush()

    db_session.add_all(
        (
            LeadEvent(
                lead_id=lead_hot.id,
                event_type="reply_received",
                event_at=datetime(2026, 4, 17, 9, 0, tzinfo=UTC),
                payload={"details": "Phone screen"},
            ),
            LeadEvent(
                lead_id=lead_careerjet.id,
                event_type="rejected",
                event_at=datetime(2026, 4, 16, 14, 0, tzinfo=UTC),
                payload={"reason": "Language"},
            ),
            LeadEvent(
                lead_id=lead_followup.id,
                event_type="viewed",
                event_at=datetime(2026, 4, 16, 7, 5, tzinfo=UTC),
                payload={},
            ),
        )
    )
    db_session.add(
        ReminderTask(
            user_profile_id=user_profile.id,
            lead_id=lead_hot.id,
            title="Написать follow-up",
            due_at=datetime(2026, 4, 14, 10, 0, tzinfo=UTC),
            next_action_at=datetime(2026, 4, 14, 10, 0, tzinfo=UTC),
            is_done=False,
        )
    )
    db_session.commit()


def _seed_unrelated_profile_noise(db_session: Session, owner_records: dict[str, object]) -> None:
    user_profile = owner_records["user_profile"]

    secondary_profile = SearchProfile(
        user_profile_id=user_profile.id,
        name="Шумный профиль",
        is_active=False,
        desired_roles=["офис"],
        excluded_roles=[],
        preferred_locations=["Munchen"],
        relocation_ready=False,
        shift_ok=False,
        physical_work_ok=False,
        housing_needed=False,
        start_availability_text="Позже",
        driver_license=None,
        car_available=False,
        notes="Нужен только для проверки scoping.",
    )
    db_session.add(secondary_profile)
    db_session.flush()

    canonical_noise = VacancyCanonical(
        normalized_title="office-assistant",
        company_name="Noise GmbH",
        location_text="Munchen",
        country_code="DE",
        city="Munchen",
        first_seen_at=datetime(2026, 4, 16, 8, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 4, 16, 8, 0, tzinfo=UTC),
    )
    db_session.add(canonical_noise)
    db_session.flush()
    db_session.add(
        VacancySourceRecord(
            canonical_id=canonical_noise.id,
            source_name="NoiseBoard",
            external_id="noise-1",
            first_seen_at=datetime(2026, 4, 16, 8, 0, tzinfo=UTC),
            last_seen_at=datetime(2026, 4, 16, 8, 0, tzinfo=UTC),
        )
    )
    db_session.add(
        VacancyScore(
            vacancy_canonical_id=canonical_noise.id,
            search_profile_id=secondary_profile.id,
            score_total=98,
            bucket="hot",
            explanation="Noise fit",
            scored_at=datetime(2026, 4, 16, 8, 5, tzinfo=UTC),
        )
    )
    db_session.add(
        ApplicationLead(
            user_profile_id=user_profile.id,
            search_profile_id=secondary_profile.id,
            source_id="noise",
            source_name="NoiseBoard",
            source_external_id="noise-1",
            vacancy_title="Office Assistant",
            found_at=datetime(2026, 4, 16, 8, 10, tzinfo=UTC),
            saved_at=datetime(2026, 4, 16, 8, 20, tzinfo=UTC),
            applied_at=datetime(2026, 4, 16, 9, 0, tzinfo=UTC),
            reply_at=datetime(2026, 4, 17, 9, 0, tzinfo=UTC),
            status="reply_received",
        )
    )
    db_session.add(
        SearchRun(
            search_profile_id=secondary_profile.id,
            started_at=datetime(2026, 4, 16, 8, 0, tzinfo=UTC),
            finished_at=datetime(2026, 4, 16, 8, 5, tzinfo=UTC),
            status="completed",
            total_fetched=1,
            total_new=1,
            total_updated=0,
            total_scored=1,
        )
    )
    db_session.commit()


def test_stats_service_builds_kpi_source_and_timeline_views(
    db_session: Session,
    owner_records: dict[str, object],
) -> None:
    _seed_stats_dataset(db_session, owner_records)
    _seed_unrelated_profile_noise(db_session, owner_records)
    service = StatsService(now_provider=lambda: datetime(2026, 4, 17, 12, 0, tzinfo=UTC))

    dashboard = service.build_dashboard_data(db_session, window_key="7d")

    assert dashboard.window.key == "7d"
    assert dashboard.owner_label_ru == "Основной поиск"
    assert dashboard.kpi.vacancies_found_total == 2
    assert dashboard.kpi.hot_count == 1
    assert dashboard.kpi.maybe_count == 1
    assert dashboard.kpi.rejected_count == 0
    assert dashboard.kpi.viewed_count == 3
    assert dashboard.kpi.opened_original_count == 1
    assert dashboard.kpi.saved_count == 2
    assert dashboard.kpi.applications_sent == 2
    assert dashboard.kpi.replies_received == 1
    assert dashboard.kpi.rejections_count == 1
    assert dashboard.kpi.interviews_count == 1
    assert dashboard.kpi.archived_count == 1
    assert dashboard.kpi.due_follow_ups == 1
    assert dashboard.kpi.overdue_next_actions == 2

    assert dashboard.source_rows[0].source_name == "BA"
    assert dashboard.source_rows[0].found_count == 1
    assert dashboard.source_rows[0].hot_count == 1
    assert dashboard.source_rows[0].applied_count == 1
    assert dashboard.source_rows[0].reply_count == 1
    assert dashboard.source_rows[0].interview_count == 1

    assert dashboard.timeline_mode_label_ru == "по дням"
    assert len(dashboard.timeline_rows) == 7
    assert any(row.label_ru == "15.04" and row.found_count == 1 for row in dashboard.timeline_rows)
    assert all(row.source_name != "NoiseBoard" for row in dashboard.source_rows)
    assert dashboard.attention_items[0].overdue is True
    assert dashboard.recent_activities[0].event_label_ru in {"Получен ответ", "Назначено собеседование", "Поиск вакансий"}


def test_stats_service_uses_weekly_timeline_for_30_days(
    db_session: Session,
    owner_records: dict[str, object],
) -> None:
    _seed_stats_dataset(db_session, owner_records)
    service = StatsService(now_provider=lambda: datetime(2026, 4, 17, 12, 0, tzinfo=UTC))

    dashboard = service.build_dashboard_data(db_session, window_key="30d")

    assert dashboard.window.key == "30d"
    assert dashboard.timeline_mode_label_ru == "по неделям"
    assert dashboard.timeline_rows
    assert dashboard.timeline_rows[0].label_ru.startswith("Неделя ")


def test_stats_service_returns_empty_state_without_profile(db_session: Session) -> None:
    service = StatsService(now_provider=lambda: datetime(2026, 4, 17, 12, 0, tzinfo=UTC))

    dashboard = service.build_dashboard_data(db_session, window_key="7d")

    assert dashboard.empty_state is True
    assert dashboard.warning_message == "Сначала сохраните профиль и выполните поиск, чтобы статистика стала полезной."
    assert dashboard.kpi.vacancies_found_total == 0
