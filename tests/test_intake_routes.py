from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.services.intake_models import IntakeAnalysisResult, IntakeProfileDraft, IntakeSaveResult
from app.web.deps import get_intake_agent


EXAMPLE_TEXT = (
    "Я в Германии, по 24 параграфу, немецкого почти не знаю, английский слабый, "
    "ищу склад, упаковку, производство, можно по всей Германии, готов к переезду, смены ок."
)


def _build_client_with_sqlite_db() -> TestClient:
    app = create_app()
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_intake_page_renders() -> None:
    client = TestClient(create_app())

    response = client.get("/profile/intake")

    assert response.status_code == 200
    assert "Intake профиля" in response.text


def test_intake_analyze_returns_confirmation_and_questions() -> None:
    client = TestClient(create_app())

    response = client.post("/profile/intake/analyze", data={"free_text": "Ищу работу"})

    assert response.status_code == 200
    assert "Подтверждение профиля" in response.text
    assert "Нужно уточнить несколько критичных полей" in response.text


def test_intake_confirm_saves_profile_when_critical_fields_are_complete() -> None:
    class _FakeAgent:
        def analyze(self, free_text: str, followup_answers: dict | None = None) -> IntakeAnalysisResult:
            return IntakeAnalysisResult(
                draft=IntakeProfileDraft(
                    desired_roles=["Склад"],
                    preferred_regions=["Deutschland"],
                    willing_to_relocate=True,
                    german_level="none",
                    shift_ok=True,
                    work_authorized=True,
                    legal_status="section_24",
                ),
                missing_fields=(),
                questions=(),
                warnings=(),
            )

        def save_confirmed_analysis(self, *, analysis: IntakeAnalysisResult, free_text: str, db: Session) -> IntakeSaveResult:
            return IntakeSaveResult(saved=True, message="Профиль успешно сохранен.", user_profile_id=1, search_profile_id=1)

    app = create_app()
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_intake_agent] = lambda: _FakeAgent()
    client = TestClient(app)

    response = client.post("/profile/intake/confirm", data={"free_text": EXAMPLE_TEXT})

    assert response.status_code == 200
    assert "Профиль успешно сохранен" in response.text


def test_intake_confirm_does_not_double_analyze() -> None:
    class FakeIntakeAgent:
        def __init__(self) -> None:
            self.analyze_calls = 0
            self.save_calls = 0

        def analyze(self, free_text: str, followup_answers: dict[str, str] | None = None) -> IntakeAnalysisResult:
            _ = free_text
            _ = followup_answers
            self.analyze_calls += 1
            return IntakeAnalysisResult(
                draft=IntakeProfileDraft(
                    desired_roles=["Склад"],
                    preferred_regions=["Вся Германия"],
                    willing_to_relocate=True,
                    german_level="basic",
                    shift_ok=True,
                    work_authorized=True,
                ),
                missing_fields=(),
                questions=(),
                warnings=(),
            )

        def save_confirmed_analysis(self, *, analysis: IntakeAnalysisResult, free_text: str, db: Session) -> IntakeSaveResult:
            _ = analysis
            _ = free_text
            _ = db
            self.save_calls += 1
            return IntakeSaveResult(saved=True, message="Профиль успешно сохранен.", user_profile_id=1, search_profile_id=1)

    fake_agent = FakeIntakeAgent()

    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_intake_agent] = lambda: fake_agent

    client = TestClient(app)
    response = client.post("/profile/intake/confirm", data={"free_text": EXAMPLE_TEXT})

    assert response.status_code == 200
    assert fake_agent.analyze_calls == 1
    assert fake_agent.save_calls == 1
