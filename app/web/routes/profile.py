from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.core.constants import PAGE_META
from app.db.session import get_db
from app.services.profile_catalog_service import ProfileCatalogService
from app.web.deps import get_profile_catalog_service
from app.web.form_utils import read_form_data
from app.web.views import render_page, render_partial

router = APIRouter(tags=["web-profile"])


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
        extra_context={"profile_entries": entries},
    )


@router.post("/profile/{profile_id}/set-default", response_class=HTMLResponse)
def profile_set_default(
    profile_id: int,
    request: Request,
    db: Session = Depends(get_db),
    catalog_service: ProfileCatalogService = Depends(get_profile_catalog_service),
) -> RedirectResponse:
    catalog_service.set_default(db, profile_id=profile_id)
    return RedirectResponse(url="/profile", status_code=303)


@router.post("/profile/{profile_id}/duplicate", response_class=HTMLResponse)
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
) -> HTMLResponse:
    profile = catalog_service.get_profile(db, profile_id=profile_id)
    if profile is None:
        return RedirectResponse(url="/profile", status_code=303)
    meta = PAGE_META["profile"]
    return render_page(
        request,
        "profile/edit.html",
        page_key="profile",
        page_title="Редактировать профиль",
        page_subtitle=profile.name,
        extra_context={"profile": profile},
    )


@router.post("/profile/{profile_id}/edit")
async def profile_edit_submit(
    profile_id: int,
    request: Request,
    db: Session = Depends(get_db),
    catalog_service: ProfileCatalogService = Depends(get_profile_catalog_service),
) -> RedirectResponse:
    form_data: FormData = await read_form_data(request)
    catalog_service.update_profile(
        db,
        profile_id=profile_id,
        name=str(form_data.get("name", "")).strip() or None,
        desired_roles_text=str(form_data.get("desired_roles", "")).strip() or None,
        preferred_locations_text=str(form_data.get("preferred_locations", "")).strip() or None,
        relocation_ready=_parse_bool(form_data.get("relocation_ready")),
        shift_ok=_parse_bool(form_data.get("shift_ok")),
        physical_work_ok=_parse_bool(form_data.get("physical_work_ok")),
        no_german_required=_parse_checkbox(form_data.get("no_german_required")),
        notes=str(form_data.get("notes", "")).strip() or None,
    )
    return RedirectResponse(url="/profile", status_code=303)


@router.post("/profile/{profile_id}/delete", response_class=HTMLResponse)
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
