from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.services.notifications.base import DigestPayload, NotifyResult
from app.services.notifications.email_digest_notifier import EmailDigestNotifier
from app.services.notifications.telegram_notifier import TelegramNotifier


def _make_payload() -> DigestPayload:
    return DigestPayload(
        profile_label="Склад / логистика",
        hot_count=3,
        maybe_count=5,
        total_leads=10,
        applied_count=2,
        overdue_followups=1,
        digest_text="Есть новые горячие вакансии.",
    )


class TestTelegramNotifier:
    def test_not_configured_without_env(self) -> None:
        settings = Settings.__new__(Settings)
        object.__setattr__(settings, "telegram_bot_token", None)
        object.__setattr__(settings, "telegram_chat_id", None)
        notifier = TelegramNotifier(settings=settings)
        assert notifier.is_configured() is False

    def test_configured_with_token_and_chat(self) -> None:
        settings = Settings.__new__(Settings)
        object.__setattr__(settings, "telegram_bot_token", "test-token")
        object.__setattr__(settings, "telegram_chat_id", "12345")
        notifier = TelegramNotifier(settings=settings)
        assert notifier.is_configured() is True

    def test_send_digest_not_configured_returns_failure(self) -> None:
        settings = Settings.__new__(Settings)
        object.__setattr__(settings, "telegram_bot_token", None)
        object.__setattr__(settings, "telegram_chat_id", None)
        notifier = TelegramNotifier(settings=settings)
        result = notifier.send_digest(_make_payload())
        assert result.ok is False
        assert "не настроен" in result.message.lower() or "telegram" in result.message.lower()

    def test_send_digest_configured_sends_request(self) -> None:
        settings = Settings.__new__(Settings)
        object.__setattr__(settings, "telegram_bot_token", "test-token")
        object.__setattr__(settings, "telegram_chat_id", "12345")
        notifier = TelegramNotifier(settings=settings)

        mock_response = MagicMock()
        mock_response.read.return_value = b"{}"
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("app.services.notifications.telegram_notifier.urlopen", return_value=mock_response):
            result = notifier.send_digest(_make_payload())

        assert result.ok is True

    def test_send_digest_handles_http_error(self) -> None:
        from urllib.error import HTTPError

        settings = Settings.__new__(Settings)
        object.__setattr__(settings, "telegram_bot_token", "bad-token")
        object.__setattr__(settings, "telegram_chat_id", "12345")
        notifier = TelegramNotifier(settings=settings)

        with patch(
            "app.services.notifications.telegram_notifier.urlopen",
            side_effect=HTTPError(url="", code=401, msg="Unauthorized", hdrs=MagicMock(), fp=None),
        ):
            result = notifier.send_digest(_make_payload())

        assert result.ok is False
        assert "401" in result.message


class TestEmailDigestNotifier:
    def test_not_configured_without_smtp(self) -> None:
        settings = Settings.__new__(Settings)
        object.__setattr__(settings, "notification_smtp_host", None)
        object.__setattr__(settings, "notification_email_from", None)
        object.__setattr__(settings, "notification_email_to", None)
        notifier = EmailDigestNotifier(settings=settings)
        assert notifier.is_configured() is False

    def test_configured_with_smtp_settings(self) -> None:
        settings = Settings.__new__(Settings)
        object.__setattr__(settings, "notification_smtp_host", "smtp.example.com")
        object.__setattr__(settings, "notification_email_from", "from@example.com")
        object.__setattr__(settings, "notification_email_to", "to@example.com")
        notifier = EmailDigestNotifier(settings=settings)
        assert notifier.is_configured() is True

    def test_send_digest_not_configured_returns_failure(self) -> None:
        settings = Settings.__new__(Settings)
        object.__setattr__(settings, "notification_smtp_host", None)
        object.__setattr__(settings, "notification_email_from", None)
        object.__setattr__(settings, "notification_email_to", None)
        notifier = EmailDigestNotifier(settings=settings)
        result = notifier.send_digest(_make_payload())
        assert result.ok is False
        assert "не настроены" in result.message.lower() or "smtp" in result.message.lower()
