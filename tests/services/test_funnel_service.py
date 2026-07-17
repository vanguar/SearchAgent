from __future__ import annotations

from datetime import UTC, datetime

from app.db.models.crm import ApplicationLead
from app.db.models.profiles import SearchProfile
from app.db.models.vacancies import VacancyCanonical, VacancySourceRecord
from app.services.funnel_service import FunnelService
from sqlalchemy.orm import Session


def _seed_funnel_dataset(db_session: Session, owner_records: dict[str, object]) -> None:
    user_profile = owner_records["user_profile"]
    search_profile = owner_records["search_profile"]

    canonical_one = VacancyCanonical(
        normalized_title="lagermitarbeiter",
        company_name="Logistik Nord GmbH",
        location_text="Berlin",
        country_code="DE",
        city="Berlin",
        first_seen_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
    )
    canonical_two = VacancyCanonical(
        normalized_title="verpacker",
        company_name="Pack Team GmbH",
        location_text="Hamburg",
        country_code="DE",
        city="Hamburg",
        first_seen_at=datetime(2026, 4, 16, 8, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 4, 16, 8, 0, tzinfo=UTC),
    )
    db_session.add_all((canonical_one, canonical_two))
    db_session.flush()
    db_session.add_all(
        (
            VacancySourceRecord(
                canonical_id=canonical_one.id,
                source_name="BA",
                external_id="ba-1",
                first_seen_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
                last_seen_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
            ),
            VacancySourceRecord(
                canonical_id=canonical_two.id,
                source_name="Careerjet",
                external_id="cj-1",
                first_seen_at=datetime(2026, 4, 16, 8, 0, tzinfo=UTC),
                last_seen_at=datetime(2026, 4, 16, 8, 0, tzinfo=UTC),
            ),
        )
    )
    db_session.add_all(
        (
            ApplicationLead(
                user_profile_id=user_profile.id,
                search_profile_id=search_profile.id,
                source_id="ba",
                source_name="BA",
                source_external_id="ba-1",
                vacancy_title="Lagermitarbeiter/in",
                found_at=datetime(2026, 4, 15, 9, 0, tzinfo=UTC),
                viewed_at=datetime(2026, 4, 15, 9, 5, tzinfo=UTC),
                opened_original_at=datetime(2026, 4, 15, 9, 10, tzinfo=UTC),
                saved_at=datetime(2026, 4, 15, 9, 20, tzinfo=UTC),
                applied_at=datetime(2026, 4, 16, 10, 0, tzinfo=UTC),
                reply_at=datetime(2026, 4, 17, 8, 0, tzinfo=UTC),
                interview_at=datetime(2026, 4, 17, 11, 0, tzinfo=UTC),
                status="interview_scheduled",
            ),
            ApplicationLead(
                user_profile_id=user_profile.id,
                search_profile_id=search_profile.id,
                source_id="careerjet",
                source_name="Careerjet",
                source_external_id="cj-1",
                vacancy_title="Verpacker/in",
                found_at=datetime(2026, 4, 16, 9, 0, tzinfo=UTC),
                viewed_at=datetime(2026, 4, 16, 9, 5, tzinfo=UTC),
                saved_at=datetime(2026, 4, 16, 10, 0, tzinfo=UTC),
                status="saved",
            ),
        )
    )
    db_session.commit()


def _seed_unrelated_profile_noise(db_session: Session, owner_records: dict[str, object]) -> None:
    user_profile = owner_records["user_profile"]

    secondary_profile = SearchProfile(
        user_profile_id=user_profile.id,
        name="Второй профиль",
        is_active=False,
        desired_roles=["офис"],
        excluded_roles=[],
        preferred_locations=["Munchen"],
        relocation_ready=False,
        shift_ok=False,
        physical_work_ok=False,
        housing_needed=False,
        start_availability_text="Через месяц",
        driver_license=None,
        car_available=False,
        notes="Шум для проверки scoping.",
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
        ApplicationLead(
            user_profile_id=user_profile.id,
            search_profile_id=secondary_profile.id,
            source_id="noise",
            source_name="NoiseBoard",
            source_external_id="noise-1",
            vacancy_title="Office Assistant",
            found_at=datetime(2026, 4, 16, 9, 0, tzinfo=UTC),
            viewed_at=datetime(2026, 4, 16, 9, 5, tzinfo=UTC),
            saved_at=datetime(2026, 4, 16, 9, 10, tzinfo=UTC),
            applied_at=datetime(2026, 4, 16, 9, 15, tzinfo=UTC),
            status="applied",
        )
    )
    db_session.commit()


def test_funnel_service_builds_stage_counts_and_conversions(
    db_session: Session,
    owner_records: dict[str, object],
) -> None:
    _seed_funnel_dataset(db_session, owner_records)
    _seed_unrelated_profile_noise(db_session, owner_records)
    service = FunnelService(now_provider=lambda: datetime(2026, 4, 17, 12, 0, tzinfo=UTC))

    dashboard = service.build_dashboard_data(db_session, window_key="7d")

    stage_map = {stage.key: stage.count for stage in dashboard.stages}
    metric_map = {metric.key: metric.value_ru for metric in dashboard.metrics}

    assert dashboard.empty_state is False
    assert dashboard.cohort_label_ru == "Когорта лидов, найденных за последние 7 дней"
    assert stage_map == {
        "found": 2,
        "viewed": 2,
        "opened": 1,
        "saved": 2,
        "applied": 1,
        "reply": 1,
        "interview": 1,
        "rejected": 0,
        "archived": 0,
    }
    assert metric_map["found_to_saved"] == "2 из 2 (100%)"
    assert metric_map["saved_to_applied"] == "1 из 2 (50%)"
    assert metric_map["applied_to_reply"] == "1 из 1 (100%)"
    assert metric_map["applied_to_interview"] == "1 из 1 (100%)"
    assert metric_map["applied_to_rejected"] == "0 из 1 (0%)"


def test_funnel_service_returns_empty_state_without_profile(db_session: Session) -> None:
    service = FunnelService(now_provider=lambda: datetime(2026, 4, 17, 12, 0, tzinfo=UTC))

    dashboard = service.build_dashboard_data(db_session, window_key="7d")

    assert dashboard.empty_state is True
    assert dashboard.warning_message == "Сначала сохраните профиль и выполните поиск, чтобы статистика стала полезной."
