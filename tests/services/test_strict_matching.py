from dataclasses import replace

import pytest
from app.core.time import utc_now
from app.services.filter_engine import FilterEngine
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.normalizer import VacancyNormalizer
from app.services.scorer import VacancyScorer
from app.services.search_models import SearchProfileContext
from app.services.search_service import _assign_bucket
from app.services.source_adapters.models import SourceRecordPreview
from app.services.warehouse_transport import WAREHOUSE_ROUTE_ROLE

CITIES = ("Berlin", "Neustrelitz", "Neubrandenburg", "Rostock")
PROFILE = SearchProfileContext(
    profile_label="Strict test", profile_source="saved", strict_match_mode="no_german",
    legal_status="section_24", work_authorized=True, german_level="A1", english_level="none",
    desired_roles=("Lagerarbeiter", "Verpacker", "Produktionshelfer", "Auslieferungsfahrer", "Reinigungskraft"),
    preferred_locations=CITIES, driver_license="B",
)
BODY = (
    "Wir suchen Mitarbeiter. Aufgaben: Verpackung und Kommissionierung von Waren. "
    "Quereinsteiger willkommen. Keine Berufserfahrung erforderlich. Vollzeit. "
    "Wir bieten einen unbefristeten Arbeitsvertrag mit Einarbeitung im Team. "
)


@pytest.mark.parametrize("text,decision", [
    ("Warenverkehr im Sprinter zwischen unseren Lagern. Führerschein B. Deutsch nicht erforderlich.", "allow"),
    ("Warenverkehr im Sprinter zwischen unseren Lagern. Ihr Profil: Führerschein B, Zuverlässigkeit.", "review"),
    ("Warenverkehr im Sprinter zwischen unseren Lagern. Führerschein B. Gute Deutschkenntnisse.", "reject"),
    ("Warenverkehr zwischen unseren Lagern. Führerschein CE erforderlich. Deutsch nicht erforderlich.", "reject"),
    ("Paketzustellung im Sprinter. Führerschein B. Deutsch nicht erforderlich.", "reject"),
])
def test_goods_routes_still_require_compatible_language_and_licence(text, decision):
    profile = replace(PROFILE, strict_match_mode="a1", desired_roles=(WAREHOUSE_ROUTE_ROLE, "Lagerhelfer"))
    result = FilterEngine().evaluate(vacancy(text, title="Fahrer Klasse B"), profile)
    assert result.decision == decision, result


@pytest.mark.parametrize("requirement", ["Deutsch (Grundkenntnisse)", "Basic German"])
def test_basic_german_word_order_is_reviewed_only_in_a1_mode(requirement):
    profile = replace(PROFILE, strict_match_mode="a1")
    result = FilterEngine().evaluate(vacancy(requirement), profile)
    assert result.decision == "review"
    assert "strict_basic_german_review" in {h.code for h in result.review_hits}
    assert FilterEngine().evaluate(vacancy(requirement), PROFILE).hard_reject
    assert FilterEngine().evaluate(vacancy(requirement + ". Deutsch B2 erforderlich."), profile).hard_reject


def test_partner_goods_routes_and_eu_licence_remain_for_review():
    text = (
        "Du lieferst Waren im Sprinter an unsere Partner in Berlin und anderen Städten. "
        "Qualifikation: EU-Führerschein Klasse B. Deutsch (Grundkenntnisse)."
    )
    profile = replace(PROFILE, strict_match_mode="a1", desired_roles=(WAREHOUSE_ROUTE_ROLE, "Lagerhelfer"))
    result = FilterEngine().evaluate(vacancy(text, title="Fahrer Kühltransport"), profile)
    assert result.decision == "review"
    assert {"strict_route_review", "strict_basic_german_review", "strict_license_document_review"} <= {
        h.code for h in result.review_hits
    }


def test_truncated_language_requirement_is_not_absent_language():
    result = FilterEngine().evaluate(vacancy("Ihr Profil: Zuverlässig; gute Sprachkenntnisse in De…"), PROFILE)
    assert "strict_language_requirement_truncated" in {h.code for h in result.rejection_hits}


@pytest.mark.parametrize("place,rejected", [("Binz", True), ("Berlin", False)])
def test_repeated_job_headline_with_workplace_overrides_aggregator_city(place, rejected):
    result = FilterEngine().evaluate(vacancy(f"Reinigungskraft | Teilzeit | {place} Kennziffer: 12345. Deutsch nicht erforderlich."), PROFILE)
    assert ("strict_workplace_mismatch" in {h.code for h in result.rejection_hits}) == rejected


def vacancy(
    text="Deutsch nicht erforderlich.", *, title="Lagerhelfer / Verpacker", location="Berlin, Deutschland",
    complete=True,
):
    record = VacancyNormalizer().normalize_source_record(SourceRecordPreview(
        source_id="ba", source_name="BA", external_id="strict-test", source_reference="strict-test",
        title=title, company="Test GmbH", location=location, posted_at=utc_now().date().isoformat(),
        detail_url="https://example.org/strict-test", raw_payload={"description": BODY + text},
        description_complete=complete,
    ))
    return CanonicalVacancyGroup(
        canonical_key="strict-test", normalized_title=record.normalized_title,
        company_name=record.normalized_company, location_text=record.normalized_location.normalized_text,
        country_code=record.normalized_location.country_code, city=record.normalized_location.city,
        posted_date=record.posted_date, language_signals=record.language_signals,
        source_records=(record,), provenance=(record.source_record_key,),
    )


@pytest.mark.parametrize("city", CITIES)
def test_complete_explicit_match_passes_in_every_requested_city(city):
    assert FilterEngine().evaluate(vacancy(location=city), PROFILE).decision == "allow"


@pytest.mark.parametrize(("text", "code"), [
    ("", "strict_requirements_unavailable"),
    ("Ukrainer willkommen.", "strict_requirements_unavailable"),
    ("English only.", "strict_english_mismatch"),
    ("Deutsch nicht erforderlich. Fluent English required.", "strict_english_mismatch"),
    ("Deutsch nicht erforderlich. Good English.", "strict_english_mismatch"),
    ("Deutsch nicht erforderlich. Englisch erforderlich.", "strict_english_mismatch"),
    ("Deutsch nicht erforderlich. Polnisch erforderlich.", "strict_other_language"),
    ("Grundkenntnisse in Deutsch erforderlich.", "strict_german_mismatch"),
    ("Deutsch A1 erforderlich.", "strict_german_mismatch"),
    ("Deutsch A2 erforderlich.", "strict_german_mismatch"),
    ("Deutsch nicht erforderlich. Deutsch B2 erforderlich.", "strict_german_mismatch"),
    ("Deutsch nicht erforderlich. Staplerschein erforderlich.", "strict_equipment_permit"),
    ("Deutsch nicht erforderlich. Eigener PKW erforderlich.", "strict_own_car_review"),
])
def test_unknown_and_incompatible_conditions_cannot_be_rescued_by_score(text, code):
    canonical = vacancy(text)
    result = FilterEngine().evaluate(canonical, PROFILE)
    assert result.hard_reject
    assert code in {hit.code for hit in result.rejection_hits}
    score = VacancyScorer().score(canonical, PROFILE, filter_result=result)
    assert _assign_bucket(filter_result=result, score=100, negative_hits=score.negative_hits) == "rejected"


@pytest.mark.parametrize("complete", [False, None])
def test_snippets_never_count_as_complete_even_with_explicit_language(complete):
    result = FilterEngine().evaluate(vacancy(complete=complete), PROFILE)
    assert result.decision == "review"
    assert "strict_description_unconfirmed" in {h.code for h in result.review_hits}
    assert _assign_bucket(filter_result=result, score=100, negative_hits=()) == "maybe"


@pytest.mark.parametrize("location", ["Deutschland", "Hamburg", None, "Gewerbegebiet Nordwest"])
def test_geography_must_be_confirmed(location):
    result = FilterEngine().evaluate(vacancy(location=location), PROFILE)
    assert "strict_location_unconfirmed" in {h.code for h in result.rejection_hits}


def test_a1_policy_reviews_an_unbounded_basic_level_and_rejects_higher_level():
    profile = replace(PROFILE, strict_match_mode="a1")
    assert FilterEngine().evaluate(vacancy("Deutsch A1 ausreichend."), profile).decision == "allow"
    assert FilterEngine().evaluate(vacancy("Grundkenntnisse in Deutsch erforderlich."), profile).decision == "review"
    assert FilterEngine().evaluate(vacancy("Deutsch A2 erforderlich."), profile).hard_reject
    assert FilterEngine().evaluate(vacancy(), profile).decision == "allow"
    assert FilterEngine().evaluate(
        vacancy("Deutsch A1 ausreichend."), replace(profile, german_level="none"),
    ).hard_reject


def test_driving_requires_confirmed_category_b_but_warehouse_does_not():
    assert FilterEngine().evaluate(vacancy(), PROFILE).decision == "allow"
    for category, allowed in [("", False), ("B", True), ("CE", False)]:
        text = "Deutsch nicht erforderlich. " + (f"Führerschein Klasse {category} erforderlich." if category else "")
        result = FilterEngine().evaluate(vacancy(text, title="Auslieferungsfahrer"), PROFILE)
        assert (result.decision == "allow") == allowed, result


@pytest.mark.parametrize("title", ["Reinigungskraft", "Produktionshelfer", "Verpacker"])
def test_strict_policy_supports_other_low_barrier_roles(title):
    assert FilterEngine().evaluate(vacancy(title=title), PROFILE).decision == "allow"


def test_negative_and_unknown_profile_conditions_fail_closed():
    for profile in (
        replace(PROFILE, work_authorized=False),
        replace(PROFILE, desired_roles=()),
        replace(PROFILE, preferred_locations=()),
        replace(PROFILE, strict_match_mode="typo"),
    ):
        assert FilterEngine().evaluate(vacancy(), profile).hard_reject


@pytest.mark.parametrize("text", ["", "English only.", "Grundkenntnisse in Deutsch erforderlich."])
def test_ordinary_mode_retains_previous_behavior(text):
    profile = replace(PROFILE, strict_match_mode="off")
    result = FilterEngine().evaluate(vacancy(text), profile)
    assert result.decision == "allow"
    assert not any(hit.code.startswith("strict_") for hit in result.rejection_hits)


def test_optional_english_does_not_create_an_invented_requirement():
    result = FilterEngine().evaluate(vacancy("Deutsch nicht erforderlich. Englisch nicht erforderlich."), PROFILE)
    assert result.decision == "allow"


@pytest.mark.parametrize("text", ["Ohne Deutsch.", "Deutsch nicht erforderlich. English courses available."])
def test_explicit_no_german_or_language_course_benefit_remain_allowed(text):
    assert FilterEngine().evaluate(vacancy(text), PROFILE).decision == "allow"


def test_negated_no_german_promise_is_not_positive_evidence():
    result = FilterEngine().evaluate(vacancy("Ohne Deutsch arbeiten ist nicht möglich."), PROFILE)
    assert result.hard_reject


def test_partial_source_cannot_supply_missing_evidence_after_merge():
    full = vacancy("")
    snippet = vacancy("Deutsch nicht erforderlich.", complete=False)
    merged = replace(full, source_records=full.source_records + snippet.source_records)
    result = FilterEngine().evaluate(merged, PROFILE)
    assert result.decision == "review"
    assert "strict_language_evidence_partial" in {h.code for h in result.review_hits}


def test_entry_level_words_cannot_cancel_a_separate_mandatory_experience_requirement():
    result = FilterEngine().evaluate(vacancy(
        "Deutsch nicht erforderlich. Mindestens 3 Jahre Berufserfahrung erforderlich.",
    ), PROFILE)
    assert "strict_qualification" in {h.code for h in result.rejection_hits}


def test_search_pipeline_separates_snippets_from_confirmed_matches():
    from app.services.search_service import SearchService
    from app.services.source_adapters.base import BaseSourceAdapter
    from app.services.source_adapters.models import AdapterSearchResponse, SourceSearchInput
    from app.services.source_adapters.registry import SourceAdapterRegistry

    class Adapter(BaseSourceAdapter):
        source_id = "strict-test"
        display_name = "Strict fixture"

        def is_enabled(self):
            return True

        def search(self, search_input):
            records = tuple(SourceRecordPreview(
                source_id=self.source_id, source_name=self.display_name, external_id=key, source_reference=key,
                title=title, company=key + " GmbH", location="Berlin", posted_at=utc_now().date().isoformat(),
                detail_url="https://example.org/" + key, raw_payload={"description": BODY + "Deutsch nicht erforderlich."},
                description_complete=complete,
            ) for key, title, complete in [
                ("confirmed", "Lagerhelfer", True), ("snippet", "Verpacker", False),
            ])
            return AdapterSearchResponse(
                source_id=self.source_id, source_name=self.display_name, records=records, total_count=2,
                page=1, page_size=10, raw_payload={},
            )

    service = SearchService(registry=SourceAdapterRegistry(adapters=(Adapter(),)))
    result = service.search(
        search_input=SourceSearchInput(query="Lagerarbeiter", location="Berlin"),
        profile=PROFILE, enrich_with_llm=False,
    )
    assert [item.primary_record.external_id for item in result.hot_results] == ["confirmed"]
    assert [item.primary_record.external_id for item in result.maybe_results] == ["snippet"]
    assert len(result.hidden_filtered_items) == 0


@pytest.mark.parametrize("complete", [True, False])
def test_unknown_language_with_requirements_is_review_never_hot(complete):
    result = FilterEngine().evaluate(vacancy("Ihr Profil: Zuverlässigkeit und Teamfähigkeit.", complete=complete), PROFILE)
    assert result.decision == "review"
    assert "strict_language_unconfirmed" in {h.code for h in result.review_hits}
    assert _assign_bucket(filter_result=result, score=100, negative_hits=()) == "maybe"


@pytest.mark.parametrize("title", ["MFA Arzthelfer", "Helfer Verkauf", "Helfer Sanitärinstallation"])
def test_broad_helfer_query_cannot_confirm_unrelated_job(title):
    profile = replace(PROFILE, search_query_terms=("Helfer",))
    result = FilterEngine().evaluate(vacancy(title=title), profile)
    assert result.hard_reject


@pytest.mark.parametrize("text", [
    "Ihr Profil: gute Deutschkenntnisse  Wünschenswert sind Erfahrungen in der Logistik.",
    "Ihr Profil: Deutschkenntnisse ausreichend für die Verständigung.",
    "Anforderungen: Deutschkenntnisse.",
    "Deutsch A1 ausreichend. Gute Deutschkenntnisse erforderlich.",
    "Grundkenntnisse in Deutsch. Fluent German required.",
])
def test_detects_language_requirements_in_realistic_snippets(text):
    result = FilterEngine().evaluate(vacancy(text, complete=False), replace(PROFILE, strict_match_mode="a1"))
    assert "strict_german_mismatch" in {h.code for h in result.rejection_hits}


def test_optional_experience_does_not_cancel_separate_specialist_requirement():
    result = FilterEngine().evaluate(vacancy(
        "Deutsch nicht erforderlich. Erste Berufserfahrung wünschenswert  Erfahrungen und Kenntnisse als Laminierer.",
    ), PROFILE)
    assert "strict_qualification" in {h.code for h in result.rejection_hits}


def test_polish_no_language_statement_and_independent_commute_are_reviewed():
    result = FilterEngine().evaluate(vacancy(
        "Znajomość języka nie jest wymagana. Własny dojazd do miejsca pracy.", complete=False,
    ), PROFILE)
    assert result.decision == "review"
    codes = {h.code for h in result.review_hits}
    assert "strict_language_unconfirmed" not in codes
    assert "strict_commute_review" in codes


def test_bare_title_is_not_enough_even_for_desired_job():
    canonical = vacancy()
    record = replace(canonical.source_records[0], body_text=None, description_complete=False)
    result = FilterEngine().evaluate(replace(canonical, source_records=(record,)), PROFILE)
    assert "strict_description_unavailable" in {h.code for h in result.rejection_hits}


def test_start_date_uncertainty_remains_visible_without_hard_rejection():
    profile = replace(PROFILE, start_availability_text="01.01.2099")
    result = FilterEngine().evaluate(vacancy("Deutsch nicht erforderlich. Ab sofort."), profile)
    assert result.decision == "review"
    assert "start_date_review" in {h.code for h in result.review_hits}


def test_nearby_workplace_is_excluded_even_if_source_labels_it_berlin():
    result = FilterEngine().evaluate(vacancy(
        "Deutsch nicht erforderlich. Ihr Einsatzort befindet sich in Kleinmachnow.",
    ), PROFILE)
    assert "strict_workplace_mismatch" in {h.code for h in result.rejection_hits}


def test_requirements_word_in_duties_is_not_a_candidate_requirements_section():
    result = FilterEngine().evaluate(vacancy(
        "Bestellungen gemäß den vorgegebenen Anforderungen zusammenstellen.", complete=False,
    ), PROFILE)
    assert "strict_requirements_unavailable" in {h.code for h in result.rejection_hits}


@pytest.mark.parametrize("text", ["Berufserfahrungen erwartet.", "Fundierte Kenntnisse der MS-Office-Anwendungen."])
def test_required_specialist_skills_are_not_assumed(text):
    result = FilterEngine().evaluate(vacancy("Deutsch nicht erforderlich. " + text), PROFILE)
    assert "strict_qualification" in {h.code for h in result.rejection_hits}


def test_driver_b_not_lost_when_unrelated_experience_is_optional():
    result = FilterEngine().evaluate(vacancy(
        "Dein Profil Du besitzt einen gültigen Führerschein der Klasse B und hast Freude am Fahren "
        "Erste Erfahrung im Ausfahren ist von Vorteil, aber keine Voraussetzung.",
        title="Botenfahrer", complete=False,
    ), PROFILE)
    assert result.decision == "review"
    assert "strict_license_unconfirmed" not in {h.code for h in result.rejection_hits}


def test_single_qualification_heading_is_recognized_for_driver():
    result = FilterEngine().evaluate(vacancy(
        "Qualifikation Du hast einen gültigen Führerschein der Klasse B. Zuverlässigkeit.",
        title="Fahrer / Fahrzeugpfleger", complete=False,
    ), PROFILE)
    assert result.decision == "review"


def test_negated_b_does_not_prove_driver_eligibility():
    result = FilterEngine().evaluate(vacancy(
        "Deutsch nicht erforderlich. Kein Führerschein der Klasse B erforderlich.", title="Auslieferungsfahrer",
    ), PROFILE)
    assert result.hard_reject


def test_optional_b_cannot_override_mandatory_ce():
    result = FilterEngine().evaluate(vacancy(
        "Deutsch nicht erforderlich. Führerschein Klasse B von Vorteil. Klasse CE erforderlich.", title="Auslieferungsfahrer",
    ), PROFILE)
    assert result.hard_reject
