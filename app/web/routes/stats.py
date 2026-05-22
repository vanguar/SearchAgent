from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.constants import PAGE_META
from app.db.session import get_db
from app.services.digest_service import DigestService
from app.services.funnel_service import FunnelService
from app.services.profile_catalog_service import ProfileCatalogService
from app.services.stats_service import StatsService
from app.web.deps import get_profile_catalog_service
from app.web.views import render_page

router = APIRouter(tags=["web-stats"])


def get_stats_service() -> StatsService:
    return StatsService()


def get_funnel_service() -> FunnelService:
    return FunnelService()


def get_digest_service() -> DigestService:
    return DigestService()


@router.get("/stats", response_class=HTMLResponse)
def stats_page(
    request: Request,
    window: str = "7d",
    profile_id: int | None = None,
    db: Session = Depends(get_db),
    stats_service: StatsService = Depends(get_stats_service),
    funnel_service: FunnelService = Depends(get_funnel_service),
    digest_service: DigestService = Depends(get_digest_service),
    catalog_service: ProfileCatalogService = Depends(get_profile_catalog_service),
) -> HTMLResponse:
    meta = PAGE_META["stats"]

    # Resolve default profile if not explicitly selected
    effective_profile_id = profile_id
    if effective_profile_id is None:
        effective_profile_id = catalog_service.resolve_default_profile_id(db)

    stats_dashboard = stats_service.build_dashboard_data(db, window_key=window, profile_id=effective_profile_id)
    funnel_dashboard = funnel_service.build_dashboard_data(
        db, window_key=stats_dashboard.window.key, profile_id=effective_profile_id
    )
    stats_digest = digest_service.build_digest(
        stats_dashboard=stats_dashboard,
        funnel_dashboard=funnel_dashboard,
    )
    profile_entries = catalog_service.list_profiles(db)
    return render_page(
        request,
        "pages/stats.html",
        page_key="stats",
        page_title=meta["title"],
        page_subtitle="Поиск, источники, воронка и ближайшие действия в одном русском read model.",
        extra_context={
            "stats_dashboard": stats_dashboard,
            "funnel_dashboard": funnel_dashboard,
            "stats_digest": stats_digest,
            "profile_entries": profile_entries,
            "selected_profile_id": effective_profile_id,
        },
    )
