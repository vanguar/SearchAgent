"""Web routes package."""

from app.web.routes.dashboard import router as dashboard_router
from app.web.routes.email import router as email_router
from app.web.routes.feedback import router as feedback_router
from app.web.routes.home import router as home_router
from app.web.routes.intake import router as intake_router
from app.web.routes.interviews import router as interviews_router
from app.web.routes.jobs import router as jobs_router
from app.web.routes.leads import router as leads_router
from app.web.routes.notifications import router as notifications_router
from app.web.routes.placeholders import router as placeholders_router
from app.web.routes.profile import router as profile_router
from app.web.routes.settings import router as settings_router
from app.web.routes.stats import router as stats_router

WEB_ROUTERS = (
    home_router,
    dashboard_router,
    profile_router,
    intake_router,
    jobs_router,
    leads_router,
    email_router,
    interviews_router,
    stats_router,
    notifications_router,
    settings_router,
    feedback_router,
    placeholders_router,
)
