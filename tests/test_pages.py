import re
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.llm_client import LLMStatus, _set_runtime_status


PAGE_CASES: tuple[tuple[str, str, str], ...] = (
    ("/dashboard", "Главная", "Главная"),
    ("/profile", "Профиль", "Профиль"),
    ("/jobs", "Поиск вакансий", "Поиск вакансий"),
    ("/leads", "Отклики", "Отклики"),
    ("/email", "Переписка", "Переписка"),
    ("/interviews", "Собеседования", "Собеседования"),
    ("/stats", "Статистика", "Статистика"),
    ("/settings", "Настройки", "Настройки"),
)


@pytest.mark.parametrize(("path", "title", "active_label"), PAGE_CASES)
def test_phase3_pages_render_with_active_menu(path: str, title: str, active_label: str) -> None:
    client = TestClient(create_app())

    response = client.get(path)

    assert response.status_code == 200
    assert f"<h2>{title}</h2>" in response.text

    active_link_pattern = rf'class="menu-item is-active"[^>]*>\s*{re.escape(active_label)}\s*</a>'
    assert re.search(active_link_pattern, response.text)


def test_htmx_placeholder_fragment_contract() -> None:
    client = TestClient(create_app())

    response = client.get("/htmx/placeholder/jobs", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert "рабочая зона" in response.text
    assert "<li>" in response.text
    assert "<html" not in response.text.lower()


def test_sidebar_shows_configured_when_key_exists_before_first_llm_call() -> None:
    _set_runtime_status(LLMStatus.MISSING_CONFIG)
    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-key"}):
        client = TestClient(create_app())
        response = client.get("/profile")

    assert response.status_code == 200
    assert "ИИ настроен" in response.text
    assert "ИИ отключён" not in response.text
