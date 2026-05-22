from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.crm import ApplicationLead
from app.db.models.search import VacancyScore
from app.db.models.vacancies import VacancyCanonical, VacancySourceRecord
from app.main import create_app
from app.web.routes.stats import get_funnel_service, get_stats_service
from app.services.funnel_service import FunnelService
from app.services.stats_service import StatsService


def _seed_route_dataset(db_session: Session, owner_records: dict[str, object]) -> None:
    user_profile = owner_records["user_profile"]
    search_profile = owner_records["search_profile"]

    canonical = VacancyCanonical(
        normalized_title="lagermitarbeiter",
        company_name="Logistik Nord GmbH",
        location_text="Berlin",
        country_code="DE",
        city="Berlin",
        first_seen_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
    )
    db_session.add(canonical)
    db_session.flush()
    db_session.add(
        VacancySourceRecord(
            canonical_id=canonical.id,
            source_name="BA",
            external_id="ba-1",
            first_seen_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
            last_seen_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
        )
    )
    db_session.add(
        VacancyScore(
            vacancy_canonical_id=canonical.id,
            search_profile_id=search_profile.id,
            score_total=91,
            bucket="hot",
            explanation="Strong fit",
            scored_at=datetime(2026, 4, 15, 8, 5, tzinfo=UTC),
        )
    )
    db_session.add(
        ApplicationLead(
            user_profile_id=user_profile.id,
            search_profile_id=search_profile.id,
            source_id="ba",
            source_name="BA",
            source_external_id="ba-1",
            vacancy_title="Lagermitarbeiter/in",
            translated_title_ru="Сотрудник склада",
            found_at=datetime(2026, 4, 15, 8, 10, tzinfo=UTC),
            viewed_at=datetime(2026, 4, 15, 8, 12, tzinfo=UTC),
            saved_at=datetime(2026, 4, 15, 8, 20, tzinfo=UTC),
            applied_at=datetime(2026, 4, 16, 9, 0, tzinfo=UTC),
            reply_at=datetime(2026, 4, 17, 10, 0, tzinfo=UTC),
            interview_at=datetime(2026, 4, 17, 11, 0, tzinfo=UTC),
            status="interview_scheduled",
        )
    )
    db_session.commit()


def test_stats_page_renders_dashboard_blocks(client: TestClient, db_session: Session, owner_records: dict[str, object]) -> None:
    _seed_route_dataset(db_session, owner_records)
    app = client.app
    app.dependency_overrides[get_stats_service] = lambda: StatsService(
        now_provider=lambda: datetime(2026, 4, 17, 12, 0, tzinfo=UTC)
    )
    app.dependency_overrides[get_funnel_service] = lambda: FunnelService(
        now_provider=lambda: datetime(2026, 4, 17, 12, 0, tzinfo=UTC)
    )

    response = client.get("/stats?window=7d")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Сводка по периоду" in response.text
    assert "Короткий digest" in response.text
    assert "Воронка" in response.text
    assert "Качество источников" in response.text
    assert "BA" in response.text
    assert "Найдено вакансий" in response.text
    assert "response rate" in response.text


def test_stats_page_shows_empty_state_when_no_data(client: TestClient) -> None:
    app = client.app
    app.dependency_overrides[get_stats_service] = lambda: StatsService(
        now_provider=lambda: datetime(2026, 4, 17, 12, 0, tzinfo=UTC)
    )
    app.dependency_overrides[get_funnel_service] = lambda: FunnelService(
        now_provider=lambda: datetime(2026, 4, 17, 12, 0, tzinfo=UTC)
    )

    response = client.get("/stats?window=7d")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Пока мало данных" in response.text
    assert "Статистика" in response.text
