from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import app.db.models  # noqa: F401
import pytest
from app.db.base import Base
from app.db.models.profiles import SearchProfile, UserProfile
from app.db.session import get_db
from app.main import create_app
from app.services.lead_service import LeadService, SearchLeadCandidate
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = testing_session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def owner_records(db_session: Session) -> dict[str, object]:
    user_profile = UserProfile(
        display_name="Основной профиль",
        country_code="DE",
        city="Berlin",
        legal_status="Section 24",
        work_authorized=True,
        german_level="A2",
        english_level="A1",
        raw_profile_text="Ищу склад и производство по Германии.",
    )
    db_session.add(user_profile)
    db_session.flush()

    search_profile = SearchProfile(
        user_profile_id=user_profile.id,
        name="Основной поиск",
        is_active=True,
        desired_roles=["склад", "упаковка", "производство"],
        excluded_roles=[],
        preferred_locations=["Berlin", "Deutschland"],
        relocation_ready=True,
        shift_ok=True,
        physical_work_ok=True,
        housing_needed=False,
        start_availability_text="Сразу",
        driver_license=None,
        car_available=False,
        notes="Ищу быстрый старт.",
    )
    db_session.add(search_profile)
    db_session.commit()
    return {"user_profile": user_profile, "search_profile": search_profile}


@pytest.fixture()
def lead_candidate() -> SearchLeadCandidate:
    return SearchLeadCandidate(
        source_id="ba",
        source_name="BA (Bundesagentur fur Arbeit)",
        source_external_id="10000-1234567890-S",
        vacancy_title="Lagermitarbeiter/in",
        translated_title_ru="Сотрудник склада",
        company_name="Logistik Nord GmbH",
        location_text="Berlin",
        summary_ru="Складская вакансия без высокого языкового барьера.",
        original_url="https://example.org/jobs/10000-1234567890-S",
        canonical_key="canonical-warehouse-berlin",
        bucket="hot",
    )


@pytest.fixture()
def saved_lead(db_session: Session, owner_records: dict[str, object], lead_candidate: SearchLeadCandidate):
    _ = owner_records
    result = LeadService().create_or_get_from_search_candidate(
        db_session,
        candidate=lead_candidate,
        action="save",
        event_at=datetime(2026, 4, 16, 8, 0, tzinfo=UTC),
    )
    db_session.commit()
    return result.lead
