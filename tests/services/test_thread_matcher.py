from __future__ import annotations

from app.services.email.base import LeadMatchSnapshot
from app.services.email.email_parser import EmailParser
from app.services.email.thread_matcher import ThreadMatcher


def _lead_snapshot() -> LeadMatchSnapshot:
    return LeadMatchSnapshot(
        id=1,
        vacancy_title="Lagermitarbeiter Berlin",
        translated_title_ru="Сотрудник склада Берлин",
        company_name="Logistik Nord GmbH",
        contact_email="hr@example.de",
        original_url="https://example.org/jobs/10000-123",
        source_external_id="10000-123",
        status="applied",
    )


def test_thread_matcher_returns_exact_match_by_original_url() -> None:
    parser = EmailParser()
    matcher = ThreadMatcher()
    payload = parser.parse_message_snapshot(
        subject="Re: Lagermitarbeiter Berlin",
        from_email="hr@example.de",
        to_emails=("me@gmail.com",),
        cc_emails=(),
        bcc_emails=(),
        participant_emails=("hr@example.de", "me@gmail.com"),
        body_text="Please see https://example.org/jobs/10000-123",
        sent_at=None,
        external_message_id="m-1",
        external_thread_id="t-1",
    )

    result = matcher.match(parsed_payload=payload, leads=(_lead_snapshot(),))

    assert result.status == "exact"
    assert result.candidates[0].strength == "exact"


def test_thread_matcher_returns_strong_match_by_company_and_title() -> None:
    parser = EmailParser()
    matcher = ThreadMatcher()
    payload = parser.parse_message_snapshot(
        subject="Interview with Logistik Nord GmbH",
        from_email="jobs@example.de",
        to_emails=("me@gmail.com",),
        cc_emails=(),
        bcc_emails=(),
        participant_emails=("jobs@example.de", "me@gmail.com"),
        body_text="Lagermitarbeiter Berlin shift role.",
        sent_at=None,
        external_message_id="m-2",
        external_thread_id="t-2",
    )

    result = matcher.match(parsed_payload=payload, leads=(_lead_snapshot(),))

    assert result.status == "strong"
    assert result.candidates[0].strength == "strong"


def test_thread_matcher_returns_weak_match_for_single_signal() -> None:
    parser = EmailParser()
    matcher = ThreadMatcher()
    payload = parser.parse_message_snapshot(
        subject="Update from Logistik Nord GmbH",
        from_email="jobs@example.de",
        to_emails=("me@gmail.com",),
        cc_emails=(),
        bcc_emails=(),
        participant_emails=("jobs@example.de", "me@gmail.com"),
        body_text="Quick update only.",
        sent_at=None,
        external_message_id="m-3",
        external_thread_id="t-3",
    )

    result = matcher.match(parsed_payload=payload, leads=(_lead_snapshot(),))

    assert result.status == "weak"
    assert result.candidates[0].strength == "weak"


def test_thread_matcher_returns_no_match_without_signals() -> None:
    parser = EmailParser()
    matcher = ThreadMatcher()
    payload = parser.parse_message_snapshot(
        subject="Newsletter",
        from_email="news@example.com",
        to_emails=("me@gmail.com",),
        cc_emails=(),
        bcc_emails=(),
        participant_emails=("news@example.com", "me@gmail.com"),
        body_text="General updates and marketing copy.",
        sent_at=None,
        external_message_id="m-4",
        external_thread_id="t-4",
    )

    result = matcher.match(parsed_payload=payload, leads=(_lead_snapshot(),))

    assert result.status == "none"
    assert result.candidates == ()
