from __future__ import annotations

from app.core.config import Settings
from app.core.logging import logger
from app.services.email.base import GmailClientState, GmailSyncResult


class GmailClient:
    """Config-aware Gmail boundary for future API integration."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def configuration_state(self) -> GmailClientState:
        missing_fields = tuple(
            field_name
            for field_name, value in (
                ("GMAIL_ACCOUNT_EMAIL", self.settings.gmail_account_email),
                ("GMAIL_CLIENT_ID", self.settings.gmail_client_id),
                ("GMAIL_CLIENT_SECRET", self.settings.gmail_client_secret),
                ("GMAIL_REFRESH_TOKEN", self.settings.gmail_refresh_token),
            )
            if not value
        )
        if missing_fields:
            return GmailClientState(
                mode="missing_config",
                configured=False,
                available=False,
                account_email=self.settings.gmail_account_email,
                message_ru=(
                    "Gmail пока не настроен. Для будущего sync scaffold укажите "
                    + ", ".join(missing_fields)
                    + "."
                ),
                missing_fields=missing_fields,
            )
        return GmailClientState(
            mode="stubbed",
            configured=True,
            available=True,
            account_email=self.settings.gmail_account_email,
            message_ru=(
                "Gmail scaffold готов для локальной проверки. В PHASE 10 реальный API-вызов "
                "не выполняется, только dry-run слой."
            ),
        )

    def dry_run_sync(self, *, max_results: int = 20) -> GmailSyncResult:
        state = self.configuration_state()
        if not state.configured:
            logger.info("gmail_unavailable reason=config_missing missing=%s", ",".join(state.missing_fields))
            return GmailSyncResult(
                status="not_configured",
                dry_run=True,
                fetched_count=0,
                imported_count=0,
                notice_kind="warning",
                notice_message_ru=state.message_ru,
            )

        logger.info(
            "gmail_sync_scaffold_dry_run account=%s max_results=%s",
            state.account_email,
            max_results,
        )
        return GmailSyncResult(
            status="dry_run",
            dry_run=True,
            fetched_count=0,
            imported_count=0,
            notice_kind="info",
            notice_message_ru=(
                "Gmail sync scaffold отработал в dry-run режиме. "
                "PHASE 10 не делает реальный импорт из Gmail автоматически."
            ),
        )
