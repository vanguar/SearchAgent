"""Создание, выбор, редактирование и независимость профилей поиска через веб-формы.

Это регрессия на главное требование: профили создаёт и правит сам пользователь
через интерфейс, каждый профиль живёт отдельно, и правка одного не задевает
другой. Ни одного предустановленного профиля в базе появляться не должно.
"""
from __future__ import annotations

from app.db.models.profiles import SearchProfile, UserProfile
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

_VEHICLE_FORM = {
    "name": "Перегон автомобилей",
    "primary_role": "Перегон автомобилей",
    "additional_roles": "Fahrzeuglogistik",
    "excluded_roles": "Paketzustellung",
    "search_query_terms": "Fahrzeugüberführer, Überführungsfahrer",
    "additional_search_terms": "PKW-Rangierer",
    "primary_city": "Neustrelitz",
    "additional_cities": "Berlin",
    "search_radius_km": "50",
    "employment_types": "full_time",
    "min_salary_eur_per_hour": "16",
    "physical_work_ok": "false",
    "driver_license": "B",
    "car_available": "true",
    "self_employment_ok": "false",
    "relocation_ready": "false",
    "shift_ok": "true",
    "housing_needed": "",
    "start_availability_text": "с 16.11.2026",
    "no_german_required_present": "1",
    "notes": "минимум общения на немецком",
}

_WAREHOUSE_FORM = {
    "name": "Склад",
    "primary_role": "склад",
    "primary_city": "Rostock",
    "search_radius_km": "25",
    "no_german_required_present": "1",
}


def _profiles(db_session: Session) -> list[SearchProfile]:
    return list(db_session.query(SearchProfile).order_by(SearchProfile.id).all())


# ---------------------------------------------------------------------------
# Никаких предустановленных профилей
# ---------------------------------------------------------------------------


def test_fresh_install_has_no_preinstalled_profile(client: TestClient, db_session: Session) -> None:
    """База пустая до того, как человек сам создаст профиль."""
    response = client.get("/profile")

    assert response.status_code == 200
    assert _profiles(db_session) == []
    assert "Профилей пока нет" in response.text


# ---------------------------------------------------------------------------
# Создание
# ---------------------------------------------------------------------------


def test_new_profile_form_exposes_every_search_criterion(client: TestClient) -> None:
    """Все критерии, влияющие на поиск, задаются через форму, а не правкой базы."""
    response = client.get("/profile/new")

    assert response.status_code == 200
    for field in (
        "primary_role",
        "additional_roles",
        "excluded_roles",
        "search_query_terms",
        "additional_search_terms",
        "primary_city",
        "additional_cities",
        "search_radius_km",
        "employment_types",
        "min_salary_eur_per_hour",
        "physical_work_ok",
        "driver_license",
        "car_available",
        "self_employment_ok",
        "relocation_ready",
        "shift_ok",
        "housing_needed",
        "start_availability_text",
        "no_german_required",
        "notes",
    ):
        assert f'name="{field}"' in response.text, field


def test_new_profile_form_keeps_the_free_text_path(client: TestClient) -> None:
    """Форма добавлена РЯДОМ с рассказом свободным текстом, а не вместо него."""
    assert '/profile/intake' in client.get("/profile/new").text
    assert '/profile/intake' in client.get("/profile").text


def test_create_profile_stores_every_submitted_criterion(client: TestClient, db_session: Session) -> None:
    response = client.post("/profile/new", data=_VEHICLE_FORM, follow_redirects=False)

    assert response.status_code == 303
    profile = _profiles(db_session)[0]
    assert profile.name == "Перегон автомобилей"
    assert profile.desired_roles == ["Перегон автомобилей", "Fahrzeuglogistik"]
    assert profile.excluded_roles == ["Paketzustellung"]
    assert profile.search_query_terms == ["Fahrzeugüberführer", "Überführungsfahrer"]
    assert profile.additional_search_terms == ["PKW-Rangierer"]
    assert profile.preferred_locations == ["Neustrelitz", "Berlin"]
    assert profile.search_radius_km == 50
    assert profile.employment_types == ["full_time"]
    assert profile.min_salary_eur_per_hour == 16.0
    assert profile.physical_work_ok is False
    assert profile.driver_license == "B"
    assert profile.car_available is True
    assert profile.self_employment_ok is False
    assert profile.relocation_ready is False
    assert profile.shift_ok is True
    assert profile.start_availability_text == "с 16.11.2026"
    assert profile.notes == "минимум общения на немецком"


def test_create_profile_derives_search_prefill_from_terms(client: TestClient, db_session: Session) -> None:
    """Главный запрос — осознанно введённое слово, а не автоперевод роли."""
    client.post("/profile/new", data=_VEHICLE_FORM, follow_redirects=False)

    profile = _profiles(db_session)[0]
    assert profile.search_query_de == "Fahrzeugüberführer"
    assert profile.search_location_de == "Neustrelitz, Berlin"


def test_unfilled_fields_stay_unspecified(client: TestClient, db_session: Session) -> None:
    """Незаполненное поле остаётся None, а не подменяется значением по умолчанию."""
    client.post("/profile/new", data={"name": "Минимум", "primary_role": "склад"}, follow_redirects=False)

    profile = _profiles(db_session)[0]
    assert profile.search_radius_km is None
    assert profile.min_salary_eur_per_hour is None
    assert profile.self_employment_ok is None
    assert profile.physical_work_ok is None
    assert profile.relocation_ready is None
    assert profile.employment_types is None


def test_first_profile_becomes_default_and_second_does_not(client: TestClient, db_session: Session) -> None:
    client.post("/profile/new", data=_VEHICLE_FORM, follow_redirects=False)
    client.post("/profile/new", data=_WAREHOUSE_FORM, follow_redirects=False)

    first, second = _profiles(db_session)
    assert first.is_default is True
    assert second.is_default is False


def test_create_profile_without_user_profile_creates_one(client: TestClient, db_session: Session) -> None:
    """Человек может начать сразу с формы, не проходя интейк."""
    client.post("/profile/new", data=_WAREHOUSE_FORM, follow_redirects=False)

    assert db_session.query(UserProfile).count() == 1
    assert _profiles(db_session)[0].user_profile_id == db_session.query(UserProfile).first().id


# ---------------------------------------------------------------------------
# Редактирование
# ---------------------------------------------------------------------------


def test_edit_form_shows_saved_values(client: TestClient, db_session: Session) -> None:
    client.post("/profile/new", data=_VEHICLE_FORM, follow_redirects=False)
    profile_id = _profiles(db_session)[0].id

    response = client.get(f"/profile/{profile_id}/edit")

    assert response.status_code == 200
    assert 'value="Перегон автомобилей"' in response.text
    assert 'value="Fahrzeugüberführer, Überführungsfahrer"' in response.text
    assert 'value="Neustrelitz"' in response.text
    assert '<option value="50"\n        selected' in response.text or 'value="50"' in response.text


def test_edit_changes_only_submitted_fields(client: TestClient, db_session: Session) -> None:
    """Поле, которого в форме не было, не трогается."""
    client.post("/profile/new", data=_VEHICLE_FORM, follow_redirects=False)
    profile_id = _profiles(db_session)[0].id

    response = client.post(
        f"/profile/{profile_id}/edit",
        data={"name": "Перегон (изменён)", "search_radius_km": "75"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.expire_all()
    profile = db_session.get(SearchProfile, profile_id)
    assert profile.name == "Перегон (изменён)"
    assert profile.search_radius_km == 75
    # Не отправляли — значит не меняли.
    assert profile.search_query_terms == ["Fahrzeugüberführer", "Überführungsfahrer"]
    assert profile.driver_license == "B"


def test_edit_can_clear_a_submitted_list(client: TestClient, db_session: Session) -> None:
    """Пустое пришедшее поле очищает список: иначе его нельзя было бы сократить."""
    client.post("/profile/new", data=_VEHICLE_FORM, follow_redirects=False)
    profile_id = _profiles(db_session)[0].id

    client.post(
        f"/profile/{profile_id}/edit",
        data={"name": "Перегон", "excluded_roles": "", "additional_search_terms": ""},
        follow_redirects=False,
    )

    db_session.expire_all()
    profile = db_session.get(SearchProfile, profile_id)
    assert profile.excluded_roles is None
    assert profile.additional_search_terms is None


def test_edit_can_turn_a_tristate_back_to_unspecified(client: TestClient, db_session: Session) -> None:
    client.post("/profile/new", data=_VEHICLE_FORM, follow_redirects=False)
    profile_id = _profiles(db_session)[0].id

    client.post(
        f"/profile/{profile_id}/edit",
        data={"name": "Перегон", "self_employment_ok": "", "physical_work_ok": ""},
        follow_redirects=False,
    )

    db_session.expire_all()
    profile = db_session.get(SearchProfile, profile_id)
    assert profile.self_employment_ok is None
    assert profile.physical_work_ok is None


def test_changing_roles_without_terms_drops_stale_terms(client: TestClient, db_session: Session) -> None:
    """Сменилась профессия, а слова формой не задавали — старые слова не о ней."""
    client.post("/profile/new", data=_VEHICLE_FORM, follow_redirects=False)
    profile_id = _profiles(db_session)[0].id

    client.post(
        f"/profile/{profile_id}/edit",
        data={"name": "Склад", "primary_role": "склад"},
        follow_redirects=False,
    )

    db_session.expire_all()
    profile = db_session.get(SearchProfile, profile_id)
    assert profile.desired_roles == ["склад"]
    assert profile.search_query_terms is None


def test_unchecking_no_german_required_is_saved(client: TestClient, db_session: Session) -> None:
    client.post(
        "/profile/new",
        data={**_WAREHOUSE_FORM, "no_german_required": "on"},
        follow_redirects=False,
    )
    profile_id = _profiles(db_session)[0].id
    assert db_session.get(SearchProfile, profile_id).no_german_required is True

    client.post(
        f"/profile/{profile_id}/edit",
        data={"name": "Склад", "no_german_required_present": "1"},
        follow_redirects=False,
    )

    db_session.expire_all()
    assert db_session.get(SearchProfile, profile_id).no_german_required is False


# ---------------------------------------------------------------------------
# Независимость профилей
# ---------------------------------------------------------------------------


def test_editing_one_profile_leaves_the_other_untouched(client: TestClient, db_session: Session) -> None:
    client.post("/profile/new", data=_VEHICLE_FORM, follow_redirects=False)
    client.post("/profile/new", data=_WAREHOUSE_FORM, follow_redirects=False)
    vehicle, warehouse = _profiles(db_session)
    warehouse_snapshot = (
        warehouse.name,
        list(warehouse.desired_roles or []),
        warehouse.search_radius_km,
        list(warehouse.preferred_locations or []),
    )

    client.post(
        f"/profile/{vehicle.id}/edit",
        data={
            "name": "Перегон v2",
            "primary_role": "Перегон автомобилей",
            "search_radius_km": "100",
            "primary_city": "Neubrandenburg",
        },
        follow_redirects=False,
    )

    db_session.expire_all()
    changed = db_session.get(SearchProfile, vehicle.id)
    untouched = db_session.get(SearchProfile, warehouse.id)
    assert changed.search_radius_km == 100
    assert changed.preferred_locations == ["Neubrandenburg"]
    assert (
        untouched.name,
        list(untouched.desired_roles or []),
        untouched.search_radius_km,
        list(untouched.preferred_locations or []),
    ) == warehouse_snapshot


def test_duplicate_carries_new_criteria_and_stays_separate(client: TestClient, db_session: Session) -> None:
    client.post("/profile/new", data=_VEHICLE_FORM, follow_redirects=False)
    source_id = _profiles(db_session)[0].id

    client.post(f"/profile/{source_id}/duplicate", follow_redirects=False)

    db_session.expire_all()
    source, copy = _profiles(db_session)
    assert copy.id != source.id
    assert copy.name == "Перегон автомобилей (копия)"
    assert copy.search_radius_km == 50
    assert copy.employment_types == ["full_time"]
    assert copy.min_salary_eur_per_hour == 16.0
    assert copy.self_employment_ok is False
    assert copy.additional_search_terms == ["PKW-Rangierer"]
    assert copy.is_default is False


def test_delete_one_profile_keeps_the_other(client: TestClient, db_session: Session) -> None:
    client.post("/profile/new", data=_VEHICLE_FORM, follow_redirects=False)
    client.post("/profile/new", data=_WAREHOUSE_FORM, follow_redirects=False)
    vehicle, warehouse = _profiles(db_session)

    client.post(f"/profile/{vehicle.id}/delete", follow_redirects=False)

    db_session.expire_all()
    remaining = _profiles(db_session)
    assert [p.id for p in remaining] == [warehouse.id]
    # Профиль по умолчанию удалён — следующий занимает его место, иначе поиску
    # нечего было бы открывать.
    assert remaining[0].is_default is True


# ---------------------------------------------------------------------------
# Выбор профиля при поиске
# ---------------------------------------------------------------------------


def test_search_form_prefills_from_the_selected_profile(client: TestClient, db_session: Session) -> None:
    client.post("/profile/new", data=_VEHICLE_FORM, follow_redirects=False)
    client.post("/profile/new", data=_WAREHOUSE_FORM, follow_redirects=False)
    vehicle, warehouse = _profiles(db_session)

    vehicle_page = client.get(f"/jobs?profile_id={vehicle.id}")
    warehouse_page = client.get(f"/jobs?profile_id={warehouse.id}")

    assert 'value="Fahrzeugüberführer"' in vehicle_page.text
    assert "Neustrelitz, Berlin" in vehicle_page.text
    assert 'value="50"\n            selected' in vehicle_page.text or "+50 км" in vehicle_page.text

    assert "Rostock" in warehouse_page.text
    assert "Neustrelitz" not in warehouse_page.text


def test_profile_autofill_partial_returns_the_chosen_profile_radius(
    client: TestClient, db_session: Session
) -> None:
    client.post("/profile/new", data=_WAREHOUSE_FORM, follow_redirects=False)
    profile_id = _profiles(db_session)[0].id

    response = client.get(f"/jobs/profile-autofill?profile_id={profile_id}")

    assert response.status_code == 200
    assert 'name="radius_km"' in response.text
    assert 'value="25"' in response.text


# ---------------------------------------------------------------------------
# Общий профиль пользователя отдельно от профилей поиска
# ---------------------------------------------------------------------------


def test_language_levels_are_shared_by_every_search_profile(
    client: TestClient, db_session: Session
) -> None:
    """Немецкий не становится другим от того, что создан второй профиль поиска."""
    client.post("/profile/new", data=_VEHICLE_FORM, follow_redirects=False)
    client.post("/profile/new", data=_WAREHOUSE_FORM, follow_redirects=False)

    response = client.post(
        "/profile/languages",
        data={"german_level": "A1", "english_level": "A2", "legal_status": "§24"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.expire_all()
    user_profile = db_session.query(UserProfile).first()
    assert (user_profile.german_level, user_profile.english_level) == ("A1", "A2")
    assert user_profile.legal_status == "§24"
    # Оба профиля поиска ссылаются на один и тот же профиль человека.
    assert {p.user_profile_id for p in _profiles(db_session)} == {user_profile.id}


def test_blank_language_level_clears_it(client: TestClient, db_session: Session) -> None:
    """Пустое значение — это «не указано», и оно должно сохраняться как таковое."""
    client.post("/profile/new", data=_WAREHOUSE_FORM, follow_redirects=False)
    client.post("/profile/languages", data={"german_level": "B1"}, follow_redirects=False)
    db_session.expire_all()
    assert db_session.query(UserProfile).first().german_level == "B1"

    client.post("/profile/languages", data={"german_level": ""}, follow_redirects=False)

    db_session.expire_all()
    assert db_session.query(UserProfile).first().german_level is None
