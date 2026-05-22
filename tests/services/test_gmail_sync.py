from __future__ import annotations

from app.services.email.gmail_client import GmailClient
from app.services.email.gmail_sync import GmailSyncService


def test_gmail_client_reports_missing_config(monkeypatch) -> None:
    for env_name in ("GMAIL_ACCOUNT_EMAIL", "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.delenv(env_name, raising=False)

    state = GmailClient().configuration_state()

    assert state.configured is False
    assert state.mode == "missing_config"
    assert "GMAIL_ACCOUNT_EMAIL" in state.message_ru


def test_gmail_sync_scaffold_returns_dry_run_when_config_present(monkeypatch) -> None:
    monkeypatch.setenv("GMAIL_ACCOUNT_EMAIL", "me@gmail.com")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client-id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "refresh-token")

    result = GmailSyncService().run_manual_sync()

    assert result.status == "dry_run"
    assert result.dry_run is True
    assert "dry-run" in result.notice_message_ru.lower()
