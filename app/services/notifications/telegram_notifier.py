from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import Settings
from app.core.logging import logger
from app.services.notifications.base import DigestPayload, NotifyResult

_TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_MESSAGE_LENGTH = 4096


class TelegramNotifier:
    """Send digest messages via a Telegram bot."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()

    def is_configured(self) -> bool:
        return bool(self._settings.telegram_bot_token and self._settings.telegram_chat_id)

    def send_digest(self, payload: DigestPayload) -> NotifyResult:
        if not self.is_configured():
            return NotifyResult(ok=False, message="Telegram не настроен: TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы.")

        text = _format_message(payload)
        return self._send(text)

    def _send(self, text: str) -> NotifyResult:
        token = self._settings.telegram_bot_token
        chat_id = self._settings.telegram_chat_id
        url = _TELEGRAM_API_BASE.format(token=token)

        # Truncate to Telegram's limit
        if len(text) > _MAX_MESSAGE_LENGTH:
            text = text[: _MAX_MESSAGE_LENGTH - 3] + "..."

        body = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }).encode("utf-8")

        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(req, timeout=10) as resp:
                resp.read()
            logger.info("telegram_notification_sent chat_id=%s", chat_id)
            return NotifyResult(ok=True, message="Сообщение отправлено в Telegram.")
        except HTTPError as exc:
            logger.warning("telegram_notification_failed status=%s", exc.code)
            return NotifyResult(ok=False, message=f"Ошибка Telegram API: {exc.code}.")
        except URLError as exc:
            logger.warning("telegram_notification_failed reason=%s", exc.reason)
            return NotifyResult(ok=False, message=f"Не удалось подключиться к Telegram: {exc.reason}.")
        except Exception:
            logger.exception("telegram_notification_unexpected_error")
            return NotifyResult(ok=False, message="Неизвестная ошибка при отправке в Telegram.")


def _format_message(payload: DigestPayload) -> str:
    lines = [
        f"<b>SmartJob — дайджест</b>",
        f"Профиль: <i>{payload.profile_label}</i>",
        "",
        f"🔥 Горячих: {payload.hot_count}",
        f"🔍 На проверку: {payload.maybe_count}",
        f"📋 Всего лидов: {payload.total_leads}",
        f"📨 Откликов: {payload.applied_count}",
    ]
    if payload.overdue_followups > 0:
        lines.append(f"⚠️ Просроченных follow-up: {payload.overdue_followups}")
    if payload.digest_text:
        lines.append("")
        lines.append(payload.digest_text)
    return "\n".join(lines)
