from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

EmailDirection = Literal["incoming", "outgoing", "unknown"]
ManualImportMode = Literal["message", "thread_only"]
MatchStrength = Literal["exact", "strong", "weak", "none"]
ImportStatus = Literal["imported", "validation_error", "owner_missing", "db_unavailable"]
LinkStatus = Literal["linked", "unlinked", "not_found", "validation_error", "db_unavailable"]
GmailStateMode = Literal["missing_config", "stubbed"]
GmailSyncStatus = Literal["not_configured", "dry_run"]


class EmailValidationError(ValueError):
    def __init__(self, message_ru: str) -> None:
        super().__init__(message_ru)
        self.message_ru = message_ru


@dataclass(frozen=True, slots=True)
class EmailCueFlags:
    has_reply_signal: bool = False
    has_rejection_signal: bool = False
    has_interview_signal: bool = False

    @property
    def labels_ru(self) -> tuple[str, ...]:
        labels: list[str] = []
        if self.has_reply_signal:
            labels.append("Похоже на ответ")
        if self.has_rejection_signal:
            labels.append("Похоже на отказ")
        if self.has_interview_signal:
            labels.append("Похоже на интервью")
        return tuple(labels)


@dataclass(frozen=True, slots=True)
class ManualEmailInput:
    import_mode: ManualImportMode = "message"
    subject: str | None = None
    participant_emails_raw: str | None = None
    from_email_raw: str | None = None
    to_emails_raw: str | None = None
    cc_emails_raw: str | None = None
    bcc_emails_raw: str | None = None
    body_text: str | None = None
    sent_at_text: str | None = None
    external_message_id: str | None = None
    external_thread_id: str | None = None
    lead_id: int | None = None
    direction_mode: Literal["auto", "incoming", "outgoing"] = "auto"


@dataclass(frozen=True, slots=True)
class ParsedEmailPayload:
    subject_clean: str | None
    from_email: str | None
    to_emails: tuple[str, ...]
    cc_emails: tuple[str, ...]
    bcc_emails: tuple[str, ...]
    participant_emails: tuple[str, ...]
    body_text: str | None
    sent_at: datetime | None
    external_message_id: str
    external_thread_id: str
    subject_tokens: tuple[str, ...]
    body_tokens: tuple[str, ...]
    contact_emails: tuple[str, ...]
    extracted_urls: tuple[str, ...]
    thread_token: str
    cues: EmailCueFlags
    direction: EmailDirection
    direction_source: str

    @property
    def searchable_text(self) -> str:
        parts = [
            self.subject_clean or "",
            self.body_text or "",
            " ".join(self.contact_emails),
            " ".join(self.extracted_urls),
            self.external_message_id,
            self.external_thread_id,
            self.thread_token,
        ]
        return " ".join(part for part in parts if part).casefold()

    @property
    def all_tokens(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.subject_tokens + self.body_tokens))


@dataclass(frozen=True, slots=True)
class LeadMatchSnapshot:
    id: int
    vacancy_title: str
    translated_title_ru: str | None = None
    company_name: str | None = None
    contact_email: str | None = None
    original_url: str | None = None
    source_external_id: str | None = None
    status: str | None = None

    @property
    def label_ru(self) -> str:
        title = self.translated_title_ru or self.vacancy_title
        if self.company_name:
            return f"{title} · {self.company_name}"
        return title


@dataclass(frozen=True, slots=True)
class EmailLeadMatchCandidate:
    lead_id: int
    label_ru: str
    strength: MatchStrength
    score: int
    reasons_ru: tuple[str, ...]

    @property
    def strength_label_ru(self) -> str:
        return {
            "exact": "Точное совпадение",
            "strong": "Сильный кандидат",
            "weak": "Слабый кандидат",
            "none": "Нет совпадения",
        }[self.strength]


@dataclass(frozen=True, slots=True)
class EmailLeadMatchResult:
    status: MatchStrength
    summary_ru: str
    candidates: tuple[EmailLeadMatchCandidate, ...] = ()

    @property
    def best_candidate(self) -> EmailLeadMatchCandidate | None:
        if not self.candidates:
            return None
        return self.candidates[0]

    @classmethod
    def empty(cls) -> EmailLeadMatchResult:
        return cls(status="none", summary_ru="Совпадений с лидами пока нет.", candidates=())


@dataclass(frozen=True, slots=True)
class EmailLeadOption:
    lead_id: int
    label_ru: str


@dataclass(frozen=True, slots=True)
class EmailMessageView:
    message_id: int
    subject: str
    from_email: str | None
    recipients_line: str
    sent_at: datetime | None
    direction_label_ru: str
    cue_labels_ru: tuple[str, ...]
    extracted_contact_emails: tuple[str, ...]
    extracted_urls: tuple[str, ...]
    body_preview: str
    linked_lead_id: int | None
    linked_lead_label: str | None


@dataclass(frozen=True, slots=True)
class EmailThreadListItem:
    thread_id: int
    subject: str
    participant_line: str
    message_count: int
    last_message_at: datetime | None
    linked_lead_id: int | None
    linked_lead_label: str | None
    cue_labels_ru: tuple[str, ...]
    selected: bool = False


@dataclass(frozen=True, slots=True)
class SelectedThreadView:
    thread_id: int
    subject: str
    participant_line: str
    linked_lead_id: int | None
    linked_lead_label: str | None
    messages: tuple[EmailMessageView, ...]
    match_result: EmailLeadMatchResult


@dataclass(frozen=True, slots=True)
class GmailClientState:
    mode: GmailStateMode
    configured: bool
    available: bool
    account_email: str | None
    message_ru: str
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GmailSyncResult:
    status: GmailSyncStatus
    dry_run: bool
    fetched_count: int
    imported_count: int
    notice_kind: str
    notice_message_ru: str


@dataclass(frozen=True, slots=True)
class EmailNotice:
    kind: str
    message_ru: str


@dataclass(frozen=True, slots=True)
class EmailWorkspaceData:
    owner_label: str | None
    warning_message: str | None
    notice: EmailNotice | None
    degraded: bool
    can_import: bool
    threads: tuple[EmailThreadListItem, ...]
    selected_thread: SelectedThreadView | None
    lead_options: tuple[EmailLeadOption, ...]
    gmail_state: GmailClientState


@dataclass(frozen=True, slots=True)
class LeadEmailThreadSummary:
    thread_id: int
    subject: str
    participant_line: str
    message_count: int
    last_message_at: datetime | None


@dataclass(frozen=True, slots=True)
class LeadEmailSummary:
    warning_message: str | None
    threads: tuple[LeadEmailThreadSummary, ...]


@dataclass(frozen=True, slots=True)
class ManualEmailIngestResult:
    status: ImportStatus
    notice_kind: str
    notice_message_ru: str
    commit_required: bool
    redirect_thread_id: int | None = None
    message_id: int | None = None
    linked_lead_id: int | None = None
    match_result: EmailLeadMatchResult = EmailLeadMatchResult(status="none", summary_ru="Совпадений с лидами пока нет.")


@dataclass(frozen=True, slots=True)
class ManualLinkResult:
    status: LinkStatus
    notice_kind: str
    notice_message_ru: str
    commit_required: bool
    redirect_thread_id: int | None = None
    message_id: int | None = None
    lead_id: int | None = None
