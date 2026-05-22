from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DigestPayload:
    """Data passed to a notifier for delivery."""

    profile_label: str
    hot_count: int
    maybe_count: int
    total_leads: int
    applied_count: int
    overdue_followups: int
    digest_text: str


@dataclass(frozen=True, slots=True)
class NotifyResult:
    ok: bool
    message: str


class Notifier(Protocol):
    """Send a digest notification through some channel."""

    def send_digest(self, payload: DigestPayload) -> NotifyResult: ...

    def is_configured(self) -> bool: ...
