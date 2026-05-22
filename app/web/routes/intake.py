from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.db.session import get_db
from app.services.intake_agent import IntakeAgentService
from app.services.intake_models import IntakeSaveResult
from app.web.deps import get_intake_agent
from app.web.form_utils import read_form_data
from app.web.views import render_page, render_partial

router = APIRouter(tags=["web-intake"])

ANSWER_FIELDS: tuple[str, ...] = (
    "current_country",
    "preferred_regions",
    "desired_roles",
    "german_level",
    "willing_to_relocate",
    "shift_ok",
    "work_authorized",
)


def _extract_free_text(form_data: Mapping[str, object]) -> str:
    raw_value = form_data.get("free_text", "")
    return str(raw_value).strip()


def _extract_answers(form_data: Mapping[str, object]) -> dict[str, str]:
    answers: dict[str, str] = {}
    for field_name in ANSWER_FIELDS:
        value = form_data.get(f"answer_{field_name}")
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            answers[field_name] = normalized
    return answers


@router.get("/profile/intake", response_class=HTMLResponse)
def intake_page(request: Request) -> HTMLResponse:
    return render_page(
        request,
        "profile/intake.html",
        page_key="profile",
        page_title="Профиль",
        page_subtitle="Опишите ситуацию свободным текстом, а система соберет структурированный профиль.",
    )


@router.post("/profile/intake/analyze", response_class=HTMLResponse)
async def intake_analyze(
    request: Request,
    intake_agent: IntakeAgentService = Depends(get_intake_agent),
) -> HTMLResponse:
    form_data: FormData = await read_form_data(request)
    free_text = _extract_free_text(form_data)
    answers = _extract_answers(form_data)

    analysis = intake_agent.analyze(free_text, followup_answers=answers)
    return render_partial(
        request,
        "profile/partials/profile_confirmation.html",
        page_key="profile",
        page_title="Профиль",
        page_subtitle="Проверьте интерпретацию и при необходимости ответьте на уточняющие вопросы.",
        extra_context={
            "analysis": analysis,
            "free_text": free_text,
            "answers": answers,
            "answer_fields": ANSWER_FIELDS,
            "save_result": None,
        },
    )


@router.post("/profile/intake/confirm", response_class=HTMLResponse)
async def intake_confirm(
    request: Request,
    db: Session = Depends(get_db),
    intake_agent: IntakeAgentService = Depends(get_intake_agent),
) -> HTMLResponse:
    form_data: FormData = await read_form_data(request)
    free_text = _extract_free_text(form_data)
    answers = _extract_answers(form_data)

    analysis = intake_agent.analyze(free_text, followup_answers=answers)
    save_result = None

    if not analysis.missing_fields:
        try:
            save_result = intake_agent.save_confirmed_analysis(analysis=analysis, free_text=free_text, db=db)
        except SQLAlchemyError:
            db.rollback()
            save_result = IntakeSaveResult(saved=False, message="Не удалось сохранить профиль в базу.")

    return render_partial(
        request,
        "profile/partials/profile_confirmation.html",
        page_key="profile",
        page_title="Профиль",
        page_subtitle="Подтверждение профиля перед сохранением.",
        extra_context={
            "analysis": analysis,
            "free_text": free_text,
            "answers": answers,
            "answer_fields": ANSWER_FIELDS,
            "save_result": save_result,
        },
    )
