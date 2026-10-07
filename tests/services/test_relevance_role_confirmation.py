"""Проблема 3: признак роли ставится только по делу."""
from __future__ import annotations

import pytest
from app.services.filter_engine import FilterEngine
from app.services.rule_catalog import inspect_vacancy
from app.services.scorer import VacancyScorer
from app.services.search_service import _assign_bucket
from tests.services.relevance_support import build_canonical, courier_profile, courier_run, items_titled


def _bucket(title: str, body: str, **overrides: object) -> tuple[str, set[str]]:
    profile = courier_profile(**overrides)
    canonical = build_canonical(title=title, body=body)
    verdict = FilterEngine().evaluate(canonical, profile)
    score = VacancyScorer().score(canonical, profile, filter_result=verdict)
    bucket = _assign_bucket(filter_result=verdict, score=score.score, negative_hits=score.negative_hits)
    return bucket, {hit.code for hit in score.negative_hits}


def test_shift_and_location_alone_do_not_make_a_role_fit() -> None:
    bucket, codes = _bucket(
        "CvD (m/w/d)",
        "Bereitschaft zu Schicht- und Wochenenddiensten. Der Berliner Kurier gehört zu unserem Verlag. "
        "Unbefristete Anstellung in Berlin.",
    )

    assert bucket == "rejected"
    assert "role_not_confirmed" in codes


def test_review_flag_does_not_lift_an_unrelated_role_into_maybe() -> None:
    bucket, codes = _bucket(
        "Maler (m/w/d) - Spandau / Potsdam",
        "Wände streichen und lackieren. Abgeschlossene Ausbildung als Maler zwingend erforderlich.",
    )

    assert bucket == "rejected"
    assert "role_not_confirmed" in codes


def test_role_named_twice_in_body_is_confirmed() -> None:
    signals = inspect_vacancy(
        build_canonical(
            title="Minijob 14 €/h (m/w/d)",
            body="Wir suchen Auslieferungsfahrer. Als Fahrer lieferst du Getränke an unsere Kunden aus.",
        ),
        courier_profile(),
    )

    assert signals.role_confirmed is True


def test_role_named_in_title_is_confirmed() -> None:
    signals = inspect_vacancy(build_canonical(title="Paketzusteller (m/w/d)", body="Vollzeit."), courier_profile())

    assert signals.role_confirmed is True


def test_role_confirmation_is_not_applied_to_it_profiles() -> None:
    signals = inspect_vacancy(
        build_canonical(title="Python Developer", body="FastAPI, Django."),
        courier_profile(desired_roles=("Python Developer",), driver_license=None),
    )

    assert signals.role_confirmed is None


def test_apprenticeship_goes_to_irrelevant_with_its_own_reason() -> None:
    bucket, codes = _bucket(
        "Ausbildung Fachkraft Kurier-, Express- u. Postdienstleistungen (m/w/d) in 2026",
        "Deine Ausbildung bei der Deutschen Post: Paketzustellung und Briefzustellung lernen.",
    )

    assert bucket == "rejected"
    assert "apprenticeship" in codes


def test_apprenticeship_is_kept_for_a_profile_looking_for_one() -> None:
    _, codes = _bucket(
        "Ausbildung zum Berufskraftfahrer (m/w/d)",
        "Ausbildung zum Fahrer.",
        desired_roles=("Ausbildung Fahrer",),
    )

    assert "apprenticeship" not in codes


@pytest.mark.parametrize(
    ("fragment", "company"),
    [("CvD (m/w/d)", "Berliner Verlag"), ("Maler (m/w/d)", "Vonovia"), ("Ausbildung Fachkraft Kurier", None)],
)
def test_real_run_moves_non_roles_to_irrelevant(fragment: str, company: str | None) -> None:
    result = courier_run()

    assert not items_titled((*result.hot_results, *result.maybe_results), fragment, company=company)
    assert items_titled(result.rejected_results, fragment, company=company)


def test_english_delivery_title_confirms_the_role() -> None:
    signals = inspect_vacancy(build_canonical(title="Delivery Driver", body="Full time."), courier_profile())

    assert signals.role_confirmed is True
