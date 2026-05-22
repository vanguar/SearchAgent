from __future__ import annotations

from app.services.email.base import ManualEmailInput
from app.services.email.email_parser import EmailParser


def test_email_parser_normalizes_addresses_extracts_cues_and_direction() -> None:
    parser = EmailParser()

    parsed = parser.parse_manual_input(
        ManualEmailInput(
            subject="Re: Interview for Lagermitarbeiter Berlin",
            from_email_raw="HR Team <hr@example.de>",
            to_emails_raw="me@gmail.com; jobs@example.de",
            body_text=(
                "We would like to invite you to an interview. "
                "Please review https://example.org/jobs/10000-123 and reply to hr@example.de"
            ),
            sent_at_text="2026-04-18T09:30",
        ),
        known_user_emails=("me@gmail.com",),
    )

    assert parsed.subject_clean == "Interview for Lagermitarbeiter Berlin"
    assert parsed.from_email == "hr@example.de"
    assert parsed.to_emails == ("me@gmail.com", "jobs@example.de")
    assert parsed.contact_emails == ("hr@example.de", "me@gmail.com", "jobs@example.de")
    assert parsed.extracted_urls == ("https://example.org/jobs/10000-123",)
    assert parsed.cues.has_reply_signal is True
    assert parsed.cues.has_interview_signal is True
    assert parsed.direction == "incoming"


def test_email_parser_manual_direction_override_and_deterministic_ids() -> None:
    parser = EmailParser()
    payload = ManualEmailInput(
        subject="Fw: Bewerbung Lager",
        from_email_raw="me@gmail.com",
        to_emails_raw="hr@example.de",
        body_text="Short update for the warehouse role.",
        direction_mode="outgoing",
    )

    first = parser.parse_manual_input(payload, known_user_emails=("me@gmail.com",))
    second = parser.parse_manual_input(payload, known_user_emails=("me@gmail.com",))

    assert first.direction == "outgoing"
    assert first.direction_source == "manual"
    assert first.external_thread_id == second.external_thread_id
    assert first.external_message_id == second.external_message_id
