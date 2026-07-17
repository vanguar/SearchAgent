from app.main import create_app
from fastapi.testclient import TestClient


def test_root_redirects_to_dashboard() -> None:
    client = TestClient(create_app())

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"


def test_dashboard_after_redirect_has_russian_menu() -> None:
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "Профиль" in response.text
    assert "Поиск вакансий" in response.text
    assert "Главная" in response.text
