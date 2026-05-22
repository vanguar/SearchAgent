"""LLM-first profile extractor.

Primary path for Profile Intake. Calls LLM, validates response with Pydantic,
returns ProfileExtractionResult.

On LLM failure returns None so the caller can use the conservative fallback.
"""
from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from app.services.llm_client import LLMClient
from app.services.profile_extraction_model import ProfileExtractionResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """\
Ты — семантический экстрактор профиля соискателя. Твоя цель — понять НАМЕРЕНИЕ \
пользователя, а не просто вытащить ключевые слова. Читай весь текст целиком, учитывай \
контекст и отрицания, прежде чем заполнять любое поле.

══════════════════════════════════════════════════════════════════
КРИТИЧЕСКИЕ ПРАВИЛА (нарушение = неверный профиль)
══════════════════════════════════════════════════════════════════

РОЛИ И НАМЕРЕНИЕ:
1. desired_roles — только роли, которые пользователь ХОЧЕТ искать (явное позитивное намерение).
2. excluded_roles — роли/сферы, которые пользователь явно НЕ хочет.
3. Если роль/сфера появляется после "не интересны", "не хочу", "не рассматриваю", \
"без связи с", "исключить", "физическая работа: нет", "ручная физическая работа" → \
ТОЛЬКО excluded_roles. Никогда не desired_roles.
4. "Не интересны роли без связи с Python" → excluded_role_families включает non_it.
5. "Склад, производство" в контексте нежелания → excluded_roles, НЕ desired_roles.
6. Упоминание прошлого опыта ("работал водителем") ≠ желание работать сейчас. \
Если контекст "ищу Python backend" → Driver/Водитель не в desired_roles.
7. "warehouse automation software developer" → это IT-роль, НЕ Warehouse labor.
8. "production ML engineer" → это ML/IT, НЕ производство.

ВОДИТЕЛЬСКИЕ ПРАВА VS РАБОТА ВОДИТЕЛЕМ:
9. "есть права B" / "права категории B" / "водительское удостоверение B" → \
driving_license="B". НЕ добавляй "Водитель"/"Driver" в desired_roles!
10. "Водитель" / "Driver" / "Fahrer" в desired_roles допустим ТОЛЬКО если написано: \
"ищу работу водителем", "хочу работать водителем", "ищу курьера", "delivery driver".
11. В evidence_by_field["driving_license"] напиши точную цитату из текста.

ЛОКАЦИЯ:
12. current_city — ТОЛЬКО если явно написано "живу в X", "нахожусь в X", \
"мой город X", "проживаю в X". \
Список предпочтительных регионов поиска ("Берлин, Гамбург, вся Германия") ≠ current_city!
13. preferred_regions — где хочет ИСКАТЬ работу. Строго официальное немецкое написание: \
Berlin, Hamburg, München, Rostock, Deutschland.
14. Если пользователь указал конкретный город проживания ("живу в Трибзесе") + \
хочет искать в других местах → current_city="Tribsees", preferred_regions=["Berlin", "Hamburg"].
15. В evidence_by_field["current_city"] напиши цитату или null если нет явного указания.

ФИЗИЧЕСКИЙ ТРУД:
16. "физическая работа: нет" / "физическую работу не рассматриваю" / "без физического труда" → \
physical_work_allowed=false.
17. При physical_work_allowed=false: excluded_role_families должен содержать \
"warehouse", "production_manufacturing", "manual_labor".
18. При physical_work_allowed=false: excluded_roles должен содержать \
соответствующие физические роли/сферы.

ЯЗЫКИ:
19. "A1", "basic", "базовый" → german_level="basic".
20. "working proficiency", "intermediate", "B1", "рабочий уровень" → english_level="intermediate".
21. "не знаю немецкий", "нет немецкого" → german_level="none".
22. §24 AufenthG / "24 параграф" / "временная защита" → \
legal_status="section_24", work_authorization=true.

СМЕННЫЙ ГРАФИК:
23. Для IT/backend/AI профилей shift_work_allowed=null допустим (не добавляй в questions_needed).
24. Для warehouse/production/logistics сменный график критичен → добавь в questions_needed \
если не указан.

ПОИСКОВЫЕ КЛЮЧЕВЫЕ СЛОВА:
25. search_query_terms — ТОЛЬКО на немецком и/или английском языке. \
Это ключевые слова для немецких порталов вакансий (Arbeitsagentur, Adzuna, EURES, StepStone). \
Пример для Python-разработчика: ["Python Entwickler", "Backend Developer", "Software Engineer", "FastAPI"]. \
Пример для складского: ["Lagerist", "Lagerhelfer", "Kommissionierer", "Warehouse Worker"]. \
НЕ использовать русские слова в search_query_terms!

ВОПРОСЫ:
26. questions_needed — только действительно неизвестные КРИТИЧЕСКИЕ поля. \
Не добавляй вопросы про смены если профиль явно IT.
27. Не спрашивай про жильё если profil явно remote без релокации.
28. Не спрашивай про машину если профиль не требует вождения.

══════════════════════════════════════════════════════════════════
JSON СХЕМА ОТВЕТА
══════════════════════════════════════════════════════════════════

Верни ТОЛЬКО JSON-объект с этими полями (null/[] если нет данных):

{{
  "current_country": "string | null",
  "current_city": "string | null  ← ТОЛЬКО при явном указании места жительства",
  "current_location_raw": "string | null  ← сырая фраза из текста о локации",
  "legal_status": "section_24 | eu_citizen | work_visa | other | null",
  "work_authorization": "boolean | null",
  "german_level": "none | basic | intermediate | advanced | null",
  "english_level": "none | basic | intermediate | advanced | null",
  "native_languages": ["string"],
  "desired_roles": ["string  ← ТОЛЬКО желаемые, не упомянутые в отрицательном контексте"],
  "excluded_roles": ["string  ← нежелательные роли/сферы"],
  "desired_role_families": ["it_software|ai_automation|backend|scraping_data|telegram_bots|fullstack|frontend_only|devops_infra|ml_data|driving_delivery|warehouse|production_manufacturing|manual_labor|office_admin|customer_service|healthcare|education|hospitality|construction"],
  "excluded_role_families": ["same values as above"],
  "preferred_regions": ["официальные немецкие названия: Berlin, Hamburg, Rostock, Deutschland..."],
  "remote_allowed": "boolean | null",
  "international_remote_allowed": "boolean | null",
  "relocation_ready": "boolean | null",
  "work_modes": ["remote|hybrid|office"],
  "employment_types": ["full-time|part-time|project-based|freelance|contract"],
  "shift_work_allowed": "boolean | null",
  "physical_work_allowed": "boolean | null",
  "housing_needed": "boolean | null",
  "availability": "string | null  ← 'immediately' | 'from May' | НЕ год получения прав",
  "driving_license": "B | yes | none | null",
  "has_car": "boolean | null",
  "core_stack": ["string  ← технологии/языки/фреймворки"],
  "tools": ["string"],
  "project_types": ["string"],
  "hard_filters": ["string  ← жёсткие must-exclude"],
  "soft_preferences": ["string"],
  "search_query_terms": ["string  ← ключевые слова для поиска вакансий НА НЕМЕЦКОМ И АНГЛИЙСКОМ (напр. 'Python Entwickler', 'Backend Developer', 'Lagerist') — для German job boards (Arbeitsagentur, Adzuna, EURES)"],
  "negative_query_terms": ["string  ← исключать из поиска, тоже на немецком/английском"],
  "profile_summary": "string | null  ← 1-2 предложения о профиле",
  "questions_needed": ["string  ← ТОЛЬКО критичные вопросы о реально неизвестных полях"],
  "confidence_by_field": {{"field": 0.0-1.0}},
  "evidence_by_field": {{"field": "цитата из текста или null"}}
}}

══════════════════════════════════════════════════════════════════
ТЕКСТ СОИСКАТЕЛЯ:
{text}
══════════════════════════════════════════════════════════════════

Верни только валидный JSON без markdown, комментариев и объяснений."""


class ProfileLLMExtractor:
    """LLM-first profile extractor. Primary path in Profile Intake pipeline."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def extract(self, text: str) -> ProfileExtractionResult | None:
        """Extract structured profile from free text via LLM.

        Returns None on LLM failure so the caller can use conservative fallback.
        """
        raw_dict = self._llm.extract_profile_fields_v2(text, _EXTRACTION_PROMPT)
        if not raw_dict:
            logger.warning("profile_llm_extractor: LLM returned empty result")
            return None

        try:
            result = ProfileExtractionResult.model_validate(raw_dict)
            logger.info(
                "profile_llm_extractor: extracted desired=%d excluded=%d",
                len(result.desired_roles),
                len(result.excluded_roles),
            )
            return result
        except ValidationError as exc:
            logger.warning("profile_llm_extractor: pydantic validation failed: %s", exc)
            # Attempt lenient parse — strip unknown/invalid fields and retry
            cleaned = {k: v for k, v in raw_dict.items() if v not in (None, "", [])}
            try:
                return ProfileExtractionResult.model_validate(cleaned)
            except ValidationError:
                return None
