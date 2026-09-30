from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.core.constants import PAGE_META
from app.db.session import get_db
from app.services.profile_catalog_service import ProfileCatalogService
from app.services.profile_form_spec import (
    DRIVER_LICENSE_OPTIONS,
    EMPLOYMENT_TYPE_CHOICES,
    SEARCH_RADIUS_OPTIONS,
    parse_profile_form,
)
from app.web.deps import get_profile_catalog_service
from app.web.form_utils import read_form_data
from app.web.profile_labels import LEGAL_STATUS_LABELS, legal_status_label
from app.web.views import render_page

router = APIRouter(tags=["web-profile"])


# Контекст, одинаковый для форм создания и редактирования: варианты, из которых
# человек выбирает. Один источник значений — иначе список прав или форм занятости
# разъезжается между двумя формами.
def _form_context(profile: object | None) -> dict[str, object]:
    return {
        "profile": profile,
        "radius_options": SEARCH_RADIUS_OPTIONS,
        "employment_choices": EMPLOYMENT_TYPE_CHOICES,
        "driver_license_options": DRIVER_LICENSE_OPTIONS,
    }


@router.get("/profile", response_class=HTMLResponse)
def profile_page(
    request: Request,
    db: Session = Depends(get_db),
    catalog_service: ProfileCatalogService = Depends(get_profile_catalog_service),
) -> HTMLResponse:
    meta = PAGE_META["profile"]
    entries = catalog_service.list_profiles(db)
    return render_page(
        request,
        "pages/profile.html",
        page_key="profile",
        page_title=meta["title"],
        page_subtitle="Ваши профили поиска. Выберите активный профиль или создайте новый.",
        extra_context={
            "profile_entries": entries,
            "home_city": catalog_service.get_home_city(db),
            # Языковые уровни и статус — свойства человека, общие для всех профилей.
            "user_profile": entries[0].user_profile if entries else None,
            "legal_status_labels": LEGAL_STATUS_LABELS,
            "legal_status_label": legal_status_label,
            # Какой блок только что сохранён — чтобы показать «Сохранено» рядом с
            # ним, а не одним баннером на всю страницу.
            "saved_block": request.query_params.get("saved"),
        },
    )


@router.post("/profile/home-city")
async def profile_set_home_city(
    request: Request,
    db: Session = Depends(get_db),
    catalog_service: ProfileCatalogService = Depends(get_profile_catalog_service),
) -> RedirectResponse:
    form = await read_form_data(request)
    catalog_service.set_home_city(db, city=_read_text(form, "home_city"))
    return RedirectResponse(url="/profile?saved=home-city", status_code=303)


def _read_text(form: FormData, key: str) -> str | None:
    value = form.get(key)
    return value if isinstance(value, str) else None


@router.get("/profile/new", response_class=HTMLResponse)
def profile_new_page(
    request: Request,
) -> HTMLResponse:
    """Форма создания профиля вручную.

    Существует РЯДОМ с интейком свободным текстом, а не вместо него: часть людей
    предпочитает заполнить поля, часть — рассказать словами, и отнимать второй
    путь только потому, что появился первый, незачем.
    """
    return render_page(
        request,
        "profile/new.html",
        page_key="profile",
        page_title="Новый профиль поиска",
        page_subtitle="Заполните критерии поиска. Любое поле можно оставить пустым.",
        extra_context=_form_context(None),
    )


@router.post("/profile/new")
async def profile_new_submit(
    request: Request,
    db: Session = Depends(get_db),
    catalog_service: ProfileCatalogService = Depends(get_profile_catalog_service),
) -> Response:
    form_data: FormData = await read_form_data(request)
    fields = parse_profile_form(form_data)
    created = catalog_service.create_profile_from_form(db, fields=fields)
    if created is None:
        # Форма возвращается заполненной: у разобранных полей те же имена, что у
        # профиля, поэтому шаблон читает их так же. Заставлять человека набирать
        # два десятка критериев заново из-за неудачной записи незачем.
        return render_page(
            request,
            "profile/new.html",
            page_key="profile",
            page_title="Новый профиль поиска",
            page_subtitle="Не удалось сохранить профиль. Проверьте поля и попробуйте снова.",
            extra_context={
                **_form_context(fields),
                "form_error": "Профиль не сохранился. Введённое осталось в форме — проверьте поля и отправьте снова.",
            },
        )
    return RedirectResponse(url="/profile", status_code=303)


@router.post("/profile/languages")
async def profile_set_languages(
    request: Request,
    db: Session = Depends(get_db),
    catalog_service: ProfileCatalogService = Depends(get_profile_catalog_service),
) -> RedirectResponse:
    """Уровни языков и правовой статус — одни на все профили поиска."""
    form = await read_form_data(request)
    catalog_service.set_user_languages(
        db,
        german_level=_read_text(form, "german_level") or None,
        english_level=_read_text(form, "english_level") or None,
        legal_status=_read_text(form, "legal_status") or None,
    )
    return RedirectResponse(url="/profile?saved=languages", status_code=303)


@router.post("/profile/{profile_id}/set-default")
def profile_set_default(
    profile_id: int,
    request: Request,
    db: Session = Depends(get_db),
    catalog_service: ProfileCatalogService = Depends(get_profile_catalog_service),
) -> RedirectResponse:
    catalog_service.set_default(db, profile_id=profile_id)
    return RedirectResponse(url="/profile", status_code=303)


@router.post("/profile/{profile_id}/duplicate")
def profile_duplicate(
    profile_id: int,
    request: Request,
    db: Session = Depends(get_db),
    catalog_service: ProfileCatalogService = Depends(get_profile_catalog_service),
) -> RedirectResponse:
    catalog_service.duplicate_profile(db, source_id=profile_id)
    return RedirectResponse(url="/profile", status_code=303)


@router.get("/profile/{profile_id}/edit", response_class=HTMLResponse)
def profile_edit_page(
    profile_id: int,
    request: Request,
    db: Session = Depends(get_db),
    catalog_service: ProfileCatalogService = Depends(get_profile_catalog_service),
) -> Response:
    profile = catalog_service.get_profile(db, profile_id=profile_id)
    if profile is None:
        return RedirectResponse(url="/profile", status_code=303)
    return render_page(
        request,
        "profile/edit.html",
        page_key="profile",
        page_title="Редактировать профиль",
        page_subtitle=profile.name,
        extra_context=_form_context(profile),
    )


@router.post("/profile/{profile_id}/edit")
async def profile_edit_submit(
    profile_id: int,
    request: Request,
    db: Session = Depends(get_db),
    catalog_service: ProfileCatalogService = Depends(get_profile_catalog_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    fields = parse_profile_form(form_data)
    existing = catalog_service.get_profile(db, profile_id=profile_id)
    if existing is not None and form_data.get("driver_license") == existing.driver_license:
        fields = replace(fields, driver_license=existing.driver_license)
    catalog_service.apply_form_fields(
        db,
        profile_id=profile_id,
        fields=fields,
    )
    return RedirectResponse(url="/profile", status_code=303)


@router.post("/profile/{profile_id}/delete")
def profile_delete(
    profile_id: int,
    db: Session = Depends(get_db),
    catalog_service: ProfileCatalogService = Depends(get_profile_catalog_service),
) -> RedirectResponse:
    catalog_service.delete_profile(db, profile_id=profile_id)
    return RedirectResponse(url="/profile", status_code=303)


def _parse_checkbox(value: object) -> bool | None:
    """Checkbox sends 'on' when checked, absent when unchecked."""
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_bool(value: object) -> bool | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return None
