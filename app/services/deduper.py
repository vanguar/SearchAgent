from __future__ import annotations

from datetime import date

from app.services.hashers import normalize_text_for_fingerprint, token_similarity
from app.services.normalization_models import (
    CanonicalVacancySnapshot,
    DuplicateCandidate,
    NormalizedVacancyRecord,
)


class VacancyDeduper:
    """Deterministic duplicate evaluation based on normalized fields and fingerprints."""

    def find_duplicate_candidate(
        self,
        record: NormalizedVacancyRecord,
        candidates: tuple[CanonicalVacancySnapshot, ...],
    ) -> DuplicateCandidate | None:
        best_candidate: DuplicateCandidate | None = None
        for candidate in candidates:
            evaluation = self.evaluate(record, candidate)
            if not evaluation.is_duplicate:
                continue
            if best_candidate is None or _candidate_rank(evaluation) > _candidate_rank(best_candidate):
                best_candidate = evaluation
        return best_candidate

    def evaluate(
        self,
        record: NormalizedVacancyRecord,
        candidate: CanonicalVacancySnapshot,
    ) -> DuplicateCandidate:
        title_similarity = token_similarity(record.title_tokens, candidate.title_tokens)
        content_similarity = token_similarity(record.content_tokens, candidate.content_tokens)
        company_match = bool(
            record.normalized_company
            and candidate.normalized_company
            and record.normalized_company == candidate.normalized_company
        )
        location_match = _locations_match(record, candidate)
        posting_date_close = _dates_are_close(record.posted_date, candidate.posted_date)

        reason_codes: list[str] = []
        if title_similarity >= 0.82:
            reason_codes.append("title_match_strong")
        elif title_similarity >= 0.68:
            reason_codes.append("title_match_partial")
        if company_match:
            reason_codes.append("company_match")
        if location_match:
            reason_codes.append("location_match")
        if content_similarity >= 0.58:
            reason_codes.append("content_match")
        if posting_date_close:
            reason_codes.append("posting_date_close")

        is_duplicate = (
            title_similarity >= 0.82
            and company_match
            and (location_match or content_similarity >= 0.58)
        ) or (
            title_similarity >= 0.9
            and company_match
            and posting_date_close
            and content_similarity >= 0.4
        )

        return DuplicateCandidate(
            canonical_key=candidate.canonical_key,
            is_duplicate=is_duplicate,
            title_similarity=title_similarity,
            content_similarity=content_similarity,
            company_match=company_match,
            location_match=location_match,
            posting_date_close=posting_date_close,
            reason_codes=tuple(reason_codes or ("no_safe_duplicate_signal",)),
        )


def _locations_match(
    record: NormalizedVacancyRecord,
    candidate: CanonicalVacancySnapshot,
) -> bool:
    left_text = record.normalized_location.normalized_text
    right_text = candidate.normalized_location.normalized_text
    if left_text and right_text and left_text == right_text:
        return True

    left_city = normalize_text_for_fingerprint(record.normalized_location.city)
    right_city = normalize_text_for_fingerprint(candidate.normalized_location.city)
    return bool(left_city and right_city and left_city == right_city)


def _dates_are_close(left: date | None, right: date | None) -> bool:
    if left is None or right is None:
        return False
    return abs((left - right).days) <= 7


def _candidate_rank(candidate: DuplicateCandidate) -> tuple[float, float, int, int]:
    return (
        candidate.title_similarity,
        candidate.content_similarity,
        int(candidate.company_match) + int(candidate.location_match),
        int(candidate.posting_date_close),
    )
