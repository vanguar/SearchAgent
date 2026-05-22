from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/", include_in_schema=False)
def home_page() -> RedirectResponse:
    """Redirect root to dashboard page."""
    return RedirectResponse(url="/dashboard", status_code=307)
