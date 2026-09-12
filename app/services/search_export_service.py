"""Сериализация результата поиска в JSON для внешнего анализа (в т.ч. нейросетью).

Отличие от HTML-экспорта: HTML показывает карточки — то, что человек и так видит в UI.
JSON выгружает РЕШЕНИЯ движка: какой запрос реально ушёл в источник после перевода роли,
какие попытки fallback сработали, из каких правил сложился score, по какому коду вакансия
попала в rejected или была срезана жёстким фильтром. Без этого разбор «почему выдача плохая»
остаётся догадкой.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from typing import Any

from app.services.normalization_models import NormalizedVacancyRecord
from app.services.search_models import (
    HiddenFilteredItem,
    RuleHit,
    SearchAttemptSummary,
    SearchProfileContext,
    SearchResultItem,
    SearchRunResult,
    SearchSourceState,
    VacancySignalSnapshot,
)
from app.services.source_adapters.models import SourceSearchInput

EXPORT_SCHEMA_VERSION = 1

# Два режима выгрузки. Разница измерена на реальном прогоне (94 вакансии):
# полная — ~448 КБ (~112k токенов), из них 98% занимают тексты вакансий и нулевые сигналы,
# а собственно диагностика — 2%. Такой файл в окно модели уже не кладётся.
# Диагностический режим оставляет всё, что нужно для разбора выдачи, и выкидывает балласт.
EXPORT_MODE_DIAGNOSTICS = "diagnostics"
EXPORT_MODE_FULL = "full"
EXPORT_MODES = (EXPORT_MODE_DIAGNOSTICS, EXPORT_MODE_FULL)

# Сколько символов описания класть в выгрузку. Достаточно, чтобы увидеть требования по
# языку и опыту (именно по ним работает скорер), но не раздувает файл на сотне вакансий.
DEFAULT_DESCRIPTION_EXCERPT_CHARS = 1500

# Поле с полным склеенным текстом вакансии — дублирует description_excerpt и весит много.
_SKIPPED_SIGNAL_FIELDS = frozenset({"combined_text"})


def build_search_export(
    result: SearchRunResult,
    *,
    task_id: str | None = None,
    generated_at: datetime,
    search_input: SourceSearchInput | None = None,
    source_ids_requested: tuple[str, ...] = (),
    description_excerpt_chars: int = DEFAULT_DESCRIPTION_EXCERPT_CHARS,
    mode: str = EXPORT_MODE_DIAGNOSTICS,
) -> dict[str, Any]:
    """Снимок поискового прогона.

    `mode="diagnostics"` (по умолчанию) — компактный разбор: параметры прогона, профиль,
    источники, вся цепочка попыток, и по каждой вакансии только решение движка (bucket,
    score, коды сработавших правил, ненулевые сигналы). Тексты вакансий не включаются.

    `mode="full"` — дополнительно тексты: описание, русское резюме и пояснение, ярлыки
    правил. Нужен, когда разбираются сами формулировки вакансий, а не работа движка.
    """
    if mode not in EXPORT_MODES:
        raise ValueError(f"Неизвестный режим выгрузки: {mode!r}. Допустимы: {EXPORT_MODES}.")
    full = mode == EXPORT_MODE_FULL
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "mode": mode,
        "generated_at": generated_at.isoformat(),
        "task_id": task_id,
        "run": _run_section(result, search_input, source_ids_requested),
        "profile": _profile_section(result.profile),
        "sources": [_source_state(state) for state in result.source_states],
        "attempts": _attempts_section(result.attempt_summary),
        "vacancies": [
            _vacancy(item, description_excerpt_chars=description_excerpt_chars, full=full)
            for item in (*result.hot_results, *result.maybe_results, *result.rejected_results)
        ],
        "hidden_filtered": [_hidden_item(item, full=full) for item in result.hidden_filtered_items],
    }


def _run_section(
    result: SearchRunResult,
    search_input: SourceSearchInput | None,
    source_ids_requested: tuple[str, ...],
) -> dict[str, Any]:
    section: dict[str, Any] = {
        "source_ids_requested": list(source_ids_requested),
        "totals": {
            "raw_records": result.total_raw_records,
            "normalized_records": result.total_normalized_records,
            "canonical_results": result.total_canonical_results,
            "hot": len(result.hot_results),
            "maybe": len(result.maybe_results),
            "rejected": len(result.rejected_results),
            "hidden_by_hard_filter": len(result.hidden_filtered_items),
        },
    }
    if search_input is not None:
        section["query_submitted"] = search_input.query
        section["location"] = search_input.location
        section["radius_km"] = search_input.radius_km
        section["search_mode"] = search_input.search_mode
        section["page_size"] = search_input.page_size
    return section


def _profile_section(profile: SearchProfileContext) -> dict[str, Any]:
    return {
        "label": profile.profile_label,
        "source": profile.profile_source,
        "note_ru": profile.note_ru,
        "legal_status": profile.legal_status,
        "work_authorized": profile.work_authorized,
        "german_level": profile.german_level,
        "english_level": profile.english_level,
        "no_german_required": profile.no_german_required,
        "section24_interpreted": profile.section24_interpreted,
        "desired_roles": list(profile.desired_roles),
        "excluded_roles": list(profile.excluded_roles),
        "preferred_locations": list(profile.preferred_locations),
        "search_query_terms": list(profile.search_query_terms),
        "relocation_ready": profile.relocation_ready,
        "shift_ok": profile.shift_ok,
        "physical_work_ok": profile.physical_work_ok,
        "housing_needed": profile.housing_needed,
        "start_availability_text": profile.start_availability_text,
        "driver_license": profile.driver_license,
    }


def _source_state(state: SearchSourceState) -> dict[str, Any]:
    return {
        "source_id": state.source_id,
        "source_name": state.source_name,
        "status_kind": state.status_kind,
        "status_label": state.status_label,
        "raw_count": state.raw_count,
        "total_count": state.total_count,
        "normalized_count": state.normalized_count,
        "canonical_count": state.canonical_count,
        "warnings": list(state.warnings),
        "error_message": state.error_message,
    }


def _attempts_section(summary: SearchAttemptSummary | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "primary_query": summary.primary_query,
        "final_query_used": summary.final_query_used,
        "fallback_used": summary.fallback_used,
        "language_relaxation_used": summary.language_relaxation_used,
        "user_message_ru": summary.user_message_ru,
        "attempts": [
            {
                "attempt_number": attempt.attempt_number,
                "stage_name": attempt.stage_name,
                "query_used": attempt.query_used,
                "language_relaxation_applied": attempt.language_relaxation_applied,
                "raw_count": attempt.raw_count,
                "normalized_count": attempt.normalized_count,
                "deduped_count": attempt.deduped_count,
                "hot_count": attempt.hot_count,
                "review_count": attempt.review_count,
                "rejected_count": attempt.rejected_count,
                "non_rejected_count": attempt.non_rejected_count,
                "reason_continued": attempt.reason_continued,
                # Главный сигнал для разбора: по каким кодам сыпались вакансии на этой попытке.
                "rejection_reason_counts": dict(attempt.rejection_reason_counts),
            }
            for attempt in summary.attempts
        ],
    }


def _vacancy(item: SearchResultItem, *, description_excerpt_chars: int, full: bool) -> dict[str, Any]:
    group = item.canonical_group
    record = item.primary_record
    vacancy: dict[str, Any] = {
        "canonical_key": group.canonical_key,
        "bucket": item.bucket,
        "score": item.score_result.score,
        "relevance_band": item.relevance_band,
        "role_family": item.role_family,
        "search_query": item.search_query,
        "title_original": record.original_title,
        "company": group.company_name,
        "location_text": group.location_text,
        "country_code": group.country_code,
        "city": group.city,
        "posted_date": _iso_date(group.posted_date),
        "url": record.source_url or record.source_reference,
        "sources": [_provenance(source, full=full) for source in group.source_records],
        "filter": {
            "decision": item.filter_result.decision,
            "positive_hits": _rule_hits(item.filter_result.positive_hits, full=full),
            "rejection_hits": _rule_hits(item.filter_result.rejection_hits, full=full),
            "review_hits": _rule_hits(item.filter_result.review_hits, full=full),
        },
        "score_hits": {
            "positive": _rule_hits(item.score_result.positive_hits, full=full),
            "negative": _rule_hits(item.score_result.negative_hits, full=full),
        },
        "signals": _signals(item.signals, full=full),
    }
    if full:
        vacancy["title_normalized"] = group.normalized_title
        vacancy["title_ru"] = item.translated_title_ru
        vacancy["summary_ru"] = item.summary_ru
        vacancy["explanation_ru"] = item.explanation_ru
        vacancy["description_excerpt"] = _excerpt(record.body_text, description_excerpt_chars)
    return vacancy


def _provenance(record: NormalizedVacancyRecord, *, full: bool) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "source_id": record.source_id,
        "external_id": record.external_id,
    }
    if full:
        provenance["source_name"] = record.source_name
        provenance["url"] = record.source_url or record.source_reference
        provenance["posted_date"] = _iso_date(record.posted_date)
    return provenance


def _hidden_item(item: HiddenFilteredItem, *, full: bool) -> dict[str, Any]:
    hidden: dict[str, Any] = {
        "title": item.title,
        "company": item.company_name,
        "location_text": item.location_text,
        "source_name": item.source_name,
        "rejection_reasons": _rule_hits(item.rejection_reasons, full=full),
    }
    if full:
        hidden["canonical_key"] = item.canonical_key
        hidden["url"] = item.original_url
    return hidden


def _signals(signals: VacancySignalSnapshot, *, full: bool) -> dict[str, Any]:
    """Сигналы сериализуются по полям датакласса — новые признаки попадут в выгрузку сами.

    В диагностическом режиме пустые/выключенные сигналы опускаются: их подавляющее
    большинство, и они ничего не сообщают — важно то, что СРАБОТАЛО.
    """
    serialized: dict[str, Any] = {}
    for field in dataclasses.fields(signals):
        if field.name in _SKIPPED_SIGNAL_FIELDS:
            continue
        value = getattr(signals, field.name)
        if isinstance(value, tuple) and value and isinstance(value[0], RuleHit):
            serialized[field.name] = _rule_hits(value, full=full)
        elif isinstance(value, tuple):
            serialized[field.name] = list(value)
        elif not full and value in (False, None):
            continue
        else:
            serialized[field.name] = value
    if not full:
        serialized = {key: value for key, value in serialized.items() if value not in ([], ())}
    return serialized


def _rule_hits(hits: tuple[RuleHit, ...], *, full: bool) -> list[Any]:
    """В диагностике достаточно кода и веса; русский ярлык — это текст для человека."""
    if full:
        return [{"code": hit.code, "label_ru": hit.label_ru, "weight": hit.weight} for hit in hits]
    return [{"code": hit.code, "weight": hit.weight} if hit.weight else hit.code for hit in hits]


def _excerpt(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + "…"


def _iso_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def build_export_filename(
    task_id: str | None,
    generated_at: datetime,
    *,
    mode: str = EXPORT_MODE_DIAGNOSTICS,
) -> str:
    stamp = generated_at.strftime("%Y%m%d-%H%M")
    suffix = f"-{task_id}" if task_id else ""
    return f"smartjob-search-{stamp}{suffix}-{mode}.json"
