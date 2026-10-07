from __future__ import annotations

import dataclasses
import re
import threading
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from app.core.config import Settings
from app.core.logging import logger
from app.services.ai_tools_profile import AI_TOOLS_WESTERN_QUERY_TRANSLATIONS
from app.services.employer_boilerplate import strip_employer_boilerplate
from app.services.filter_engine import FilterEngine, is_b_only_driving_profile
from app.services.geo_distance import place_weight
from app.services.hashers import normalize_text_for_fingerprint
from app.services.match_explainer import MatchExplainer
from app.services.normalization_models import CanonicalVacancyGroup, NormalizedVacancyRecord
from app.services.relevance_memory_service import FeedbackLabel, ProfileFeedbackMemory, RelevanceMemoryService
from app.services.role_family import (
    RoleFamily,
    classify_query_ru,
    classify_role_families,
    classify_vacancy_de,
    families_are_compatible,
    is_specific_family,
)
from app.services.role_intent import normalize_role_intent
from app.services.rule_catalog import HOT_BUCKET_MIN_SCORE, MAYBE_BUCKET_MIN_SCORE, inspect_vacancy
from app.services.scorer import IRRELEVANT_SCORE_CODES, VacancyScorer
from app.services.search_fallback import (
    ENOUGH_NON_REJECTED,
    LOW_LANGUAGE_FALLBACK,
    MAX_LLM_STAGES,
    VEHICLE_QUERY_GROUPS,
    get_fallback_keywords,
    get_intent_fallback_keywords,
    get_light_transport_query_representatives,
    get_profile_fallback_keywords,
    get_vehicle_query_representatives,
    is_low_language_profile,
    vehicle_query_family,
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
    SearchCityResultGroup,
    SearchProfileContext,
    SearchQueryResultGroup,
    SearchResultItem,
    SearchRunResult,
    SearchSourceState,
    SourceStatusKind,
    VacancySignalSnapshot,
)
from app.services.search_normalizer import is_german_city_name, parse_search_cities
from app.services.search_profile_resolver import DatabaseSearchProfileResolver
from app.services.source_adapters.base import SourceAdapter
from app.services.source_adapters.errors import SourceAdapterError
from app.services.source_adapters.models import (
    AdapterSearchResponse,
    SourceAdapterDescriptor,
    SourceRecordPreview,
    SourceSearchInput,
)
from app.services.source_adapters.registry import SourceAdapterRegistry
from app.services.source_merge import SourceMergeService
from app.services.summary_service import SummaryService
from app.services.translation_service import TranslationService
from app.services.vacancy_processing import VacancyProcessingService

if TYPE_CHECKING:
    from app.services.llm_client import LLMClient

_BUCKET_PRIORITY: dict[SearchBucket, int] = {"hot": 0, "maybe": 1, "rejected": 2}
_EXPLICIT_WEAK_SCORE_CAP = HOT_BUCKET_MIN_SCORE - 1
_EXPLICIT_IRRELEVANT_SCORE_CAP = MAYBE_BUCKET_MIN_SCORE - 1
_RUSSIAN_LANGUAGE_SOURCE_IDS = frozenset({"hh", "dou_rss", "djinni_rss"})
_DUAL_MODE_SOURCE_IDS = frozenset({"greenhouse", "jooble", "lever"})
# Arbeitnow's feed is office/IT jobs from company ATS boards: in the source audit it gave
# 0 visible results across all germany_local profiles (17 of 20 calls empty), so it only
# runs for remote searches.
_REMOTE_ONLY_SOURCE_IDS = frozenset({"arbeitnow"})
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]")
# Upper bound on concurrent source fetches per search attempt (I/O-bound network calls).
_MAX_FETCH_WORKERS = 8
# Upper bound on concurrent LLM enrichment calls for the final displayed results.
_MAX_ENRICH_WORKERS = 8
# Buckets whose items are rendered as cards (and therefore worth LLM enrichment).
_DISPLAYED_BUCKETS: frozenset[SearchBucket] = frozenset({"hot", "maybe"})


class _FetchBudget:
    """Per-run request bound and circuit breaker, shared by fallback attempts."""

    def __init__(self, *, vehicle_diversity: bool = False, diversity_queries: tuple[str, ...] = ()) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._counts: Counter[str] = Counter()
        self._blocked: set[str] = set()
        self.vehicle_diversity = vehicle_diversity
        self._vehicle_families: set[str] = set()
        self._empty_arbeitnow_queries: set[str] = set()
        self._required_queries = frozenset(normalize_text_for_fingerprint(q) for q in diversity_queries)

    def record_response(self, source_id: str, query: str, *, empty: bool) -> None:
        if not self.vehicle_diversity:
            return
        with self._lock:
            family = normalize_text_for_fingerprint(query) if self._required_queries else vehicle_query_family(query)
            if family is not None:
                self._vehicle_families.add(family)
            if source_id == "arbeitnow":
                if empty:
                    self._empty_arbeitnow_queries.add(query.casefold())
                    if len(self._empty_arbeitnow_queries) >= 2:
                        self._blocked.add(source_id)
                else:
                    self._empty_arbeitnow_queries.clear()

    def diversity_ready(self) -> bool:
        with self._lock:
            if self._required_queries:
                return self._required_queries <= self._vehicle_families
            return not self.vehicle_diversity or len(self._vehicle_families) >= len(VEHICLE_QUERY_GROUPS)

    def consume(self, source_id: str) -> bool:
        with self._lock:
            if source_id in self._blocked or self._total >= 48 or self._counts[source_id] >= 24:
                return False
            self._total += 1
            self._counts[source_id] += 1
            return True

    def block(self, source_id: str) -> None:
        with self._lock:
            self._blocked.add(source_id)

    def has_capacity(self, source_ids: Sequence[str]) -> bool:
        with self._lock:
            return self._total < 48 and any(
                source_id not in self._blocked and self._counts[source_id] < 24
                for source_id in source_ids
            )


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
        settings: Settings | None = None,
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
        # Глубина выдачи источника. Читается из настроек, чтобы менять её можно было
        # не правя код; max_pages=1 полностью сохраняет прежнее поведение.
        self._max_pages = max(1, (settings or Settings()).search_max_pages)

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
        served_requests: set[str] | None = None,
        fetch_budget: _FetchBudget | None = None,
    ) -> SearchRunResult:
        resolved_profile = _with_run_geography(
            profile or self.get_profile_context(profile_id=profile_id), search_input
        )
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
        if served_requests is not None:
            active_source_ids, repeated_source_ids = self._drop_already_served_sources(
                active_source_ids, search_input, served_requests
            )
        else:
            repeated_source_ids = ()
        fetch_budget = fetch_budget or _FetchBudget()
        outcomes: dict[str, tuple[AdapterSearchResponse | None, SearchSourceState | None]] = {}
        if active_source_ids and fetch_budget.vehicle_diversity:
            # Stable priority matters when the last few budget slots are contested.
            priority = {"ba": 0, "adzuna": 1, "careerjet": 2}
            for source_id in sorted(active_source_ids, key=lambda sid: priority.get(sid, 3)):
                if stop_event is not None and stop_event.is_set():
                    break
                if fetch_budget.has_capacity((source_id,)):
                    outcomes[source_id] = self._fetch_source(source_id, search_input, stop_event, fetch_budget)
        elif active_source_ids:
            max_workers = min(len(active_source_ids), _MAX_FETCH_WORKERS)
            running_total = 0
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="src-fetch") as pool:
                future_to_id = {
                    pool.submit(self._fetch_source, source_id, search_input, stop_event, fetch_budget): source_id
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

        if not successful_responses and not failed_states and repeated_source_ids:
            # Каждый источник этой попытки уже отвечал на такой же запрос: его записи
            # лежат в общем котле с прошлой попытки. Пустое состояние источников —
            # чтобы попытка ничего не добавила и ничего не испортила при слиянии.
            return SearchRunResult(
                profile=resolved_profile,
                source_states=(),
                results=(),
                query_result_groups=(SearchQueryResultGroup(query=search_input.query),),
            )

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

        # Сигналы и решение фильтра считаются ОДИН раз на вакансию. Раньше обе
        # операции выполнялись дважды: сначала при отборе жёстко скрытых, потом
        # заново при построении карточки. inspect_vacancy прогоняет свыше сотни
        # регулярок по всему тексту объявления — это самый дорогой участок прогона,
        # и его удвоение ничего не давало.
        hidden_items: list[HiddenFilteredItem] = []
        results_list: list[SearchResultItem] = []
        # Шаблонные абзацы работодателя вырезаются после дедупликации и до
        # анализа: признаки должны опираться на текст конкретной вакансии.
        for canonical in strip_employer_boilerplate(processed.canonical_groups):
            signals = inspect_vacancy(canonical, resolved_profile)
            filter_result = self.filter_engine.evaluate(
                canonical, resolved_profile, signals=signals, search_mode=search_input.search_mode
            )
            if filter_result.hard_reject:
                hidden_items.append(
                    self._build_hidden_filtered_item(canonical=canonical, filter_result=filter_result)
                )
                continue
            # None здесь означал бы жёсткое отклонение, а его мы уже отсеяли выше;
            # проверка оставлена, чтобы у списка был точный тип без приведений.
            item = self._build_result_item(
                canonical=canonical,
                profile=resolved_profile,
                signals=signals,
                filter_result=filter_result,
                feedback_memory=feedback_memory,
                search_query=search_input.query,
                search_mode=search_input.search_mode,
                enrich_with_llm=enrich_with_llm,
            )
            if item is not None:
                results_list.append(item)

        hidden_filtered_items = tuple(hidden_items)
        ordered_results = tuple(sorted(results_list, key=_result_sort_key))

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
            normalized_records=processed.normalized_records,
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
        """Run explicit terms and optional expansion; merge results with attempt metadata."""
        # Города заказа: поле формы читается один раз, дальше и веер, и фильтр,
        # и разбивка выдачи опираются на один и тот же разобранный список.
        search_cities = parse_search_cities(search_input.location)
        resolved_profile = dataclasses.replace(
            _with_run_geography(self.get_profile_context(profile_id=profile_id), search_input),
            search_cities=search_cities,
        )
        resolved_source_ids = self._resolve_source_ids(source_ids)
        primary_query = search_input.query
        primary_role = resolved_profile.desired_roles[0] if resolved_profile.desired_roles else None
        low_language = is_low_language_profile(
            german_level=resolved_profile.german_level,
            english_level=resolved_profile.english_level,
        )
        # Профиль с категорией B: тяжёлые ключевики отсекаем ДО сети — тот же самый признак
        # используется жёстким фильтром, который иначе отклонит эти вакансии после скачивания.
        light_vehicle_only = is_b_only_driving_profile(resolved_profile)

        profile_terms = _resolve_profile_search_terms(
            resolved_profile,
            source_ids=resolved_source_ids,
        )
        if profile_terms:
            # What the user typed is an explicit instruction and runs FIRST; the profile's
            # own search plan then runs in full as the following stages. When the field was
            # prefilled from the profile both agree and dedup collapses them, so the plan is
            # unchanged — the typed query only ever adds a stage, it never drops one.
            effective_primary_query = _effective_query_for_profile_run(
                submitted_query=primary_query,
                profile_terms=profile_terms,
                preserve_raw_terms=(
                    _uses_only_russian_language_sources(resolved_source_ids)
                    # Worldwide-источники (Remotive, RemoteJobs, Arbeitnow) — англоязычные:
                    # немецкий перевод там обнуляет выдачу, см. _effective_query_for_profile_run.
                    or search_input.search_mode == "remote_worldwide"
                ),
            )
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
                profile_terms=(effective_primary_query, *profile_terms),
                family=query_family,
                role_primary_de=primary_intent.canonical_keyword if primary_intent is not None else "",
                broaden=(
                    is_specific_family(query_family)
                    and not _uses_only_russian_language_sources(resolved_source_ids)
                ),
                light_vehicle_only=light_vehicle_only,
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
                    light_vehicle_only=light_vehicle_only,
                )
            else:
                effective_primary_query = primary_query
                query_family = classify_query_ru(primary_query)
                fallback_keywords = get_fallback_keywords(
                    primary_query=primary_query,
                    role=primary_role,
                    low_language=low_language,
                    light_vehicle_only=light_vehicle_only,
                )

        # Общая на весь прогон память о том, какие запросы источники уже обслужили.
        # Разные ключевые слова часто сводятся источником к одному запросу (у Djinni
        # 16 терминов AI-профиля дают одну пару рубрик), и без этой памяти конвейер
        # разбирал бы один и тот же фид на каждой попытке.
        served_requests: set[str] = set()
        light_transport = (
            query_family is RoleFamily.LIGHT_GOODS_TRANSPORT
            or classify_role_families(resolved_profile.desired_roles) == frozenset({RoleFamily.LIGHT_GOODS_TRANSPORT})
        )
        vehicle_diversity = (
            (query_family is RoleFamily.VEHICLE_LOGISTICS or light_transport)
            and not _uses_only_russian_language_sources(resolved_source_ids)
        )
        vehicle_locations: Iterator[str | None] = iter(())
        vehicle_min_legs = 0
        if vehicle_diversity:
            queries = (
                get_light_transport_query_representatives(effective_primary_query) if light_transport
                else get_vehicle_query_representatives(effective_primary_query, profile_terms)
            )
            effective_primary_query = queries[0]
            cities = search_cities or (search_input.location,)
            # The largest requested city (GeoNames postal-code count, form order on ties)
            # gets every query family first; the smaller cities share what budget remains.
            anchor = max(cities, key=place_weight)
            secondary_queries = get_light_transport_query_representatives("Sprinterfahrer") if light_transport else queries
            legs = tuple((query, anchor) for query in queries) + tuple(
                (query, city) for query in secondary_queries for city in cities if city != anchor
            )
            # Cover the anchor, then one transfer query or two light-goods queries per other city.
            # The shared call budget remains the upper bound for unusually large city lists.
            vehicle_min_legs = len(queries) + (2 if light_transport else 1) * (len(cities) - 1)
            fallback_keywords = tuple(query for query, _ in legs[1:])
            vehicle_locations = iter(city for _, city in legs)
        fetch_budget = _FetchBudget(
            vehicle_diversity=vehicle_diversity,
            diversity_queries=queries if vehicle_diversity and light_transport else (),
        )

        def _merge_attempts(results: list[SearchRunResult]) -> SearchRunResult:
            return self._merge_and_rescore_attempts(
                results, profile=resolved_profile, search_mode=search_input.search_mode,
                feedback_memory=feedback_memory,
            )

        def _run_one(query: str, location: str | None) -> SearchRunResult:
            # Attempts are scored deterministically (no LLM). LLM enrichment is applied once,
            # at the end, to the merged displayed results only — see _enrich_run_result.
            return self.search(
                search_input=dataclasses.replace(
                    search_input,
                    query=query,
                    location=location,
                    # Радиус прогона идёт в источник как есть. Если радиус не задан
                    # вовсе, а город назван — спрашиваем строго этот город: ноль
                    # понимает BA (umkreis=0), остальные просто не получают радиуса,
                    # и город проверяется уже на нашей стороне.
                    radius_km=_leg_radius_km(search_input.radius_km, location),
                ),
                source_ids=resolved_source_ids,
                profile=resolved_profile,
                progress_callback=progress_callback,
                stop_event=stop_event,
                feedback_memory=feedback_memory,
                enrich_with_llm=False,
                served_requests=served_requests,
                fetch_budget=fetch_budget,
            )

        def _run(query: str) -> SearchRunResult:
            if progress_callback is not None:
                progress_callback("attempt", 0, None, query)
            if vehicle_diversity:
                city = next(vehicle_locations)
                result = _run_one(query, city)
                return _tag_results_with_city(result, city) if city else result
            if not search_cities:
                return _run_one(query, search_input.location)

            # Города спрашиваются по одному: job-API понимают одно место в поле,
            # а строку "Berlin, Rostock" BA молча сводит к Берлину, Careerjet —
            # к поиску по всей стране. Порядок запросов — порядок из формы.
            city_results: list[SearchRunResult] = []
            for city in search_cities:
                if stop_event is not None and stop_event.is_set():
                    break
                city_results.append(_tag_results_with_city(_run_one(query, city), city))
            if not city_results:
                return _run_one(query, search_input.location)
            return (
                _merge_attempts(city_results)
                if len(city_results) > 1
                else city_results[0]
            )

        def _non_rejected(r: SearchRunResult) -> int:
            return len(r.hot_results) + len(r.maybe_results)

        def _is_enough(r: SearchRunResult) -> bool:
            if len(attempt_results) < vehicle_min_legs:
                return False
            return fetch_budget.diversity_ready() and _sufficient_canonical_count(r, resolved_profile) >= ENOUGH_NON_REJECTED

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
                _merge_attempts(attempt_results)
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
        accumulated = r0
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

        explicit_keywords = set() if vehicle_diversity else {term.strip().casefold() for term in profile_terms}

        best_result: SearchRunResult = r0
        # winning_query tracks the query that produced best_result — updated only when best_result is updated
        winning_query = effective_primary_query
        winning_lang_relax = False

        # --- Deterministic fallback stages ---
        deterministic_keywords = fallback_keywords
        for idx, kw in enumerate(deterministic_keywords, start=1):
            if stop_event is not None and stop_event.is_set():
                break
            # The user's complete plan runs first. Only generated expansion is
            # optional, and sufficiency uses the accumulated deduplicated evidence.
            if kw.strip().casefold() not in explicit_keywords and _is_enough(accumulated):
                break
            if not fetch_budget.has_capacity(resolved_source_ids):
                break
            attempt_records[-1] = dataclasses.replace(
                attempt_records[-1], reason_continued=(
                    "Проверяются разные семейства запросов и города автомобильной логистики."
                    if vehicle_diversity
                    else "Выполняются явные поисковые термины профиля."
                    if kw.strip().casefold() in explicit_keywords
                    else "Недостаточно уникальных подходящих вакансий; автоматическое расширение."
                ),
            )
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
            accumulated = _merge_attempts(attempt_results)
            rec = _record(
                r, stage, kw, lang_relax,
                None,
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

        # --- LLM-assisted stage (last resort only) ---
        if (
            not _is_enough(accumulated)
            and not vehicle_diversity
            and not (stop_event is not None and stop_event.is_set())
            and fetch_budget.has_capacity(resolved_source_ids)
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
                attempt_records[-1] = dataclasses.replace(
                    attempt_records[-1],
                    reason_continued="Недостаточно уникальных подходящих вакансий; последний этап расширения.",
                )
                lang_relax = low_language
                if lang_relax:
                    language_relaxation_used = True
                logger.info(
                    "search_orchestrator stage=llm_1 query=%r location=%r lang_relax=%s source_ids=%s profile_id=%s",
                    llm_kw, search_input.location, lang_relax, resolved_source_ids, profile_id,
                )
                r = _run(llm_kw)
                attempt_results.append(r)
                accumulated = _merge_attempts(attempt_results)
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
        result_for_summary = accumulated
        # Enrich once: only the merged displayed (hot+maybe) results get LLM
        # translation/summaries. Everything above ran deterministically for speed.
        result_for_summary = self._enrich_run_result(result_for_summary)
        executed_queries = {attempt.query_used.strip().casefold() for attempt in attempt_records}
        query_label = (
            "all profile queries" if profile_terms and not vehicle_diversity and explicit_keywords <= executed_queries
            else "executed queries"
        )
        stop_reason = (
            "cancelled" if stop_event is not None and stop_event.is_set()
            else "sufficient_canonical_results" if _is_enough(accumulated) and explicit_keywords <= executed_queries
            else "source_budget_or_blocked" if not fetch_budget.has_capacity(resolved_source_ids)
            else "queries_exhausted"
        )
        attempt_records[-1] = dataclasses.replace(attempt_records[-1], reason_continued=None)
        summary = SearchAttemptSummary(
            primary_query=primary_query,
            final_query_used=(
                query_label
                if multiple_attempts
                else winning_query
            ),
            fallback_used=fallback_used,
            language_relaxation_used=language_relaxation_used,
            attempts=tuple(attempt_records),
            stop_reason=stop_reason,
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
        return dataclasses.replace(
            result_for_summary,
            attempt_summary=summary,
            city_result_groups=_build_city_result_groups(result_for_summary, search_cities),
            unresolved_cities=tuple(city for city in search_cities if not is_german_city_name(city)),
        )

    def _merge_and_rescore_attempts(
        self,
        results: list[SearchRunResult],
        *,
        profile: SearchProfileContext,
        search_mode: str | None,
        feedback_memory: ProfileFeedbackMemory | None = None,
    ) -> SearchRunResult:
        """Deduplicate source evidence across queries/cities before scoring it once."""
        merged = _merge_search_results_for_worldwide(results)
        if not all(result.normalized_records or result.total_normalized_records == 0 for result in results):
            # Legacy callers may provide scored results without source evidence.
            return merged
        # Additional terms were explicitly selected too; rescoring must preserve
        # their intent after the individual query context is no longer available.
        profile = dataclasses.replace(profile, run_query_terms=tuple(dict.fromkeys(
            (*profile.run_query_terms, *profile.additional_search_terms)
        )))
        records_by_key: dict[tuple[str, str], NormalizedVacancyRecord] = {}
        for result in results:
            for record in result.normalized_records:
                # Different snippets of one source ID can expose different mandatory
                # requirements. Keep both versions; length alone cannot discard evidence.
                key = (record.source_record_key, record.content_fingerprint)
                current = records_by_key.get(key)
                if current is None or (record.description_complete is True, len(record.body_text or "")) > (
                    current.description_complete is True, len(current.body_text or "")
                ):
                    records_by_key[key] = record
        records = tuple(records_by_key.values())
        groups = strip_employer_boilerplate(SourceMergeService().merge_records(records).canonical_groups)
        items: list[SearchResultItem] = []
        hidden: list[HiddenFilteredItem] = []
        for group in groups:
            signals = inspect_vacancy(group, profile)
            verdict = self.filter_engine.evaluate(group, profile, signals=signals, search_mode=search_mode)
            if verdict.hard_reject:
                hidden.append(self._build_hidden_filtered_item(canonical=group, filter_result=verdict))
                continue
            source_keys = {r.source_record_key for r in group.source_records}
            origin = next((item for result in results for item in result.results
                           if any(r.source_record_key in source_keys for r in item.canonical_group.source_records)), None)
            item = self._build_result_item(
                canonical=group, profile=profile, signals=signals, filter_result=verdict,
                feedback_memory=feedback_memory, search_mode=search_mode, enrich_with_llm=False,
                search_query=origin.search_query if origin else None,
            )
            if item is not None:
                items.append(dataclasses.replace(item, search_city=origin.search_city if origin else None))
        ordered = tuple(sorted(items, key=_result_sort_key))
        query_groups: list[SearchQueryResultGroup] = []
        for query_group in merged.query_result_groups:
            source_keys = {r.source_record_key for item in (*query_group.hot_results, *query_group.maybe_results)
                           for r in item.canonical_group.source_records}
            members = tuple(item for item in ordered
                            if any(r.source_record_key in source_keys for r in item.canonical_group.source_records))
            query_groups.append(dataclasses.replace(
                query_group, hot_results=tuple(i for i in members if i.bucket == "hot"),
                maybe_results=tuple(i for i in members if i.bucket == "maybe"),
            ))
        return dataclasses.replace(
            merged, profile=profile, normalized_records=records, results=ordered,
            hot_results=tuple(i for i in ordered if i.bucket == "hot"),
            maybe_results=tuple(i for i in ordered if i.bucket == "maybe"),
            rejected_results=tuple(i for i in ordered if i.bucket == "rejected"),
            hidden_filtered_items=tuple(hidden), total_canonical_results=len(groups),
            deduped_preview_items=tuple(_build_dedup_preview_item(g) for g in groups),
            query_result_groups=tuple(query_groups),
        )

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
                or (
                    search_mode == "remote_worldwide"
                    and (source.global_remote or source.source_id in _REMOTE_ONLY_SOURCE_IDS)
                )
                or (
                    search_mode != "remote_worldwide"
                    and not source.global_remote
                    and source.source_id not in _REMOTE_ONLY_SOURCE_IDS
                )
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

    def _drop_already_served_sources(
        self,
        source_ids: list[str],
        search_input: SourceSearchInput,
        served_requests: set[str],
    ) -> tuple[list[str], tuple[str, ...]]:
        """Отсеять источники, которые в этом прогоне уже отвечали на такой же запрос.

        Считается ДО отправки в пул потоков, последовательно в вызывающем потоке,
        поэтому общее множество не нуждается в блокировке.
        """
        keep: list[str] = []
        repeated: list[str] = []
        for source_id in source_ids:
            try:
                fingerprint = self.registry.get(source_id).request_fingerprint(search_input)
            except SourceAdapterError:
                # Источник не отдал отпечаток (например выключен) — пусть решает
                # обычный путь запроса, он умеет сообщать об ошибке.
                keep.append(source_id)
                continue
            if fingerprint is None:
                keep.append(source_id)
                continue
            if fingerprint in served_requests:
                repeated.append(source_id)
                logger.info(
                    "source_request_already_served source_id=%s query=%r fingerprint=%r",
                    source_id,
                    search_input.query,
                    fingerprint,
                )
                continue
            served_requests.add(fingerprint)
            keep.append(source_id)
        return keep, tuple(repeated)

    def _fetch_source(
        self,
        source_id: str,
        search_input: SourceSearchInput,
        stop_event: threading.Event | None = None,
        fetch_budget: _FetchBudget | None = None,
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
            response = self._fetch_source_pages(adapter, search_input, stop_event, fetch_budget)
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

    def _fetch_source_pages(
        self,
        adapter: SourceAdapter,
        search_input: SourceSearchInput,
        stop_event: threading.Event | None = None,
        fetch_budget: _FetchBudget | None = None,
    ) -> AdapterSearchResponse:
        """Continue full, useful pages within a bounded shared request budget."""
        budget = fetch_budget or _FetchBudget()
        records: list[SourceRecordPreview] = []
        seen: set[str] = set()
        warnings: list[str] = []
        first: AdapterSearchResponse | None = None
        page_limit = 1 if budget.vehicle_diversity else min(4, max(1, self._max_pages))
        for offset in range(page_limit):
            if stop_event is not None and stop_event.is_set():
                break
            if not budget.consume(adapter.source_id):
                warnings.append("Дальнейшие запросы остановлены: лимит запросов или ограничение источника.")
                break
            try:
                response = adapter.search(dataclasses.replace(search_input, page=search_input.page + offset))
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status is None and budget.vehicle_diversity:
                    status = getattr(exc.__cause__, "status_code", None)
                if status in {403, 429}:
                    budget.block(adapter.source_id)
                if first is None:
                    raise
                logger.warning("source_adapter_extra_page_failed source_id=%s page=%d",
                               adapter.source_id, search_input.page + offset)
                warnings.append("Не удалось загрузить следующую страницу; показана полученная часть выдачи.")
                break
            budget.record_response(adapter.source_id, search_input.query, empty=not response.records)
            if first is None:
                first = response
            new_records = []
            for record in response.records:
                key = f"{record.source_id}:{record.external_id}"
                if key not in seen:
                    seen.add(key)
                    new_records.append(record)
            records.extend(new_records)
            warnings.extend(response.warnings)
            if len(response.records) < search_input.page_size or not new_records:
                break
            if response.total_count is not None and response.page * response.page_size >= response.total_count:
                break
            # Do not deepen a query whose entire page is explicitly another profession.
            intent = normalize_role_intent(search_input.query)
            if intent is not None and is_specific_family(intent.family):
                families = [classify_vacancy_de(normalize_text_for_fingerprint(r.title)) for r in new_records]
                if all(is_specific_family(f) and not families_are_compatible(intent.family, f) for f in families):
                    break
            if offset + 1 == page_limit:
                warnings.append("Достигнут лимит страниц; выдача источника может быть неполной.")
        if first is None:
            return AdapterSearchResponse(
                source_id=adapter.source_id, source_name=adapter.display_name, records=(),
                total_count=None, page=search_input.page, page_size=search_input.page_size,
                raw_payload=None, warnings=tuple(warnings),
            )
        return dataclasses.replace(first, records=tuple(records), warnings=tuple(dict.fromkeys(warnings)))

    def _build_result_item(
        self,
        *,
        canonical: CanonicalVacancyGroup,
        profile: SearchProfileContext,
        signals: VacancySignalSnapshot | None = None,
        filter_result: FilterResult | None = None,
        feedback_memory: ProfileFeedbackMemory | None = None,
        search_query: str | None = None,
        search_mode: str | None = None,
        enrich_with_llm: bool = True,
    ) -> SearchResultItem | None:
        """Карточка вакансии.

        `signals` и `filter_result` принимаются готовыми: вызывающий цикл уже
        посчитал их один раз на вакансию. Без них метод считает сам — так он
        остаётся пригодным для точечных вызовов из тестов.
        """
        primary_record = _pick_primary_record(canonical)
        if signals is None:
            signals = inspect_vacancy(canonical, profile)
        if filter_result is None:
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
        bucket = _assign_bucket(
            filter_result=filter_result, score=score_result.score, negative_hits=score_result.negative_hits
        )
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
        filter_result: FilterResult,
    ) -> HiddenFilteredItem:
        """Запись о жёстко скрытой вакансии по УЖЕ посчитанному решению фильтра."""
        primary_record = _pick_primary_record(canonical)
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


def _effective_query_for_profile_run(
    *,
    submitted_query: str,
    profile_terms: tuple[str, ...],
    preserve_raw_terms: bool,
) -> str:
    """Primary query for a run that also has a saved profile plan.

    The submitted query wins — it is the one thing the user stated for THIS run.

    Two cases, and the difference matters a lot:

    1. The query just repeats one of the profile's own terms (the normal case — the form is
       prefilled from the profile). It is then kept verbatim as that term, so it collapses
       in dedup instead of adding a near-duplicate stage.
    2. The query is something else. It is translated to German exactly like a query with no
       profile at all, because German sources answer German titles: on live APIs
       "warehouse worker" returns 50 hits against 13071 for "lagermitarbeiter", and
       "cleaner" 270 against 5707 for "reinigungskraft".

    `preserve_raw_terms` turns that translation off for runs whose sources are not German:
    Russian-only lanes, and worldwide remote lanes, which are English — "python developer"
    returns 20 vacancies on RemoteJobs.org and 8 on Arbeitnow where "softwareentwickler"
    returns 0 on both.

    An empty submitted query falls back to the profile plan.
    """
    query = submitted_query.strip()
    if not query:
        return profile_terms[0]

    normalized_query = query.casefold()
    for term in profile_terms:
        if term.strip().casefold() == normalized_query:
            return term

    if preserve_raw_terms:
        # Источник отвечает не по-немецки — перевод только сузил бы выдачу.
        return query
    intent = normalize_role_intent(query)
    return intent.primary_de if intent is not None else query


def _with_run_geography(
    profile: SearchProfileContext,
    search_input: SourceSearchInput,
) -> SearchProfileContext:
    """Перенести города ИЗ ФОРМЫ в контекст на время этого прогона.

    Города поиска — свойство запроса, а не профиля: завтра тот же профиль ищут
    по другим городам. Место жительства при этом остаётся профильным.

    Уже проставленный список городов не переписывается: веер запускает по
    одному запросу на город, и каждой ветке нужен ВЕСЬ заказ целиком. Иначе
    вакансия из Ростока, которую источник принёс на запрос Берлина, была бы
    отклонена в берлинской ветке и потеряна при слиянии.
    """
    return dataclasses.replace(
        profile,
        search_location=search_input.location or None,
        search_cities=profile.search_cities or parse_search_cities(search_input.location),
        # Введённый запрос — тоже часть заказа этого прогона. Он задаёт семейство
        # ролей наравне с ролями профиля, иначе явная настройка запуска молча
        # отсекалась бы планом профиля.
        run_query_terms=profile.run_query_terms or _run_query_terms(search_input.query),
        # Радиус этого прогона. Форма — явная настройка запуска, и она сильнее
        # сохранённого в профиле значения; когда в форме радиуса нет, остаётся
        # профильный. Ноль означает «строго в перечисленных городах».
        search_radius_km=(
            search_input.radius_km if search_input.radius_km is not None else profile.search_radius_km
        ),
    )


def _leg_radius_km(run_radius_km: int | None, location: str | None) -> int | None:
    """Радиус для одной ветки веера городов."""
    if run_radius_km is not None:
        return run_radius_km
    return 0 if location else None


def _run_query_terms(query: str) -> tuple[str, ...]:
    stripped = (query or "").strip()
    return (stripped,) if stripped else ()


def _resolve_profile_search_terms(
    profile: SearchProfileContext,
    *,
    source_ids: Sequence[str] = (),
) -> tuple[str, ...]:
    preserve_raw_terms = _uses_only_russian_language_sources(source_ids)
    # Дополнительные слова идут ПОСЛЕ основных: человек дописал их, чтобы
    # расширить план, а не чтобы перебить главный запрос. Порядок и есть приоритет.
    extra = tuple(profile.additional_search_terms)
    if profile.search_query_terms:
        return _normalize_profile_terms_for_sources(
            (*profile.search_query_terms, *extra),
            preserve_raw_terms=preserve_raw_terms,
        )
    if profile.profile_source == "saved" and len(profile.desired_roles) > 1:
        return _normalize_profile_terms_for_sources(
            (*profile.desired_roles, *extra),
            preserve_raw_terms=preserve_raw_terms,
        )
    if extra and profile.desired_roles:
        # Основных слов нет, но дополнительные заданы: план всё равно должен их
        # пройти, иначе поле в форме профиля ничего бы не меняло.
        return _normalize_profile_terms_for_sources(
            (*profile.desired_roles, *extra),
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
    translated_ai_tools_term = AI_TOOLS_WESTERN_QUERY_TRANSLATIONS.get(
        " ".join(term.split()).casefold()
    )
    if translated_ai_tools_term is not None:
        return translated_ai_tools_term
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


def _tag_results_with_city(result: SearchRunResult, city: str) -> SearchRunResult:
    """Пометить вакансии городом, по запросу которого их отдал источник.

    Нужно там, где работодатель не назвал место: по тексту вакансии город не
    восстановить, но спрашивали её у конкретного города — в его раздел она и
    попадёт.
    """

    def tag(items: tuple[SearchResultItem, ...]) -> tuple[SearchResultItem, ...]:
        return tuple(dataclasses.replace(item, search_city=city) for item in items)

    return dataclasses.replace(
        result,
        results=tag(result.results),
        hot_results=tag(result.hot_results),
        maybe_results=tag(result.maybe_results),
        rejected_results=tag(result.rejected_results),
        query_result_groups=tuple(
            dataclasses.replace(
                group,
                hot_results=tag(group.hot_results),
                maybe_results=tag(group.maybe_results),
            )
            for group in result.query_result_groups
        ),
    )


# Заголовок для вакансий, у которых работодатель не назвал город и по запросу
# которых город тоже не восстановить.
_CITY_UNKNOWN_LABEL = "Без указанного города"


def _build_city_result_groups(
    result: SearchRunResult,
    cities: tuple[str, ...],
) -> tuple[SearchCityResultGroup, ...]:
    """Показываемые вакансии по городам, в порядке из формы поиска.

    Человек перечисляет города по важности, а выдача раньше шла сплошным
    списком вперемешку. Порядок групп — это порядок запроса, а не число
    найденного: пустой первый город тоже ответ.
    """
    if not cities:
        return ()

    hot: dict[str, list[SearchResultItem]] = {city: [] for city in cities}
    maybe: dict[str, list[SearchResultItem]] = {city: [] for city in cities}
    hot[_CITY_UNKNOWN_LABEL] = []
    maybe[_CITY_UNKNOWN_LABEL] = []

    def city_of(item: SearchResultItem) -> str:
        matched = item.signals.matched_search_city
        if matched in hot:
            return matched
        if item.search_city in hot:
            return item.search_city
        return _CITY_UNKNOWN_LABEL

    for item in result.hot_results:
        hot[city_of(item)].append(item)
    for item in result.maybe_results:
        maybe[city_of(item)].append(item)

    groups = [
        SearchCityResultGroup(city=city, hot_results=tuple(hot[city]), maybe_results=tuple(maybe[city]))
        for city in cities
    ]
    if hot[_CITY_UNKNOWN_LABEL] or maybe[_CITY_UNKNOWN_LABEL]:
        groups.append(
            SearchCityResultGroup(
                city=_CITY_UNKNOWN_LABEL,
                hot_results=tuple(hot[_CITY_UNKNOWN_LABEL]),
                maybe_results=tuple(maybe[_CITY_UNKNOWN_LABEL]),
            )
        )
    return tuple(groups)


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

    # Одна вакансия не может быть одновременно карточкой и жёстко скрытой. Попытки
    # собирают канонические группы из разных наборов записей, поэтому у одной и той же
    # вакансии текст в двух попытках разный: в одной требование нашлось, в другой нет.
    # Побеждает НАЙДЕННОЕ — по тому же принципу, по которому отсутствие требования
    # нельзя утверждать по вырезке, а его наличие можно (см. _has_analyzable_body).
    for canonical_key in hidden_by_key:
        result_by_key.pop(canonical_key, None)

    preview_by_key: dict[str, DedupPreviewItem] = {}
    for result in results:
        for item in result.deduped_preview_items:
            preview_by_key.setdefault(item.canonical_key, item)

    ordered_results = _collapse_items_sharing_a_source_record(
        sorted(result_by_key.values(), key=_merge_result_sort_key)
    )
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


def _collapse_items_sharing_a_source_record(
    items: list[SearchResultItem],
) -> tuple[SearchResultItem, ...]:
    """Схлопнуть карточки, у которых есть общая исходная запись источника.

    Каждая попытка поиска группирует свои записи заново, и одна и та же вакансия
    получает в разных попытках разный canonical_key: в одной попытке она попала
    в группу с двумя соседями, в другой пришла одна. Объединение попыток шло
    только по ключу, и вакансия показывалась дважды — на реальном прогоне обе
    карточки ссылались на одну и ту же запись BA 10001-1003661150-S.

    Общий идентификатор внутри одного источника — однозначное тождество, поэтому
    здесь не нужны пороги похожести. Список приходит уже отсортированным, и
    остаётся первая, то есть лучшая карточка.
    """
    kept: list[SearchResultItem] = []
    seen_records: set[str] = set()
    for item in items:
        record_keys = {
            f"{record.source_id}:{record.external_id}"
            for record in getattr(getattr(item, "canonical_group", None), "source_records", ())
            if getattr(record, "external_id", None)
        }
        if record_keys & seen_records:
            continue
        seen_records |= record_keys
        kept.append(item)
    return tuple(kept)


def _result_item_key(item: SearchResultItem) -> str:
    key = getattr(getattr(item, "canonical_group", None), "canonical_key", None)
    if isinstance(key, str) and key:
        return key
    return f"result-object:{id(item)}"


def _merge_result_sort_key(item: SearchResultItem) -> tuple[int, int, int, int, str]:
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
    return (bucket_rank, _user_priority_rank(item), -score, -posted_rank, _result_item_key(item))


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


def _assign_bucket(
    *,
    filter_result: FilterResult,
    score: int,
    negative_hits: tuple[RuleHit, ...] = (),
) -> SearchBucket:
    if filter_result.hard_reject:
        return "rejected"
    if any(hit.code in IRRELEVANT_SCORE_CODES for hit in negative_hits):
        # Не та работа: «на проверку» здесь нечего проверять.
        return "rejected"
    if filter_result.review_required:
        return "maybe"
    if score >= HOT_BUCKET_MIN_SCORE:
        return "hot"
    if score >= MAYBE_BUCKET_MIN_SCORE:
        return "maybe"
    return "rejected"


def _sufficient_canonical_count(result: SearchRunResult, profile: SearchProfileContext) -> int:
    """Count visible unique matches, never raw rows, hard rejects or contrary families."""
    families = {family for family in classify_role_families((
        *profile.desired_roles, *profile.search_query_terms,
        *profile.additional_search_terms, *profile.run_query_terms,
    )) if is_specific_family(family)}
    eligible: set[str] = set()
    for item in (*result.hot_results, *result.maybe_results):
        if item.filter_result.hard_reject:
            continue
        family = RoleFamily(item.role_family) if item.role_family else RoleFamily.GENERIC
        if families and is_specific_family(family):
            if not any(families_are_compatible(wanted, family) for wanted in families):
                continue
        elif families and not (item.signals.positive_role_hits or item.signals.desired_role_hits):
            continue
        eligible.add(item.canonical_group.canonical_key)
    return len(eligible)


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


def _user_priority_rank(result: SearchResultItem) -> int:
    signals = getattr(result, "signals", None)
    ukrainian_welcome = bool(getattr(signals, "ukrainian_welcome_signal", False))
    german_not_required = bool(getattr(signals, "german_not_required_signal", False))
    basic_german = bool(getattr(signals, "basic_german_signal", False))
    no_mandatory_german = bool(getattr(signals, "no_mandatory_german_mentioned", False))
    ai_tools_language_fit = bool(getattr(signals, "ai_tools_language_fit_signal", False))

    if ukrainian_welcome and (
        ai_tools_language_fit or german_not_required or basic_german or no_mandatory_german
    ):
        return 0
    if ai_tools_language_fit:
        return 1
    if german_not_required:
        return 2
    if basic_german:
        return 3
    if ukrainian_welcome:
        return 4
    if no_mandatory_german:
        return 5
    return 6


def _result_sort_key(result: SearchResultItem) -> tuple[int, int, int, int, str]:
    posted_date = result.canonical_group.posted_date
    posted_rank = posted_date.toordinal() if posted_date is not None else 0
    return (
        _BUCKET_PRIORITY[result.bucket],
        _user_priority_rank(result),
        -result.score_result.score,
        -posted_rank,
        result.canonical_group.canonical_key,
    )
