from __future__ import annotations

from urllib.parse import urlsplit

from app.services.email.base import (
    EmailLeadMatchCandidate,
    EmailLeadMatchResult,
    LeadMatchSnapshot,
    MatchStrength,
    ParsedEmailPayload,
)
from app.services.email.email_parser import normalize_email_address, tokenize_text


class ThreadMatcher:
    """Deterministic email-to-lead matching with explicit strengths."""

    def match(
        self,
        *,
        parsed_payload: ParsedEmailPayload,
        leads: tuple[LeadMatchSnapshot, ...],
        explicit_lead_id: int | None = None,
    ) -> EmailLeadMatchResult:
        if not leads:
            return EmailLeadMatchResult.empty()

        candidates: list[EmailLeadMatchCandidate] = []
        for lead in leads:
            matched_candidate = self._match_single_lead(
                lead=lead,
                parsed_payload=parsed_payload,
                explicit_lead_id=explicit_lead_id,
            )
            if matched_candidate is not None:
                candidates.append(matched_candidate)

        if not candidates:
            return EmailLeadMatchResult.empty()

        ordered_candidates = tuple(
            sorted(
                candidates,
                key=lambda candidate: (_strength_rank(candidate.strength), candidate.score, -candidate.lead_id),
                reverse=True,
            )[:5]
        )
        best_candidate = ordered_candidates[0]
        return EmailLeadMatchResult(
            status=best_candidate.strength,
            summary_ru=_summary_for_strength(best_candidate.strength),
            candidates=ordered_candidates,
        )

    def _match_single_lead(
        self,
        *,
        lead: LeadMatchSnapshot,
        parsed_payload: ParsedEmailPayload,
        explicit_lead_id: int | None,
    ) -> EmailLeadMatchCandidate | None:
        reasons: list[str] = []
        score = 0
        manual_selected = explicit_lead_id is not None and lead.id == explicit_lead_id
        original_url_match = False
        contact_email_match = False
        company_match = False
        title_match = False
        source_id_match = False

        if manual_selected:
            reasons.append("Лид выбран вручную при импорте.")
            score += 200

        lead_url = normalize_url(lead.original_url)
        payload_urls = {normalize_url(url) for url in parsed_payload.extracted_urls if normalize_url(url)}
        if lead_url and lead_url in payload_urls:
            reasons.append("В письме найден URL той же вакансии.")
            score += 120
            original_url_match = True

        lead_contact_email = normalize_email_address(lead.contact_email)
        if lead_contact_email and lead_contact_email in parsed_payload.contact_emails:
            reasons.append(f"Совпадает контактный email: {lead_contact_email}.")
            score += 70
            contact_email_match = True

        lead_source_id = (lead.source_external_id or "").casefold()
        if lead_source_id and lead_source_id in parsed_payload.searchable_text:
            reasons.append(f"В письме найден source ID вакансии: {lead.source_external_id}.")
            score += 65
            source_id_match = True

        company_overlap = token_overlap(lead.company_name, parsed_payload.all_tokens)
        if company_overlap >= 1:
            reasons.append(f"Совпадает компания: {lead.company_name}.")
            score += 35 + max(0, company_overlap - 1) * 5
            company_match = True

        title_overlap = max(
            token_overlap(lead.vacancy_title, parsed_payload.all_tokens),
            token_overlap(lead.translated_title_ru, parsed_payload.all_tokens),
        )
        if title_overlap >= 2:
            reasons.append(f"Совпадает тема вакансии: {lead.vacancy_title}.")
            score += 40 + max(0, title_overlap - 2) * 5
            title_match = True
        elif title_overlap == 1:
            reasons.append(f"Есть слабое совпадение по названию вакансии: {lead.vacancy_title}.")
            score += 20

        strength = classify_strength(
            manual_selected=manual_selected,
            original_url_match=original_url_match,
            contact_email_match=contact_email_match,
            company_match=company_match,
            title_match=title_match,
            source_id_match=source_id_match,
            score=score,
        )
        if strength == "none":
            return None

        return EmailLeadMatchCandidate(
            lead_id=lead.id,
            label_ru=lead.label_ru,
            strength=strength,
            score=score,
            reasons_ru=tuple(reasons),
        )


def classify_strength(
    *,
    manual_selected: bool,
    original_url_match: bool,
    contact_email_match: bool,
    company_match: bool,
    title_match: bool,
    source_id_match: bool,
    score: int,
) -> MatchStrength:
    if manual_selected:
        return "exact"
    if original_url_match:
        return "exact"
    if contact_email_match and company_match and title_match:
        return "exact"
    if source_id_match and (company_match or title_match):
        return "strong"
    if contact_email_match and (company_match or title_match):
        return "strong"
    if company_match and title_match:
        return "strong"
    if score >= 35:
        return "weak"
    return "none"


def token_overlap(value: str | None, payload_tokens: tuple[str, ...]) -> int:
    if not value or not payload_tokens:
        return 0
    lead_tokens = set(tokenize_text(value))
    return len(lead_tokens.intersection(payload_tokens))


def normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    normalized_path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{normalized_path}"


def _strength_rank(strength: MatchStrength) -> int:
    return {"none": 0, "weak": 1, "strong": 2, "exact": 3}[strength]


def _summary_for_strength(strength: MatchStrength) -> str:
    if strength == "exact":
        return "Найдено точное совпадение с лидом."
    if strength == "strong":
        return "Найден сильный кандидат на привязку."
    if strength == "weak":
        return "Есть слабое совпадение. Лучше проверить вручную."
    return "Совпадений с лидами пока нет."
