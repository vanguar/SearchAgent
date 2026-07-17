from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

from app.core.config import Settings
from app.core.logging import logger
from app.services.notifications.base import DigestPayload, NotifyResult


class EmailDigestNotifier:
    """Send digest as a plain-text email via SMTP."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()

    def is_configured(self) -> bool:
        s = self._settings
        return bool(s.notification_smtp_host and s.notification_email_from and s.notification_email_to)

    def send_digest(self, payload: DigestPayload) -> NotifyResult:
        if not self.is_configured():
            return NotifyResult(
                ok=False,
                message=(
                    "Email-уведомления не настроены: "
                    "задайте NOTIFICATION_SMTP_HOST, NOTIFICATION_EMAIL_FROM, NOTIFICATION_EMAIL_TO."
                ),
            )

        subject = f"SmartJob дайджест — {payload.profile_label}"
        body = _format_body(payload)
        return self._send(subject=subject, body=body)

    def _send(self, *, subject: str, body: str) -> NotifyResult:
        s = self._settings
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = s.notification_email_from
        msg["To"] = s.notification_email_to

        try:
            with smtplib.SMTP(s.notification_smtp_host, s.notification_smtp_port, timeout=10) as server:
                server.ehlo()
                server.starttls()
                if s.notification_smtp_user and s.notification_smtp_password:
                    server.login(s.notification_smtp_user, s.notification_smtp_password)
                server.sendmail(s.notification_email_from, [s.notification_email_to], msg.as_string())
            logger.info(
                "email_notification_sent to=%s subject=%s", s.notification_email_to, subject
            )
            return NotifyResult(ok=True, message=f"Письмо отправлено на {s.notification_email_to}.")
        except smtplib.SMTPException as exc:
            logger.warning("email_notification_failed error=%s", exc)
            return NotifyResult(ok=False, message=f"Ошибка SMTP при отправке письма: {exc}.")
        except OSError as exc:
            logger.warning("email_notification_connection_failed error=%s", exc)
            return NotifyResult(ok=False, message=f"Не удалось подключиться к SMTP-серверу: {exc}.")
        except Exception:
            logger.exception("email_notification_unexpected_error")
            return NotifyResult(ok=False, message="Неизвестная ошибка при отправке письма.")


def _format_body(payload: DigestPayload) -> str:
    lines = [
        "SmartJob — дайджест поиска",
        f"Профиль: {payload.profile_label}",
        "",
        f"Горячих вакансий: {payload.hot_count}",
        f"На проверку: {payload.maybe_count}",
        f"Всего лидов: {payload.total_leads}",
        f"Откликов отправлено: {payload.applied_count}",
    ]
    if payload.overdue_followups > 0:
        lines.append(f"Просроченных follow-up: {payload.overdue_followups}")
    if payload.digest_text:
        lines.append("")
        lines.append(payload.digest_text)
    return "\n".join(lines)
