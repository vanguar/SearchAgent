from __future__ import annotations

from app.core.logging import logger
from app.services.email.base import GmailSyncResult
from app.services.email.gmail_client import GmailClient


class GmailSyncService:
    """Manual-only sync scaffold for later Gmail integration."""

    def __init__(self, *, gmail_client: GmailClient | None = None) -> None:
        self.gmail_client = gmail_client or GmailClient()

    def run_manual_sync(self, *, max_results: int = 20) -> GmailSyncResult:
        result = self.gmail_client.dry_run_sync(max_results=max_results)
        if result.status == "not_configured":
            logger.info("gmail_sync_scaffold_not_configured")
        else:
            logger.info("gmail_sync_scaffold_completed status=%s", result.status)
        return result
