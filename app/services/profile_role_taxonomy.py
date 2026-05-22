"""Role family taxonomy for profile classification.

Single source of truth for:
- What constitutes an IT profile (no shift_ok required)
- What constitutes a physical/manual profile (excluded when physical_work=false)
- What families require shift_ok as a critical field
- What families make driving/car critical
"""
from __future__ import annotations

from enum import Enum


class RoleFamily(str, Enum):
    IT_SOFTWARE = "it_software"
    AI_AUTOMATION = "ai_automation"
    BACKEND = "backend"
    SCRAPING_DATA = "scraping_data"
    TELEGRAM_BOTS = "telegram_bots"
    FULLSTACK = "fullstack"
    FRONTEND_ONLY = "frontend_only"
    DEVOPS_INFRA = "devops_infra"
    ML_DATA = "ml_data"
    DRIVING_DELIVERY = "driving_delivery"
    WAREHOUSE = "warehouse"
    PRODUCTION_MANUFACTURING = "production_manufacturing"
    MANUAL_LABOR = "manual_labor"
    OFFICE_ADMIN = "office_admin"
    CUSTOMER_SERVICE = "customer_service"
    HEALTHCARE = "healthcare"
    EDUCATION = "education"
    HOSPITALITY = "hospitality"
    CONSTRUCTION = "construction"
    UNKNOWN = "unknown"


_IT_FAMILIES: frozenset[RoleFamily] = frozenset({
    RoleFamily.IT_SOFTWARE,
    RoleFamily.AI_AUTOMATION,
    RoleFamily.BACKEND,
    RoleFamily.SCRAPING_DATA,
    RoleFamily.TELEGRAM_BOTS,
    RoleFamily.FULLSTACK,
    RoleFamily.FRONTEND_ONLY,
    RoleFamily.DEVOPS_INFRA,
    RoleFamily.ML_DATA,
})

_PHYSICAL_FAMILIES: frozenset[RoleFamily] = frozenset({
    RoleFamily.WAREHOUSE,
    RoleFamily.PRODUCTION_MANUFACTURING,
    RoleFamily.MANUAL_LABOR,
    RoleFamily.CONSTRUCTION,
})

_DRIVING_FAMILIES: frozenset[RoleFamily] = frozenset({
    RoleFamily.DRIVING_DELIVERY,
})

# Families where shift schedule is a relevant/critical question
_SHIFT_RELEVANT_FAMILIES: frozenset[RoleFamily] = frozenset({
    RoleFamily.WAREHOUSE,
    RoleFamily.PRODUCTION_MANUFACTURING,
    RoleFamily.MANUAL_LABOR,
    RoleFamily.CONSTRUCTION,
    RoleFamily.HEALTHCARE,
    RoleFamily.HOSPITALITY,
    RoleFamily.DRIVING_DELIVERY,
})

# Keyword → family mapping. Order matters: more specific entries first.
# All keywords are lowercase and matched as substrings of the lowercased role.
_KEYWORD_TO_FAMILY: list[tuple[str, RoleFamily]] = [
    # ── AI / Automation ───────────────────────────────────────────────────────
    ("ai agent", RoleFamily.AI_AUTOMATION),
    ("ai-agent", RoleFamily.AI_AUTOMATION),
    ("llm integration", RoleFamily.AI_AUTOMATION),
    ("llm", RoleFamily.AI_AUTOMATION),
    ("automation developer", RoleFamily.AI_AUTOMATION),
    ("automation engineer", RoleFamily.AI_AUTOMATION),
    ("mvp developer", RoleFamily.AI_AUTOMATION),
    ("mvp builder", RoleFamily.AI_AUTOMATION),
    ("ai-assisted", RoleFamily.AI_AUTOMATION),
    # ── Scraping / Data extraction ────────────────────────────────────────────
    ("scraping", RoleFamily.SCRAPING_DATA),
    ("data extraction", RoleFamily.SCRAPING_DATA),
    ("web scraper", RoleFamily.SCRAPING_DATA),
    ("парсинг", RoleFamily.SCRAPING_DATA),
    # ── Telegram bots ─────────────────────────────────────────────────────────
    ("telegram bot", RoleFamily.TELEGRAM_BOTS),
    ("телеграм бот", RoleFamily.TELEGRAM_BOTS),
    ("telegram bot developer", RoleFamily.TELEGRAM_BOTS),
    # ── Backend ───────────────────────────────────────────────────────────────
    ("backend", RoleFamily.BACKEND),
    ("fastapi", RoleFamily.BACKEND),
    ("django", RoleFamily.BACKEND),
    ("flask", RoleFamily.BACKEND),
    ("rest api", RoleFamily.BACKEND),
    ("api developer", RoleFamily.BACKEND),
    ("internal tools", RoleFamily.BACKEND),
    ("внутренние инструменты", RoleFamily.BACKEND),
    # ── DevOps / Infra ────────────────────────────────────────────────────────
    ("devops", RoleFamily.DEVOPS_INFRA),
    ("sre", RoleFamily.DEVOPS_INFRA),
    ("kubernetes", RoleFamily.DEVOPS_INFRA),
    ("docker", RoleFamily.DEVOPS_INFRA),
    ("site reliability", RoleFamily.DEVOPS_INFRA),
    # ── ML / Data ─────────────────────────────────────────────────────────────
    ("machine learning", RoleFamily.ML_DATA),
    ("ml engineer", RoleFamily.ML_DATA),
    ("data scientist", RoleFamily.ML_DATA),
    ("data analyst", RoleFamily.ML_DATA),
    ("аналитик данных", RoleFamily.ML_DATA),
    ("data engineer", RoleFamily.ML_DATA),
    # ── Fullstack ─────────────────────────────────────────────────────────────
    ("fullstack", RoleFamily.FULLSTACK),
    ("full stack", RoleFamily.FULLSTACK),
    ("full-stack", RoleFamily.FULLSTACK),
    # ── Frontend only ─────────────────────────────────────────────────────────
    ("frontend developer", RoleFamily.FRONTEND_ONLY),
    ("frontend engineer", RoleFamily.FRONTEND_ONLY),
    ("ui developer", RoleFamily.FRONTEND_ONLY),
    ("react developer", RoleFamily.FRONTEND_ONLY),
    ("vue developer", RoleFamily.FRONTEND_ONLY),
    # ── General IT / Software ─────────────────────────────────────────────────
    ("python developer", RoleFamily.IT_SOFTWARE),
    ("python engineer", RoleFamily.IT_SOFTWARE),
    ("software developer", RoleFamily.IT_SOFTWARE),
    ("software engineer", RoleFamily.IT_SOFTWARE),
    ("разработчик", RoleFamily.IT_SOFTWARE),
    ("программист", RoleFamily.IT_SOFTWARE),
    ("entwickler", RoleFamily.IT_SOFTWARE),
    ("developer", RoleFamily.IT_SOFTWARE),
    # ── Driving / Delivery ────────────────────────────────────────────────────
    ("водитель", RoleFamily.DRIVING_DELIVERY),
    ("driver", RoleFamily.DRIVING_DELIVERY),
    ("fahrer", RoleFamily.DRIVING_DELIVERY),
    ("курьер", RoleFamily.DRIVING_DELIVERY),
    ("kurier", RoleFamily.DRIVING_DELIVERY),
    ("доставщик", RoleFamily.DRIVING_DELIVERY),
    ("zusteller", RoleFamily.DRIVING_DELIVERY),
    ("delivery", RoleFamily.DRIVING_DELIVERY),
    # ── Warehouse / Logistics ─────────────────────────────────────────────────
    ("склад", RoleFamily.WAREHOUSE),
    ("warehouse", RoleFamily.WAREHOUSE),
    ("lager", RoleFamily.WAREHOUSE),
    ("логистик", RoleFamily.WAREHOUSE),
    ("logistik", RoleFamily.WAREHOUSE),
    ("комплектовщик", RoleFamily.WAREHOUSE),
    ("упаковщик", RoleFamily.WAREHOUSE),
    ("упаковк", RoleFamily.WAREHOUSE),
    ("verpackung", RoleFamily.WAREHOUSE),
    # ── Production / Manufacturing ────────────────────────────────────────────
    ("производств", RoleFamily.PRODUCTION_MANUFACTURING),
    ("produktion", RoleFamily.PRODUCTION_MANUFACTURING),
    ("fertigu", RoleFamily.PRODUCTION_MANUFACTURING),
    ("manufacturing", RoleFamily.PRODUCTION_MANUFACTURING),
    ("helfer", RoleFamily.MANUAL_LABOR),
    ("werker", RoleFamily.MANUAL_LABOR),
    ("грузчик", RoleFamily.MANUAL_LABOR),
    ("рабочий помощник", RoleFamily.MANUAL_LABOR),
    # ── Office / Admin ────────────────────────────────────────────────────────
    ("office manager", RoleFamily.OFFICE_ADMIN),
    ("assistant", RoleFamily.OFFICE_ADMIN),
    ("секретарь", RoleFamily.OFFICE_ADMIN),
    ("администратор", RoleFamily.OFFICE_ADMIN),
    # ── Customer Service ──────────────────────────────────────────────────────
    ("customer service", RoleFamily.CUSTOMER_SERVICE),
    ("support agent", RoleFamily.CUSTOMER_SERVICE),
    ("оператор", RoleFamily.CUSTOMER_SERVICE),
    # ── Healthcare ────────────────────────────────────────────────────────────
    ("pfleger", RoleFamily.HEALTHCARE),
    ("медбрат", RoleFamily.HEALTHCARE),
    ("медсестра", RoleFamily.HEALTHCARE),
    ("nurse", RoleFamily.HEALTHCARE),
]


def classify_roles(roles: list[str]) -> set[RoleFamily]:
    """Map a list of role strings to their role families."""
    families: set[RoleFamily] = set()
    for role in roles:
        role_lower = role.lower()
        matched = False
        for keyword, family in _KEYWORD_TO_FAMILY:
            if keyword in role_lower:
                families.add(family)
                matched = True
                break
        if not matched:
            families.add(RoleFamily.UNKNOWN)
    return families


def is_it_profile(families: set[RoleFamily]) -> bool:
    """True if at least one IT family is present."""
    return bool(families & _IT_FAMILIES)


def is_physical_family(family: RoleFamily) -> bool:
    return family in _PHYSICAL_FAMILIES


def is_driving_family(family: RoleFamily) -> bool:
    return family in _DRIVING_FAMILIES


def requires_shift_question(families: set[RoleFamily]) -> bool:
    """Return True only when shift schedule is a meaningful question for these families."""
    return bool(families & _SHIFT_RELEVANT_FAMILIES)


def get_physical_families() -> frozenset[RoleFamily]:
    return _PHYSICAL_FAMILIES
