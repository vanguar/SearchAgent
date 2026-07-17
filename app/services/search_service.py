from __future__ import annotations

import dataclasses
import re
import threading
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from app.core.logging import logger
from app.services.filter_engine import FilterEngine
from app.services.hashers import normalize_text_for_fingerprint
from app.services.match_explainer import MatchExplainer
from app.services.normalization_models import CanonicalVacancyGroup, NormalizedVacancyRecord
from app.services.relevance_memory_service import FeedbackLabel, ProfileFeedbackMemory, RelevanceMemoryService
from app.services.role_family import (
    RoleFamily,
    classify_query_ru,
    classify_vacancy_de,
    families_are_compatible,
    is_specific_family,
)
from app.services.role_intent import normalize_role_intent
from app.services.rule_catalog import HOT_BUCKET_MIN_SCORE, MAYBE_BUCKET_MIN_SCORE, inspect_vacancy
from app.services.scorer import VacancyScorer
from app.services.search_fallback import (
    ENOUGH_HOT,
    ENOUGH_NON_REJECTED,
    LOW_LANGUAGE_FALLBACK,
    MAX_DETERMINISTIC_STAGES,
    MAX_LLM_STAGES,
    get_fallback_keywords,
    get_intent_fallback_keywords,
    get_profile_fallback_keywords,
    is_low_language_profile,
)
from app.services.search_models import (
    DedupPreviewItem,
    FilterResult,
    HiddenFilteredItem,
    RelevanceBand,
    RuleHit,
    ScoreResult,
    SearchAttemptRecord,
    SearchAttemptSummary,
    SearchBucket,
    SearchProfileContext,
    SearchQueryResultGroup,
    SearchResultItem,
    SearchRunResult,
    SearchSourceState,
    SourceStatusKind,
)
from app.services.search_profile_resolver import DatabaseSearchProfileResolver
from app.services.source_adapters.errors import SourceAdapterError
from app.services.source_adapters.models import (
    AdapterSearchResponse,
    SourceAdapterDescriptor,
    SourceRecordPreview,
    SourceSearchInput,
)
from app.services.source_adapters.registry import SourceAdapterRegistry
from app.services.summary_service import SummaryService
from app.services.translation_service import TranslationService
from app.services.vacancy_processing import VacancyProcessingService

if TYPE_CHECKING:
    from app.services.llm_client import LLMClient

_BUCKET_PRIORITY: dict[SearchBucket, int] = {"hot": 0, "maybe": 1, "rejected": 2}
_EXPLICIT_WEAK_SCORE_CAP = HOT_BUCKET_MIN_SCORE - 1
_EXPLICIT_IRRELEVANT_SCORE_CAP = MAYBE_BUCKET_MIN_SCORE - 1
_RUSSIAN_LANGUAGE_SOURCE_IDS = frozenset({"hh", "dou_rss", "djinni_rss"})
_DUAL_MODE_SOURCE_IDS = frozenset({"arbeitnow", "greenhouse", "jooble", "lever"})
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]")
# Upper bound on concurrent source fetches per search attempt (I/O-bound network calls).
_MAX_FETCH_WORKERS = 8
# Upper bound on concurrent LLM enrichment calls for the final displayed results.
_MAX_ENRICH_WORKERS = 8
# Buckets whose items are rendered as cards (and therefore worth LLM enrichment).
_DISPLAYED_BUCKETS: frozenset[SearchBucket] = frozenset({"hot", "maybe"})


class SearchService:
    """PHASE 7 orchestration: fetch, normalize, dedup, filter, score, explain, summarize."""

    def __init__(
        self,
        *,
        registry: SourceAdapterRegistry | None = None,
        vacancy_processing_service: VacancyProcessingService | None = None,
        filter_engine: FilterEngine | None = None,
        scorer: VacancyScorer | None = None,
        match_explainer: MatchExplainer | None = None,
        translation_service: TranslationService | None = None,
        summary_service: SummaryService | None = None,
        profile_resolver: DatabaseSearchProfileResolver | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.registry = registry or SourceAdapterRegistry()
        self.vacancy_processing_service = vacancy_processing_service or VacancyProcessingService()
        self.filter_engine = filter_engine or FilterEngine()
        self.scorer = scorer or VacancyScorer(filter_engine=self.filter_engine)
        self.match_explainer = match_explainer or MatchExplainer()
        self.translation_service = translation_service or TranslationService()
        self.summary_service = summary_service or SummaryService(translation_service=self.translation_service)
        self.profile_resolver = profile_resolver or DatabaseSearchProfileResolver()
        self._llm_client = llm_client
        self._memory_service = RelevanceMemoryService()

    def list_sources(self) -> tuple[SourceAdapterDescriptor, ...]:
        return self.registry.list_sources()

    def get_profile_context(self, *, profile_id: int | None = None) -> SearchProfileContext:
        return self.profile_resolver.resolve(profile_id=profile_id)

    def search(
        self,
        *,
        search_input: SourceSearchInput,
        source_ids: Sequence[str] = (),
        profile: SearchProfileContext | None = None,
        profile_id: int | None = None,
        progress_callback: Callable[[str, int, str | None, str | None], None] | None = None,
        stop_event: threading.Event | None = None,
        feedback_memory: ProfileFeedbackMemory | None = None,
        enrich_with_llm: bool = True,
    ) -> SearchRunResult:
        resolved_profile = profile or self.get_profile_context(profile_id=profile_id)
        resolved_source_ids = self._resolve_source_ids(source_ids)

        fetched_records: list[SourceRecordPreview] = []
        successful_responses: list[AdapterSearchResponse] = []
        failed_states: list[SearchSourceState] = []

        # Sources are independent network calls — fetch them concurrently. Results are
        # reassembled in the original resolved_source_ids order afterwards so that
        # downstream dedup/scoring stays fully deterministic regardless of completion order.
        active_source_ids = [
            source_id
            for source_id in resolved_source_ids
            if not (stop_event is not None and stop_event.is_set())
        ]
        outcomes: dict[str, tuple[AdapterSearchResponse | None, SearchSourceState | None]] = {}
        if active_source_ids:
            max_workers = min(len(active_source_ids), _MAX_FETCH_WORKERS)
            running_total = 0
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="src-fetch") as pool:
                future_to_id = {
                    pool.submit(self._fetch_source, source_id, search_input): source_id
                    for source_id in active_source_ids
                }
                for future in as_completed(future_to_id):
                    source_id = future_to_id[future]
                    response, failed = future.result()
                    outcomes[source_id] = (response, failed)
                    if response is not None and progress_callback is not None:
                        running_total += len(response.records)
                        progress_callback("fetch", running_total, response.source_name, search_input.query)

        for source_id in active_source_ids:
            response, failed = outcomes.get(source_id, (None, None))
            if response is not None:
                successful_responses.append(response)
                fetched_records.extend(response.records)
            elif failed is not None:
                failed_states.append(failed)

        if not successful_responses:
            source_states = tuple(
                failed_states
                or [
                    SearchSourceState(
                        source_id="search",
                        source_name="Поиск",
                        status_label="Нет данных",
                        status_kind="warning",
                        error_message="Нет доступных результатов для обработки.",
                    )
                ]
            )
            return SearchRunResult(
                profile=resolved_profile,
                source_states=source_states,
                results=(),
                query_result_groups=(SearchQueryResultGroup(query=search_input.query),),
            )

        if progress_callback is not None:
            progress_callback("postprocess", len(fetched_records), None, search_input.query)

        aggregated_response = AdapterSearchResponse(
            source_id="phase7",
            source_name="PHASE 7 Search Orchestration",
            records=tuple(fetched_records),
            total_count=len(fetched_records),
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload={"sources": tuple(response.source_id for response in successful_responses)},
            warnings=tuple(warning for response in successful_responses for warning in response.warnings),
        )
        processed = self.vacancy_processing_service.process_adapter_response(aggregated_response)

        deduped_preview_items = tuple(
            _build_dedup_preview_item(canonical)
            for canonical in processed.canonical_groups
        )
        hidden_filtered_items = tuple(
            hidden
            for canonical in processed.canonical_groups
            if (hidden := self._build_hidden_filtered_item(
                canonical=canonical,
                profile=resolved_profile,
                search_mode=search_input.search_mode,
            )) is not None
        )

        results = tuple(
            item
            for canonical in processed.canonical_groups
            if (item := self._build_result_item(
                canonical=canonical,
                profile=resolved_profile,
                feedback_memory=feedback_memory,
                search_query=search_input.query,
                search_mode=search_input.search_mode,
                enrich_with_llm=enrich_with_llm,
            )) is not None
        )
        ordered_results = tuple(sorted(results, key=_result_sort_key))

        source_states = self._build_source_states(
            successful_responses=tuple(successful_responses),
            failed_states=tuple(failed_states),
            normalized_records=processed.normalized_records,
            canonical_groups=processed.canonical_groups,
        )

        return SearchRunResult(
            profile=resolved_profile,
            source_states=source_states,
            results=ordered_results,
            hot_results=tuple(result for result in ordered_results if result.bucket == "hot"),
            maybe_results=tuple(result for result in ordered_results if result.bucket == "maybe"),
            rejected_results=tuple(result for result in ordered_results if result.bucket == "rejected"),
            query_result_groups=(
                SearchQueryResultGroup(
                    query=search_input.query,
                    hot_results=tuple(result for result in ordered_results if result.bucket == "hot"),
                    maybe_results=tuple(result for result in ordered_results if result.bucket == "maybe"),
                ),
            ),
            deduped_preview_items=deduped_preview_items,
            hidden_filtered_items=hidden_filtered_items,
            total_raw_records=len(fetched_records),
            total_normalized_records=len(processed.normalized_records),
            total_canonical_results=len(processed.canonical_groups),
        )

    def orchestrated_search(
        self,
        *,
        search_input: SourceSearchInput,
        source_ids: Sequence[str] = (),
        profile_id: int | None = None,
        progress_callback: Callable[[str, int, str | None, str | None], None] | None = None,
        partial_result_callback: Callable[[SearchRunResult], None] | None = None,
        stop_event: threading.Event | None = None,
        feedback_memory: ProfileFeedbackMemory | None = None,
    ) -> SearchRunResult:
        """Run search with staged keyword fallback. Returns the best result with attempt_summary attached."""
        resolved_profile = self.get_profile_context(profile_id=profile_id)
        resolved_source_ids = self._resolve_source_ids(source_ids)
        primary_query = search_input.query
        primary_role = resolved_profile.desired_roles[0] if resolved_profile.desired_roles else None
        low_language = is_low_language_profile(
            german_level=resolved_profile.german_level,
            english_level=resolved_profile.english_level,
        )

        profile_terms = _resolve_profile_search_terms(
            resolved_profile,
            source_ids=resolved_source_ids,
        )
        if profile_terms:
            effective_primary_query = profile_terms[0]
            # Family from the role intent (handles already-German terms like "lager", which
            # classify_query_ru — Russian-only — would mislabel as GENERIC).
            primary_intent = normalize_role_intent(effective_primary_query)
            query_family = (
                primary_intent.family
                if primary_intent is not None
                else classify_query_ru(effective_primary_query)
            )
            # Broaden with same-family alternative titles for German/western sources only;
            # Russian-only source runs keep the raw profile terms.
            fallback_keywords = get_profile_fallback_keywords(
                profile_terms=profile_terms,
                family=query_family,
                role_primary_de=primary_intent.primary_de if primary_intent is not None else "",
                broaden=(
                    is_specific_family(query_family)
                    and not _uses_only_russian_language_sources(resolved_source_ids)
                ),
            )
        else:
            intent = normalize_role_intent(primary_query)
            if intent is not None:
                effective_primary_query = intent.primary_de
                query_family = intent.family
                fallback_keywords = get_intent_fallback_keywords(
                    intent=intent,
                    primary_query=effective_primary_query,
                    low_language=low_language,
                )
            else:
                effective_primary_query = primary_query
                query_family = classify_query_ru(primary_query)
                fallback_keywords = get_fallback_keywords(
                    primary_query=primary_query,
                    role=primary_role,
                    low_language=low_language,
                )

        def _run(query: str) -> SearchRunResult:
            if progress_callback is not None:
                progress_callback("attempt", 0, None, query)
            # Attempts are scored deterministically (no LLM). LLM enrichment is applied once,
            # at the end, to the winning attempt's displayed results only — see _enrich_run_result.
            return self.search(
                search_input=dataclasses.replace(search_input, query=query),
                source_ids=resolved_source_ids,
                profile=resolved_profile,
                progress_callback=progress_callback,
                stop_event=stop_event,
                feedback_memory=feedback_memory,
                enrich_with_llm=False,
            )

        def _non_rejected(r: SearchRunResult) -> int:
            return len(r.hot_results) + len(r.maybe_results)

        def _is_enough(r: SearchRunResult) -> bool:
            enough_non_rejected, enough_hot = _early_stop_thresholds(search_input.search_mode)
            return _non_rejected(r) >= enough_non_rejected and len(r.hot_results) >= enough_hot

        def _is_better(a: SearchRunResult, b: SearchRunResult | None) -> bool:
            if b is None:
                return True
            return (_non_rejected(a), len(a.hot_results)) > (_non_rejected(b), len(b.hot_results))

        def _record(
            result: SearchRunResult,
            stage: str,
            query: str,
            lang_relax: bool,
            reason: str | None,
            attempt_no: int,
            primary_q: str,
        ) -> SearchAttemptRecord:
            return SearchAttemptRecord(
                attempt_number=attempt_no,
                primary_query=primary_q,
                stage_name=stage,
                query_used=query,
                language_relaxation_applied=lang_relax,
                raw_count=result.total_raw_records,
                normalized_count=result.total_normalized_records,
                deduped_count=result.total_canonical_results,
                hot_count=len(result.hot_results),
                review_count=len(result.maybe_results),
                rejected_count=len(result.rejected_results) + len(result.hidden_filtered_items),
                non_rejected_count=_non_rejected(result),
                reason_continued=reason,
                rejection_reason_counts=_rejection_reason_counts(result),
            )

        attempt_records: list[SearchAttemptRecord] = []
        attempt_num = 0  # 0-based counter across all stages

        attempt_results: list[SearchRunResult] = []
        language_relaxation_used = False

        def _publish_partial() -> None:
            if partial_result_callback is None or not attempt_results:
                return
            partial_result = (
                _merge_search_results_for_worldwide(attempt_results)
                if len(attempt_results) > 1
                else attempt_results[0]
            )
            partial_summary = SearchAttemptSummary(
                primary_query=primary_query,
                final_query_used="in progress",
                fallback_used=len(attempt_records) > 1,
                language_relaxation_used=language_relaxation_used,
                attempts=tuple(attempt_records),
                user_message_ru=None,
            )
            partial_result_callback(dataclasses.replace(partial_result, attempt_summary=partial_summary))

        # --- Primary attempt ---
        logger.info(
            "search_orchestrator stage=primary query=%r effective=%r location=%r source_ids=%s profile_id=%s",
            primary_query, effective_primary_query, search_input.location, resolved_source_ids, profile_id,
        )
        r0 = _run(effective_primary_query)
        attempt_results.append(r0)
        primary_sufficient = _is_enough(r0)
        primary_rec = _record(
            r0, "primary", effective_primary_query, False,
            None if primary_sufficient else "Недостаточно результатов.",
            attempt_num, primary_query,
        )
        attempt_records.append(primary_rec)
        attempt_num += 1
        _log_attempt_result(primary_rec, resolved_source_ids, profile_id)
        _publish_partial()

        exhaustive_search = True

        best_result: SearchRunResult = r0
        # winning_query tracks the query that produced best_result — updated only when best_result is updated
        winning_query = effective_primary_query
        winning_lang_relax = False

        # --- Deterministic fallback stages ---
        deterministic_keywords = (
            fallback_keywords
            if exhaustive_search
            else fallback_keywords[:MAX_DETERMINISTIC_STAGES]
        )
        for idx, kw in enumerate(deterministic_keywords, start=1):
            if stop_event is not None and stop_event.is_set():
                break
            stage = f"fallback_{idx}"
            lang_relax = low_language and kw in LOW_LANGUAGE_FALLBACK
            if lang_relax:
                language_relaxation_used = True
            logger.info(
                "search_orchestrator stage=%s query=%r location=%r lang_relax=%s source_ids=%s profile_id=%s",
                stage, kw, search_input.location, lang_relax, resolved_source_ids, profile_id,
            )
            r = _run(kw)
            attempt_results.append(r)
            will_continue = not _is_enough(r) and (
                idx < len(deterministic_keywords)
                or (self._llm_client is not None and MAX_LLM_STAGES > 0)
            )
            rec = _record(
                r, stage, kw, lang_relax,
                "Продолжается расширение поиска." if will_continue else None,
                attempt_num, primary_query,
            )
            attempt_records.append(rec)
            attempt_num += 1
            _log_attempt_result(rec, resolved_source_ids, profile_id)
            _publish_partial()
            if _is_better(r, best_result):
                best_result = r
                winning_query = kw
                winning_lang_relax = lang_relax
            if not exhaustive_search and search_input.search_mode != "remote_worldwide" and _is_enough(r):
                break

        # --- LLM-assisted stage (last resort only) ---
        if (
            (search_input.search_mode == "remote_worldwide" or not _is_enough(best_result))
            and self._llm_client is not None
            and MAX_LLM_STAGES > 0
            and is_specific_family(query_family)
        ):
            already_tried: set[str] = {
                primary_query.strip().lower(),
                effective_primary_query.strip().lower(),
            } | {kw.strip().lower() for kw in deterministic_keywords}
            llm_kws = self._llm_suggest_keywords(
                primary_role=primary_role,
                already_tried=already_tried,
                query_family=query_family,
            )
            for llm_kw in llm_kws[:MAX_LLM_STAGES]:
                if stop_event is not None and stop_event.is_set():
                    break
                lang_relax = low_language
                if lang_relax:
                    language_relaxation_used = True
                logger.info(
                    "search_orchestrator stage=llm_1 query=%r location=%r lang_relax=%s source_ids=%s profile_id=%s",
                    llm_kw, search_input.location, lang_relax, resolved_source_ids, profile_id,
                )
                r = _run(llm_kw)
                attempt_results.append(r)
                rec = _record(r, "llm_1", llm_kw, lang_relax, None, attempt_num, primary_query)
                attempt_records.append(rec)
                attempt_num += 1
                _log_attempt_result(rec, resolved_source_ids, profile_id)
                _publish_partial()
                if _is_better(r, best_result):
                    best_result = r
                    winning_query = llm_kw
                    winning_lang_relax = lang_relax

        fallback_used = winning_query != primary_query or len(attempt_results) > 1
        if winning_lang_relax:
            language_relaxation_used = True
        multiple_attempts = len(attempt_results) > 1
        result_for_summary = (
            _merge_search_results_for_worldwide(attempt_results)
            if exhaustive_search and multiple_attempts
            else best_result
        )
        # Enrich once: only the winning attempt's displayed (hot+maybe) results get LLM
        # translation/summaries. Everything above ran deterministically for speed.
        result_for_summary = self._enrich_run_result(result_for_summary)
        exhaustive_query_label = "all profile queries" if profile_terms else "all planned queries"
        summary = SearchAttemptSummary(
            primary_query=primary_query,
            final_query_used=(
                exhaustive_query_label
                if exhaustive_search and multiple_attempts
                else winning_query
            ),
            fallback_used=fallback_used,
            language_relaxation_used=language_relaxation_used,
            attempts=tuple(attempt_records),
            user_message_ru=(
                None
                if primary_sufficient
                else _build_attempt_user_message(
                    primary_query=primary_query,
                    winning_query=winning_query,
                    fallback_used=fallback_used,
                    language_relaxation_used=language_relaxation_used,
                    best_result=result_for_summary,
                )
            ),
        )
        return dataclasses.replace(result_for_summary, attempt_summary=summary)

    def _llm_suggest_keywords(
        self,
        *,
        primary_role: str | None,
        already_tried: set[str],
        query_family: RoleFamily | None = None,
    ) -> list[str]:
        if primary_role is None or self._llm_client is None:
            return []

        try:
            raw = self._llm_client.suggest_fallback_keywords(primary_role, list(already_tried))
        except Exception:
            logger.warning("search_orchestrator_llm_suggest_failed role=%s", primary_role)
            return []

        if not raw:
            return []

        candidates = [
            kw.strip().lower()
            for kw in raw.split()
            if kw.strip() and kw.strip().lower() not in already_tried
        ]

        if query_family is None or not is_specific_family(query_family):
            return candidates[:3]

        filtered: list[str] = []
        for kw in candidates:
            normalized_kw = normalize_text_for_fingerprint(kw)
            kw_family = classify_vacancy_de(normalized_kw)

            # Для специфичных семейств не допускаем generic-мусор вроде techniker.
            if not is_specific_family(kw_family):
                continue

            if kw_family is query_family or families_are_compatible(query_family, kw_family):
                filtered.append(kw)

        return filtered[:3]

    def _resolve_source_ids(self, source_ids: Sequence[str]) -> tuple[str, ...]:
        if source_ids:
            return tuple(source_ids)
        return tuple(
            source.source_id
            for source in self.list_sources()
            if source.enabled and source.status_kind != "error"
        )

    def resolve_source_ids_for_search(
        self,
        *,
        source_ids: Sequence[str] = (),
        search_mode: str = "germany_local",
        source_scope: str = "western",
    ) -> tuple[str, ...]:
        if source_ids:
            return tuple(source_ids)
        descriptors = self.list_sources()
        if source_scope == "russian":
            return tuple(
                source.source_id
                for source in descriptors
                if source.enabled
                and source.status_kind != "error"
                and source.source_id in _RUSSIAN_LANGUAGE_SOURCE_IDS
            )
        return tuple(
            source.source_id
            for source in descriptors
            if source.enabled
            and source.status_kind != "error"
            and source.source_id not in _RUSSIAN_LANGUAGE_SOURCE_IDS
            and (
                source.source_id in _DUAL_MODE_SOURCE_IDS
                or
                (search_mode == "remote_worldwide" and source.global_remote)
                or (search_mode != "remote_worldwide" and not source.global_remote)
            )
        )

    def _enrich_run_result(self, result: SearchRunResult) -> SearchRunResult:
        """Apply LLM translation/summaries to the displayed (hot+maybe) results only, once.

        Scoring/bucketing already happened deterministically. Here we replace just the
        translated_title_ru/summary_ru of the cards the user will actually see, in parallel.
        No-op when there is no LLM client or nothing to display.
        """
        if self._llm_client is None:
            return result
        display_items = [item for item in result.results if item.bucket in _DISPLAYED_BUCKETS]
        if not display_items:
            return result

        enriched_by_key: dict[str, SearchResultItem] = {}
        max_workers = min(len(display_items), _MAX_ENRICH_WORKERS)
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="llm-enrich") as pool:
            future_to_key = {
                pool.submit(self._enrich_item, item): item.canonical_group.canonical_key
                for item in display_items
            }
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    enriched_by_key[key] = future.result()
                except Exception:
                    # A single enrichment failure must not drop the card — keep it deterministic.
                    logger.warning("llm_enrichment_failed canonical_key=%s", key)

        if not enriched_by_key:
            return result

        def _swap(item: SearchResultItem) -> SearchResultItem:
            return enriched_by_key.get(item.canonical_group.canonical_key, item)

        new_results = tuple(_swap(item) for item in result.results)
        new_groups = tuple(
            dataclasses.replace(
                group,
                hot_results=tuple(_swap(item) for item in group.hot_results),
                maybe_results=tuple(_swap(item) for item in group.maybe_results),
            )
            for group in result.query_result_groups
        )
        return dataclasses.replace(
            result,
            results=new_results,
            hot_results=tuple(item for item in new_results if item.bucket == "hot"),
            maybe_results=tuple(item for item in new_results if item.bucket == "maybe"),
            rejected_results=tuple(item for item in new_results if item.bucket == "rejected"),
            query_result_groups=new_groups,
        )

    def _enrich_item(self, item: SearchResultItem) -> SearchResultItem:
        """Recompute the LLM-backed title/summary for a single displayed result."""
        translated_title_ru = self.translation_service.translate_title(
            normalized_title=item.canonical_group.normalized_title,
            original_title=item.primary_record.original_title,
            use_llm=True,
        )
        summary_ru = self.summary_service.build_summary(
            item.canonical_group,
            item.signals,
            translated_title_ru=translated_title_ru,
            use_llm=True,
        )
        return dataclasses.replace(
            item,
            translated_title_ru=translated_title_ru,
            summary_ru=summary_ru,
        )

    def _fetch_source(
        self,
        source_id: str,
        search_input: SourceSearchInput,
    ) -> tuple[AdapterSearchResponse | None, SearchSourceState | None]:
        """Fetch one source. Returns (response, None) on success or (None, failed_state) on error.

        Safe to run from a worker thread: adapters are stateless and share no mutable state.
        """
        descriptor: SourceAdapterDescriptor | None = None
        try:
            adapter = self.registry.get(source_id)
            descriptor = adapter.describe()
            logger.info(
                "source_adapter_start source_id=%s source_name=%r query=%r location=%r "
                "page=%d page_size=%d mode=%s enabled=%s",
                source_id,
                descriptor.display_name,
                search_input.query,
                search_input.location,
                search_input.page,
                search_input.page_size,
                search_input.search_mode,
                descriptor.enabled,
            )
            response = adapter.search(search_input)
        except SourceAdapterError as exc:
            logger.warning(
                "source_adapter_error source_id=%s source_name=%r code=%s retryable=%s message=%r",
                exc.source_id,
                exc.source_name,
                exc.code,
                exc.retryable,
                exc.message,
            )
            return None, SearchSourceState(
                source_id=exc.source_id,
                source_name=exc.source_name,
                status_label="Ошибка",
                status_kind="error",
                error_message=exc.message,
            )
        except Exception:
            source_name = descriptor.display_name if descriptor is not None else source_id.upper()
            logger.exception("search_service_unexpected_error source_id=%s", source_id)
            return None, SearchSourceState(
                source_id=source_id,
                source_name=source_name,
                status_label="Ошибка",
                status_kind="error",
                error_message=f"Не удалось выполнить поиск по источнику {source_name}.",
            )

        logger.info(
            "source_adapter_success source_id=%s source_name=%r raw=%d total=%s "
            "page=%d page_size=%d warnings=%d raw_payload=%s",
            response.source_id,
            response.source_name,
            len(response.records),
            response.total_count,
            response.page,
            response.page_size,
            len(response.warnings),
            _summarize_raw_payload(response.raw_payload),
        )
        for warning in response.warnings:
            logger.warning(
                "source_adapter_warning source_id=%s source_name=%r warning=%r",
                response.source_id,
                response.source_name,
                warning,
            )
        return response, None

    def _build_result_item(
        self,
        *,
        canonical: CanonicalVacancyGroup,
        profile: SearchProfileContext,
        feedback_memory: ProfileFeedbackMemory | None = None,
        search_query: str | None = None,
        search_mode: str | None = None,
        enrich_with_llm: bool = True,
    ) -> SearchResultItem | None:
        primary_record = _pick_primary_record(canonical)
        signals = inspect_vacancy(canonical, profile)
        filter_result = self.filter_engine.evaluate(
            canonical, profile, signals=signals, search_mode=search_mode
        )
        if filter_result.hard_reject:
            return None

        # Deterministic role family from canonical normalized title — None when title is generic.
        role_family_str: str | None = None
        normalized_for_classification = normalize_text_for_fingerprint(canonical.normalized_title or "")
        if normalized_for_classification:
            classified = classify_vacancy_de(normalized_for_classification)
            if classified.value != "generic":
                role_family_str = classified.value

        # Compute bounded feedback adjustment (0 when no memory available).
        feedback_adjustment: int | None = None
        feedback_note_ru: str | None = None
        explicit_feedback_label: FeedbackLabel | None = None
        if feedback_memory is not None:
            explicit_feedback_label = feedback_memory.explicit_feedback_by_canonical_key.get(
                canonical.canonical_key
            )
            adj_result = self._memory_service.compute_score_adjustment(
                feedback_memory,
                normalized_title=canonical.normalized_title or primary_record.original_title,
                role_family=role_family_str,
                source_name=primary_record.source_name,
            )
            if adj_result.adjustment != 0:
                feedback_adjustment = adj_result.adjustment
                feedback_note_ru = adj_result.note_ru

        score_result = self.scorer.score(
            canonical,
            profile,
            signals=signals,
            filter_result=filter_result,
            feedback_adjustment=feedback_adjustment,
            search_mode=search_mode,
        )
        score_result = _apply_explicit_feedback_to_score(score_result, explicit_feedback_label)
        bucket = _assign_bucket(filter_result=filter_result, score=score_result.score)
        bucket = _apply_explicit_feedback_to_bucket(bucket, explicit_feedback_label)
        relevance_band = _compute_relevance_band(bucket)
        translated_title_ru = self.translation_service.translate_title(
            normalized_title=canonical.normalized_title,
            original_title=primary_record.original_title,
            use_llm=enrich_with_llm,
        )
        summary_ru = self.summary_service.build_summary(
            canonical,
            signals,
            translated_title_ru=translated_title_ru,
            use_llm=enrich_with_llm,
        )
        feedback_note_ru = _explicit_feedback_note(explicit_feedback_label) or feedback_note_ru
        explanation_ru = self.match_explainer.explain_with_feedback_note(
            filter_result=filter_result,
            score_result=score_result,
            relevance_band=relevance_band,
            feedback_note_ru=feedback_note_ru,
        )
        if explicit_feedback_label == "irrelevant":
            explanation_ru = "Скорее не подходит: вы уже отметили эту вакансию как нерелевантную."

        return SearchResultItem(
            canonical_group=canonical,
            primary_record=primary_record,
            signals=signals,
            filter_result=filter_result,
            score_result=score_result,
            bucket=bucket,
            explanation_ru=explanation_ru,
            summary_ru=summary_ru,
            translated_title_ru=translated_title_ru,
            relevance_band=relevance_band,
            role_family=role_family_str,
            search_query=search_query,
        )

    def _build_hidden_filtered_item(
        self,
        *,
        canonical: CanonicalVacancyGroup,
        profile: SearchProfileContext,
        search_mode: str | None = None,
    ) -> HiddenFilteredItem | None:
        primary_record = _pick_primary_record(canonical)
        signals = inspect_vacancy(canonical, profile)
        filter_result = self.filter_engine.evaluate(
            canonical, profile, signals=signals, search_mode=search_mode
        )
        if not filter_result.hard_reject:
            return None

        return HiddenFilteredItem(
            canonical_key=canonical.canonical_key,
            title=primary_record.original_title,
            company_name=primary_record.original_company or canonical.company_name,
            location_text=primary_record.original_location or canonical.location_text,
            source_name=primary_record.source_name,
            original_url=_pick_any_usable_source_url(canonical),
            rejection_reasons=filter_result.rejection_hits,
        )

    def _build_source_states(
        self,
        *,
        successful_responses: tuple[AdapterSearchResponse, ...],
        failed_states: tuple[SearchSourceState, ...],
        normalized_records: tuple[NormalizedVacancyRecord, ...],
        canonical_groups: tuple[CanonicalVacancyGroup, ...],
    ) -> tuple[SearchSourceState, ...]:
        normalized_counts = Counter(record.source_id for record in normalized_records)
        canonical_counts: Counter[str] = Counter()
        for canonical in canonical_groups:
            seen_source_ids = {record.source_id for record in canonical.source_records}
            for source_id in seen_source_ids:
                canonical_counts[source_id] += 1

        states = list(failed_states)
        for response in successful_responses:
            raw_count = len(response.records)
            if raw_count > 0:
                status_label = "Успех"
                status_kind: SourceStatusKind = "success"
            else:
                status_label = "Нет результатов"
                status_kind = "warning"
            states.append(
                SearchSourceState(
                    source_id=response.source_id,
                    source_name=response.source_name,
                    status_label=status_label,
                    status_kind=status_kind,
                    raw_count=raw_count,
                    total_count=response.total_count,
                    normalized_count=normalized_counts.get(response.source_id, 0),
                    canonical_count=canonical_counts.get(response.source_id, 0),
                    warnings=response.warnings,
                )
            )
            logger.info(
                "source_adapter_postprocess source_id=%s source_name=%r status=%s "
                "raw=%d total=%s normalized=%d deduped=%d warnings=%d",
                response.source_id,
                response.source_name,
                status_label,
                raw_count,
                response.total_count,
                normalized_counts.get(response.source_id, 0),
                canonical_counts.get(response.source_id, 0),
                len(response.warnings),
            )

        return tuple(sorted(states, key=lambda state: (state.error_message is not None, state.source_id)))


def _log_attempt_result(
    record: SearchAttemptRecord,
    source_ids: tuple[str, ...],
    profile_id: int | None,
) -> None:
    """Log structured result of a single search attempt with the full observable payload."""
    logger.info(
        "search_orchestrator_attempt_result "
        "attempt_number=%d stage=%s primary_query=%r query_used=%r "
        "lang_relax=%s source_ids=%s profile_id=%s "
        "raw=%d normalized=%d deduped=%d hot=%d review=%d rejected=%d non_rejected=%d "
        "rejection_reasons=%s reason=%s",
        record.attempt_number,
        record.stage_name,
        record.primary_query,
        record.query_used,
        record.language_relaxation_applied,
        source_ids,
        profile_id,
        record.raw_count,
        record.normalized_count,
        record.deduped_count,
        record.hot_count,
        record.review_count,
        record.rejected_count,
        record.non_rejected_count,
        record.rejection_reason_counts,
        record.reason_continued,
    )


def _resolve_profile_search_terms(
    profile: SearchProfileContext,
    *,
    source_ids: Sequence[str] = (),
) -> tuple[str, ...]:
    preserve_raw_terms = _uses_only_russian_language_sources(source_ids)
    if profile.search_query_terms:
        return _normalize_profile_terms_for_sources(
            profile.search_query_terms,
            preserve_raw_terms=preserve_raw_terms,
        )
    if profile.profile_source == "saved" and len(profile.desired_roles) > 1:
        return _normalize_profile_terms_for_sources(
            profile.desired_roles,
            preserve_raw_terms=preserve_raw_terms,
        )
    return ()


def _uses_only_russian_language_sources(source_ids: Sequence[str]) -> bool:
    normalized_ids = tuple(source_id for source_id in source_ids if source_id)
    return bool(normalized_ids) and all(source_id in _RUSSIAN_LANGUAGE_SOURCE_IDS for source_id in normalized_ids)


def _normalize_profile_terms_for_sources(
    terms: Sequence[str],
    *,
    preserve_raw_terms: bool,
) -> tuple[str, ...]:
    normalized_terms: list[str] = []
    for raw_term in terms:
        term = raw_term.strip()
        if not term:
            continue
        normalized_terms.append(_normalize_profile_search_term(term, preserve_raw_terms=preserve_raw_terms))
    return tuple(dict.fromkeys(normalized_terms))


def _normalize_profile_search_term(term: str, *, preserve_raw_terms: bool) -> str:
    if preserve_raw_terms or _CYRILLIC_RE.search(term) is None:
        return term
    intent = normalize_role_intent(term)
    if intent is None:
        return term
    return intent.primary_de


def _summarize_raw_payload(raw_payload: object) -> str:
    if isinstance(raw_payload, dict):
        parts = []
        for key, value in raw_payload.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                parts.append(f"{key}={value!r}")
            elif isinstance(value, (list, tuple, set)):
                parts.append(f"{key}=<{type(value).__name__} len={len(value)}>")
            elif isinstance(value, dict):
                parts.append(f"{key}=<dict keys={tuple(value)[:8]!r}>")
            else:
                parts.append(f"{key}=<{type(value).__name__}>")
        return "{" + ", ".join(parts) + "}"
    if isinstance(raw_payload, (list, tuple, set)):
        return f"<{type(raw_payload).__name__} len={len(raw_payload)}>"
    return repr(raw_payload)


def _rejection_reason_counts(result: SearchRunResult) -> tuple[tuple[str, int], ...]:
    counter: Counter[str] = Counter()
    for item in result.hidden_filtered_items:
        for reason in item.rejection_reasons:
            counter[reason.code] += 1
    for item in result.rejected_results:
        if item.filter_result.rejection_hits:
            for reason in item.filter_result.rejection_hits:
                counter[reason.code] += 1
        else:
            counter["low_score"] += 1
    return tuple(sorted(counter.items()))


def _merge_search_results_for_worldwide(results: list[SearchRunResult]) -> SearchRunResult:
    """Merge all worldwide search attempts instead of picking a single winning query."""
    if not results:
        raise ValueError("Cannot merge an empty worldwide search result list.")

    result_by_key: dict[str, SearchResultItem] = {}
    for result in results:
        for item in result.results:
            key = _result_item_key(item)
            current = result_by_key.get(key)
            if current is None or _merge_result_sort_key(item) < _merge_result_sort_key(current):
                result_by_key[key] = item

    hidden_by_key: dict[str, HiddenFilteredItem] = {}
    for result in results:
        for item in result.hidden_filtered_items:
            hidden_by_key.setdefault(item.canonical_key, item)

    preview_by_key: dict[str, DedupPreviewItem] = {}
    for result in results:
        for item in result.deduped_preview_items:
            preview_by_key.setdefault(item.canonical_key, item)

    ordered_results = tuple(sorted(result_by_key.values(), key=_merge_result_sort_key))
    query_groups = tuple(
        group
        for result in results
        for group in (
            result.query_result_groups
            or (
                SearchQueryResultGroup(
                    query=(result.results[0].search_query if result.results and result.results[0].search_query else ""),
                    hot_results=result.hot_results,
                    maybe_results=result.maybe_results,
                ),
            )
        )
        if group.query
    )
    return SearchRunResult(
        profile=results[0].profile,
        source_states=_merge_source_states_for_worldwide(results),
        results=ordered_results,
        hot_results=tuple(item for item in ordered_results if item.bucket == "hot"),
        maybe_results=tuple(item for item in ordered_results if item.bucket == "maybe"),
        rejected_results=tuple(item for item in ordered_results if item.bucket == "rejected"),
        query_result_groups=query_groups,
        deduped_preview_items=tuple(preview_by_key.values()),
        hidden_filtered_items=tuple(hidden_by_key.values()),
        total_raw_records=sum(result.total_raw_records for result in results),
        total_normalized_records=sum(result.total_normalized_records for result in results),
        total_canonical_results=len(preview_by_key),
    )


def _merge_source_states_for_worldwide(results: list[SearchRunResult]) -> tuple[SearchSourceState, ...]:
    grouped: dict[str, list[SearchSourceState]] = {}
    for result in results:
        for state in result.source_states:
            grouped.setdefault(state.source_id, []).append(state)

    merged: list[SearchSourceState] = []
    for source_id, states in grouped.items():
        raw_count = sum(state.raw_count for state in states)
        normalized_count = sum(state.normalized_count for state in states)
        canonical_count = sum(state.canonical_count for state in states)
        total_values = [state.total_count for state in states if state.total_count is not None]
        warnings = tuple(dict.fromkeys(warning for state in states for warning in state.warnings))
        errors = tuple(dict.fromkeys(state.error_message for state in states if state.error_message))

        if raw_count > 0:
            status_kind: SourceStatusKind = "success"
            status_label = "Успех"
            error_message = None
        elif errors:
            status_kind = "error"
            status_label = "Ошибка"
            error_message = "; ".join(errors)
        else:
            status_kind = "warning"
            status_label = "Нет результатов"
            error_message = None

        merged.append(
            SearchSourceState(
                source_id=source_id,
                source_name=states[0].source_name,
                status_label=status_label,
                status_kind=status_kind,
                raw_count=raw_count,
                total_count=sum(total_values) if total_values else None,
                normalized_count=normalized_count,
                canonical_count=canonical_count,
                warnings=warnings,
                error_message=error_message,
            )
        )

    return tuple(sorted(merged, key=lambda state: (state.error_message is not None, state.source_id)))


def _result_item_key(item: SearchResultItem) -> str:
    key = getattr(getattr(item, "canonical_group", None), "canonical_key", None)
    if isinstance(key, str) and key:
        return key
    return f"result-object:{id(item)}"


def _merge_result_sort_key(item: SearchResultItem) -> tuple[int, int, int, str]:
    bucket = getattr(item, "bucket", "rejected")
    bucket_rank = _BUCKET_PRIORITY.get(bucket, _BUCKET_PRIORITY["rejected"])
    score = getattr(getattr(item, "score_result", None), "score", 0)
    if not isinstance(score, int):
        score = 0
    posted_date = getattr(getattr(item, "canonical_group", None), "posted_date", None)
    try:
        posted_rank = posted_date.toordinal() if posted_date is not None and hasattr(posted_date, "toordinal") else 0
    except Exception:
        posted_rank = 0
    if not isinstance(posted_rank, int):
        posted_rank = 0
    return (bucket_rank, -score, -posted_rank, _result_item_key(item))


def _pick_primary_record(canonical: CanonicalVacancyGroup) -> NormalizedVacancyRecord:
    return min(
        canonical.source_records,
        key=lambda record: (
            not _has_usable_source_url(record.source_url),
            -(len(record.body_text or "")),
            record.source_id,
            record.external_id,
        ),
    )


def _has_usable_source_url(source_url: str | None) -> bool:
    return bool(source_url and source_url.strip())

def _pick_any_usable_source_url(canonical: CanonicalVacancyGroup) -> str | None:
    primary_record = _pick_primary_record(canonical)
    if _has_usable_source_url(primary_record.source_url):
        return primary_record.source_url.strip()

    for record in canonical.source_records:
        if _has_usable_source_url(record.source_url):
            return record.source_url.strip()

    return None


def _build_dedup_preview_item(canonical: CanonicalVacancyGroup) -> DedupPreviewItem:
    primary_record = _pick_primary_record(canonical)
    return DedupPreviewItem(
        canonical_key=canonical.canonical_key,
        title=primary_record.original_title,
        company_name=primary_record.original_company or canonical.company_name,
        location_text=primary_record.original_location or canonical.location_text,
        source_name=primary_record.source_name,
        source_count=len(canonical.source_records),
        original_url=_pick_any_usable_source_url(canonical),
    )

def _compute_relevance_band(bucket: SearchBucket) -> RelevanceBand:
    if bucket == "hot":
        return "high"
    if bucket == "maybe":
        return "medium"
    return "low"


def _assign_bucket(*, filter_result: FilterResult, score: int) -> SearchBucket:
    if filter_result.hard_reject:
        return "rejected"
    if filter_result.review_required:
        return "maybe"
    if score >= HOT_BUCKET_MIN_SCORE:
        return "hot"
    if score >= MAYBE_BUCKET_MIN_SCORE:
        return "maybe"
    return "rejected"


def _early_stop_thresholds(search_mode: str | None) -> tuple[int, int]:
    return ENOUGH_NON_REJECTED, ENOUGH_HOT


def _apply_explicit_feedback_to_score(
    score_result: ScoreResult,
    feedback_label: FeedbackLabel | None,
) -> ScoreResult:
    if feedback_label == "weak":
        return _cap_score_with_negative_hit(
            score_result,
            score_cap=_EXPLICIT_WEAK_SCORE_CAP,
            code="explicit_feedback_weak",
            label_ru="вы отметили эту вакансию как слабое совпадение",
        )
    if feedback_label == "irrelevant":
        return _cap_score_with_negative_hit(
            score_result,
            score_cap=_EXPLICIT_IRRELEVANT_SCORE_CAP,
            code="explicit_feedback_irrelevant",
            label_ru="вы отметили эту вакансию как нерелевантную",
        )
    return score_result


def _cap_score_with_negative_hit(
    score_result: ScoreResult,
    *,
    score_cap: int,
    code: str,
    label_ru: str,
) -> ScoreResult:
    capped_score = min(score_result.score, score_cap)
    hit = RuleHit(
        code=code,
        label_ru=label_ru,
        weight=capped_score - score_result.score,
    )
    return ScoreResult(
        score=capped_score,
        positive_hits=score_result.positive_hits,
        negative_hits=_append_hit(score_result.negative_hits, hit),
    )


def _append_hit(hits: tuple[RuleHit, ...], hit: RuleHit) -> tuple[RuleHit, ...]:
    if any(existing.code == hit.code for existing in hits):
        return hits
    return (*hits, hit)


def _apply_explicit_feedback_to_bucket(
    bucket: SearchBucket,
    feedback_label: FeedbackLabel | None,
) -> SearchBucket:
    if feedback_label == "irrelevant":
        return "rejected"
    if feedback_label == "weak" and bucket == "hot":
        return "maybe"
    return bucket


def _explicit_feedback_note(feedback_label: FeedbackLabel | None) -> str | None:
    if feedback_label == "weak":
        return "Вы уже отметили эту вакансию как слабое совпадение."
    if feedback_label == "irrelevant":
        return "Вы уже отметили эту вакансию как нерелевантную."
    return None


def _build_attempt_user_message(
    *,
    primary_query: str,
    winning_query: str,
    fallback_used: bool,
    language_relaxation_used: bool,
    best_result: SearchRunResult,
) -> str | None:
    if not fallback_used:
        return None
    non_rej = len(best_result.hot_results) + len(best_result.maybe_results)
    if non_rej == 0:
        msg = (
            f"По запросу «{primary_query}» и смежным ключевым словам вакансий не найдено. "
            "Попробуйте изменить запрос или локацию."
        )
    else:
        msg = (
            f"По точному запросу «{primary_query}» не нашлось достаточно результатов. "
            f"Система расширила поиск на близкие ключевые слова «{winning_query}» — "
            "показаны наиболее близкие найденные варианты."
        )
    if language_relaxation_used:
        msg += (
            " Также поиск был смещён в сторону вакансий с низким языковым барьером, "
            "потому что в профиле не указано уверенное знание немецкого или английского."
        )
    return msg


def _result_sort_key(result: SearchResultItem) -> tuple[int, int, int, str]:
    posted_date = result.canonical_group.posted_date
    posted_rank = posted_date.toordinal() if posted_date is not None else 0
    return (
        _BUCKET_PRIORITY[result.bucket],
        -result.score_result.score,
        -posted_rank,
        result.canonical_group.canonical_key,
    )
