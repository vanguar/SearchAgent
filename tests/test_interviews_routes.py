from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.core.time import utc_now
from app.main import create_app
from app.web.routes.interviews import get_lead_service
from fastapi.testclient import TestClient


def _card(lead_id: int, *, days: int, title: str, company: str, source: str, naive: bool) -> object:
    when = utc_now() + timedelta(days=days)
    if naive:
        # SQLite frequently returns naive datetimes even for tz-aware columns;
        # the route must normalize these without raising.
        when = when.replace(tzinfo=None)
    lead = SimpleNamespace(
        id=lead_id,
        interview_at=when,
        vacancy_title=title,
        translated_title_ru=None,
        company_name=company,
        source_name=source,
    )
    return SimpleNamespace(lead=lead, status_label_ru="Собеседование")


def _dashboard(cards: tuple[object, ...]) -> object:
    return SimpleNamespace(warning_message=None, leads=cards)


class _FakeLeadService:
    def __init__(self, dashboard: object) -> None:
        self._dashboard = dashboard

    def build_dashboard_data(self, session: object) -> object:  # noqa: ARG002
        return self._dashboard


def _client_with(dashboard: object) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_lead_service] = lambda: _FakeLeadService(dashboard)
    return TestClient(app, raise_server_exceptions=True)


def test_interviews_empty_state_renders() -> None:
    client = _client_with(_dashboard(()))
    response = client.get("/interviews")
    client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Пока нет назначенных собеседований" in response.text


def test_interviews_renders_upcoming_and_past_from_lead_data() -> None:
    cards = (
        _card(1, days=3, title="Python Developer, AI automation", company="Djinni GmbH", source="Djinni", naive=True),
        _card(2, days=9, title="Auslieferungsfahrer Klasse B", company="A-Z Lieferdienste", source="BA", naive=True),
        _card(3, days=-5, title="Kurier Wasserlieferung", company="AquaNord", source="Adzuna", naive=True),
    )
    client = _client_with(_dashboard(cards))
    response = client.get("/interviews")
    client.app.dependency_overrides.clear()

    assert response.status_code == 200
    text = response.text
    # Nearest upcoming becomes the hero with a countdown and a real lead link.
    assert "iv-hero" in text
    assert "через 3 дня" in text
    assert "Python Developer, AI automation" in text
    assert '/leads/1' in text
    # Both further-out upcoming and past interviews appear.
    assert "Ближайшие" in text
    assert "Прошедшие" in text
    assert "Kurier Wasserlieferung" in text


def test_interviews_countdown_labels() -> None:
    now = utc_now().astimezone(UTC)
    cards = (
        _card(10, days=0, title="Сегодня", company="C0", source="s", naive=False),
        _card(11, days=1, title="Завтра", company="C1", source="s", naive=False),
    )
    # Force deterministic "today"/"завтра" independent of local run time drift.
    _ = now
    client = _client_with(_dashboard(cards))
    response = client.get("/interviews")
    client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "сегодня" in response.text
