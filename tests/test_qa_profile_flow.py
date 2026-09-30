from __future__ import annotations

import re
from dataclasses import replace

import pytest
from app.core.config import Settings
from app.db.models.profiles import SearchProfile, UserProfile
from app.services.profile_form_spec import parse_profile_form
from app.services.search_history_service import SearchHistoryService
from app.services.search_profile_resolver import DatabaseSearchProfileResolver
from app.services.search_service import SearchService
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.models import AdapterSearchResponse, SourceRecordPreview
from app.services.source_adapters.registry import SourceAdapterRegistry
from app.web.routes.jobs import get_search_history_service, get_search_service
from sqlalchemy.orm import sessionmaker


class FlowAdapter(BaseSourceAdapter):
    source_id = "test"
    display_name = "Test"

    def __init__(self):
        self.inputs = []

    def is_enabled(self):
        return True

    def search(self, search_input):
        self.inputs.append(search_input)
        records = tuple(SourceRecordPreview(
            source_id=self.source_id, source_name=self.display_name, external_id=str(i), source_reference=None,
            title=title, company="Example", location="Berlin", posted_at=None, detail_url=None,
            raw_payload={"description": "Vollzeit. Klasse B. Kein Gewerbeschein notwendig. Deutsch A1 ausreichend."},
            description_complete=True,
        ) for i, title in enumerate(("Fahrzeugüberführer", "Lagerhelfer", "Python Developer", "Produktionshelfer")))
        return AdapterSearchResponse(self.source_id, self.display_name, records, len(records),
                                     search_input.page, search_input.page_size, {})


def test_create_edit_select_and_search_keeps_profiles_independent(client, db_session):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    adapter = FlowAdapter()
    resolver = DatabaseSearchProfileResolver(session_factory=factory)
    service = SearchService(registry=SourceAdapterRegistry(adapters=(adapter,)),
                            profile_resolver=resolver, settings=Settings(search_max_pages=1))
    client.app.dependency_overrides[get_search_service] = lambda: service
    client.app.dependency_overrides[get_search_history_service] = lambda: SearchHistoryService(session_factory=factory)
    vehicle = {
        "name": "Мой первый поиск", "primary_role": "Перегон автомобилей",
        "additional_roles": "Fahrzeuglogistik", "excluded_roles": "Paketzusteller",
        "search_query_terms": "Fahrzeugüberführer", "additional_search_terms": "PKW-Rangierer",
        "primary_city": "Berlin", "additional_cities": "Potsdam", "search_radius_km": "25",
        "employment_types": "full_time", "min_salary_eur_per_hour": "16",
        "physical_work_ok": "false", "driver_license": "B", "car_available": "false",
        "self_employment_ok": "false", "relocation_ready": "false", "shift_ok": "true",
        "start_availability_text": "с 16.11.2099", "notes": "Мои заметки",
        "no_german_required_present": "1",
    }
    assert client.post("/profile/new", data=vehicle, follow_redirects=False).status_code == 303
    assert client.post("/profile/new", data={"name": "Второй", "primary_role": "склад", "primary_city": "Berlin"},
                       follow_redirects=False).status_code == 303
    first, second = db_session.query(SearchProfile).order_by(SearchProfile.id).all()
    before = resolver.resolve(profile_id=second.id)
    assert client.post(f"/profile/{first.id}/edit", data={**vehicle, "search_radius_km": "50", "notes": "Изменено"},
                       follow_redirects=False).status_code == 303
    assert resolver.resolve(profile_id=second.id) == before
    assert client.get(f"/profile/{first.id}/edit").status_code == 200
    page = client.get(f"/jobs?profile_id={first.id}")
    assert page.status_code == 200 and "Мой первый поиск" in page.text
    result = client.post("/jobs/search-results", data={
        "profile_id": str(first.id), "query": "Fahrzeugüberführer", "location": "Berlin",
        "source": "test", "radius_km": "",
    })
    assert result.status_code == 200
    assert "Fahrzeugüberführer" in result.text and "Изменено" in result.text
    assert adapter.inputs and all(x.radius_km == 50 for x in adapter.inputs)
    context = resolver.resolve(profile_id=first.id)
    assert context.desired_roles == ("Перегон автомобилей", "Fahrzeuglogistik")
    assert context.excluded_roles == ("Paketzusteller",)
    assert context.additional_search_terms == ("PKW-Rangierer",)
    assert context.preferred_locations == ("Berlin", "Potsdam")
    assert context.employment_types == ("full_time",)
    assert context.min_salary_eur_per_hour == 16
    assert context.physical_work_ok is False and context.self_employment_ok is False
    assert context.car_available is False and context.driver_license == "B"
    assert context.shift_ok is True and context.relocation_ready is False
    assert context.start_availability_text == "с 16.11.2099"
    assert resolver.resolve(profile_id=second.id) == before
    # Renaming never changes any selection/scoring attributes.
    renamed = replace(context, profile_label="Другое имя")
    assert replace(renamed, profile_label=context.profile_label) == context


def test_unchecking_all_employment_types_clears_only_selected_profile(client, db_session):
    for name in ("Первый", "Второй"):
        client.post("/profile/new", data={"name": name, "employment_types": "full_time"})
    first, second = db_session.query(SearchProfile).order_by(SearchProfile.id).all()
    page = client.get(f"/profile/{first.id}/edit")
    assert 'name="employment_types_present"' in page.text
    client.post(f"/profile/{first.id}/edit", data={"employment_types_present": "1"})
    db_session.expire_all()
    assert first.employment_types is None
    assert second.employment_types == ["full_time"]


def _license_shown_as_chosen(page_text: str) -> str:
    """Категории прав, отмеченные на странице правки, в порядке разметки.

    Проверять разметку построчно нельзя: способ выбора категорий — дело внешнего
    вида и меняется. Неизменно другое: страница правки обязана показывать ровно
    то, что сохранено, иначе открыть профиль значило бы молча его переписать.
    """
    checked = re.findall(
        r'<input[^>]*name="driver_license"[^>]*value="([^"]*)"[^>]*checked',
        page_text,
    )
    return ", ".join(value for value in checked if value)


@pytest.mark.parametrize("license_text", ["B, CE", "B/CE", "B (EU)", "B196"])
def test_edit_preserves_existing_license_representation(client, db_session, owner_records, license_text):
    profile = owner_records["search_profile"]
    profile.driver_license = license_text
    db_session.commit()
    page = client.get(f"/profile/{profile.id}/edit")
    assert _license_shown_as_chosen(page.text) == license_text
    client.post(f"/profile/{profile.id}/edit", data={"name": "Переименован", "driver_license": license_text})
    db_session.refresh(profile)
    assert profile.driver_license == license_text


@pytest.mark.parametrize("salary", ["nan", "NaN", "inf", "-inf"])
def test_non_finite_salary_is_not_saved(salary):
    assert parse_profile_form({"min_salary_eur_per_hour": salary}).min_salary_eur_per_hour is None


def test_profile_page_survives_an_unreadable_database(client, db_session):
    """Непромигрированная база — это пустой список, а не 500.

    Читаются два поля подряд: список профилей и город проживания. Без отката
    после первой ошибки сессия остаётся в сломанной транзакции, и второе чтение
    роняет страницу целиком.
    """
    SearchProfile.__table__.drop(db_session.get_bind())
    UserProfile.__table__.drop(db_session.get_bind())

    response = client.get("/profile")

    assert response.status_code == 200
    assert "Профилей пока нет" in response.text
