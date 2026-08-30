from __future__ import annotations

import re
import unicodedata

from app.services.search_models import RuleHit, SearchProfileContext, normalize_profile_text
from app.services.normalization_models import CanonicalVacancyGroup

AI_TOOLS_PROFILE_NAME = "AI Automation / Claude Code / Codex"
AI_TOOLS_DESIRED_ROLES: tuple[str, ...] = (
    "AI Automation Specialist",
    "AI Tools / LLM / Agents",
)
AI_TOOLS_SEARCH_QUERY_TERMS: tuple[str, ...] = (
    "AI Automation Specialist",
    "AI Workflow Automation",
    "AI Tools Specialist",
    "AI Implementation Specialist",
    "AI Integration Specialist",
    "LLM Automation Specialist",
    "AI Agent Builder",
    "Prompt Engineer",
    "Generative AI Specialist",
    "Claude Code",
    "OpenAI Codex",
    "AI Coding Agent",
    "n8n AI Automation",
    "KI Automatisierung",
    "специалист по нейросетям",
    "AI интегратор",
)
AI_TOOLS_WESTERN_QUERY_TRANSLATIONS: dict[str, str] = {
    "специалист по нейросетям": "AI Tools Specialist",
    "ai интегратор": "AI Integration Specialist",
}
AI_TOOLS_PROFILE_NOTES = (
    "Фокус: практическая работа с LLM, AI coding agents, prompt engineering, "
    "AI/no-code/low-code automation и интеграция готовых AI-сервисов. "
    "Не классический senior software-engineering профиль; английский не должен "
    "быть обязательным, немецкий допустим без требования или на уровне A1/basic."
)

_SPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^0-9a-zа-яёіїєґ]+")

_AI_TOOLS_PROFILE_SIGNATURE = frozenset(
    normalize_profile_text(value) for value in AI_TOOLS_DESIRED_ROLES
)
_AI_TOOLS_QUERY_SIGNATURE = frozenset(
    normalize_profile_text(value)
    for value in ("Claude Code", "OpenAI Codex", "AI Agent Builder", "Prompt Engineer")
)

_AI_TOOLS_RULES: tuple[tuple[str, str, int, tuple[str, ...]], ...] = (
    (
        "ai_coding_brands",
        "упоминаются Claude Code, Codex или AI coding agents",
        32,
        (
            r"\bclaude code\b", r"\banthropic claude\b", r"\bopenai codex\b",
            r"\bcodex cli\b", r"\bai coding agents?\b", r"\bcoding agents?\b",
            r"\bagentic coding(?: tools?)?\b", r"\bai coding assistants?\b",
        ),
    ),
    (
        "ai_agents_workflows",
        "работа с AI-агентами и agentic workflows",
        28,
        (
            r"\bai agents?\b", r"\bagent builder\b", r"\bagentic workflows?\b",
            r"\bagentic ai\b", r"\bllm agents?\b", r"\bai orchestration\b",
            r"\bai агенты\b", r"\bагенты ии\b", r"\bсоздани\w* ai агент\w*\b",
        ),
    ),
    (
        "ai_automation_workflows",
        "работа с AI, LLM и автоматизацией",
        28,
        (
            r"\bai automation\b", r"\bai workflow(?: automation)?\b",
            r"\bllm automation\b", r"\bautomation with ai\b", r"\bn8n\b.{0,40}\b(?:ai|llm|gpt|claude)\b",
            r"\b(?:ai|llm|gpt|claude)\b.{0,40}\bn8n\b", r"\bzapier\b.{0,30}\bai\b",
            r"\bmake\b.{0,30}\bautomation\b", r"\bki automatisierung\b",
            r"\bautomatisierung mit ki\b", r"\bai автоматизаци\w*\b",
            r"\bавтоматизаци\w* (?:с )?(?:нейросет\w*|ии)\b",
            r"\bспециалист по автоматизаци\w* ии\b",
        ),
    ),
    (
        "ai_tools_implementation",
        "внедрение и интеграция AI-инструментов",
        24,
        (
            r"\bai tools? specialist\b", r"\bai implementation specialist\b",
            r"\bai integration specialist\b", r"\bai operations specialist\b",
            r"\bai enablement\b", r"\bai adoption specialist\b", r"\bai productivity specialist\b",
            r"\bgenerative ai specialist\b", r"\bgenai specialist\b", r"\bllm specialist\b",
            r"\bprompt engineer(?:ing)?\b", r"\bprompt specialist\b",
            r"\bki spezialist\b", r"\bki tools\b", r"\bki integration\b",
            r"\bспециалист по (?:ии|ai|llm|нейросет\w*)\b", r"\bпромпт инженер\b",
            r"\bвнедрени\w* ии\b", r"\bинтеграци\w* ии\b", r"\bai интегратор\b",
            r"\bнейросет\w* для бизнеса\b",
        ),
    ),
    (
        "ai_low_code",
        "подходят no-code/low-code AI-инструменты",
        10,
        (
            r"\bno code\b", r"\blow code\b", r"\bбез кода\b",
            r"\bno code автоматизаци\w*\b", r"\blow code автоматизаци\w*\b",
        ),
    ),
    (
        "ai_basic_technical_fit",
        "достаточно базовых API или scripting навыков",
        5,
        (
            r"\bbasic python\b", r"\bsome scripting experience\b", r"\bbasic scripting\b",
            r"\bbasic api knowledge\b", r"\bwork with apis\b", r"\bwebhooks?\b",
        ),
    ),
)

_CLASSIC_ENGINEERING_TITLE_PATTERNS: tuple[str, ...] = (
    r"\bsenior (?:software|backend|frontend|full stack|fullstack|java|python|react|android|ios) (?:engineer|developer)\b",
    r"\b(?:python|java|c|c sharp|net|react|android|ios) developer\b",
    r"\bml research scientist\b",
    r"\bdata scientist\b",
)
_DEEP_ENGINEERING_REQUIREMENT_PATTERNS: tuple[str, ...] = (
    r"\b(?:3|4|5|6|7|8|9|10)\+? years? (?:of )?(?:commercial |professional )?(?:software )?(?:development|engineering|experience)\b",
    r"\b(?:strong|advanced|deep) (?:algorithms?|data structures?|system design)\b",
    r"\bcomputer science (?:degree|fundamentals?) (?:is )?(?:required|mandatory)\b",
    r"\bproduction level coding expertise\b", r"\bexpert (?:java|c\+\+|c sharp|c#|react|python)\b",
    r"\b(?:coding|algorithmic) interview\b", r"\bdeep software engineering background\b",
    r"\bphd\b.{0,50}\b(?:mathematics|machine learning|statistics)\b",
    r"\badvanced (?:mathematics|statistics|ml theory)\b",
)


def normalize_multilingual_text(value: str | None) -> str:
    folded = unicodedata.normalize("NFKD", value or "").casefold()
    without_marks = "".join(character for character in folded if not unicodedata.combining(character))
    return _SPACE_RE.sub(" ", _NON_WORD_RE.sub(" ", without_marks)).strip()


def is_ai_tools_profile(profile: SearchProfileContext) -> bool:
    desired = frozenset(normalize_profile_text(value) for value in profile.desired_roles)
    if _AI_TOOLS_PROFILE_SIGNATURE.issubset(desired):
        return True
    queries = frozenset(normalize_profile_text(value) for value in profile.search_query_terms)
    classic_stack = any(token in queries for token in ("python developer", "fastapi", "django"))
    return not classic_stack and len(queries.intersection(_AI_TOOLS_QUERY_SIGNATURE)) >= 3


def match_ai_tools_signals(text: str) -> tuple[RuleHit, ...]:
    normalized = normalize_multilingual_text(text)
    return tuple(
        RuleHit(code=code, label_ru=label, weight=weight)
        for code, label, weight, patterns in _AI_TOOLS_RULES
        if any(re.search(pattern, normalized) for pattern in patterns)
    )


def build_ai_tools_match_text(canonical: CanonicalVacancyGroup) -> str:
    parts = [
        canonical.normalized_title,
        canonical.company_name or "",
        canonical.location_text or "",
    ]
    for record in canonical.source_records:
        parts.extend((record.original_title, record.body_text or ""))
    return " ".join(part for part in parts if part)


def build_ai_tools_title_text(canonical: CanonicalVacancyGroup) -> str:
    return " ".join(
        part
        for part in (
            canonical.normalized_title,
            *(record.original_title for record in canonical.source_records),
        )
        if part
    )


def has_classic_engineering_title(title: str) -> bool:
    normalized = normalize_multilingual_text(title)
    return any(re.search(pattern, normalized) for pattern in _CLASSIC_ENGINEERING_TITLE_PATTERNS)


def has_deep_classic_engineering_requirements(text: str) -> bool:
    normalized = normalize_multilingual_text(text)
    return any(re.search(pattern, normalized) for pattern in _DEEP_ENGINEERING_REQUIREMENT_PATTERNS)
