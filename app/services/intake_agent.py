from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models.profiles import SearchProfile, UserProfile
from app.services.intake_models import IntakeAnalysisResult, IntakeSaveResult
from app.services.llm_client import LLMClient, LLMStatus, get_runtime_status
from app.services.profile_mapper import map_to_profile_payload
from app.services.profile_parser import ConservativeProfileParser, extraction_result_to_draft
from app.services.profile_questions import ProfileQuestionGenerator
from app.services.profile_validator import ProfileValidator
from app.services.search_normalizer import normalize_query_from_roles, normalize_search_location_for_profile

try:
    from app.services.profile_llm_extractor import ProfileLLMExtractor
    from app.services.profile_post_processor import process as post_process
    _NEW_PIPELINE_AVAILABLE = True
except ImportError:
    _NEW_PIPELINE_AVAILABLE = False

logger = logging.getLogger(__name__)


class IntakeAgentService:
    """Orchestrates profile intake: LLM extraction → post-processing → validation → persistence."""

    def __init__(
        self,
        *,
        llm_client: LLMClient | None = None,
        validator: ProfileValidator | None = None,
        question_generator: ProfileQuestionGenerator | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._validator = validator or ProfileValidator()
        self._question_generator = question_generator or ProfileQuestionGenerator()
        self._conservative_parser = ConservativeProfileParser()

        # Build LLM extractor if client available
        self._llm_extractor: ProfileLLMExtractor | None = None
        if llm_client is not None and _NEW_PIPELINE_AVAILABLE:
            self._llm_extractor = ProfileLLMExtractor(llm_client)

    def analyze(self, free_text: str, followup_answers: dict[str, str] | None = None) -> IntakeAnalysisResult:
        """Parse free text → validate → return structured result with questions."""
        from app.services.profile_extraction_model import ProfileExtractionResult

        answers = followup_answers or {}

        # ── Primary path: LLM semantic extraction ──────────────────────────────
        extraction = None
        llm_attempted = self._llm_extractor is not None
        if self._llm_extractor is not None:
            extraction = self._llm_extractor.extract(free_text)
        llm_succeeded = extraction is not None

        # ── Fallback: conservative deterministic parser ────────────────────────
        fallback_used = False
        if extraction is None:
            fallback_used = True
            extraction = self._conservative_parser.parse(free_text, answers or None)
        elif answers:
            # Merge followup answers on top of LLM extraction
            data = extraction.model_dump()
            ConservativeProfileParser._apply_followup(data, answers)
            extraction = ProfileExtractionResult.model_validate(data)

        # ── Post-processing: normalization + safety rules ──────────────────────
        if _NEW_PIPELINE_AVAILABLE:
            extraction = post_process(extraction, raw_text_lower=free_text.lower())

        # ── Convert to legacy IntakeProfileDraft for validator/persistence ─────
        draft = extraction_result_to_draft(extraction)

        # ── Validate completeness ──────────────────────────────────────────────
        validation = self._validator.validate(draft)
        questions = self._question_generator.generate(validation.missing_critical_fields)
        warnings = list(validation.consistency_warnings)
        if llm_attempted and fallback_used:
            status = get_runtime_status()
            if status == LLMStatus.PROVIDER_ERROR:
                warnings.append("AI extraction failed / timed out. Использован локальный deterministic fallback.")
            else:
                warnings.append("AI extraction returned no usable data. Использован локальный deterministic fallback.")

        logger.info(
            "profile_intake_analyze text_length=%d llm_attempted=%s llm_succeeded=%s fallback_used=%s "
            "llm_status=%s desired_roles=%s excluded_roles=%s missing_fields=%s",
            len(free_text),
            llm_attempted,
            llm_succeeded,
            fallback_used,
            get_runtime_status().value,
            "|".join(draft.desired_roles),
            "|".join(draft.excluded_roles),
            "|".join(validation.missing_critical_fields),
        )

        return IntakeAnalysisResult(
            draft=validation.normalized_draft,
            missing_fields=validation.missing_critical_fields,
            questions=questions,
            warnings=tuple(warnings),
        )

    def save_confirmed_profile(
        self,
        *,
        free_text: str,
        followup_answers: dict[str, str] | None,
        db: Session,
    ) -> IntakeSaveResult:
        analysis = self.analyze(free_text, followup_answers)
        return self.save_confirmed_analysis(analysis=analysis, free_text=free_text, db=db)

    def save_confirmed_analysis(
        self,
        *,
        analysis: IntakeAnalysisResult,
        free_text: str,
        db: Session,
    ) -> IntakeSaveResult:
        if analysis.missing_fields:
            return IntakeSaveResult(
                saved=False,
                message="Профиль пока нельзя сохранить: нужно заполнить обязательные поля.",
            )

        mapped = map_to_profile_payload(analysis.draft, raw_profile_text=free_text)

        user_profile = db.query(UserProfile).order_by(UserProfile.id.asc()).first()
        if user_profile is None:
            user_profile = UserProfile(display_name=mapped.user_profile.display_name)
            db.add(user_profile)
            db.flush()

        user_profile.display_name = mapped.user_profile.display_name
        user_profile.country_code = mapped.user_profile.country_code
        user_profile.city = mapped.user_profile.city
        user_profile.legal_status = mapped.user_profile.legal_status
        user_profile.work_authorized = mapped.user_profile.work_authorized
        user_profile.german_level = mapped.user_profile.german_level
        user_profile.english_level = mapped.user_profile.english_level
        user_profile.raw_profile_text = mapped.user_profile.raw_profile_text

        search_profile = SearchProfile(user_profile_id=user_profile.id, name=mapped.search_profile.name)
        db.add(search_profile)

        search_profile.is_active = True
        search_profile.desired_roles = mapped.search_profile.desired_roles
        search_profile.excluded_roles = mapped.search_profile.excluded_roles
        search_profile.preferred_locations = mapped.search_profile.preferred_locations
        search_profile.relocation_ready = mapped.search_profile.relocation_ready
        search_profile.shift_ok = mapped.search_profile.shift_ok
        search_profile.physical_work_ok = mapped.search_profile.physical_work_ok
        search_profile.housing_needed = mapped.search_profile.housing_needed
        search_profile.start_availability_text = mapped.search_profile.start_availability_text
        search_profile.driver_license = mapped.search_profile.driver_license
        search_profile.car_available = mapped.search_profile.car_available
        search_profile.no_german_required = mapped.search_profile.no_german_required

        # Save LLM-extracted multi-term search vocabulary
        if analysis.draft.search_query_terms:
            search_profile.search_query_terms = analysis.draft.search_query_terms

        if not search_profile.search_query_de:
            search_profile.search_query_de = normalize_query_from_roles(analysis.draft.desired_roles)
        if not search_profile.search_location_de:
            search_profile.search_location_de = normalize_search_location_for_profile(
                analysis.draft.preferred_regions,
                relocation_ready=analysis.draft.willing_to_relocate,
            )

        if self._llm_client is not None:
            # Prefer first LLM-extracted search term as primary query (already DE/EN).
            # Fall back to translating first desired role only if no terms extracted.
            if search_profile.search_query_terms:
                search_profile.search_query_de = search_profile.search_query_terms[0]
            elif analysis.draft.desired_roles:
                translated_q = self._llm_client.translate_roles_to_de(analysis.draft.desired_roles[:1])
                if translated_q:
                    search_profile.search_query_de = translated_q.strip()
            if analysis.draft.preferred_regions and search_profile.search_location_de:
                translated_l = self._llm_client.translate_location_to_de(analysis.draft.preferred_regions[0])
                if translated_l:
                    search_profile.search_location_de = translated_l.strip()

        db.commit()
        db.refresh(user_profile)
        db.refresh(search_profile)

        return IntakeSaveResult(
            saved=True,
            message="Профиль успешно сохранен.",
            user_profile_id=user_profile.id,
            search_profile_id=search_profile.id,
            mapped_payload=mapped,
        )
