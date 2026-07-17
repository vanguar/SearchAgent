from __future__ import annotations

import json
import logging
import threading
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

try:
    import openai as _openai_sdk
    _OPENAI_AVAILABLE = True
except ImportError:
    _openai_sdk = None  # type: ignore[assignment]
    _OPENAI_AVAILABLE = False


class LLMStatus(str, Enum):
    """Последнее известное состояние LLM-интеграции."""

    CONFIGURED = "configured"  # ключ есть, клиент готов
    MISSING_CONFIG = "missing_config"  # OPENAI_API_KEY не задан
    PROVIDER_ERROR = "provider_error"  # последний вызов упал, используется fallback
    EXTRACTION_SUCCEEDED = "extraction_succeeded"  # последний profile extraction успешен


# Модульная переменная — отражает реальный последний исход LLM-вызовов.
# Обновляется build_llm_client() и каждым _call().
# Читается settings-страницей через get_runtime_status().
_runtime_status: LLMStatus = LLMStatus.MISSING_CONFIG


def _set_runtime_status(status: LLMStatus) -> None:
    global _runtime_status
    _runtime_status = status


def get_runtime_status() -> LLMStatus:
    """Вернуть последнее известное состояние LLM для UI/логов."""
    return _runtime_status


def _mark_configured_if_no_history() -> None:
    """Повысить статус до CONFIGURED только из MISSING_CONFIG.

    PROVIDER_ERROR и EXTRACTION_SUCCEEDED отражают исходы реальных вызовов;
    конструирование клиента (происходит на каждый запрос через Depends)
    не должно их затирать — иначе settings-страница скрывает ошибку провайдера.
    """
    if _runtime_status is LLMStatus.MISSING_CONFIG:
        _set_runtime_status(LLMStatus.CONFIGURED)


def _clean_json_response(raw: str) -> str:
    """Strip markdown fences and keep the outer JSON object when present."""
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start:end + 1]
    return cleaned


class LLMClient(Protocol):
    """Граница для опциональных LLM-операций в ProfileParser."""

    def extract_profile_fields(self, text: str) -> dict[str, Any]:
        """Вернуть поля профиля, извлечённые из свободного текста."""

    def extract_profile_fields_v2(self, text: str, prompt_template: str) -> dict[str, Any]:
        """Вернуть полную ProfileExtractionResult-совместимую структуру через переданный промпт."""

    def translate_location_to_de(self, location: str) -> str | None:
        """Перевести название города/региона (рус/укр/англ) в немецкое написание."""

    def translate_roles_to_de(self, roles: list[str]) -> str | None:
        """Перевести список ролей (рус/укр/англ) в немецкие ключевые слова для поиска."""

    def suggest_fallback_keywords(self, role: str, already_tried: list[str]) -> str | None:
        """Предложить дополнительные немецкие ключевые слова для роли, исключая уже использованные."""


class OpenAILLMClient:
    """
    Реальный LLM-клиент через OpenAI API.

    Структурно реализует три протокола:
    - LLMClient         (extract_profile_fields)
    - TranslationHelper (translate_title)
    - SummaryHelper     (summarize)

    При ошибке провайдера — логирует, возвращает None/{} и устанавливает
    _runtime_status = PROVIDER_ERROR, чтобы UI показывал корректное состояние.
    """

    def __init__(self, *, api_key: str, model: str, timeout_seconds: float) -> None:
        if not _OPENAI_AVAILABLE:
            raise RuntimeError("Пакет openai не установлен. Выполните: pip install openai")
        self._client = _openai_sdk.OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=1)
        self._model = model
        self._timeout_seconds = timeout_seconds

    def _call(self, prompt: str, *, max_tokens: int = 512, json_mode: bool = False) -> str | None:
        try:
            logger.info(
                "llm_request_start provider=openai model=%s timeout_seconds=%s max_tokens=%s json_mode=%s",
                self._model,
                self._timeout_seconds,
                max_tokens,
                json_mode,
            )
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            response = self._client.chat.completions.create(
                **kwargs,
            )
            text = response.choices[0].message.content
            if text is None:
                return None
            _set_runtime_status(LLMStatus.CONFIGURED)
            return text.strip()
        except Exception as exc:
            logger.warning(
                "llm_provider_error provider=openai model=%s timeout_seconds=%s error=%s",
                self._model,
                self._timeout_seconds,
                exc,
            )
            _set_runtime_status(LLMStatus.PROVIDER_ERROR)
            return None

    def extract_profile_fields(self, text: str) -> dict[str, Any]:
        """Извлечь поля профиля из свободного русского текста через LLM."""
        prompt = _PROFILE_EXTRACT_PROMPT.format(text=text)
        raw = self._call(prompt, max_tokens=1000, json_mode=True)
        if not raw:
            return {}
        cleaned = _clean_json_response(raw)
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            logger.warning("llm_json_parse_error raw=%r", raw[:200])
        return {}

    def extract_profile_fields_v2(self, text: str, prompt_template: str) -> dict[str, Any]:
        """Full-schema extraction using a caller-supplied prompt template."""
        prompt = prompt_template.format(text=text)
        raw = self._call(prompt, max_tokens=3000, json_mode=True)
        if not raw:
            return {}
        cleaned = _clean_json_response(raw)
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                _set_runtime_status(LLMStatus.EXTRACTION_SUCCEEDED)
                return data
        except json.JSONDecodeError:
            logger.warning("llm_json_parse_error_v2 raw=%r", raw[:300])
        return {}

    def translate_title(self, text: str) -> str | None:
        """Перевести название должности на русский (2-5 слов)."""
        if not text or not text.strip():
            return None
        prompt = _TRANSLATE_TITLE_PROMPT.format(text=text.strip())
        return self._call(prompt, max_tokens=64)

    def summarize(self, text: str) -> str | None:
        """Написать краткое резюме вакансии на русском (1-2 предложения)."""
        if not text or len(text.strip()) < 20:
            return None
        prompt = _SUMMARIZE_VACANCY_PROMPT.format(text=text.strip()[:2000])
        return self._call(prompt, max_tokens=200)

    def translate_location_to_de(self, location: str) -> str | None:
        """Перевести название города/региона (рус/укр/англ) в немецкое написание."""
        if not location or not location.strip():
            return None
        prompt = _TRANSLATE_LOCATION_TO_DE_PROMPT.format(location=location.strip())
        return self._call(prompt, max_tokens=40)

    def translate_roles_to_de(self, roles: list[str]) -> str | None:
        """Перевести список ролей (рус/укр/англ) в немецкие ключевые слова для поиска."""
        if not roles:
            return None
        prompt = _TRANSLATE_ROLES_TO_DE_PROMPT.format(roles=", ".join(roles))
        return self._call(prompt, max_tokens=80)

    def suggest_fallback_keywords(self, role: str, already_tried: list[str]) -> str | None:
        """Предложить дополнительные немецкие ключевые слова, исключая уже использованные."""
        if not role:
            return None
        tried_str = ", ".join(already_tried) if already_tried else "—"
        prompt = _SUGGEST_FALLBACK_KEYWORDS_PROMPT.format(role=role.strip(), already_tried=tried_str)
        return self._call(prompt, max_tokens=60)

    def explain_match(self, *, deterministic_explanation: str, body_text: str) -> str | None:
        """Уточнить детерминированное объяснение, опираясь на текст вакансии."""
        if not body_text or len(body_text.strip()) < 30:
            return None
        prompt = _EXPLAIN_MATCH_PROMPT.format(
            explanation=deterministic_explanation,
            body_text=body_text.strip()[:800],
        )
        return self._call(prompt, max_tokens=150)


class LazyOpenAILLMClient:
    """Lazy OpenAI wrapper: keeps LLM enabled without paying SDK setup on page load."""

    def __init__(self, *, api_key: str, model: str, timeout_seconds: float) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._client: OpenAILLMClient | None = None
        self._lock = threading.Lock()
        _mark_configured_if_no_history()

    def _get_client(self) -> OpenAILLMClient:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                self._client = OpenAILLMClient(
                    api_key=self._api_key,
                    model=self._model,
                    timeout_seconds=self._timeout_seconds,
                )
        return self._client

    def extract_profile_fields(self, text: str) -> dict[str, Any]:
        return self._get_client().extract_profile_fields(text)

    def extract_profile_fields_v2(self, text: str, prompt_template: str) -> dict[str, Any]:
        return self._get_client().extract_profile_fields_v2(text, prompt_template)

    def translate_title(self, text: str) -> str | None:
        return self._get_client().translate_title(text)

    def summarize(self, text: str) -> str | None:
        return self._get_client().summarize(text)

    def translate_location_to_de(self, location: str) -> str | None:
        return self._get_client().translate_location_to_de(location)

    def translate_roles_to_de(self, roles: list[str]) -> str | None:
        return self._get_client().translate_roles_to_de(roles)

    def suggest_fallback_keywords(self, role: str, already_tried: list[str]) -> str | None:
        return self._get_client().suggest_fallback_keywords(role, already_tried)

    def explain_match(self, *, deterministic_explanation: str, body_text: str) -> str | None:
        return self._get_client().explain_match(
            deterministic_explanation=deterministic_explanation,
            body_text=body_text,
        )


def build_llm_client(settings: Settings) -> OpenAILLMClient | None:
    """
    Вернуть OpenAILLMClient если OPENAI_API_KEY задан, иначе None.

    None = детерминированный режим: все сервисы используют локальный fallback.
    Вызов этой функции НЕ сбрасывает _runtime_status после ошибки провайдера —
    статус обновляется только реальными LLM-вызовами или при отсутствии ключа.
    """
    if not settings.openai_api_key:
        _set_runtime_status(LLMStatus.MISSING_CONFIG)
        logger.info("llm_status=missing_config OPENAI_API_KEY не задан, детерминированный режим")
        return None
    # Ключ есть → помечаем как configured, но только если статуса ещё нет:
    # provider_error/extraction_succeeded от реальных вызовов сохраняются.
    _mark_configured_if_no_history()
    return OpenAILLMClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def build_lazy_llm_client(settings: Settings) -> LazyOpenAILLMClient | None:
    """Return an LLM helper that creates the OpenAI SDK client only on first use."""
    if not settings.openai_api_key:
        _set_runtime_status(LLMStatus.MISSING_CONFIG)
        logger.info("llm_status=missing_config OPENAI_API_KEY не задан, детерминированный режим")
        return None
    return LazyOpenAILLMClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


# ---------------------------------------------------------------------------
# Промпты — лаконичные, на русском языке (соответствует целевому UI)
# ---------------------------------------------------------------------------

_PROFILE_EXTRACT_PROMPT = """\
Ты — экстрактор профиля соискателя для поиска вакансий. Твоя задача — понять НАМЕРЕНИЕ, \
а не просто вытащить ключевые слова.

КРИТИЧЕСКИЕ ПРАВИЛА (нарушение = неверный профиль):

1. desired_roles — только роли/работы которые пользователь ХОЧЕТ искать.
2. excluded_roles — роли/работы которые пользователь ЯВНО НЕ хочет.
3. Если роль упоминается после "не интересны", "не хочу", "не рассматриваю", \
"исключить", "без связи с", "не ищу" → это ТОЛЬКО excluded_roles, НИКОГДА не desired_roles.
4. "есть водительские права B" / "права категории B" → driving_license="B". \
НЕ добавляй "Водитель" / "Driver" в desired_roles! Наличие прав ≠ желание работать водителем.
5. "Водитель" в desired_roles допустим ТОЛЬКО если написано: \
"ищу работу водителем", "хочу работать водителем", "курьер/доставка" как желаемая роль.
6. current_city — ТОЛЬКО если явно написано "живу в X", "нахожусь в X", "мой город X", \
"проживаю в X". Список предпочтительных регионов поиска ("Берлин, Гамбург, вся Германия") \
НЕ является current_city!
7. preferred_regions — где хочет ИСКАТЬ работу. Это отдельно от current_city.
8. "физическая работа: нет" / "физическую работу не рассматриваю" → physical_work_ok=false. \
Все склады, производства, физический труд → только excluded_roles.
9. Если профиль явно IT/backend/AI — shift_ok может быть null (не критично).
10. "§24 AufenthG" / "24 параграф" / "временная защита" → legal_status="section_24", \
work_authorized=true.
11. "working proficiency", "intermediate" для английского → english_level="intermediate".
12. "A1", "basic", "базовый" для немецкого → german_level="basic".
13. Не придумывай роли которых нет в тексте. Если поле не ясно — оставь null.

Поля для заполнения (null если нет данных):
- current_country: string или null — страна проживания
- current_city: string или null — город проживания (ТОЛЬКО официальное немецкое написание, \
ТОЛЬКО при явном указании места жительства)
- legal_status: "section_24" | "eu_citizen" | "work_visa" | "other" или null
- work_authorized: boolean или null
- german_level: "none" | "basic" | "intermediate" | "advanced" или null
  (none = не знаю/не владею; basic = A1-A2; intermediate = B1/working; advanced = B2-C2)
- english_level: "none" | "basic" | "intermediate" | "advanced" или null
- desired_roles: список строк или null — ТОЛЬКО желаемые роли (на языке оригинала)
- excluded_roles: список строк или null — нежелательные роли и типы работы
- preferred_regions: список строк или null — СТРОГО официальное немецкое написание \
(["Berlin", "Hamburg", "Rostock", "Deutschland"])
- willing_to_relocate: boolean или null
- shift_ok: boolean или null — null допустим для IT-профилей
- physical_work_ok: boolean или null
- housing_needed: boolean или null
- start_availability: string или null — когда готов начать (НЕ год получения прав/стаж)
- driving_license: "B" | "yes" | "none" или null
- has_car: boolean или null

Текст соискателя:
{text}

Верни только валидный JSON-объект без markdown и пояснений."""

_TRANSLATE_TITLE_PROMPT = """\
Переведи это немецкое название должности на русский язык в 2-5 слов.
Верни только русский перевод без кавычек и пояснений.
Название: {text}"""

_SUMMARIZE_VACANCY_PROMPT = """\
Напиши краткое резюме на русском для немецкой вакансии.
1-2 предложения: что за работа, где, ключевые условия (язык, смены, старт).
Текст вакансии: {text}"""

_EXPLAIN_MATCH_PROMPT = """\
Соискатель ищет работу в Германии. Уточни объяснение в 1 предложении на русском.
Опирайся на текст вакансии, не меняй оценку (подходит/не подходит/осторожно).
Оценка: {explanation}
Текст вакансии: {body_text}"""

_TRANSLATE_LOCATION_TO_DE_PROMPT = """\
Переведи название города или региона на немецкий язык (официальное немецкое написание).
Верни только одно слово/фразу, без пояснений и кавычек.
Примеры: "Берлин" → "Berlin", "Мюнхен" → "München", "Кёльн" → "Köln"
Если это уже немецкое название — верни как есть.
Город: {location}"""

_SUGGEST_FALLBACK_KEYWORDS_PROMPT = """\
Ты помощник по поиску работы в Германии.
Для профессии "{role}" предложи 3-5 немецких ключевых слов для job-бордов.
Не используй уже использованные слова: {already_tried}.
Верни только слова через пробел, без запятых, пояснений и кавычек."""

_TRANSLATE_ROLES_TO_DE_PROMPT = """\
Ты помощник по поиску работы в Германии.
Переведи список профессий/ролей в 2-4 немецких ключевых слова для поиска на job-бордах.
Роли могут быть на русском, украинском или английском.
Верни только слова через пробел, без запятых, без пояснений, без кавычек.
Примеры: "склад логистика" → "lager lagermitarbeiter logistik"
         "курьер, водитель" → "kurier fahrer zusteller"
         "software engineer" → "softwareentwickler developer"
Роли: {roles}"""
