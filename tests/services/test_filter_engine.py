
from app.services.filter_engine import FilterEngine, _classify_body_family
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.normalizer import VacancyNormalizer
from app.services.role_family import RoleFamily
from app.services.scorer import VacancyScorer
from app.services.search_models import SearchProfileContext
from app.services.search_service import _assign_bucket
from app.services.source_adapters.models import SourceRecordPreview


def _build_profile(**overrides: object) -> SearchProfileContext:
    payload = {
        "profile_label": "Основной поиск",
        "profile_source": "saved",
        "note_ru": "Используется сохраненный профиль поиска.",
        "legal_status": "Section 24",
        "work_authorized": True,
        "german_level": "basic",
        "desired_roles": ("склад", "упаковка", "производство"),
        "excluded_roles": (),
        "preferred_locations": (),
        "relocation_ready": True,
        "shift_ok": True,
        "physical_work_ok": True,
    }
    payload.update(overrides)
    return SearchProfileContext(**payload)


def _build_canonical_multi_body(*, title: str, bodies: tuple[str, ...]) -> CanonicalVacancyGroup:
    """Build a CanonicalVacancyGroup with multiple source records, one per body string."""
    normalizer = VacancyNormalizer()
    records = tuple(
        normalizer.normalize_source_record(
            SourceRecordPreview(
                source_id="ba",
                source_name="BA",
                external_id=f"fixture-{i}",
                source_reference=f"fixture-{i}",
                title=title,
                company="Nord Team GmbH",
                location="Berlin, Deutschland",
                posted_at="2026-04-16",
                detail_url=f"https://example.org/jobs/fixture-{i}",
                raw_payload={"description": body},
            )
        )
        for i, body in enumerate(bodies, start=1)
    )
    first = records[0]
    return CanonicalVacancyGroup(
        canonical_key="canonical-fixture-multi",
        normalized_title=first.normalized_title,
        company_name=first.normalized_company,
        location_text=first.normalized_location.normalized_text,
        country_code=first.normalized_location.country_code,
        city=first.normalized_location.city,
        posted_date=first.posted_date,
        language_signals=first.language_signals,
        source_records=records,
        provenance=tuple(r.source_record_key for r in records),
    )


def _build_canonical(*, title: str, body: str, location: str = "Berlin, Deutschland") -> CanonicalVacancyGroup:
    record = VacancyNormalizer().normalize_source_record(
        SourceRecordPreview(
            source_id="ba",
            source_name="BA",
            external_id="fixture-1",
            source_reference="fixture-1",
            title=title,
            company="Nord Team GmbH",
            location=location,
            posted_at="2026-04-16",
            detail_url="https://example.org/jobs/fixture-1",
            raw_payload={"description": body},
        )
    )
    return CanonicalVacancyGroup(
        canonical_key="canonical-fixture",
        normalized_title=record.normalized_title,
        company_name=record.normalized_company,
        location_text=record.normalized_location.normalized_text,
        country_code=record.normalized_location.country_code,
        city=record.normalized_location.city,
        posted_date=record.posted_date,
        language_signals=record.language_signals,
        source_records=(record,),
        provenance=(record.source_record_key,),
    )


def test_filter_engine_rejects_clearly_mismatched_role() -> None:
    engine = FilterEngine()
    profile = _build_profile()
    canonical = _build_canonical(
        title="Softwareentwickler/in",
        body="Python backend development. Mehrjahrige Erfahrung erforderlich.",
    )

    result = engine.evaluate(canonical, profile)

    assert result.decision == "reject"
    assert any(hit.code in ("clear_role_mismatch", "profession_family_mismatch") for hit in result.rejection_hits)


def test_filter_engine_rejects_local_location_mismatch_for_non_relocating_profile() -> None:
    engine = FilterEngine()
    profile = _build_profile(
        desired_roles=("Python Developer",),
        preferred_locations=("München",),
        relocation_ready=False,
    )
    canonical = _build_canonical(
        title="Python Developer",
        body="Build Python backend services.",
        location="Berlin, Deutschland",
    )

    result = engine.evaluate(canonical, profile)

    assert result.decision == "reject"
    assert any(hit.code == "location_mismatch" for hit in result.rejection_hits)


def test_filter_engine_skips_location_mismatch_for_remote_worldwide_profile() -> None:
    """A saved 'Дистанционно' location must be treated as worldwide remote, not a local filter."""
    engine = FilterEngine()
    profile = _build_profile(
        desired_roles=("Python Developer",),
        preferred_locations=("Дистанционно",),
        relocation_ready=False,
    )
    canonical = _build_canonical(
        title="Python Developer",
        body="Build Python backend services.",
        location="Kyiv, Ukraine",
    )

    result = engine.evaluate(canonical, profile)

    assert not any(hit.code == "location_mismatch" for hit in result.rejection_hits)


def test_filter_engine_search_mode_overrides_local_location_filter() -> None:
    """Explicit remote_worldwide mode must bypass location_mismatch even for a local saved location."""
    engine = FilterEngine()
    profile = _build_profile(
        desired_roles=("Python Developer",),
        preferred_locations=("München",),
        relocation_ready=False,
    )
    canonical = _build_canonical(
        title="Python Developer",
        body="Build Python backend services.",
        location="Kyiv, Ukraine",
    )

    local = engine.evaluate(canonical, profile)
    worldwide = engine.evaluate(canonical, profile, search_mode="remote_worldwide")

    assert any(hit.code == "location_mismatch" for hit in local.rejection_hits)
    assert not any(hit.code == "location_mismatch" for hit in worldwide.rejection_hits)


def test_filter_engine_keeps_optional_german_for_no_german_profile() -> None:
    """"German is an asset" means optional — must NOT trigger german_required_mismatch."""
    engine = FilterEngine()
    profile = _build_profile(
        desired_roles=("Python Developer",),
        no_german_required=True,
        preferred_locations=(),
    )
    canonical = _build_canonical(
        title="Python Developer",
        body="Build Python backend services. German is an asset but not required.",
        location="Remote",
    )

    result = engine.evaluate(canonical, profile)

    assert not any(hit.code == "german_required_mismatch" for hit in result.rejection_hits)


def test_filter_engine_rejects_mandatory_german_for_no_german_profile() -> None:
    """A genuine 'German required' must still be rejected for a no_german profile."""
    engine = FilterEngine()
    profile = _build_profile(
        desired_roles=("Python Developer",),
        no_german_required=True,
        preferred_locations=(),
    )
    canonical = _build_canonical(
        title="Python Developer",
        body="Build Python backend services. German is required for this role.",
        location="Remote",
    )

    result = engine.evaluate(canonical, profile)

    assert any(hit.code == "german_required_mismatch" for hit in result.rejection_hits)


def test_filter_engine_rejects_strong_german_requirement_for_low_german_profile() -> None:
    engine = FilterEngine()
    profile = _build_profile(german_level="A1")
    canonical = _build_canonical(
        title="Lagermitarbeiter/in",
        body="Kommissionierung im Lager. Gute Deutschkenntnisse erforderlich.",
    )

    result = engine.evaluate(canonical, profile)

    assert result.decision == "reject"
    assert any(hit.code == "strong_german_mismatch" for hit in result.rejection_hits)


def test_filter_engine_rejects_degree_ausbildung_and_experience_mismatch_for_low_barrier_profile() -> None:
    engine = FilterEngine()
    profile = _build_profile()
    canonical = _build_canonical(
        title="Produktionshelfer/in",
        body=(
            "Abgeschlossene Ausbildung als Fachlagerist erforderlich. "
            "Bachelor oder Studium ist von Vorteil. Mehrjahrige Erfahrung erforderlich."
        ),
    )

    result = engine.evaluate(canonical, profile)

    assert result.decision == "reject"
    rejection_codes = {hit.code for hit in result.rejection_hits}
    assert {"degree_mismatch", "vocational_mismatch", "experience_mismatch"} <= rejection_codes


def test_filter_engine_marks_sponsorship_style_ambiguity_as_reviewable() -> None:
    engine = FilterEngine()
    profile = _build_profile()
    canonical = _build_canonical(
        title="Verpacker/in",
        body="Verpackung im Lager. Work permit and visa questions are discussed individually.",
    )

    result = engine.evaluate(canonical, profile)

    assert result.decision == "review"
    assert any(hit.code == "sponsorship_review" for hit in result.review_hits)


def test_filter_engine_does_not_treat_generic_mitarbeiter_role_as_helper_match() -> None:
    engine = FilterEngine()
    profile = _build_profile()
    canonical = _build_canonical(
        title="Buchhaltungsmitarbeiter/in",
        body="Rechnungen prufen, Buchhaltung und Controlling im Backoffice.",
    )

    result = engine.evaluate(canonical, profile)

    assert result.decision == "reject"
    assert all(hit.code != "helper_family" for hit in result.positive_hits)


def test_filter_engine_does_not_treat_package_manager_as_packaging_role() -> None:
    engine = FilterEngine()
    profile = _build_profile()
    canonical = _build_canonical(
        title="Package Manager",
        body="Manage enterprise software release packages for B2B clients.",
    )

    result = engine.evaluate(canonical, profile)

    assert result.decision == "reject"
    assert all(hit.code != "packaging_family" for hit in result.positive_hits)


def test_filter_engine_rejects_generic_title_with_it_body_for_warehouse_profile() -> None:
    """Generic title + clear IT body signals → profession_family_mismatch for warehouse profile."""
    engine = FilterEngine()
    profile = _build_profile(desired_roles=("склад", "упаковка"))
    canonical = _build_canonical(
        title="Mitarbeiter (m/w/d)",
        body="Softwareentwickler gesucht. Backend Python Django REST API. Frontend React.",
    )

    result = engine.evaluate(canonical, profile)

    assert result.decision == "reject"
    assert any(hit.code == "profession_family_mismatch" for hit in result.rejection_hits)


def test_filter_engine_passes_generic_title_with_neutral_body_for_warehouse_profile() -> None:
    """Generic title + neutral body → no profession_family_mismatch (conservative pass)."""
    engine = FilterEngine()
    profile = _build_profile(desired_roles=("склад", "упаковка"))
    canonical = _build_canonical(
        title="Mitarbeiter (m/w/d)",
        body="Allgemeine Lagerarbeiten. Koerperlich belastbar. Deutsch von Vorteil.",
    )

    result = engine.evaluate(canonical, profile)

    assert not any(hit.code == "profession_family_mismatch" for hit in result.rejection_hits)


# ---------------------------------------------------------------------------
# _classify_body_family — regression tests for early-return bug fix
# ---------------------------------------------------------------------------

def test_classify_body_family_detects_signal_in_second_record() -> None:
    """First body is neutral, second body has strong IT signal → IT detected (not GENERIC)."""
    canonical = _build_canonical_multi_body(
        title="Mitarbeiter (m/w/d)",
        bodies=(
            "Allgemeine Taetigkeiten im Buero.",            # neutral — no strong signal
            "Wir suchen einen erfahrenen Softwareentwickler fuer unser Team.",  # IT signal
        ),
    )
    assert _classify_body_family(canonical) == RoleFamily.IT


def test_classify_body_family_returns_generic_when_no_body_has_signal() -> None:
    """No body contains a strong signal → GENERIC."""
    canonical = _build_canonical_multi_body(
        title="Mitarbeiter (m/w/d)",
        bodies=(
            "Allgemeine Taetigkeiten. Koerperlich belastbar.",
            "Gute Arbeitszeiten und Team.",
        ),
    )
    assert _classify_body_family(canonical) == RoleFamily.GENERIC


def test_classify_body_family_detects_signal_in_first_record() -> None:
    """Strong signal in first body → correct family returned immediately."""
    canonical = _build_canonical_multi_body(
        title="Mitarbeiter (m/w/d)",
        bodies=(
            "Einsatz in der Pflege und Pflegedokumentation erforderlich.",  # HEALTHCARE signal
            "Allgemeine Bueroarbeit.",
        ),
    )
    assert _classify_body_family(canonical) == RoleFamily.HEALTHCARE


def test_classify_body_family_kitchen_kuchenhilfe_token() -> None:
    """kuchenhilfe token correctly identifies KITCHEN family in body text."""
    canonical = _build_canonical_multi_body(
        title="Mitarbeiter (m/w/d)",
        bodies=("Wir suchen eine Kuchenhilfe fuer unser Restaurant.",),
    )
    assert _classify_body_family(canonical) == RoleFamily.KITCHEN


def test_classify_body_family_returns_generic_for_empty_bodies() -> None:
    """All bodies are empty → GENERIC."""
    canonical = _build_canonical_multi_body(
        title="Mitarbeiter (m/w/d)",
        bodies=("",),
    )
    assert _classify_body_family(canonical) == RoleFamily.GENERIC


# ---------------------------------------------------------------------------
# no_german_required profile flag — hard filter for any German requirement
# ---------------------------------------------------------------------------

def test_no_german_required_rejects_b1_level() -> None:
    """B1 Deutsch is not caught by strong_german rule but rejected by no_german_required."""
    engine = FilterEngine()
    profile = _build_profile(german_level=None, no_german_required=True)
    canonical = _build_canonical(
        title="Lagermitarbeiter",
        body="Wir suchen Mitarbeiter fuer unser Lager. B1 Deutsch erforderlich.",
    )
    result = engine.evaluate(canonical, profile)
    assert result.decision == "reject"
    assert any(hit.code == "german_required_mismatch" for hit in result.rejection_hits)


def test_no_german_required_rejects_deutschkenntnisse_with_mandatory_marker() -> None:
    """'Deutschkenntnisse' + mandatory marker → rejected when no_german_required=True."""
    engine = FilterEngine()
    profile = _build_profile(german_level=None, no_german_required=True)
    canonical = _build_canonical(
        title="Verpacker",
        body="Verpackungsarbeiten im Lager. Deutschkenntnisse werden vorausgesetzt.",
    )
    result = engine.evaluate(canonical, profile)
    assert result.decision == "reject"
    assert any(hit.code == "german_required_mismatch" for hit in result.rejection_hits)


def test_no_german_required_does_not_reject_deutschkenntnisse_von_vorteil() -> None:
    """'Deutschkenntnisse von Vorteil' is soft preference — must NOT be rejected."""
    engine = FilterEngine()
    profile = _build_profile(german_level=None, no_german_required=True)
    canonical = _build_canonical(
        title="Lagerhelfer",
        body="Lagerarbeiten. Deutschkenntnisse von Vorteil.",
    )
    result = engine.evaluate(canonical, profile)
    assert not any(hit.code == "german_required_mismatch" for hit in result.rejection_hits)


def test_no_german_required_does_not_reject_deutschkenntnisse_ein_plus() -> None:
    """'Deutschkenntnisse sind ein Plus' is soft — must NOT be rejected."""
    engine = FilterEngine()
    profile = _build_profile(german_level=None, no_german_required=True)
    canonical = _build_canonical(
        title="Produktionshelfer",
        body="Produktion. Deutschkenntnisse sind ein Plus.",
    )
    result = engine.evaluate(canonical, profile)
    assert not any(hit.code == "german_required_mismatch" for hit in result.rejection_hits)


def test_no_german_required_rejects_gute_deutschkenntnisse() -> None:
    """'Gute Deutschkenntnisse' (qualified) → rejected (adjective signals requirement)."""
    engine = FilterEngine()
    profile = _build_profile(german_level=None, no_german_required=True)
    canonical = _build_canonical(
        title="Kommissionierer",
        body="Lager- und Kommissionierarbeiten. Gute Deutschkenntnisse erforderlich.",
    )
    result = engine.evaluate(canonical, profile)
    assert result.decision == "reject"
    assert any(hit.code == "german_required_mismatch" for hit in result.rejection_hits)


def test_no_german_required_rejects_arbeitssprache_deutsch() -> None:
    engine = FilterEngine()
    profile = _build_profile(german_level=None, no_german_required=True)
    canonical = _build_canonical(
        title="Kommissionierer",
        body="Arbeitssprache Deutsch. Schichtarbeit moeglich.",
    )
    result = engine.evaluate(canonical, profile)
    assert result.decision == "reject"
    assert any(hit.code == "german_required_mismatch" for hit in result.rejection_hits)


def test_no_german_required_does_not_reject_deutsch_von_vorteil() -> None:
    """'Deutsch von Vorteil' is NOT a hard requirement — must not be rejected."""
    engine = FilterEngine()
    profile = _build_profile(german_level=None, no_german_required=True)
    canonical = _build_canonical(
        title="Lagerhelfer",
        body="Allgemeine Lagerarbeiten. Deutsch von Vorteil aber nicht notwendig.",
    )
    result = engine.evaluate(canonical, profile)
    assert not any(hit.code == "german_required_mismatch" for hit in result.rejection_hits)


def test_no_german_required_does_not_reject_when_low_language_signal_overrides() -> None:
    """low_language_signal (ohne Deutsch / Grundkenntnisse) overrides german_any_required."""
    engine = FilterEngine()
    profile = _build_profile(german_level=None, no_german_required=True)
    canonical = _build_canonical(
        title="Warehouse Helper",
        body="Ohne Deutsch moeglich. Grundkenntnisse in Deutsch reichen. Deutschkenntnisse nicht zwingend.",
    )
    result = engine.evaluate(canonical, profile)
    assert not any(hit.code == "german_required_mismatch" for hit in result.rejection_hits)


def test_no_german_required_does_not_reject_nicht_notwendig() -> None:
    """'Deutschkenntnisse sind ein Plus aber nicht notwendig' — negation must NOT be caught.

    Regression for the removed 'notwendig' from the contextual pattern:
    if someone re-adds notwendig to the mandatory-marker list, this test will catch it.
    """
    engine = FilterEngine()
    profile = _build_profile(german_level=None, no_german_required=True)
    canonical = _build_canonical(
        title="Lagerhelfer",
        body="Lagerarbeiten. Deutschkenntnisse sind ein Plus aber nicht notwendig.",
    )
    result = engine.evaluate(canonical, profile)
    assert not any(hit.code == "german_required_mismatch" for hit in result.rejection_hits)



def test_no_german_required_flag_off_does_not_filter() -> None:
    """When no_german_required=False, german_any_required alone does NOT reject."""
    engine = FilterEngine()
    profile = _build_profile(german_level=None, no_german_required=False)
    canonical = _build_canonical(
        title="Lagerhelfer",
        body="Deutschkenntnisse erforderlich.",
    )
    result = engine.evaluate(canonical, profile)
    assert not any(hit.code == "german_required_mismatch" for hit in result.rejection_hits)


# ---------------------------------------------------------------------------
# IT profile matching — German titles and language requirements
# ---------------------------------------------------------------------------

def _build_it_profile(**overrides: object) -> SearchProfileContext:
    payload = {
        "profile_label": "Python developer / Backend developer",
        "profile_source": "saved",
        "note_ru": "Используется сохраненный профиль поиска.",
        "legal_status": "section_24",
        "work_authorized": True,
        "german_level": "A1",
        "english_level": "intermediate",
        "desired_roles": (
            "Python Developer",
            "Backend Developer",
            "FastAPI Developer",
            "AI Agent Developer",
            "Automation Developer",
        ),
        "excluded_roles": (
            "Warehouse",
            "Production",
            "manual physical work",
            "low-skilled non-IT jobs",
        ),
        "preferred_locations": (),
        "relocation_ready": True,
        "shift_ok": None,
        "physical_work_ok": False,
        "no_german_required": False,
        "search_query_terms": (
            "Python Entwickler",
            "Backend Entwickler",
            "FastAPI Entwickler",
            "Python Developer",
            "Backend Developer",
            "API Entwickler",
        ),
    }
    payload.update(overrides)
    return SearchProfileContext(**payload)


def _score_bucket(canonical: CanonicalVacancyGroup, profile: SearchProfileContext) -> tuple[str, bool, int]:
    engine = FilterEngine()
    filter_result = engine.evaluate(canonical, profile)
    score_result = VacancyScorer(filter_engine=engine).score(
        canonical,
        profile,
        filter_result=filter_result,
    )
    return _assign_bucket(filter_result=filter_result, score=score_result.score), filter_result.hard_reject, score_result.score


def test_python_entwickler_is_relevant_for_python_developer_profile() -> None:
    canonical = _build_canonical(title="Python Entwickler", body="Backend APIs mit FastAPI und Python.")
    bucket, hard_reject, _score = _score_bucket(canonical, _build_it_profile())
    assert hard_reject is False
    assert bucket in {"hot", "maybe"}


def test_backend_entwickler_is_relevant_for_python_developer_profile() -> None:
    canonical = _build_canonical(title="Backend Entwickler", body="REST APIs, Python Services und Datenbanken.")
    bucket, hard_reject, _score = _score_bucket(canonical, _build_it_profile())
    assert hard_reject is False
    assert bucket in {"hot", "maybe"}


def test_softwareentwickler_python_is_relevant_for_python_developer_profile() -> None:
    canonical = _build_canonical(title="Softwareentwickler Python", body="Softwareentwicklung mit Python und API Integration.")
    bucket, hard_reject, _score = _score_bucket(canonical, _build_it_profile())
    assert hard_reject is False
    assert bucket in {"hot", "maybe"}


def test_fastapi_entwickler_is_relevant_for_python_developer_profile() -> None:
    canonical = _build_canonical(title="FastAPI Entwickler", body="Python Backend, REST APIs und PostgreSQL.")
    bucket, hard_reject, _score = _score_bucket(canonical, _build_it_profile())
    assert hard_reject is False
    assert bucket in {"hot", "maybe"}


def test_german_language_it_posting_without_explicit_b2_c1_is_not_hard_rejected() -> None:
    canonical = _build_canonical(
        title="Python Entwickler",
        body="Wir suchen dich fur unser Backend Team. Die Anzeige ist auf Deutsch geschrieben.",
    )
    result = FilterEngine().evaluate(canonical, _build_it_profile())
    assert result.hard_reject is False
    assert not any(hit.code == "strong_german_mismatch" for hit in result.rejection_hits)


def test_deutschkenntnisse_von_vorteil_is_not_hard_rejected_for_it_profile() -> None:
    canonical = _build_canonical(
        title="Backend Entwickler",
        body="Python APIs. Deutschkenntnisse von Vorteil, Englisch im Team moglich.",
    )
    result = FilterEngine().evaluate(canonical, _build_it_profile())
    assert result.hard_reject is False


def test_deutsch_c1_erforderlich_is_rejected_or_lowered_for_a1_it_profile() -> None:
    canonical = _build_canonical(
        title="Python Entwickler",
        body="Python Backend Entwicklung. Deutsch C1 erforderlich.",
    )
    bucket, hard_reject, score = _score_bucket(canonical, _build_it_profile(german_level="A1"))
    assert hard_reject is True or bucket == "rejected" or score < 45


def test_it_profile_excluded_roles_do_not_match_long_body_noise() -> None:
    canonical = _build_canonical(
        title="Python Backend Developer",
        body=(
            "Build Python services and APIs. The company has senior engineers, "
            "support teams, sales teams, and English communication in other departments."
        ),
        location="Worldwide",
    )
    profile = _build_it_profile(
        excluded_roles=(
            "Senior-only",
            "Lead",
            "Advanced English",
            "Fluent English",
            "Sales",
            "Support",
        ),
        preferred_locations=("worldwide remote",),
        relocation_ready=False,
    )

    result = FilterEngine().evaluate(canonical, profile)
    rejection_codes = {hit.code for hit in result.rejection_hits}

    assert "excluded_role" not in rejection_codes
    assert "clear_role_mismatch" not in rejection_codes


def test_it_profile_excluded_roles_still_match_title() -> None:
    canonical = _build_canonical(
        title="Senior Python Backend Lead",
        body="Python services and APIs.",
        location="Worldwide",
    )
    profile = _build_it_profile(
        excluded_roles=("Senior", "Lead"),
        preferred_locations=("worldwide remote",),
        relocation_ready=False,
    )

    result = FilterEngine().evaluate(canonical, profile)

    assert any(hit.code == "excluded_role" for hit in result.rejection_hits)


def test_warehouse_automation_software_developer_is_not_manual_warehouse_reject() -> None:
    canonical = _build_canonical(
        title="Warehouse Automation Software Developer",
        body="Python automation software for warehouse robotics and backend APIs.",
    )
    result = FilterEngine().evaluate(canonical, _build_it_profile())
    rejection_codes = {hit.code for hit in result.rejection_hits}
    assert "profession_family_mismatch" not in rejection_codes
    assert "clear_role_mismatch" not in rejection_codes


def test_production_ml_engineer_is_not_manual_production_reject() -> None:
    canonical = _build_canonical(
        title="Production ML Engineer",
        body="Machine learning models, Python pipelines and production inference services.",
    )
    result = FilterEngine().evaluate(canonical, _build_it_profile())
    rejection_codes = {hit.code for hit in result.rejection_hits}
    assert "profession_family_mismatch" not in rejection_codes
    assert "clear_role_mismatch" not in rejection_codes


def test_produktentwickler_without_software_python_or_api_is_not_hot() -> None:
    canonical = _build_canonical(
        title="Produktentwickler",
        body="Produktentwicklung fur Hardware-Komponenten, Dokumentation und Lieferantenabstimmung.",
    )
    bucket, hard_reject, _score = _score_bucket(canonical, _build_it_profile())
    assert hard_reject is False
    assert bucket != "hot"


def test_remote_worldwide_profile_does_not_hard_reject_non_germany_location() -> None:
    canonical = _build_canonical(
        title="Python Backend Developer",
        body="Remote Python backend role, FastAPI and APIs.",
        location="Remote, Worldwide",
    )
    profile = _build_it_profile(
        preferred_locations=("worldwide remote", "Deutschland", "EU", "UK", "USA", "Canada"),
        relocation_ready=False,
    )

    result = FilterEngine().evaluate(canonical, profile)

    rejection_codes = {hit.code for hit in result.rejection_hits}
    assert "location_mismatch" not in rejection_codes


def test_freelance_writer_is_rejected_for_python_backend_profile_even_with_api_words() -> None:
    canonical = _build_canonical(
        title="Freelance Writer",
        body=(
            "Remote content writing role. Articles may cover API, REST backend, "
            "software platforms and technical topics."
        ),
        location="Worldwide",
    )
    profile = _build_it_profile(
        preferred_locations=("worldwide remote", "Deutschland", "EU", "UK", "USA", "Canada"),
        relocation_ready=False,
    )

    bucket, hard_reject, _score = _score_bucket(canonical, profile)
    result = FilterEngine().evaluate(canonical, profile)

    assert hard_reject is True
    assert bucket == "rejected"
    assert any(hit.code == "non_it_writing_title_mismatch" for hit in result.rejection_hits)
