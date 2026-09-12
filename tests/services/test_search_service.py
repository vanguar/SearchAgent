import logging
from types import SimpleNamespace

from app.services.normalization_models import CanonicalVacancyGroup
from app.services.normalizer import VacancyNormalizer
from app.services.relevance_feedback_service import RelevanceFeedbackService
from app.services.relevance_memory_service import (
    FeedbackPattern,
    ProfileFeedbackMemory,
    RelevanceMemoryService,
    SourceQualitySignal,
)
from app.services.search_models import (
    FilterResult,
    ScoreResult,
    SearchProfileContext,
    SearchResultItem,
    SearchRunResult,
    VacancySignalSnapshot,
)
from app.services.search_service import SearchService, _result_sort_key
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.errors import AdapterRequestError
from app.services.source_adapters.models import AdapterSearchResponse, SourceRecordPreview, SourceSearchInput
from app.services.source_adapters.registry import SourceAdapterRegistry
from app.services.summary_service import SummaryService
from app.services.translation_service import TranslationService


def test_result_order_uses_language_and_ukrainian_priority_before_score() -> None:
    def item(*, key: str, score: int, signals: VacancySignalSnapshot) -> SimpleNamespace:
        return SimpleNamespace(
            bucket="hot",
            signals=signals,
            score_result=SimpleNamespace(score=score),
            canonical_group=SimpleNamespace(posted_date=None, canonical_key=key),
        )

    results = (
        item(key="required-german", score=99, signals=VacancySignalSnapshot(combined_text="")),
        item(
            key="german-unspecified",
            score=90,
            signals=VacancySignalSnapshot(combined_text="", no_mandatory_german_mentioned=True),
        ),
        item(
            key="basic-german",
            score=80,
            signals=VacancySignalSnapshot(combined_text="", basic_german_signal=True),
        ),
        item(
            key="no-german",
            score=70,
            signals=VacancySignalSnapshot(combined_text="", german_not_required_signal=True),
        ),
        item(
            key="ukrainian-no-german",
            score=60,
            signals=VacancySignalSnapshot(
                combined_text="",
                german_not_required_signal=True,
                ukrainian_welcome_signal=True,
            ),
        ),
    )

    ordered = sorted(results, key=_result_sort_key)

    assert [result.canonical_group.canonical_key for result in ordered] == [
        "ukrainian-no-german",
        "no-german",
        "basic-german",
        "german-unspecified",
        "required-german",
    ]


class StubProfileResolver:
    def resolve(self, *, profile_id: int | None = None) -> SearchProfileContext:
        return SearchProfileContext(
            profile_label="Основной поиск",
            profile_source="saved",
            note_ru="Используется сохраненный профиль поиска.",
            legal_status="Section 24",
            work_authorized=True,
            german_level="basic",
            desired_roles=("склад", "логистика", "упаковка", "производство"),
            relocation_ready=True,
            shift_ok=True,
            physical_work_ok=True,
        )


class BAAdapter(BaseSourceAdapter):
    source_id = "ba"
    display_name = "BA"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=(
                SourceRecordPreview(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    external_id="ba-1",
                    source_reference="ba-1",
                    title="Lagermitarbeiter/in",
                    company="Nord Team GmbH",
                    location="Berlin, Deutschland",
                    posted_at="2026-04-16",
                    detail_url="https://example.org/jobs/ba-1",
                    raw_payload={"description": "Ohne Deutsch. 3 Schicht. Unterkunft vorhanden. Ab sofort. Keine Erfahrung."},
                ),
                SourceRecordPreview(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    external_id="ba-2",
                    source_reference="ba-2",
                    title="Pflegefachkraft",
                    company="Care Nord GmbH",
                    location="Berlin, Deutschland",
                    posted_at="2026-04-16",
                    detail_url="https://example.org/jobs/ba-2",
                    raw_payload={"description": "Pflege und Dokumentation im stationaren Bereich."},
                ),
            ),
            total_count=2,
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload={"source": "ba"},
        )


class CareerjetAdapter(BaseSourceAdapter):
    source_id = "careerjet"
    display_name = "Careerjet"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=(
                SourceRecordPreview(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    external_id="cj-1",
                    source_reference="cj-1",
                    title="Lagermitarbeiter (m/w/d)",
                    company="Nord Team GmbH",
                    location="Berlin, Deutschland",
                    posted_at="2026-04-15",
                    detail_url="https://example.org/jobs/cj-1",
                    raw_payload={"description": "Ohne Deutsch. 3 Schicht. Unterkunft vorhanden. Ab sofort. Keine Erfahrung."},
                ),
                SourceRecordPreview(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    external_id="cj-2",
                    source_reference="cj-2",
                    title="Verpacker/in",
                    company="Pack Team GmbH",
                    location="Hamburg, Deutschland",
                    posted_at="2026-04-16",
                    detail_url="https://example.org/jobs/cj-2",
                    raw_payload={"description": "Verpackung im Lager. Work permit and visa questions are discussed individually."},
                ),
            ),
            total_count=2,
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload={"source": "careerjet"},
        )


class BrokenAdapter(BaseSourceAdapter):
    source_id = "broken"
    display_name = "Broken"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        raise AdapterRequestError(source_id=self.source_id, source_name=self.display_name, message="Временный сбой.")


class OrderingAdapter(BaseSourceAdapter):
    source_id = "ordering"
    display_name = "Ordering"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=(
                SourceRecordPreview(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    external_id="ord-warehouse",
                    source_reference="ord-warehouse",
                    title="Lagermitarbeiter/in",
                    company="Alpha Lager GmbH",
                    location="Berlin, Deutschland",
                    posted_at="2026-04-17",
                    detail_url="https://example.org/jobs/ord-warehouse",
                    raw_payload={"description": "Lagerarbeit."},
                ),
                SourceRecordPreview(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    external_id="ord-production",
                    source_reference="ord-production",
                    title="Produktionshelfer/in",
                    company="Beta Produktion GmbH",
                    location="Berlin, Deutschland",
                    posted_at="2026-04-16",
                    detail_url="https://example.org/jobs/ord-production",
                    raw_payload={"description": "Produktion."},
                ),
            ),
            total_count=2,
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload={"source": "ordering"},
        )


class ITAutomationProfileResolver:
    def resolve(self, *, profile_id: int | None = None) -> SearchProfileContext:
        return SearchProfileContext(
            profile_label="IT support automation",
            profile_source="saved",
            note_ru="IT support automation profile.",
            legal_status="Section 24",
            work_authorized=True,
            german_level="basic",
            desired_roles=("it support", "automation"),
            relocation_ready=True,
            shift_ok=True,
            physical_work_ok=False,
        )


class ITAutomationAdapter(BaseSourceAdapter):
    source_id = "itauto"
    display_name = "IT Automation"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=(
                SourceRecordPreview(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    external_id="it-automation",
                    source_reference="it-automation",
                    title="IT Support Automation Specialist",
                    company="DeskOps GmbH",
                    location="Berlin, Deutschland",
                    posted_at="2026-04-17",
                    detail_url="https://example.org/jobs/it-automation",
                    raw_payload={
                        "description": "Technical support automation. Basic German. Entry level. Ab sofort."
                    },
                ),
                SourceRecordPreview(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    external_id="it-warehouse",
                    source_reference="it-warehouse",
                    title="Lagermitarbeiter/in",
                    company="Wrong Family GmbH",
                    location="Berlin, Deutschland",
                    posted_at="2026-04-17",
                    detail_url="https://example.org/jobs/it-warehouse",
                    raw_payload={"description": "Lagerarbeit."},
                ),
            ),
            total_count=2,
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload={"source": "itauto"},
        )


class CleaningProfileResolver:
    def resolve(self, *, profile_id: int | None = None) -> SearchProfileContext:
        return SearchProfileContext(
            profile_label="Cleaning",
            profile_source="saved",
            note_ru="Cleaning profile.",
            legal_status="Section 24",
            work_authorized=True,
            german_level="basic",
            desired_roles=("reinigung",),
            relocation_ready=True,
            shift_ok=True,
            physical_work_ok=True,
        )


class CleaningAdapter(BaseSourceAdapter):
    source_id = "cleaning"
    display_name = "Cleaning"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=(
                SourceRecordPreview(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    external_id="clean-1",
                    source_reference="clean-1",
                    title="Reinigungskraft",
                    company="Hotel Service GmbH",
                    location="Berlin, Deutschland",
                    posted_at="2026-04-17",
                    detail_url="https://example.org/jobs/clean-1",
                    raw_payload={
                        "description": "Reinigung im Hotel. Basic German. Entry level. Ab sofort."
                    },
                ),
            ),
            total_count=1,
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload={"source": "cleaning"},
        )


class DeliveryProfileResolver:
    def resolve(self, *, profile_id: int | None = None) -> SearchProfileContext:
        return SearchProfileContext(
            profile_label="Доставка / Курьер",
            profile_source="saved",
            note_ru="Delivery profile.",
            legal_status="Section 24",
            work_authorized=True,
            german_level="basic",
            desired_roles=("Доставка", "Курьер", "Водитель"),
            preferred_locations=("Rostock",),
            relocation_ready=True,
            shift_ok=True,
            physical_work_ok=True,
        )


class DeliveryAdapter(BaseSourceAdapter):
    source_id = "adzuna"
    display_name = "Adzuna"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=(
                SourceRecordPreview(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    external_id="verkaufsfahrer-rostock",
                    source_reference="verkaufsfahrer-rostock",
                    title="Verkaufsfahrer (m/w/d)",
                    company="bofrost* Dienstleistungs GmbH & Co. KG",
                    location="Rostock, Deutschland",
                    posted_at="2026-04-18",
                    detail_url="https://example.org/jobs/verkaufsfahrer",
                    raw_payload={
                        "description": (
                            "Fahrer im Aussendienst mit Lieferung. Basic German. "
                            "Flexible Schichten. Ab sofort."
                        )
                    },
                ),
            ),
            total_count=1,
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload={"source": "adzuna"},
        )


class _CountingLLMHelper:
    """Stands in for the LLM-backed translation/summary helper, counting invocations."""

    def __init__(self) -> None:
        self.translate_calls = 0
        self.summarize_calls = 0

    def translate_title(self, text: str) -> str:
        self.translate_calls += 1
        return f"RU:{text[:12]}"

    def summarize(self, text: str) -> str:
        self.summarize_calls += 1
        return "Краткое описание"


def _enrichment_service(helper: _CountingLLMHelper) -> SearchService:
    translation = TranslationService(helper=helper)
    summary = SummaryService(helper=helper, translation_service=translation)
    registry = SourceAdapterRegistry(adapters=(BAAdapter(), CareerjetAdapter()))
    return SearchService(
        registry=registry,
        profile_resolver=StubProfileResolver(),
        translation_service=translation,
        summary_service=summary,
        llm_client=object(),  # non-None: enables _enrich_run_result
    )


def test_search_scoring_pass_makes_no_llm_calls() -> None:
    helper = _CountingLLMHelper()
    service = _enrichment_service(helper)

    result = service.search(
        search_input=SourceSearchInput(query="lager", page=1, page_size=5),
        enrich_with_llm=False,
    )

    assert helper.translate_calls == 0
    assert helper.summarize_calls == 0
    # deterministic scoring still produced buckets
    assert len(result.hot_results) + len(result.maybe_results) >= 1


def test_enrich_run_result_enriches_only_displayed_results_once() -> None:
    helper = _CountingLLMHelper()
    service = _enrichment_service(helper)

    base = service.search(
        search_input=SourceSearchInput(query="lager", page=1, page_size=5),
        enrich_with_llm=False,
    )
    displayed = len(base.hot_results) + len(base.maybe_results)
    assert displayed >= 1

    enriched = service._enrich_run_result(base)

    # Exactly one summary call per displayed card — not per candidate, not per attempt.
    assert helper.summarize_calls == displayed
    for item in enriched.hot_results + enriched.maybe_results:
        assert item.summary_ru == "Краткое описание"


def test_enrich_run_result_is_noop_without_llm_client() -> None:
    registry = SourceAdapterRegistry(adapters=(BAAdapter(),))
    service = SearchService(registry=registry, profile_resolver=StubProfileResolver())  # llm_client=None

    base = service.search(search_input=SourceSearchInput(query="lager", page=1, page_size=5))

    assert service._enrich_run_result(base) is base


def test_search_service_orchestrates_dedup_filtering_scoring_and_bucketing() -> None:
    registry = SourceAdapterRegistry(adapters=(BAAdapter(), CareerjetAdapter(), BrokenAdapter()))
    service = SearchService(registry=registry, profile_resolver=StubProfileResolver())

    result = service.search(search_input=SourceSearchInput(query="lager", page=1, page_size=5))

    assert result.total_raw_records == 4
    assert result.total_normalized_records == 4
    assert result.total_canonical_results == 3  # canonical groups before family-mismatch filtering
    # Pflegefachkraft is silently dropped (profession_family_mismatch for warehouse profile)
    assert [item.bucket for item in result.results] == ["hot", "maybe"]
    assert len(result.hot_results) == 1
    assert len(result.maybe_results) == 1
    assert len(result.rejected_results) == 0
    assert result.hot_results[0].summary_ru is not None
    assert result.hot_results[0].explanation_ru.startswith("Подходит:")
    assert result.maybe_results[0].explanation_ru.startswith("С осторожностью:")
    assert any(state.source_id == "broken" and state.error_message == "Временный сбой." for state in result.source_states)


def test_search_service_logs_source_adapter_diagnostics(caplog) -> None:
    registry = SourceAdapterRegistry(adapters=(BAAdapter(),))
    service = SearchService(registry=registry, profile_resolver=StubProfileResolver())

    with caplog.at_level(logging.INFO):
        service.search(search_input=SourceSearchInput(query="lager", page=1, page_size=10), source_ids=("ba",))

    assert "source_adapter_start source_id=ba" in caplog.text
    assert "source_adapter_success source_id=ba" in caplog.text
    assert "source_adapter_postprocess source_id=ba" in caplog.text


def test_search_service_exposes_dedup_preview_without_resurrecting_hard_rejects() -> None:
    """Freeze lock: dedup preview is audit visibility, not a visible-result bypass."""
    registry = SourceAdapterRegistry(adapters=(BAAdapter(), CareerjetAdapter()))
    service = SearchService(registry=registry, profile_resolver=StubProfileResolver())

    result = service.search(search_input=SourceSearchInput(query="lager", page=1, page_size=5))

    assert len(result.deduped_preview_items) == result.total_canonical_results
    merged_preview = next((item for item in result.deduped_preview_items if item.source_count > 1), None)
    assert merged_preview is not None
    assert merged_preview.title.startswith("Lagermitarbeiter")
    assert merged_preview.source_count == 2
    assert merged_preview.original_url is not None
    assert merged_preview.original_url.startswith("https://example.org/jobs/")
    visible_titles = {item.primary_record.original_title for item in result.results}
    assert "Pflegefachkraft" not in visible_titles
    assert any(item.title == "Pflegefachkraft" for item in result.deduped_preview_items)
    assert any(item.title == "Pflegefachkraft" for item in result.hidden_filtered_items)
    hidden = next(item for item in result.hidden_filtered_items if item.title == "Pflegefachkraft")
    assert any(hit.code == "profession_family_mismatch" for hit in hidden.rejection_reasons)


# ---------------------------------------------------------------------------
# End-to-end feedback memory integration regression tests
# ---------------------------------------------------------------------------

def _make_service() -> SearchService:
    registry = SourceAdapterRegistry(adapters=(BAAdapter(), CareerjetAdapter()))
    return SearchService(registry=registry, profile_resolver=StubProfileResolver())


def _search_input() -> SourceSearchInput:
    return SourceSearchInput(query="lager", page=1, page_size=5)


def test_feedback_memory_changes_visible_ordering_within_allowed_candidates() -> None:
    """Feedback changes ordering among visible allowed candidates, not only raw scores."""
    registry = SourceAdapterRegistry(adapters=(OrderingAdapter(),))
    service = SearchService(registry=registry, profile_resolver=StubProfileResolver())
    search_input = SourceSearchInput(query="lager", page=1, page_size=5)

    baseline = service.search(search_input=search_input)
    memory = ProfileFeedbackMemory(
        profile_id=1,
        frequently_relevant=[FeedbackPattern("production", "relevant", 3)],
        total_feedback_count=3,
    )
    reordered = service.search(search_input=search_input, feedback_memory=memory)

    assert [item.role_family for item in baseline.results] == ["warehouse", "production"]
    assert [item.role_family for item in reordered.results] == ["production", "warehouse"]
    assert all(item.bucket in {"hot", "maybe"} for item in reordered.results)
    assert all(not item.filter_result.hard_reject for item in reordered.results)


def test_it_support_automation_feedback_boosts_visible_candidate() -> None:
    """IT/support/automation profile gets deterministic feedback memory inside its family."""
    registry = SourceAdapterRegistry(adapters=(ITAutomationAdapter(),))
    service = SearchService(registry=registry, profile_resolver=ITAutomationProfileResolver())
    search_input = SourceSearchInput(query="it support automation", page=1, page_size=5)

    baseline = service.search(search_input=search_input)
    memory = ProfileFeedbackMemory(
        profile_id=10,
        frequently_relevant=[FeedbackPattern("it", "relevant", 3)],
        total_feedback_count=3,
    )
    boosted = service.search(search_input=search_input, feedback_memory=memory)

    assert [item.role_family for item in baseline.results] == ["it"]
    assert "Automation" in baseline.results[0].primary_record.original_title
    assert boosted.results[0].score_result.score > baseline.results[0].score_result.score
    assert all("Lager" not in item.primary_record.original_title for item in boosted.results)


def test_cleaning_domain_feedback_boosts_additional_non_it_domain() -> None:
    """Cleaning is a separate non-IT domain distinct from warehouse/delivery/healthcare."""
    registry = SourceAdapterRegistry(adapters=(CleaningAdapter(),))
    service = SearchService(registry=registry, profile_resolver=CleaningProfileResolver())
    search_input = SourceSearchInput(query="reinigung", page=1, page_size=5)

    baseline = service.search(search_input=search_input)
    memory = ProfileFeedbackMemory(
        profile_id=20,
        frequently_relevant=[FeedbackPattern("cleaning", "relevant", 3)],
        total_feedback_count=3,
    )
    boosted = service.search(search_input=search_input, feedback_memory=memory)

    assert baseline.results[0].role_family == "cleaning"
    assert baseline.results[0].bucket in {"hot", "maybe"}
    assert boosted.results[0].score_result.score > baseline.results[0].score_result.score


def test_unrelated_role_family_feedback_does_not_bleed_into_cleaning_results() -> None:
    """Irrelevant IT feedback does not penalize unrelated cleaning candidates."""
    registry = SourceAdapterRegistry(adapters=(CleaningAdapter(),))
    service = SearchService(registry=registry, profile_resolver=CleaningProfileResolver())
    search_input = SourceSearchInput(query="reinigung", page=1, page_size=5)

    baseline = service.search(search_input=search_input)
    unrelated_memory = ProfileFeedbackMemory(
        profile_id=21,
        frequently_irrelevant=[FeedbackPattern("it", "irrelevant", 5)],
        total_feedback_count=5,
    )
    result = service.search(search_input=search_input, feedback_memory=unrelated_memory)

    assert result.results[0].role_family == "cleaning"
    assert result.results[0].score_result.score == baseline.results[0].score_result.score
    assert all(hit.code != "feedback_penalty" for hit in result.results[0].score_result.negative_hits)


def test_feedback_boost_applies_to_warehouse_results_without_exceeding_score_cap() -> None:
    """Warehouse boost raises score when possible and records the boost at the 100-point cap."""
    service = _make_service()

    result_baseline = service.search(search_input=_search_input())
    memory = ProfileFeedbackMemory(
        profile_id=1,
        frequently_relevant=[FeedbackPattern("warehouse", "relevant", 3)],
        total_feedback_count=3,
    )
    result_with_mem = service.search(search_input=_search_input(), feedback_memory=memory)

    # Same number of visible candidates
    assert len(result_with_mem.results) == len(result_baseline.results)

    # Warehouse candidate scores higher with memory
    baseline_scores = {i.primary_record.original_title: i.score_result.score for i in result_baseline.results}
    mem_items = {i.primary_record.original_title: i for i in result_with_mem.results}
    mem_scores = {title: item.score_result.score for title, item in mem_items.items()}
    lager_titles = [t for t in baseline_scores if "Lager" in t or "lager" in t.lower()]
    assert lager_titles, "Expected at least one Lager* vacancy in results"
    for title in lager_titles:
        if baseline_scores[title] < 100:
            assert mem_scores[title] > baseline_scores[title], f"{title}: score should increase with warehouse boost"
        else:
            assert any(hit.code == "feedback_boost" for hit in mem_items[title].score_result.positive_hits)


def test_feedback_boost_bounded_at_eight_points() -> None:
    """Feedback boost is capped at +8 regardless of feedback count."""
    service = _make_service()

    memory = ProfileFeedbackMemory(
        profile_id=1,
        frequently_relevant=[FeedbackPattern("warehouse", "relevant", 20)],
        total_feedback_count=20,
    )
    result = service.search(search_input=_search_input(), feedback_memory=memory)

    baseline = service.search(search_input=_search_input())
    for item in result.results:
        baseline_item = next(i for i in baseline.results if i.canonical_group.canonical_key == item.canonical_group.canonical_key)
        delta = item.score_result.score - baseline_item.score_result.score
        assert delta <= 8, f"Feedback boost exceeded +8: delta={delta} for {item.primary_record.original_title}"


def test_hard_rejected_items_absent_regardless_of_feedback_boost() -> None:
    """Feedback boost cannot resurrect hard-rejected vacancies (Pflegefachkraft = hard reject for warehouse profile)."""
    service = _make_service()

    # Massive positive feedback for healthcare family — should not bring hard-rejected items back
    memory = ProfileFeedbackMemory(
        profile_id=1,
        frequently_relevant=[FeedbackPattern("healthcare", "relevant", 20)],
        total_feedback_count=20,
    )
    result = service.search(search_input=_search_input(), feedback_memory=memory)

    all_titles = {item.primary_record.original_title for item in result.results}
    assert "Pflegefachkraft" not in all_titles, "Hard-rejected item must not appear regardless of feedback"
    # Allowed candidates still present
    assert any("Lager" in t for t in all_titles), "Warehouse candidates should still be present"


def test_feedback_penalty_lowers_score_for_penalised_source() -> None:
    """Source quality penalty reduces scores when a source has mostly irrelevant feedback."""
    service = _make_service()

    memory = ProfileFeedbackMemory(
        profile_id=1,
        source_signals=[
            SourceQualitySignal(
                source_name="BA",
                relevant_count=0,
                irrelevant_count=5,
                weak_count=0,
                has_penalty=True,
            )
        ],
        total_feedback_count=5,
    )
    result_baseline = service.search(search_input=_search_input())
    result_with_penalty = service.search(search_input=_search_input(), feedback_memory=memory)

    baseline_scores = {i.primary_record.original_title: i.score_result.score for i in result_baseline.results}
    penalty_scores = {i.primary_record.original_title: i.score_result.score for i in result_with_penalty.results}

    ba_titles = [
        i.primary_record.original_title
        for i in result_with_penalty.results
        if i.primary_record.source_name == "BA"
    ]
    assert ba_titles, "Expected at least one BA result to test penalty"
    for title in ba_titles:
        assert penalty_scores[title] <= baseline_scores[title], f"{title}: BA source penalty should not increase score"


def test_feedback_penalty_bounded_at_minus_eight() -> None:
    """Combined penalty never drops below -8 from feedback alone."""
    service = _make_service()

    memory = ProfileFeedbackMemory(
        profile_id=1,
        frequently_irrelevant=[FeedbackPattern("warehouse", "irrelevant", 20)],
        source_signals=[
            SourceQualitySignal(
                source_name="BA",
                relevant_count=0,
                irrelevant_count=20,
                weak_count=0,
                has_penalty=True,
            )
        ],
        total_feedback_count=20,
    )
    baseline = service.search(search_input=_search_input())
    result = service.search(search_input=_search_input(), feedback_memory=memory)

    for item in result.results:
        baseline_item = next(i for i in baseline.results if i.canonical_group.canonical_key == item.canonical_group.canonical_key)
        delta = item.score_result.score - baseline_item.score_result.score
        assert delta >= -8, f"Feedback penalty exceeded -8: delta={delta} for {item.primary_record.original_title}"


def test_delivery_domain_feedback_no_effect_on_warehouse_results() -> None:
    """Delivery-family feedback does not bleed into warehouse vacancy scoring."""
    service = _make_service()

    memory = ProfileFeedbackMemory(
        profile_id=1,
        frequently_irrelevant=[FeedbackPattern("driving", "irrelevant", 5)],
        total_feedback_count=5,
    )
    baseline = service.search(search_input=_search_input())
    result = service.search(search_input=_search_input(), feedback_memory=memory)

    for item in result.results:
        if item.role_family == "warehouse":
            baseline_item = next(
                i for i in baseline.results if i.canonical_group.canonical_key == item.canonical_group.canonical_key
            )
            assert item.score_result.score == baseline_item.score_result.score, (
                f"Driving-family penalty should not affect warehouse result: {item.primary_record.original_title}"
            )


def _delivery_service() -> SearchService:
    registry = SourceAdapterRegistry(adapters=(DeliveryAdapter(),))
    return SearchService(registry=registry, profile_resolver=DeliveryProfileResolver())


def _delivery_input() -> SourceSearchInput:
    return SourceSearchInput(query="Lieferfahrer", location="Rostock", page=1, page_size=5)


def test_recorded_irrelevant_feedback_hides_same_delivery_vacancy_from_visible_buckets(
    db_session,
    owner_records: dict,
) -> None:
    service = _delivery_service()
    baseline = service.search(search_input=_delivery_input())
    target = baseline.hot_results[0]
    profile_id = owner_records["search_profile"].id

    RelevanceFeedbackService().record_feedback(
        db_session,
        profile_id=profile_id,
        canonical_key=target.canonical_group.canonical_key,
        source_id=target.primary_record.source_id,
        source_name=target.primary_record.source_name,
        normalized_title=target.canonical_group.normalized_title,
        company_name=target.canonical_group.company_name,
        location_text=target.canonical_group.location_text,
        role_family=target.role_family,
        current_query="Lieferfahrer",
        feedback_label="irrelevant",
    )
    memory = RelevanceMemoryService().build_profile_memory(db_session, profile_id=profile_id)

    result = service.search(search_input=_delivery_input(), feedback_memory=memory)

    visible_keys = {item.canonical_group.canonical_key for item in result.hot_results + result.maybe_results}
    assert target.canonical_group.canonical_key not in visible_keys
    rejected = next(
        item for item in result.rejected_results
        if item.canonical_group.canonical_key == target.canonical_group.canonical_key
    )
    assert rejected.score_result.score < 45
    assert any(hit.code == "explicit_feedback_irrelevant" for hit in rejected.score_result.negative_hits)


def test_recorded_weak_feedback_moves_same_delivery_vacancy_from_hot_to_review(
    db_session,
    owner_records: dict,
) -> None:
    service = _delivery_service()
    baseline = service.search(search_input=_delivery_input())
    target = baseline.hot_results[0]
    profile_id = owner_records["search_profile"].id

    RelevanceFeedbackService().record_feedback(
        db_session,
        profile_id=profile_id,
        canonical_key=target.canonical_group.canonical_key,
        source_id=target.primary_record.source_id,
        source_name=target.primary_record.source_name,
        normalized_title=target.canonical_group.normalized_title,
        company_name=target.canonical_group.company_name,
        location_text=target.canonical_group.location_text,
        role_family=target.role_family,
        current_query="Lieferfahrer",
        feedback_label="weak",
    )
    memory = RelevanceMemoryService().build_profile_memory(db_session, profile_id=profile_id)

    result = service.search(search_input=_delivery_input(), feedback_memory=memory)

    assert all(
        item.canonical_group.canonical_key != target.canonical_group.canonical_key
        for item in result.hot_results
    )
    maybe = next(
        item for item in result.maybe_results
        if item.canonical_group.canonical_key == target.canonical_group.canonical_key
    )
    assert maybe.score_result.score < 70
    assert any(hit.code == "explicit_feedback_weak" for hit in maybe.score_result.negative_hits)


def test_attempts_sharing_a_source_record_collapse_into_one_card() -> None:
    """Каждая попытка группирует записи заново, и ключи получаются разные.

    На реальном прогоне одна вакансия BA (10001-1003661150-S) попала и в группу
    из трёх записей, и в отдельную карточку: объединение шло только по
    canonical_key, и в выдаче оказались две карточки одной работы.
    """
    from types import SimpleNamespace

    from app.services.search_service import _collapse_items_sharing_a_source_record

    def item(name: str, external_ids: tuple[str, ...]) -> SimpleNamespace:
        return SimpleNamespace(
            name=name,
            canonical_group=SimpleNamespace(
                source_records=[
                    SimpleNamespace(source_id="ba", external_id=external_id)
                    for external_id in external_ids
                ]
            ),
        )

    grouped = item("группа", ("10001-1003661150-S", "12811-2330043-S"))
    alone = item("одиночная", ("10001-1003661150-S",))
    other = item("другая", ("99999-1",))

    kept = _collapse_items_sharing_a_source_record([grouped, alone, other])

    assert [entry.name for entry in kept] == ["группа", "другая"]


def test_collapse_keeps_the_first_and_best_ranked_card() -> None:
    from types import SimpleNamespace

    from app.services.search_service import _collapse_items_sharing_a_source_record

    def item(name: str, external_ids: tuple[str, ...]) -> SimpleNamespace:
        return SimpleNamespace(
            name=name,
            canonical_group=SimpleNamespace(
                source_records=[
                    SimpleNamespace(source_id="ba", external_id=external_id)
                    for external_id in external_ids
                ]
            ),
        )

    alone = item("одиночная", ("10001-1003661150-S",))
    grouped = item("группа", ("10001-1003661150-S", "12811-2330043-S"))

    kept = _collapse_items_sharing_a_source_record([alone, grouped])

    assert [entry.name for entry in kept] == ["одиночная"]


def _make_result_item(*, distance_km: float | None, bucket: str = "hot") -> SearchResultItem:
    """Карточка результата с заданным расстоянием до дома."""
    record = VacancyNormalizer().normalize_source_record(
        SourceRecordPreview(
            source_id="ba", source_name="BA", external_id=f"d-{distance_km}",
            source_reference=f"d-{distance_km}", title="Fahrer (m/w/d)", company="Nord GmbH",
            location="Rostock", posted_at="2026-09-01", detail_url=None,
            raw_payload={"description": "Auslieferung."},
        )
    )
    canonical = CanonicalVacancyGroup(
        canonical_key=f"canonical-{distance_km}", normalized_title=record.normalized_title,
        company_name=record.normalized_company, location_text=record.normalized_location.normalized_text,
        country_code=record.normalized_location.country_code, city=record.normalized_location.city,
        posted_date=record.posted_date, language_signals=record.language_signals,
        source_records=(record,), provenance=(record.source_record_key,),
    )
    return SearchResultItem(
        canonical_group=canonical,
        primary_record=record,
        signals=VacancySignalSnapshot(combined_text="", distance_from_home_km=distance_km),
        filter_result=FilterResult(decision="allow"),
        score_result=ScoreResult(score=70),
        bucket=bucket,
        explanation_ru="",
    )


def _make_run_result(*, home_city: str | None, hot: tuple[SearchResultItem, ...]) -> SearchRunResult:
    profile = SearchProfileContext(
        profile_label="Driver", profile_source="saved", home_city=home_city, desired_roles=("Fahrer",)
    )
    return SearchRunResult(profile=profile, source_states=(), results=hot, hot_results=hot)


def test_nearby_and_countrywide_split_uses_the_daily_commute_limit() -> None:
    """Вакансии в получасе от дома тонули среди тех, что за шестьсот километров."""
    near = _make_result_item(distance_km=42.0)
    far = _make_result_item(distance_km=319.0)
    result = _make_run_result(home_city="Tribsees", hot=(near, far))

    assert result.nearby_results == (near,)
    assert result.countrywide_results == (far,)
    assert result.distance_split_available is True


def test_unknown_distance_is_never_called_nearby() -> None:
    """Утверждать «рядом» можно только про измеренное."""
    near = _make_result_item(distance_km=10.0)
    unknown = _make_result_item(distance_km=None)
    result = _make_run_result(home_city="Tribsees", hot=(near, unknown))

    assert result.nearby_results == (near,)
    assert result.countrywide_results == (unknown,)


def test_split_is_hidden_when_it_would_say_nothing() -> None:
    """Если всё одинаково близко или одинаково далеко, заголовок только мешает."""
    all_near = _make_run_result(home_city="Tribsees", hot=(_make_result_item(distance_km=12.0),))
    all_far = _make_run_result(home_city="Tribsees", hot=(_make_result_item(distance_km=400.0),))

    assert all_near.distance_split_available is False
    assert all_far.distance_split_available is False


def test_split_needs_a_home_city() -> None:
    result = _make_run_result(
        home_city=None,
        hot=(_make_result_item(distance_km=12.0), _make_result_item(distance_km=400.0)),
    )

    assert result.distance_split_available is False


def test_nearby_results_are_not_repeated_in_the_countrywide_lists() -> None:
    """Одна вакансия — одна карточка, даже когда выдача разделена по расстоянию."""
    near = _make_result_item(distance_km=42.0)
    far = _make_result_item(distance_km=319.0)
    maybe_near = _make_result_item(distance_km=20.0, bucket="maybe")
    result = SearchRunResult(
        profile=SearchProfileContext(
            profile_label="Driver", profile_source="saved", home_city="Tribsees", desired_roles=("Fahrer",)
        ),
        source_states=(),
        results=(near, far, maybe_near),
        hot_results=(near, far),
        maybe_results=(maybe_near,),
    )

    assert result.hot_results_beyond_commute == (far,)
    assert result.maybe_results_beyond_commute == ()
    shown = result.nearby_results + result.hot_results_beyond_commute + result.maybe_results_beyond_commute
    assert len(shown) == len({id(item) for item in shown}) == 3
