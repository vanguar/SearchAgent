"""Shared FastAPI dependency factories for web routes."""

from __future__ import annotations

from app.core.config import Settings
from app.services.intake_agent import IntakeAgentService
from app.services.llm_client import LazyOpenAILLMClient, build_lazy_llm_client
from app.services.match_explainer import MatchExplainer
from app.services.profile_catalog_service import ProfileCatalogService
from app.services.search_service import SearchService
from app.services.summary_service import SummaryService
from app.services.translation_service import TranslationService


def get_settings() -> Settings:
    return Settings()


def get_llm_client() -> LazyOpenAILLMClient | None:
    """Вернуть LLM-клиент если OPENAI_API_KEY задан, иначе None."""
    return build_lazy_llm_client(get_settings())


def get_profile_catalog_service() -> ProfileCatalogService:
    return ProfileCatalogService()


def get_intake_agent() -> IntakeAgentService:
    """
    Фабрика IntakeAgentService с LLM-клиентом если ключ задан.

    Создаётся per-request через FastAPI Depends — избегает хрупкого модульного синглтона.
    IntakeAgentService и все его зависимости (ProfileParser, ProfileValidator)
    легковесны и stateless.
    """
    return IntakeAgentService(llm_client=get_llm_client())


def get_search_service() -> SearchService:
    """Search service с LLM-хелперами если OPENAI_API_KEY задан."""
    llm = get_llm_client()
    translation = TranslationService(helper=llm)
    summary = SummaryService(translation_service=translation, helper=llm)
    explainer = MatchExplainer(llm_client=llm)
    return SearchService(
        translation_service=translation,
        summary_service=summary,
        match_explainer=explainer,
        llm_client=llm,
    )
