"""Что страница профилей показывает человеку про заданные им параметры.

Две вещи здесь проверяются как обещания интерфейса, а не как разметка:

1. Заданный параметр отмечен заполненным, незаданный — нет. Отметку ставит
   сервер, поэтому открытый на правку профиль показывает её сразу, до любого
   взаимодействия; браузерный скрипт потом только переставляет ту же отметку.
2. Внутренний код («section_24») человеку не показывается. В базе он остаётся:
   на него смотрят правила поиска, и переписывать сохранённое ради подписи
   нельзя.

Разметку тесты разбирают, а не ищут подстрокой: способ нарисовать поле — дело
внешнего вида и меняется, а эти два обещания меняться не должны.
"""
from __future__ import annotations

from html.parser import HTMLParser

from app.db.models.profiles import SearchProfile, UserProfile
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

_VOID_TAGS = frozenset({"input", "img", "br", "hr", "meta", "link", "source"})
# `option` тоже собирается: у select выбранное значение помечено на нём, а не
# на самом select.
_CONTROL_TAGS = frozenset({"input", "select", "textarea", "option"})


class _FieldCollector(HTMLParser):
    """Обёртки полей `[data-pf-field]` вместе с их состоянием и контролами."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: list[dict[str, object]] = []
        self._current: dict[str, object] | None = None
        self._tag: str | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: (value or "") for key, value in attrs}
        if self._current is None:
            if "data-pf-field" in values:
                self._current = {
                    "kind": values.get("data-pf-kind", ""),
                    "state": values.get("data-pf-state", ""),
                    "classes": values.get("class", "").split(),
                    "controls": [],
                }
                self._tag = tag
                self._depth = 1
            return

        if tag == self._tag and tag not in _VOID_TAGS:
            self._depth += 1
        if tag in _CONTROL_TAGS:
            controls = self._current["controls"]
            assert isinstance(controls, list)
            controls.append(values)

    def handle_endtag(self, tag: str) -> None:
        if self._current is None or tag != self._tag:
            return
        self._depth -= 1
        if self._depth == 0:
            self.fields.append(self._current)
            self._current = None
            self._tag = None


class _VisibleText(HTMLParser):
    """Текст, который человек действительно читает на странице."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._muted = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._muted += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._muted:
            self._muted -= 1

    def handle_data(self, data: str) -> None:
        if not self._muted:
            self._chunks.append(data)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self._chunks).split())


def _fields(page_text: str) -> list[dict[str, object]]:
    parser = _FieldCollector()
    parser.feed(page_text)
    return parser.fields


def _fields_by_name(page_text: str) -> dict[str, dict[str, object]]:
    """Поля по именам своих контролов.

    По каждому имени, а не по первому: у групп галочек внутри обёртки лежит ещё
    и скрытый маркер со своим именем, и искать поле по нему тоже разумно.
    """
    found: dict[str, dict[str, object]] = {}
    for field in _fields(page_text):
        controls = field["controls"]
        assert isinstance(controls, list)
        for control in controls:
            name = control.get("name")
            if name:
                found.setdefault(str(name), field)
    return found


def _visible_text(page_text: str) -> str:
    parser = _VisibleText()
    parser.feed(page_text)
    return parser.text


def _state(field: dict[str, object]) -> str:
    state = field["state"]
    classes = field["classes"]
    assert isinstance(classes, list)
    # Класс и атрибут — два вида одной и той же записи; разойтись они не должны,
    # иначе нарисованное состояние перестанет совпадать с посчитанным.
    assert ("is-filled" in classes) == (state == "filled"), field
    assert isinstance(state, str)
    return state


_FILLED_FORM = {
    "name": "Перегон автомобилей",
    "primary_role": "Fahrzeugüberführer",
    "primary_city": "Neustrelitz",
    "driver_license": "B",
    "car_available": "false",
    "shift_ok": "true",
    "employment_types": "full_time",
    "employment_types_present": "1",
    "search_radius_km": "50",
}


def _create(client: TestClient, data: dict[str, str]) -> None:
    response = client.post("/profile/new", data=data, follow_redirects=False)
    assert response.status_code == 303


# ---------------------------------------------------------------------------
# Заполнено против «не указано»
# ---------------------------------------------------------------------------


def test_empty_form_marks_every_field_as_unspecified(client: TestClient) -> None:
    page = client.get("/profile/new")

    fields = _fields(page.text)
    assert len(fields) >= 15
    assert {_state(field) for field in fields} == {"empty"}


def test_saved_profile_opens_with_its_fields_already_marked_filled(
    client: TestClient, db_session: Session
) -> None:
    """Открыть профиль на правку — значит сразу увидеть, что в нём задано."""
    _create(client, _FILLED_FORM)
    profile_id = db_session.query(SearchProfile).one().id

    page = client.get(f"/profile/{profile_id}/edit")

    fields = _fields_by_name(page.text)
    for name in (
        "name",
        "primary_role",
        "primary_city",
        "search_radius_km",
        "employment_types",
        "driver_license",
        "car_available",
        "shift_ok",
    ):
        assert _state(fields[name]) == "filled", name
    # Ничего про них не сказано — и форма этого не выдумывает.
    for name in ("housing_needed", "relocation_ready", "notes", "excluded_roles"):
        assert _state(fields[name]) == "empty", name


def test_unspecified_option_always_carries_an_empty_value(client: TestClient) -> None:
    """«Не указано» — это пустое значение, и на этом держится подсчёт заполненности.

    Если бы какой-нибудь вариант «не указано» получил собственный код, поле
    считалось бы заданным, не будучи заданным, — и в браузере, и на сервере.
    """
    page = client.get("/profile/new")

    for field in _fields(page.text):
        controls = field["controls"]
        assert isinstance(controls, list)
        checked = [c for c in controls if "checked" in c and c.get("type") == "radio"]
        if field["kind"] == "choice" and checked:
            assert checked[0].get("value") == "", field


def test_radio_groups_still_offer_the_same_three_values(client: TestClient) -> None:
    """Сегменты вместо select — тот же набор значений, что уходил на сервер."""
    page = client.get("/profile/new")

    fields = _fields_by_name(page.text)
    for name in (
        "relocation_ready",
        "housing_needed",
        "self_employment_ok",
        "physical_work_ok",
        "shift_ok",
        "car_available",
    ):
        controls = fields[name]["controls"]
        assert isinstance(controls, list)
        assert [c.get("value") for c in controls] == ["", "true", "false"], name


# ---------------------------------------------------------------------------
# Водительские права: несколько категорий
# ---------------------------------------------------------------------------


def test_several_driver_license_categories_are_saved_and_shown(
    client: TestClient, db_session: Session
) -> None:
    """Открытых категорий бывает несколько, и форма обязана принять их все."""
    _create(client, {"name": "Перегон", "driver_license": ["B", "BE"]})

    profile = db_session.query(SearchProfile).one()
    assert profile.driver_license == "B, BE"

    page = client.get(f"/profile/{profile.id}/edit")
    field = _fields_by_name(page.text)["driver_license"]
    controls = field["controls"]
    assert isinstance(controls, list)
    assert [c.get("value") for c in controls if "checked" in c] == ["B", "BE"]
    assert _state(field) == "filled"


def test_driver_license_can_be_cleared(client: TestClient, db_session: Session) -> None:
    """Снятые галочки браузер не присылает — очистку несёт скрытое пустое значение."""
    _create(client, {"name": "Перегон", "driver_license": "B"})
    profile_id = db_session.query(SearchProfile).one().id

    client.post(
        f"/profile/{profile_id}/edit",
        data={"name": "Перегон", "driver_license": ""},
        follow_redirects=False,
    )

    db_session.expire_all()
    assert db_session.get(SearchProfile, profile_id).driver_license is None


# ---------------------------------------------------------------------------
# Общие данные человека: правовой статус
# ---------------------------------------------------------------------------


def _user_profile(db_session: Session) -> UserProfile:
    return db_session.query(UserProfile).one()


def test_stored_legal_status_code_is_never_shown_as_a_code(
    client: TestClient, db_session: Session
) -> None:
    """В базе код, на экране — подпись. Код человеку не говорит ничего."""
    _create(client, {"name": "Склад"})
    _user_profile(db_session).legal_status = "section_24"
    db_session.commit()

    page = client.get("/profile")

    shown = _visible_text(page.text)
    assert "section_24" not in shown
    assert "§ 24 AufenthG" in shown
    # Значение в базе не переписано подписью.
    db_session.expire_all()
    assert _user_profile(db_session).legal_status == "section_24"


def test_legal_status_select_sends_back_the_stored_value(
    client: TestClient, db_session: Session
) -> None:
    """Сохранение без правки статуса не меняет то, что лежит в базе."""
    _create(client, {"name": "Склад"})
    _user_profile(db_session).legal_status = "Section 24"
    db_session.commit()

    page = client.get("/profile")
    field = _fields_by_name(page.text)["legal_status"]
    controls = field["controls"]
    assert isinstance(controls, list)
    selected = [c.get("value") for c in controls if "selected" in c]
    assert selected == ["Section 24"]
    # Записанное своими словами всё равно читается подписью, а не как есть.
    assert "§ 24 AufenthG" in _visible_text(page.text)

    client.post(
        "/profile/languages",
        data={"german_level": "B1", "english_level": "", "legal_status": "Section 24"},
        follow_redirects=False,
    )
    db_session.expire_all()
    assert _user_profile(db_session).legal_status == "Section 24"


def test_no_internal_status_code_reaches_the_page(client: TestClient, db_session: Session) -> None:
    """Ни один код из канонического словаря не должен читаться как код."""
    from app.web.profile_labels import LEGAL_STATUS_LABELS

    _create(client, {"name": "Склад"})
    for code in LEGAL_STATUS_LABELS:
        _user_profile(db_session).legal_status = code
        db_session.commit()

        shown = _visible_text(client.get("/profile").text)

        assert code not in shown, code
        assert LEGAL_STATUS_LABELS[code] in shown, code


# ---------------------------------------------------------------------------
# Общие данные человека: языки, город, подтверждение сохранения
# ---------------------------------------------------------------------------


def test_shared_settings_show_what_is_set_and_what_is_not(
    client: TestClient, db_session: Session
) -> None:
    _create(client, {"name": "Склад"})
    client.post(
        "/profile/languages",
        data={"german_level": "A1", "english_level": "", "legal_status": "eu_citizen"},
        follow_redirects=False,
    )
    client.post("/profile/home-city", data={"home_city": "Tribsees"}, follow_redirects=False)

    fields = _fields_by_name(client.get("/profile").text)

    assert _state(fields["german_level"]) == "filled"
    # «Не знаю совсем» — сказанный ответ, а не его отсутствие.
    assert _state(fields["english_level"]) == "empty"
    assert _state(fields["legal_status"]) == "filled"
    assert _state(fields["home_city"]) == "filled"

    db_session.expire_all()
    user_profile = _user_profile(db_session)
    assert (user_profile.german_level, user_profile.english_level) == ("A1", None)
    assert user_profile.legal_status == "eu_citizen"
    assert user_profile.city == "Tribsees"


def test_knowing_no_german_at_all_counts_as_an_answer(
    client: TestClient, db_session: Session
) -> None:
    """`none` в уровнях языка — это «не знаю совсем», а не «не указано»."""
    _create(client, {"name": "Склад"})
    client.post("/profile/languages", data={"german_level": "none"}, follow_redirects=False)

    fields = _fields_by_name(client.get("/profile").text)

    assert _state(fields["german_level"]) == "filled"


def test_saving_shared_settings_confirms_it_on_the_page(client: TestClient) -> None:
    """После сохранения человек видит, что изменение принято."""
    _create(client, {"name": "Склад"})

    languages = client.post("/profile/languages", data={"german_level": "B1"})
    home_city = client.post("/profile/home-city", data={"home_city": "Tribsees"})

    assert "Сохранено" in _visible_text(languages.text)
    assert "Сохранено" in _visible_text(home_city.text)
    # Без сохранения подтверждения нет — иначе оно ничего не значило бы.
    assert "Сохранено" not in _visible_text(client.get("/profile").text)


def test_home_city_is_not_rewritten_by_opening_the_page(
    client: TestClient, db_session: Session
) -> None:
    """Страница показывает город, а не подставляет свой."""
    _create(client, {"name": "Склад"})
    client.post("/profile/home-city", data={"home_city": "Tribsees"}, follow_redirects=False)

    client.get("/profile")
    client.get("/profile")

    db_session.expire_all()
    assert _user_profile(db_session).city == "Tribsees"
