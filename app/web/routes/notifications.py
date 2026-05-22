from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.digest_service import DigestService
from app.services.funnel_service import FunnelService
from app.services.notifications.base import DigestPayload
from app.services.notifications.email_digest_notifier import EmailDigestNotifier
from app.services.notifications.telegram_notifier import TelegramNotifier
from app.services.profile_catalog_service import ProfileCatalogService
from app.services.stats_service import StatsService
from app.web.views import render_partial

router = APIRouter(tags=["web-notifications"])


def get_stats_service() -> StatsService:
    return StatsService()


def get_funnel_service() -> FunnelService:
    return FunnelService()


def get_digest_service() -> DigestService:
    return DigestService()


def get_catalog_service() -> ProfileCatalogService:
    return ProfileCatalogService()


def get_telegram_notifier() -> TelegramNotifier:
    return TelegramNotifier()


def get_email_notifier() -> EmailDigestNotifier:
    return EmailDigestNotifier()


@router.post("/notifications/trigger/telegram", response_class=HTMLResponse)
def trigger_telegram_digest(
    request: Request,
    profile_id: int | None = None,
    db: Session = Depends(get_db),
    stats_service: StatsService = Depends(get_stats_service),
    funnel_service: FunnelService = Depends(get_funnel_service),
    digest_service: DigestService = Depends(get_digest_service),
    catalog_service: ProfileCatalogService = Depends(get_catalog_service),
    notifier: TelegramNotifier = Depends(get_telegram_notifier),
) -> HTMLResponse:
    payload, config_message = _build_payload(
        db,
        profile_id=profile_id,
        stats_service=stats_service,
        funnel_service=funnel_service,
        digest_service=digest_service,
        catalog_service=catalog_service,
    )
    if payload is None:
        result_message = config_message or "Профиль не найден."
        result_ok = False
    else:
        result = notifier.send_digest(payload)
        result_message = result.message
        result_ok = result.ok

    return render_partial(
        request,
        "notifications/trigger_result.html",
        page_key="settings",
        page_title="Уведомления",
        page_subtitle="",
        extra_context={
            "channel": "Telegram",
            "result_ok": result_ok,
            "result_message": result_message,
        },
    )


@router.post("/notifications/trigger/email", response_class=HTMLResponse)
def trigger_email_digest(
    request: Request,
    profile_id: int | None = None,
    db: Session = Depends(get_db),
    stats_service: StatsService = Depends(get_stats_service),
    funnel_service: FunnelService = Depends(get_funnel_service),
    digest_service: DigestService = Depends(get_digest_service),
    catalog_service: ProfileCatalogService = Depends(get_catalog_service),
    notifier: EmailDigestNotifier = Depends(get_email_notifier),
) -> HTMLResponse:
    payload, config_message = _build_payload(
        db,
        profile_id=profile_id,
        stats_service=stats_service,
        funnel_service=funnel_service,
        digest_service=digest_service,
        catalog_service=catalog_service,
    )
    if payload is None:
        result_message = config_message or "Профиль не найден."
        result_ok = False
    else:
        result = notifier.send_digest(payload)
        result_message = result.message
        result_ok = result.ok

    return render_partial(
        request,
        "notifications/trigger_result.html",
        page_key="settings",
        page_title="Уведомления",
        page_subtitle="",
        extra_context={
            "channel": "Email",
            "result_ok": result_ok,
            "result_message": result_message,
        },
    )


def _build_payload(
    db: Session,
    *,
    profile_id: int | None,
    stats_service: StatsService,
    funnel_service: FunnelService,
    digest_service: DigestService,
    catalog_service: ProfileCatalogService,
) -> tuple[DigestPayload | None, str | None]:
    effective_id = profile_id or catalog_service.resolve_default_profile_id(db)
    stats_dashboard = stats_service.build_dashboard_data(db, profile_id=effective_id)
    funnel_dashboard = funnel_service.build_dashboard_data(db, profile_id=effective_id)
    digest = digest_service.build_digest(
        stats_dashboard=stats_dashboard, funnel_dashboard=funnel_dashboard
    )

    if stats_dashboard.owner_label_ru is None:
        return None, "Нет сохранённого профиля для отправки дайджеста."

    kpi = stats_dashboard.kpi
    digest_text = ""
    if hasattr(digest, "headline_ru"):
        digest_text = digest.headline_ru

    payload = DigestPayload(
        profile_label=stats_dashboard.owner_label_ru,
        hot_count=kpi.hot_count,
        maybe_count=kpi.maybe_count,
        total_leads=kpi.saved_count,
        applied_count=kpi.applications_sent,
        overdue_followups=kpi.due_follow_ups,
        digest_text=digest_text,
    )
    return payload, None
