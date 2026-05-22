from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from app.services.email.base import EmailCueFlags, EmailValidationError, ManualEmailInput, ParsedEmailPayload

EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
SUBJECT_PREFIX_PATTERN = re.compile(r"^(?:(?:re|aw|wg|fw|fwd)\s*:\s*)+", re.IGNORECASE)
TOKEN_PATTERN = re.compile(r"[^\W_]{2,}", re.UNICODE)
REPLY_KEYWORDS = ("reply", "response", "feedback", "antwort", "ruckmeldung", "rueckmeldung", "ответ", "ответили")
REJECTION_KEYWORDS = (
    "rejection",
    "absage",
    "leider",
    "not selected",
    "declined",
    "отказ",
    "не подош",
)
INTERVIEW_KEYWORDS = (
    "interview",
    "gesprach",
    "gespräch",
    "vorstellung",
    "termin",
    "calendar invite",
    "собесед",
    "интервью",
)


class EmailParser:
    """Deterministic email parsing helpers for PHASE 10."""

    def parse_manual_input(
        self,
        payload: ManualEmailInput,
        *,
        known_user_emails: tuple[str, ...] = (),
    ) -> ParsedEmailPayload:
        participant_emails = normalize_email_list(payload.participant_emails_raw)
        sent_at = parse_optional_datetime(payload.sent_at_text)
        return self.parse_message_snapshot(
            subject=payload.subject,
            from_email=payload.from_email_raw,
            to_emails=payload.to_emails_raw,
            cc_emails=payload.cc_emails_raw,
            bcc_emails=payload.bcc_emails_raw,
            participant_emails=participant_emails,
            body_text=payload.body_text,
            sent_at=sent_at,
            external_message_id=payload.external_message_id,
            external_thread_id=payload.external_thread_id,
            known_user_emails=known_user_emails,
            direction_mode=payload.direction_mode,
        )

    def parse_message_snapshot(
        self,
        *,
        subject: str | None,
        from_email: str | None,
        to_emails: str | tuple[str, ...] | list[str] | None,
        cc_emails: str | tuple[str, ...] | list[str] | None,
        bcc_emails: str | tuple[str, ...] | list[str] | None,
        participant_emails: str | tuple[str, ...] | list[str] | None,
        body_text: str | None,
        sent_at: datetime | None,
        external_message_id: str | None,
        external_thread_id: str | None,
        known_user_emails: tuple[str, ...] = (),
        direction_mode: str = "auto",
    ) -> ParsedEmailPayload:
        sender = normalize_email_address(from_email)
        normalized_to = normalize_email_list(to_emails)
        normalized_cc = normalize_email_list(cc_emails)
        normalized_bcc = normalize_email_list(bcc_emails)
        participant_values = participant_emails if isinstance(participant_emails, tuple) else normalize_email_list(participant_emails)
        normalized_participants = _dedupe(
            (
                *participant_values,
                *((sender,) if sender else ()),
                *normalized_to,
                *normalized_cc,
                *normalized_bcc,
            )
        )
        cleaned_subject = clean_subject(subject)
        clean_body = clean_text(body_text)
        extracted_emails = _dedupe(
            (
                *normalize_email_list(" ".join(EMAIL_PATTERN.findall(clean_body or ""))),
                *((sender,) if sender else ()),
                *normalized_to,
                *normalized_cc,
                *normalized_bcc,
                *normalized_participants,
            )
        )
        urls = extract_urls(clean_body)
        cues = extract_cues(cleaned_subject, clean_body)
        direction, direction_source = detect_direction(
            sender,
            recipients=normalized_to + normalized_cc + normalized_bcc,
            known_user_emails=known_user_emails,
            direction_mode=direction_mode,
        )
        subject_tokens = tokenize_text(cleaned_subject)
        body_tokens = tokenize_text(clean_body)
        generated_thread_id = build_stable_external_id(
            "manual-thread",
            parts=(
                cleaned_subject or "",
                " ".join(normalized_participants),
                clean_body or "",
            ),
        )
        generated_message_id = build_stable_external_id(
            "manual-message",
            parts=(
                cleaned_subject or "",
                sender or "",
                " ".join(normalized_to),
                clean_body or "",
                sent_at.isoformat() if sent_at else "",
            ),
        )
        normalized_external_message_id = normalize_external_id(
            external_message_id,
            field_label_ru="Внешний ID письма",
        )
        normalized_external_thread_id = normalize_external_id(
            external_thread_id,
            field_label_ru="Внешний ID цепочки",
        )
        return ParsedEmailPayload(
            subject_clean=cleaned_subject,
            from_email=sender,
            to_emails=normalized_to,
            cc_emails=normalized_cc,
            bcc_emails=normalized_bcc,
            participant_emails=normalized_participants,
            body_text=clean_body,
            sent_at=sent_at,
            external_message_id=normalized_external_message_id or generated_message_id,
            external_thread_id=normalized_external_thread_id or generated_thread_id,
            subject_tokens=subject_tokens,
            body_tokens=body_tokens,
            contact_emails=extracted_emails,
            extracted_urls=urls,
            thread_token=build_thread_token(cleaned_subject, clean_body),
            cues=cues,
            direction=direction,
            direction_source=direction_source,
        )


def normalize_email_address(raw_value: str | None) -> str | None:
    cleaned = clean_optional_text(raw_value)
    if cleaned is None:
        return None
    bracket_match = re.search(r"<([^>]+)>", cleaned)
    if bracket_match:
        cleaned = bracket_match.group(1)
    email_match = EMAIL_PATTERN.search(cleaned)
    if email_match is None:
        return None
    return email_match.group(0).lower()


def normalize_email_list(raw_value: str | tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if raw_value is None:
        return ()
    if isinstance(raw_value, (tuple, list)):
        values = list(raw_value)
    else:
        values = re.split(r"[,;\n]+", raw_value)
    normalized = [email for value in values if (email := normalize_email_address(str(value)))]
    return _dedupe(normalized)


def clean_subject(subject: str | None) -> str | None:
    cleaned = clean_optional_text(subject)
    if cleaned is None:
        return None
    return clean_optional_text(SUBJECT_PREFIX_PATTERN.sub("", cleaned))


def clean_text(value: str | None) -> str | None:
    cleaned = clean_optional_text(value)
    if cleaned is None:
        return None
    return re.sub(r"\s+", " ", cleaned)


def clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def normalize_external_id(value: str | None, *, field_label_ru: str) -> str | None:
    cleaned = clean_optional_text(value)
    if cleaned is None:
        return None
    if len(cleaned) > 255:
        raise EmailValidationError(f"{field_label_ru} слишком длинный. Сократите значение и попробуйте снова.")
    return cleaned


def tokenize_text(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    tokens = [token.casefold() for token in TOKEN_PATTERN.findall(value)]
    return _dedupe(tokens)


def extract_urls(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return _dedupe(tuple(match.rstrip(".,);") for match in URL_PATTERN.findall(value)))


def extract_cues(subject: str | None, body_text: str | None) -> EmailCueFlags:
    combined_text = " ".join(part for part in (subject, body_text) if part).casefold()
    return EmailCueFlags(
        has_reply_signal=_contains_any(combined_text, REPLY_KEYWORDS),
        has_rejection_signal=_contains_any(combined_text, REJECTION_KEYWORDS),
        has_interview_signal=_contains_any(combined_text, INTERVIEW_KEYWORDS),
    )


def detect_direction(
    sender: str | None,
    *,
    recipients: tuple[str, ...],
    known_user_emails: tuple[str, ...],
    direction_mode: str,
) -> tuple[str, str]:
    if direction_mode == "incoming":
        return "incoming", "manual"
    if direction_mode == "outgoing":
        return "outgoing", "manual"

    normalized_known = {email for email in known_user_emails if email}
    if sender and sender in normalized_known:
        return "outgoing", "known_user_email"
    if normalized_known and any(recipient in normalized_known for recipient in recipients):
        return "incoming", "known_user_email"
    return "unknown", "auto"


def build_thread_token(subject: str | None, body_text: str | None) -> str:
    tokens = tokenize_text(" ".join(part for part in (subject, body_text) if part))
    if not tokens:
        return "manual-thread"
    return "-".join(tokens[:8])


def build_stable_external_id(prefix: str, *, parts: tuple[str, ...]) -> str:
    digest_source = "|".join(part.strip().casefold() for part in parts if part.strip())
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def parse_optional_datetime(raw_value: str | None) -> datetime | None:
    cleaned = clean_optional_text(raw_value)
    if cleaned is None:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise EmailValidationError(
            "Поле даты отправки заполнено неверно. Используйте формат 2026-04-18T10:15."
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _dedupe(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))
