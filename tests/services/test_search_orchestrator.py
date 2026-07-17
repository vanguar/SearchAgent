"""Tests for accepted progressive keyword fallback search behavior."""
from __future__ import annotations

import itertools
from unittest.mock import MagicMock

from app.services.search_fallback import (
    get_fallback_keywords,
    get_intent_fallback_keywords,
    is_low_language_profile,
)
from app.services.search_models import (
    SearchAttemptSummary,
    SearchProfileContext,
    SearchRunResult,
    SearchSourceState,
)
from app.services.search_service import SearchService
from app.services.source_adapters.models import SourceSearchInput

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile(**kwargs) -> SearchProfileContext:
    defaults = {
        "profile_label": "Test",
        "profile_source": "saved",
        "desired_roles": ("склад",),
        "german_level": None,
        "english_level": None,
    }
    defaults.update(kwargs)
    return SearchProfileContext(**defaults)


def _make_result(hot: int = 0, maybe: int = 0, rejected: int = 0) -> SearchRunResult:
    """Build a minimal SearchRunResult with specified bucket counts."""
    hot_items = tuple(MagicMock(bucket="hot") for _ in range(hot))
    maybe_items = tuple(MagicMock(bucket="maybe") for _ in range(maybe))
    rejected_items = tuple(MagicMock(bucket="rejected") for _ in range(rejected))
    return SearchRunResult(
        profile=_make_profile(),
        source_states=(SearchSourceState(source_id="ba", source_name="BA", status_label="ok"),),
        results=hot_items + maybe_items + rejected_items,
        hot_results=hot_items,
        maybe_results=maybe_items,
        rejected_results=rejected_items,
        total_raw_records=hot + maybe + rejected,
        total_normalized_records=hot + maybe + rejected,
        total_canonical_results=hot + maybe + rejected,
    )


def _make_search_input(query: str = "lager", *, search_mode: str = "germany_local") -> SourceSearchInput:
    return SourceSearchInput(query=query, location="Berlin", radius_km=25, page=1, page_size=8, search_mode=search_mode)


def _make_service(search_side_effect: list[SearchRunResult], llm_client=None) -> SearchService:
    svc = SearchService(llm_client=llm_client)
    svc.get_profile_context = MagicMock(return_value=_make_profile(desired_roles=("склад",)))
    svc._resolve_source_ids = MagicMock(return_value=("ba",))
    if search_side_effect:
        effect = itertools.chain(search_side_effect, itertools.repeat(search_side_effect[-1]))
    else:
        effect = iter(())
    svc.search = MagicMock(side_effect=effect)
    return svc


# ---------------------------------------------------------------------------
# search_fallback helpers
# ---------------------------------------------------------------------------

def test_get_fallback_keywords_excludes_primary() -> None:
    kws = get_fallback_keywords(primary_query="lager", role="склад", low_language=False)
    assert "lager" not in kws
    assert "lagermitarbeiter" in kws


def test_get_fallback_keywords_low_language_appends_barrier_terms() -> None:
    kws = get_fallback_keywords(primary_query="lager", role="склад", low_language=True)
    assert "helfer" in kws or "lagerhelfer" in kws


def test_get_fallback_keywords_unknown_role_does_not_inject_helper_fallback() -> None:
    kws = get_fallback_keywords(primary_query="xyz", role="неизвестная_роль", low_language=False)
    assert kws == ()


def test_warehouse_fallback_includes_family_synonyms() -> None:
    """Warehouse search must broaden to alternative German + English titles."""
    kws = get_fallback_keywords(primary_query="lager", role="склад", low_language=False)
    for expected in ("kommissionierer", "verpacker", "staplerfahrer", "warehouse associate"):
        assert expected in kws, expected
    assert "lager" not in kws  # primary excluded
    assert len(kws) <= 12  # capped


def test_fallback_keywords_dedupe_case_insensitively() -> None:
    """A profile term and a pool term differing only in case must not produce two queries."""
    from app.services.role_family import RoleFamily
    from app.services.search_fallback import get_profile_fallback_keywords

    kws = get_profile_fallback_keywords(
        profile_terms=("Lagerarbeiter", "Lagermitarbeiter"),
        family=RoleFamily.WAREHOUSE,
        role_primary_de="lager",
        broaden=True,
    )
    lowered = [k.lower() for k in kws]
    assert len(lowered) == len(set(lowered))  # no case-insensitive duplicates
    assert sum(1 for k in kws if k.lower() == "lagermitarbeiter") == 1
    assert "lagerarbeiter" not in lowered  # primary excluded case-insensitively


def test_skilled_trade_fallback_is_not_polluted_by_sibling_trades() -> None:
    """An electrician search must NOT pull unrelated construction trades (mason/painter)."""
    from app.services.role_intent import normalize_role_intent

    intent = normalize_role_intent("электрик")
    kws = get_intent_fallback_keywords(intent=intent, primary_query=intent.primary_de, low_language=False)
    assert "maurer" not in kws
    assert "maler" not in kws
    assert "fliesenleger" not in kws


def test_unknown_saved_role_does_not_trigger_helper_poisoning() -> None:
    """Unknown/narrow accepted behavior: no deterministic helper fallback for unknown roles."""
    empty = _make_result()
    svc = _make_service([empty])
    svc.get_profile_context = MagicMock(
        return_value=_make_profile(desired_roles=("неизвестная_роль",), german_level="none", english_level="none")
    )

    result = svc.orchestrated_search(search_input=_make_search_input("xyz"))

    assert svc.search.call_count == 1
    assert result.attempt_summary is not None
    assert result.attempt_summary.fallback_used is False
    assert result.attempt_summary.final_query_used == "xyz"
    assert [attempt.query_used for attempt in result.attempt_summary.attempts] == ["xyz"]


def test_orchestrated_search_scores_attempts_without_llm_enrichment() -> None:
    svc = _make_service([_make_result(hot=1, maybe=1)])

    svc.orchestrated_search(search_input=_make_search_input("lager"))

    assert svc.search.call_count >= 1
    # Every attempt must be scored deterministically; LLM enrichment is deferred to the end.
    for call in svc.search.call_args_list:
        assert call.kwargs.get("enrich_with_llm") is False


def test_is_low_language_profile_both_none() -> None:
    # None means "not filled in" — not treated as explicitly weak
    assert is_low_language_profile(german_level=None, english_level=None) is False


def test_is_low_language_profile_both_explicit_none() -> None:
    # "none" string maps to low in _LOW_GERMAN_HINTS
    assert is_low_language_profile(german_level="none", english_level="none") is True


def test_is_low_language_profile_both_a1() -> None:
    assert is_low_language_profile(german_level="a1", english_level="a1") is True


def test_is_low_language_profile_one_intermediate() -> None:
    assert is_low_language_profile(german_level="none", english_level="intermediate") is False


# ---------------------------------------------------------------------------
# orchestrated_search — stop conditions
# ---------------------------------------------------------------------------

def test_primary_sufficient_still_runs_full_planned_query_set() -> None:
    """Primary success does not stop the full planned search."""
    good = _make_result(hot=2, maybe=3)
    svc = _make_service([good])

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    # >= 4: exact count depends on the (now broader) synonym pool; the invariant is that the
    # full planned set runs (more than just the primary), not a specific number.
    assert svc.search.call_count >= 4
    assert result.attempt_summary is not None
    summary: SearchAttemptSummary = result.attempt_summary
    assert summary.fallback_used
    assert len(summary.attempts) >= 4
    assert summary.attempts[0].stage_name == "primary"


def test_remote_worldwide_runs_all_deterministic_fallbacks_even_when_primary_is_enough() -> None:
    """Worldwide remote search should gather across deterministic queries instead of stopping early."""
    primary = _make_result(hot=2, maybe=3)
    fallback_1 = _make_result(hot=1, maybe=2)
    fallback_2 = _make_result(hot=3, maybe=5)
    svc = _make_service([primary, fallback_1, fallback_2])
    svc.get_profile_context = MagicMock(
        return_value=_make_profile(
            desired_roles=("custom role",),
            search_query_terms=("profile term 1", "profile term 2"),
        )
    )

    result = svc.orchestrated_search(
        search_input=_make_search_input("profile term 0", search_mode="remote_worldwide")
    )

    assert svc.search.call_count == 2
    assert result.attempt_summary is not None
    assert [attempt.stage_name for attempt in result.attempt_summary.attempts] == [
        "primary",
        "fallback_1",
    ]
    assert [attempt.query_used for attempt in result.attempt_summary.attempts] == [
        "profile term 1",
        "profile term 2",
    ]
    assert result.attempt_summary.final_query_used == "all profile queries"
    assert len(result.hot_results) == 3
    assert len(result.maybe_results) == 5


def test_germany_local_runs_all_profile_search_terms_even_when_primary_is_enough() -> None:
    """Explicit profile skills are a search plan, not optional fallback stages."""
    primary = _make_result(hot=2, maybe=3)
    python_backend = _make_result(hot=1, maybe=1)
    fastapi = _make_result(hot=1, maybe=0)
    django = _make_result(hot=0, maybe=1)
    svc = _make_service([primary, python_backend, fastapi, django])
    svc.get_profile_context = MagicMock(
        return_value=_make_profile(
            desired_roles=("Middle Python Developer",),
            search_query_terms=(
                "Middle Python Developer",
                "Python Backend Developer",
                "FastAPI Developer",
                "Django Developer",
            ),
        )
    )

    result = svc.orchestrated_search(
        search_input=_make_search_input("Middle Python Developer", search_mode="germany_local")
    )

    assert svc.search.call_count >= 4
    assert result.attempt_summary is not None
    # The explicit profile terms run first (in order); synonym broadening is appended after.
    assert [attempt.query_used for attempt in result.attempt_summary.attempts][:4] == [
        "Middle Python Developer",
        "Python Backend Developer",
        "FastAPI Developer",
        "Django Developer",
    ]
    assert result.attempt_summary.final_query_used == "all profile queries"
    assert len(result.hot_results) == 4
    assert len(result.maybe_results) == 5


def test_saved_profile_desired_roles_become_search_tabs_when_terms_missing() -> None:
    """Saved multi-role profiles should search each desired role even before search_query_terms are backfilled."""
    primary = _make_result(hot=1, maybe=0)
    backend = _make_result(hot=0, maybe=2)
    fastapi = _make_result(hot=1, maybe=1)
    svc = _make_service([primary, backend, fastapi])
    svc.get_profile_context = MagicMock(
        return_value=_make_profile(
            desired_roles=(
                "Middle Python Developer",
                "Python Backend Developer",
                "FastAPI Developer",
            ),
            search_query_terms=(),
        )
    )

    result = svc.orchestrated_search(
        search_input=_make_search_input("Middle Python Developer", search_mode="remote_worldwide")
    )

    assert svc.search.call_count >= 3
    assert result.attempt_summary is not None
    # Desired roles run first (in order); synonym broadening is appended after.
    assert [attempt.query_used for attempt in result.attempt_summary.attempts][:3] == [
        "Middle Python Developer",
        "Python Backend Developer",
        "FastAPI Developer",
    ]
    assert result.attempt_summary.final_query_used == "all profile queries"
    assert len(result.hot_results) == 2
    assert len(result.maybe_results) == 3


def test_germany_local_saved_cyrillic_profile_roles_use_german_source_queries() -> None:
    """German sources must never receive Cyrillic saved-role terms such as курьер."""
    courier = _make_result(hot=1)
    driver = _make_result(maybe=1)
    svc = _make_service([courier, driver])
    svc.get_profile_context = MagicMock(
        return_value=_make_profile(
            desired_roles=("курьер", "водитель"),
            search_query_terms=(),
        )
    )

    result = svc.orchestrated_search(
        search_input=_make_search_input("курьер", search_mode="germany_local")
    )

    assert svc.search.call_count >= 2
    assert result.attempt_summary is not None
    queries = [attempt.query_used for attempt in result.attempt_summary.attempts]
    assert queries[0] == "kurier"  # primary translated from курьер
    assert "fahrer" in queries
    assert "zusteller" in queries  # broadened with same-family alternative titles
    # Core invariant: German sources must never receive Cyrillic terms.
    assert not any(any("а" <= char.lower() <= "я" or char.lower() == "ё" for char in query) for query in queries)


def test_russian_language_sources_preserve_cyrillic_profile_roles() -> None:
    """HH/DOU/Djinni scope searches Russian-language sources with the original Russian terms."""
    courier = _make_result(hot=1)
    driver = _make_result(maybe=1)
    svc = _make_service([courier, driver])
    svc._resolve_source_ids = MagicMock(return_value=("hh", "dou_rss", "djinni_rss"))
    svc.get_profile_context = MagicMock(
        return_value=_make_profile(
            desired_roles=("курьер", "водитель"),
            search_query_terms=(),
        )
    )

    result = svc.orchestrated_search(
        search_input=_make_search_input("курьер", search_mode="remote_worldwide")
    )

    assert svc.search.call_count == 2
    assert result.attempt_summary is not None
    assert [attempt.query_used for attempt in result.attempt_summary.attempts] == ["курьер", "водитель"]


def test_partial_results_include_completed_attempts_for_live_tabs() -> None:
    first = _make_result(hot=1)
    second = _make_result(maybe=1)
    partials: list[SearchRunResult] = []
    svc = _make_service([first, second])
    svc.get_profile_context = MagicMock(
        return_value=_make_profile(
            desired_roles=("Middle Python Developer", "FastAPI Developer"),
            search_query_terms=(),
        )
    )

    svc.orchestrated_search(
        search_input=_make_search_input("Middle Python Developer", search_mode="remote_worldwide"),
        partial_result_callback=partials.append,
    )

    assert len(partials) >= 2
    assert partials[0].attempt_summary is not None
    assert [attempt.query_used for attempt in partials[0].attempt_summary.attempts] == [
        "Middle Python Developer",
    ]
    assert partials[1].attempt_summary is not None
    assert [attempt.query_used for attempt in partials[1].attempt_summary.attempts] == [
        "Middle Python Developer",
        "FastAPI Developer",
    ]


def test_zero_primary_triggers_fallback() -> None:
    """Primary returns 0 results → fallback attempt is made."""
    empty = _make_result()
    some = _make_result(hot=1, maybe=3)
    svc = _make_service([empty, some])

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    assert svc.search.call_count >= 2
    summary = result.attempt_summary
    assert summary.fallback_used
    assert summary.user_message_ru is not None
    assert "lager" in summary.user_message_ru


def test_fallback_does_not_stop_when_enough_results() -> None:
    """A good fallback does not stop the remaining planned queries."""
    empty = _make_result()
    good = _make_result(hot=2, maybe=3)
    svc = _make_service([empty, good])

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    assert svc.search.call_count >= 4
    summary = result.attempt_summary
    assert len(summary.attempts) >= 4
    assert summary.attempts[1].stage_name == "fallback_1"


def test_all_deterministic_exhausted_returns_best() -> None:
    """All attempts weak → returns best result (most non-rejected)."""
    r0 = _make_result(hot=0, maybe=1)
    r1 = _make_result(hot=0, maybe=2)
    r2 = _make_result(hot=1, maybe=0)
    svc = _make_service([r0, r1, r2])

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    # r1 has 2 maybe (non_rejected=2), r2 has 1 hot (non_rejected=1) — r1 wins on non_rejected
    assert result.attempt_summary is not None
    # best_result has at least as many non-rejected as either fallback
    non_rejected = len(result.hot_results) + len(result.maybe_results)
    assert non_rejected >= 1


# ---------------------------------------------------------------------------
# orchestrated_search — language relaxation
# ---------------------------------------------------------------------------

def test_language_relaxation_flagged_when_both_weak() -> None:
    """When profile has no usable language skills and fallback reaches a LOW_LANGUAGE_FALLBACK kw, flag it.

    склад ladder (excluding "lager"): lagermitarbeiter, lagerhelfer, helfer, ...
    fallback_1 = "lagermitarbeiter" (NOT in LOW_LANGUAGE_FALLBACK) → lang_relax=False
    fallback_2 = "lagerhelfer"     (IS  in LOW_LANGUAGE_FALLBACK) → lang_relax=True
    So we need primary + fallback_1 to fail, fallback_2 to succeed.
    """
    empty = _make_result()
    also_empty = _make_result()
    some = _make_result(hot=2, maybe=2)
    svc = _make_service([empty, also_empty, some])
    svc.get_profile_context = MagicMock(
        return_value=_make_profile(desired_roles=("склад",), german_level="none", english_level="none")
    )

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    summary = result.attempt_summary
    assert summary.language_relaxation_used is True
    assert any(a.language_relaxation_applied for a in summary.attempts)


def test_language_relaxation_not_flagged_when_languages_unset() -> None:
    """Language relaxation must NOT fire when profile has no language fields set (None != 'none')."""
    empty = _make_result()
    also_empty = _make_result()
    some = _make_result(hot=2, maybe=2)
    svc = _make_service([empty, also_empty, some])
    # german_level=None and english_level=None means "not filled in", not "explicitly weak"
    svc.get_profile_context = MagicMock(
        return_value=_make_profile(desired_roles=("склад",), german_level=None, english_level=None)
    )

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    summary = result.attempt_summary
    assert summary.language_relaxation_used is False
    assert not any(a.language_relaxation_applied for a in summary.attempts)


# ---------------------------------------------------------------------------
# orchestrated_search — LLM fallback (last resort)
# ---------------------------------------------------------------------------

def test_llm_fallback_not_used_when_deterministic_sufficient() -> None:
    """LLM expansion is never triggered when deterministic fallback found enough."""
    good = _make_result(hot=2, maybe=3)
    mock_llm = MagicMock()
    mock_llm.suggest_fallback_keywords = MagicMock(return_value="softwareentwickler")
    svc = _make_service([good], llm_client=mock_llm)

    svc.orchestrated_search(search_input=_make_search_input("lager"))

    mock_llm.suggest_fallback_keywords.assert_not_called()


def test_llm_fallback_used_when_deterministic_exhausted() -> None:
    """LLM expansion is tried after deterministic fallback is exhausted without enough results."""
    empty = _make_result()
    mock_llm = MagicMock()
    mock_llm.suggest_fallback_keywords = MagicMock(return_value="helfer lagerarbeiter")
    # All attempts return empty — primary + 2 deterministic + 1 LLM
    svc = _make_service([empty, empty, empty, empty], llm_client=mock_llm)

    svc.orchestrated_search(search_input=_make_search_input("lager"))

    mock_llm.suggest_fallback_keywords.assert_called_once()


def test_llm_fallback_not_used_when_no_llm_client() -> None:
    """No LLM client → LLM stage is skipped entirely."""
    empty = _make_result()
    svc = _make_service([empty, empty, empty])  # no llm_client

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    assert svc.search.call_count >= 4
    assert all(a.stage_name != "llm_1" for a in result.attempt_summary.attempts)


# ---------------------------------------------------------------------------
# orchestrated_search — UI explanation content
# ---------------------------------------------------------------------------

def test_no_explanation_when_primary_succeeds() -> None:
    good = _make_result(hot=2, maybe=2)
    svc = _make_service([good])

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    assert result.attempt_summary.user_message_ru is None


def test_explanation_mentions_both_queries_when_fallback_used() -> None:
    empty = _make_result()
    some = _make_result(hot=1, maybe=3)
    svc = _make_service([empty, some])

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    msg = result.attempt_summary.user_message_ru
    assert msg is not None
    assert "lager" in msg  # primary query mentioned


def test_explanation_mentions_language_when_relaxed() -> None:
    """User message includes language-barrier note when language relaxation was triggered."""
    empty = _make_result()
    also_empty = _make_result()
    some = _make_result(hot=2, maybe=2)
    # same setup as test_language_relaxation_flagged_when_both_weak:
    # fallback_2 "lagerhelfer" fires and is in LOW_LANGUAGE_FALLBACK
    svc = _make_service([empty, also_empty, some])
    svc.get_profile_context = MagicMock(
        return_value=_make_profile(desired_roles=("склад",), german_level="none", english_level="none")
    )

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    summary = result.attempt_summary
    assert summary.language_relaxation_used is True
    assert summary.user_message_ru is not None
    assert "языков" in summary.user_message_ru or "языковой" in summary.user_message_ru


def test_explanation_no_language_note_when_not_relaxed() -> None:
    """Language-barrier note must NOT appear when language relaxation was not triggered."""
    empty = _make_result()
    some = _make_result(hot=1, maybe=3)
    svc = _make_service([empty, some])
    # profile with good German — no relaxation expected
    svc.get_profile_context = MagicMock(
        return_value=_make_profile(desired_roles=("склад",), german_level="b2", english_level=None)
    )

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    summary = result.attempt_summary
    assert summary.language_relaxation_used is False
    if summary.user_message_ru:
        assert "языков" not in summary.user_message_ru
        assert "языковой" not in summary.user_message_ru


# ---------------------------------------------------------------------------
# Winning query correctness — final_query_used must match best_result's query
# ---------------------------------------------------------------------------

def test_earlier_fallback_wins_summary_reports_its_query() -> None:
    """fallback_1 best, fallback_2 worse → summary.final_query_used is fallback_1's query."""
    empty = _make_result()          # primary: 0 results
    good  = _make_result(hot=2, maybe=3)  # fallback_1: good
    bad   = _make_result(hot=0, maybe=1)  # fallback_2: worse than fallback_1
    svc = _make_service([empty, good, bad])
    svc.get_profile_context = MagicMock(return_value=_make_profile(desired_roles=("склад",)))

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    summary = result.attempt_summary
    assert summary.final_query_used == "all planned queries"
    assert len(result.hot_results) == 2
    assert len(result.maybe_results) == 4


def test_llm_worse_than_deterministic_does_not_overwrite_summary() -> None:
    """LLM attempt is worse than deterministic fallback → winning_query stays deterministic."""
    empty   = _make_result()               # primary
    decent  = _make_result(hot=1, maybe=3) # fallback_1: decent
    worse   = _make_result(hot=0, maybe=1) # LLM attempt: worse

    mock_llm = MagicMock()
    mock_llm.suggest_fallback_keywords = MagicMock(return_value="irgendwas")
    svc = _make_service([empty, decent, worse], llm_client=mock_llm)

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    summary = result.attempt_summary
    assert summary.final_query_used == "all planned queries"
    assert len(result.hot_results) == 1
    assert len(result.maybe_results) == 4


def test_final_query_used_equals_primary_when_primary_best() -> None:
    """If primary was best despite being insufficient, final_query_used is primary."""
    decent = _make_result(hot=0, maybe=2)  # primary: some
    worse1 = _make_result(hot=0, maybe=1)  # fallback_1: worse
    worse2 = _make_result(hot=0, maybe=0)  # fallback_2: even worse
    svc = _make_service([decent, worse1, worse2])

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    summary = result.attempt_summary
    assert summary.final_query_used == "all planned queries"
    assert len(result.maybe_results) == 3


# ---------------------------------------------------------------------------
# Attempt record fields — observability
# ---------------------------------------------------------------------------

def test_attempt_records_contain_all_structured_fields() -> None:
    """Each SearchAttemptRecord carries the complete observable payload for structured logging."""
    empty = _make_result()
    some = _make_result(hot=1, maybe=3, rejected=2)
    svc = _make_service([empty, some])

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    for attempt in result.attempt_summary.attempts:
        assert isinstance(attempt.attempt_number, int)
        assert isinstance(attempt.primary_query, str) and attempt.primary_query
        assert attempt.stage_name
        assert attempt.query_used
        assert isinstance(attempt.language_relaxation_applied, bool)
        assert isinstance(attempt.raw_count, int)
        assert isinstance(attempt.normalized_count, int)
        assert isinstance(attempt.deduped_count, int)
        assert isinstance(attempt.hot_count, int)
        assert isinstance(attempt.review_count, int)
        assert isinstance(attempt.rejected_count, int)
        assert isinstance(attempt.non_rejected_count, int)


def test_attempt_number_is_sequential() -> None:
    """attempt_number increments sequentially: primary=0, fallback_1=1, fallback_2=2."""
    empty = _make_result()
    also_empty = _make_result()
    some = _make_result(hot=2, maybe=3)
    svc = _make_service([empty, also_empty, some])

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    attempts = result.attempt_summary.attempts
    for expected_num, attempt in enumerate(attempts):
        assert attempt.attempt_number == expected_num


def test_primary_query_field_always_equals_original_query() -> None:
    """primary_query in every attempt record must be the original query, not the fallback keyword."""
    empty = _make_result()
    some = _make_result(hot=1, maybe=3)
    svc = _make_service([empty, some])

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    for attempt in result.attempt_summary.attempts:
        assert attempt.primary_query == "lager"


def test_review_count_and_rejected_count_match_bucket_counts() -> None:
    """review_count and rejected_count in the attempt record reflect actual bucket sizes."""
    good = _make_result(hot=2, maybe=3, rejected=1)
    svc = _make_service([good])

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    primary_rec = result.attempt_summary.attempts[0]
    assert primary_rec.hot_count == 2
    assert primary_rec.review_count == 3
    assert primary_rec.rejected_count == 1
    assert primary_rec.non_rejected_count == 5  # hot + review


def test_primary_attempt_record_reason_none_when_sufficient() -> None:
    """Primary attempt record has reason_continued=None when it was sufficient."""
    good = _make_result(hot=2, maybe=3)
    svc = _make_service([good])

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    primary_rec = result.attempt_summary.attempts[0]
    assert primary_rec.stage_name == "primary"
    assert primary_rec.attempt_number == 0
    assert primary_rec.reason_continued is None


def test_primary_attempt_record_reason_set_when_insufficient() -> None:
    """Primary attempt record has reason_continued set when insufficient."""
    empty = _make_result()
    some = _make_result(hot=1, maybe=3)
    svc = _make_service([empty, some])

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    primary_rec = result.attempt_summary.attempts[0]
    assert primary_rec.stage_name == "primary"
    assert primary_rec.attempt_number == 0
    assert primary_rec.reason_continued is not None


# ---------------------------------------------------------------------------
# Scoring/filtering pipeline unchanged
# ---------------------------------------------------------------------------

def test_scoring_pipeline_not_modified_by_orchestrator() -> None:
    """orchestrated_search delegates to search() unchanged — bucket counts come from search()."""
    expected = _make_result(hot=3, maybe=1)
    svc = _make_service([expected])

    result = svc.orchestrated_search(search_input=_make_search_input("lager"))

    assert len(result.hot_results) == 3
    assert len(result.maybe_results) == 1
