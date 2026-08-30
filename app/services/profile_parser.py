"""Conservative fallback profile parser.

Used ONLY when LLM is unavailable. Extracts only high-confidence fields
via simple regex/keyword matching. Never guesses ambiguous fields.
Roles stay empty unless an explicit supported job intent is present.

Rule: it is better to leave a field null and ask a question than to fill
it incorrectly.
"""
from __future__ import annotations

import re

from app.services.intake_models import IntakeProfileDraft
from app.services.profile_extraction_model import ProfileExtractionResult

# ---------------------------------------------------------------------------
# Constants — high-confidence patterns only
# ---------------------------------------------------------------------------

CITY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("берлин", "Berlin"),
    ("берлін", "Berlin"),
    ("гамбург", "Hamburg"),
    ("мюнхен", "München"),
    ("кёльн", "Köln"),
    ("кельн", "Köln"),
    ("франкфурт", "Frankfurt"),
    ("дортмунд", "Dortmund"),
    ("штутгарт", "Stuttgart"),
    ("лейпциг", "Leipzig"),
    ("дрезден", "Dresden"),
    ("дюссельдорф", "Düsseldorf"),
    ("бремен", "Bremen"),
    ("нюрнберг", "Nürnberg"),
    ("бонн", "Bonn"),
    ("мангейм", "Mannheim"),
    ("карлсруэ", "Karlsruhe"),
    ("карлсруе", "Karlsruhe"),
    ("аугсбург", "Augsburg"),
    ("эссен", "Essen"),
    ("дуйсбург", "Duisburg"),
    ("бохум", "Bochum"),
    ("вупперталь", "Wuppertal"),
    ("билефельд", "Bielefeld"),
    ("мюнстер", "Münster"),
    ("ганновер", "Hannover"),
    ("аахен", "Aachen"),
    ("гейдельберг", "Heidelberg"),
    ("эрфурт", "Erfurt"),
    ("rostock", "Rostock"),
    ("росток", "Rostock"),
    ("stralsund", "Stralsund"),
    ("штральзунд", "Stralsund"),
    ("greifswald", "Greifswald"),
    ("грайфсвальд", "Greifswald"),
    ("грайсфальд", "Greifswald"),
    ("грейфсвальд", "Greifswald"),
    ("грейфсваль", "Greifswald"),
    ("трибзис", "Tribsees"),
    ("трибсес", "Tribsees"),
    ("tribsees", "Tribsees"),
    ("neustrelitz", "Neustrelitz"),
    ("нойштрелиц", "Neustrelitz"),
    ("висмар", "Wismar"),
    ("шверин", "Schwerin"),
    ("нойбранденбург", "Neubrandenburg"),
)

REMOTE_REGION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("worldwide remote", "worldwide remote"),
    ("remote worldwide", "worldwide remote"),
    ("по всему миру", "worldwide remote"),
    ("вакансии по всему миру", "worldwide remote"),
    ("международные remote", "international remote companies"),
    ("international remote", "international remote companies"),
    ("remote-компани", "international remote companies"),
    ("germany", "Germany"),
    ("германия", "Germany"),
    ("германии", "Germany"),
    ("deutschland", "Germany"),
    ("eu", "EU"),
    ("ес", "EU"),
    ("uk", "UK"),
    ("usa", "USA"),
    ("сша", "USA"),
    ("canada", "Canada"),
    ("канада", "Canada"),
)

ROLE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("middle python developer", "Middle Python Developer"),
    ("python developer", "Python Developer"),
    ("python-разработчик", "Python Developer"),
    ("python разработчик", "Python Developer"),
    ("backend developer", "Backend Developer"),
    ("backend-разработчик", "Backend Developer"),
    ("backend разработчик", "Backend Developer"),
    ("fastapi", "FastAPI Developer"),
    ("django", "Django Developer"),
    ("ai automation", "AI Automation Engineer"),
    ("automation developer", "Automation Developer"),
    ("automation engineer", "Automation Engineer"),
    ("ai agent developer", "AI Agent Developer"),
    ("ai-агент", "AI Agent Developer"),
    ("ai агент", "AI Agent Developer"),
    ("llm integration", "LLM Integration Developer"),
    ("llm-интеграц", "LLM Integration Developer"),
    ("telegram bot", "Telegram Bot Developer"),
    ("telegram-бот", "Telegram Bot Developer"),
    ("telegram бот", "Telegram Bot Developer"),
    ("scraping", "Scraping / Data Extraction Developer"),
    ("web scraping", "Web Scraping Developer"),
    ("parser developer", "Parser Developer"),
    ("парсинг", "Scraping / Data Extraction Developer"),
    ("парсер", "Parser Developer"),
    ("data extraction", "Data Extraction Developer"),
    ("mvp developer", "MVP Developer"),
    ("mvp builder", "MVP Developer"),
    ("internal tools", "Internal Tools Developer"),
    ("внутренних бизнес-инструментов", "Internal Tools Developer"),
)

DRIVER_B_FERNVERKEHR_ROLE = "Driver B – Fernverkehr"
DRIVER_B_FERNVERKEHR_SEARCH_TERMS: tuple[str, ...] = (
    "Fahrer Klasse B",
    "Sprinterfahrer",
    "Transporterfahrer",
    "Fahrer bis 3,5 t",
    "Fahrer Klasse B Fernverkehr",
    "Sprinterfahrer Fernverkehr",
    "Transporterfahrer Fernverkehr",
    "Fernverkehr Fahrer",
    "Fahrer Klasse B Direktfahrten",
    "Fahrer Klasse B Sonderfahrten",
    "Fahrer Klasse B Expressfahrten",
    "Planensprinter Fahrer",
    "Koffersprinter Fahrer",
)

_EXPLICIT_DRIVER_JOB_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bищу\s+(?:себе\s+)?(?:работу(?:\s+(?:в|по)\s+[a-zа-яё-]+)?\s+)?водител\w*"
    ),
    re.compile(r"\bхочу\s+работать\s+водител\w*"),
    re.compile(
        r"\bрассматриваю\s+(?:работу(?:\s+(?:в|по)\s+[a-zа-яё-]+)?\s+)?водител\w*"
    ),
    re.compile(r"\blooking\s+for\s+(?:a\s+)?(?:job\s+as\s+)?(?:driver|fahrer)\b"),
    re.compile(r"\bsuche\s+(?:eine\s+)?(?:arbeit|stelle|job)\s+als\s+fahrer\b"),
    re.compile(r"\bищу\s+(?:себе\s+)?работу\s+курьер\w*"),
    re.compile(r"\bхочу\s+работать\s+курьер\w*"),
    re.compile(r"\blooking\s+for\s+(?:a\s+)?(?:job\s+as\s+)?courier\b"),
    re.compile(r"\bsuche\s+(?:eine\s+)?(?:arbeit|stelle|job)\s+als\s+kurier(?:fahrer)?\b"),
)
_NEGATED_DRIVER_JOB_PREFIX = re.compile(r"\bне\s*$")

_DRIVER_B_SPECIALIZATION_TOKENS: tuple[str, ...] = (
    "sprinter",
    "transporter",
    "3,5 t",
    "3.5 t",
    "fernverkehr",
    "direktfahrt",
    "sonderfahrt",
    "expressfahrt",
    "planensprinter",
    "koffersprinter",
)

EXCLUSION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("senior-only", "Senior-only roles"),
    ("только senior", "Senior-only roles"),
    ("senior only", "Senior-only roles"),
    ("lead", "Lead roles"),
    ("team lead", "Team Lead roles"),
    ("principal", "Principal roles"),
    ("cto", "CTO roles"),
    ("advanced english", "Advanced English required"),
    ("fluent english", "Fluent English required"),
    ("английский fluent", "Fluent English required"),
    ("английский advanced", "Advanced English required"),
    ("office mandatory", "Mandatory office presence"),
    ("mandatory office", "Mandatory office presence"),
    ("обязательное присутствие в офисе", "Mandatory office presence"),
    ("только офис", "Mandatory office presence"),
    ("только hybrid", "Mandatory hybrid presence"),
    ("только гибрид", "Mandatory hybrid presence"),
    ("russia", "Russia-based roles/employers"),
    ("россия", "Russia-based roles/employers"),
    ("рф", "Russia-based roles/employers"),
    ("belarus", "Belarus-based roles/employers"),
    ("беларус", "Belarus-based roles/employers"),
    ("рб", "Belarus-based roles/employers"),
    (".ru", ".ru domains"),
    (".by", ".by domains"),
    ("russian employer", "Russian employers"),
    ("российские работодатели", "Russian employers"),
    ("belarus employer", "Belarus employers"),
    ("белорусские работодатели", "Belarus employers"),
    ("india-only", "India-only onsite roles"),
    ("только индия", "India-only onsite roles"),
    ("kazakhstan-only", "Kazakhstan-only onsite roles"),
    ("только казахстан", "Kazakhstan-only onsite roles"),
    ("pure ml", "Pure ML roles without backend"),
    ("data scientist", "Data Scientist roles without backend"),
    ("data engineer", "Data Engineer roles without backend"),
    ("devops without backend", "DevOps roles without backend"),
    ("devops без backend", "DevOps roles without backend"),
    ("склад", "Warehouse"),
    ("производство", "Production"),
    ("ручная физическая работа", "Manual physical work"),
    ("физическая работа", "Manual physical work"),
    ("низкоквалифицированные не-it", "Low-skilled non-IT jobs"),
    ("frontend без backend", "Frontend-only roles"),
    ("чистый frontend", "Frontend-only roles"),
    ("paketzustellung", "Mass parcel delivery"),
    ("paketzusteller", "Mass parcel delivery"),
    ("paketbote", "Mass parcel delivery"),
    ("postzusteller", "Mass parcel delivery"),
    ("briefzusteller", "Mass parcel delivery"),
)

SEARCH_TERMS_BY_ROLE: dict[str, tuple[str, ...]] = {
    "Middle Python Developer": ("Python Developer", "Python Entwickler"),
    "Python Developer": ("Python Developer", "Python Entwickler"),
    "Backend Developer": ("Backend Developer", "Backend Entwickler"),
    "FastAPI Developer": ("FastAPI Developer", "FastAPI Entwickler"),
    "Django Developer": ("Django Developer", "Django Entwickler"),
    "AI Automation Engineer": ("AI Automation Engineer", "KI Automatisierung"),
    "Automation Developer": ("Automation Developer", "Automatisierung Entwickler"),
    "Automation Engineer": ("Automation Engineer", "Automatisierung Entwickler"),
    "AI Agent Developer": ("AI Agent Developer", "KI Entwickler"),
    "LLM Integration Developer": ("LLM Integration Developer", "LLM Integration"),
    "Telegram Bot Developer": ("Telegram Bot Developer", "Telegram Bot Entwickler"),
    "Scraping / Data Extraction Developer": ("Scraping Developer", "Data Extraction Developer"),
    "Web Scraping Developer": ("Web Scraping Developer", "Scraping Entwickler"),
    "Parser Developer": ("Parser Developer", "Data Extraction Developer"),
    "Data Extraction Developer": ("Data Extraction Developer", "Scraping Developer"),
    "MVP Developer": ("MVP Developer", "MVP Entwickler"),
    "Internal Tools Developer": ("Internal Tools Developer", "Backend Developer"),
    DRIVER_B_FERNVERKEHR_ROLE: DRIVER_B_FERNVERKEHR_SEARCH_TERMS,
    "Водитель": ("Fahrer",),
}

# Explicit markers for "I currently live in <city>"
_RESIDENCE_MARKERS: tuple[str, ...] = (
    "живу в",
    "нахожусь в",
    "проживаю в",
    "мой город",
    "мой текущий город",
    "текущий город",
    "город проживания",
)

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def has_explicit_driver_job_intent(text: str) -> bool:
    for pattern in _EXPLICIT_DRIVER_JOB_PATTERNS:
        for match in pattern.finditer(text):
            if _NEGATED_DRIVER_JOB_PREFIX.search(text[:match.start()]):
                continue
            return True
    return False


def is_driver_b_fernverkehr_intent(*, text: str, driving_license: str | None) -> bool:
    return (
        driving_license == "B"
        and has_explicit_driver_job_intent(text)
        and _contains_any(text, _DRIVER_B_SPECIALIZATION_TOKENS)
    )


def _parse_bool_token(value: str | None) -> bool | None:
    if not value:
        return None
    s = value.strip().lower()
    if s in {"да", "yes", "true", "1", "ок", "готов", "есть"}:
        return True
    if s in {"нет", "no", "false", "0", "не готов", "без"}:
        return False
    return None


class ConservativeProfileParser:
    """Extract only high-confidence fields from free text. No LLM, no negation windows.

    Returns ProfileExtractionResult with only fields that can be reliably detected.
    Roles stay empty unless an explicit supported job intent is present.
    """

    def parse(self, free_text: str, followup_answers: dict[str, str] | None = None) -> ProfileExtractionResult:
        text = free_text.strip()
        lowered = text.lower()

        data: dict = {}

        # Country
        if _contains_any(lowered, ("германи", "deutschland", "germany")):
            data["current_country"] = "Germany"

        # Current city — only with explicit residence marker
        city = self._extract_city_with_marker(lowered)
        if city:
            data["current_city"] = city
            data["evidence_by_field"] = data.get("evidence_by_field", {})
            data["evidence_by_field"]["current_city"] = f"Найден маркер проживания перед '{city}'"

        # §24 / legal status — unambiguous
        if _contains_any(lowered, ("§24", "§ 24", "24 параграф", "параграф 24", "временной защит")):
            data["legal_status"] = "section_24"
            data["work_authorization"] = True

        # Work authorization — explicit phrases only
        if _contains_any(lowered, (
            "разрешение на работу есть", "есть разрешение на работу",
            "разрешение на работу: да", "разрешение на работу да",
        )):
            data["work_authorization"] = True

        # Language levels — only from unambiguous CEFR markers
        german = self._extract_cefr_level(lowered, ("немец", "deutsch"))
        if german:
            data["german_level"] = german

        english = self._extract_cefr_level(lowered, ("англий", "english"))
        if english:
            data["english_level"] = english

        # Availability — unambiguous
        if _contains_any(lowered, ("сразу", "немедленно", "immediately", "available immediately")):
            data["availability"] = "immediately"

        # Driving license — unambiguous patterns
        license_val = self._extract_driving_license(lowered)
        if license_val:
            data["driving_license"] = license_val

        # Has car — unambiguous
        has_car = self._extract_has_car(lowered)
        if has_car is not None:
            data["has_car"] = has_car

        # Relocation — unambiguous
        reloc = self._extract_relocation(lowered)
        if reloc is not None:
            data["relocation_ready"] = reloc

        # Physical work — unambiguous
        phys = self._extract_physical_work(lowered)
        if phys is not None:
            data["physical_work_allowed"] = phys

        # Preferred regions — all city matches (no ambiguity here, just geography)
        regions = self._extract_preferred_regions(lowered)
        if regions:
            data["preferred_regions"] = regions

        # Remote
        if _contains_any(lowered, ("remote", "удалённо", "удаленно", "дистанционно")):
            data["remote_allowed"] = True

        desired_roles = self._extract_desired_roles(lowered)
        if desired_roles:
            data["desired_roles"] = desired_roles
            data["search_query_terms"] = self._build_search_terms(desired_roles)

        excluded_roles = self._extract_excluded_roles(lowered)
        if excluded_roles:
            data["excluded_roles"] = excluded_roles

        employment_types = self._extract_employment_types(lowered)
        if employment_types:
            data["employment_types"] = employment_types

        work_modes = self._extract_work_modes(lowered)
        if work_modes:
            data["work_modes"] = work_modes

        # Apply followup answers
        if followup_answers:
            self._apply_followup(data, followup_answers)

        return ProfileExtractionResult(**data)

    @staticmethod
    def _extract_city_with_marker(text: str) -> str | None:
        for marker in _RESIDENCE_MARKERS:
            pos = text.find(marker)
            if pos == -1:
                continue
            after = text[pos + len(marker):pos + len(marker) + 80]
            for pattern, german_name in CITY_PATTERNS:
                if pattern in after:
                    return german_name
        return None

    @staticmethod
    def _extract_cefr_level(text: str, lang_tokens: tuple[str, ...]) -> str | None:
        token_pos = -1
        for tok in lang_tokens:
            p = text.find(tok)
            if p != -1:
                token_pos = p
                break
        if token_pos == -1:
            return None

        # Extract context around the language mention
        ctx = text[max(0, token_pos - 30):token_pos + 80].lower()

        if _contains_any(ctx, ("не знаю", "не владею", "не говор", "нет знания", "без знания")):
            return "none"
        if _contains_any(ctx, ("a1", "a2", "basic", "базовый", "начальный", "слаб")):
            return "basic"
        if _contains_any(ctx, ("b1", "intermediate", "средн", "working proficiency")):
            return "intermediate"
        if _contains_any(ctx, ("b2", "c1", "c2", "advanced", "свободн", "fluent")):
            return "advanced"
        return None

    @staticmethod
    def _extract_driving_license(text: str) -> str | None:
        if _contains_any(text, (
            "права категории b",
            "категория b",
            "категории b",
            "права b",
            "führerschein b",
            "führerschein klasse b",
            "fuehrerschein klasse b",
        )):
            return "B"
        if _contains_any(text, ("права есть", "есть права", "водительские права есть")):
            return "yes"
        if _contains_any(text, ("без прав", "прав нет", "водительских прав нет")):
            return "none"
        return None

    @staticmethod
    def _extract_has_car(text: str) -> bool | None:
        if _contains_any(text, ("есть машина", "своя машина", "авто есть", "eigenes auto")):
            return True
        if _contains_any(text, ("без машины", "машины нет", "авто нет", "личной машины нет", "kein auto")):
            return False
        return None

    @staticmethod
    def _extract_relocation(text: str) -> bool | None:
        if _contains_any(text, ("готов к переезду", "переезд ок", "могу переехать", "готов переехать", "umzugsbereit")):
            return True
        if _contains_any(text, (
            "не готов к переезду",
            "переезду не готов",
            "без переезда",
            "переезд не рассматриваю",
            "обязательный переезд",
        )):
            return False
        return None

    @staticmethod
    def _extract_physical_work(text: str) -> bool | None:
        if _contains_any(text, ("физическая работа ок", "готов к физической работе")):
            return True
        if _contains_any(text, ("физическая работа нет", "физическая работа: нет", "без физической работы",
                                 "физического труда нет", "физическую работу нет")):
            return False
        if re.search(r"физическ\w+\s+работ\w+\s*[:\-–]\s*нет", text):
            return False
        return None

    @staticmethod
    def _extract_preferred_regions(text: str) -> list[str]:
        regions: list[str] = []
        seen: set[str] = set()
        for pattern, german_name in CITY_PATTERNS:
            if pattern in text and german_name not in seen:
                regions.append(german_name)
                seen.add(german_name)
        remote_or_explicit_regions = _contains_any(
            text,
            (
                "worldwide remote",
                "remote worldwide",
                "по всему миру",
                "international remote",
                "remote-компани",
                "предпочтительные регионы",
                "регионы поиска",
            ),
        )
        for pattern, region_name in REMOTE_REGION_PATTERNS:
            if region_name in {"Germany", "EU", "UK", "USA", "Canada"} and not remote_or_explicit_regions:
                continue
            if pattern in text and region_name not in seen:
                regions.append(region_name)
                seen.add(region_name)
        if _contains_any(text, ("по всей германии", "вся германия", "по германии", "любой город", "всей германии")):
            if "Deutschland" not in seen:
                regions.append("Deutschland")
        return regions

    @staticmethod
    def _extract_desired_roles(text: str) -> list[str]:
        roles: list[str] = []
        seen: set[str] = set()
        explicit_driver_intent = has_explicit_driver_job_intent(text)
        if explicit_driver_intent:
            driving_license = ConservativeProfileParser._extract_driving_license(text)
            specialized_intent = is_driver_b_fernverkehr_intent(
                text=text,
                driving_license=driving_license,
            )
            role = DRIVER_B_FERNVERKEHR_ROLE if specialized_intent else "Водитель"
            roles.append(role)
            seen.add(role)
        for pattern, role in ROLE_PATTERNS:
            if pattern in text and role not in seen:
                roles.append(role)
                seen.add(role)
        return roles

    @staticmethod
    def _extract_excluded_roles(text: str) -> list[str]:
        excluded: list[str] = []
        seen: set[str] = set()
        negative_context = _contains_any(
            text,
            (
                "не интерес",
                "не хочу",
                "не рассматриваю",
                "исключ",
                "без связи",
                "must-exclude",
                "не подходит",
            ),
        )
        for pattern, label in EXCLUSION_PATTERNS:
            if pattern not in text:
                continue
            if label in {
                "Warehouse",
                "Production",
                "Manual physical work",
                "Mass parcel delivery",
            } and not negative_context:
                continue
            if label not in seen:
                excluded.append(label)
                seen.add(label)
        return excluded

    @staticmethod
    def _build_search_terms(roles: list[str]) -> list[str]:
        terms: list[str] = []
        seen: set[str] = set()
        for role in roles:
            for term in SEARCH_TERMS_BY_ROLE.get(role, (role,)):
                if term not in seen:
                    terms.append(term)
                    seen.add(term)
        return terms

    @staticmethod
    def _extract_employment_types(text: str) -> list[str]:
        result: list[str] = []
        checks = (
            ("full-time", ("full-time", "full time", "полная занятость")),
            ("part-time", ("part-time", "part time", "частичная занятость")),
            ("project-based", ("project-based", "проект", "mvp")),
            ("freelance", ("freelance", "фриланс")),
            ("contract", ("contract", "контракт")),
        )
        for value, tokens in checks:
            if _contains_any(text, tokens):
                result.append(value)
        return result

    @staticmethod
    def _extract_work_modes(text: str) -> list[str]:
        result: list[str] = []
        checks = (
            ("remote", ("remote", "удалён", "удален", "дистанционно")),
            ("hybrid", ("hybrid", "гибрид")),
            ("office", ("office", "офис")),
        )
        for value, tokens in checks:
            if _contains_any(text, tokens):
                result.append(value)
        return result

    @staticmethod
    def _apply_followup(data: dict, answers: dict[str, str]) -> None:
        def parse_list(v: str) -> list[str]:
            return [item.strip() for item in v.split(",") if item.strip()]

        if answers.get("current_country"):
            data["current_country"] = answers["current_country"].strip()
        if answers.get("preferred_regions"):
            data["preferred_regions"] = parse_list(answers["preferred_regions"])
        if answers.get("desired_roles"):
            data["desired_roles"] = parse_list(answers["desired_roles"])
        if answers.get("german_level"):
            data["german_level"] = answers["german_level"].strip().lower()
        reloc = _parse_bool_token(answers.get("willing_to_relocate"))
        if reloc is not None:
            data["relocation_ready"] = reloc
        shift = _parse_bool_token(answers.get("shift_ok"))
        if shift is not None:
            data["shift_work_allowed"] = shift
        auth = _parse_bool_token(answers.get("work_authorized"))
        if auth is not None:
            data["work_authorization"] = auth


# ---------------------------------------------------------------------------
# Backward-compat adapter: ProfileExtractionResult → IntakeProfileDraft
# ---------------------------------------------------------------------------

def extraction_result_to_draft(result: ProfileExtractionResult) -> IntakeProfileDraft:
    """Map new model to legacy IntakeProfileDraft for DB persistence layer."""
    return IntakeProfileDraft(
        current_country=result.current_country,
        current_city=result.current_city,
        legal_status=result.legal_status,
        work_authorized=result.work_authorization,
        german_level=result.german_level,
        english_level=result.english_level,
        desired_roles=list(result.desired_roles),
        excluded_roles=list(result.excluded_roles),
        preferred_regions=list(result.preferred_regions),
        remote_allowed=result.remote_allowed,
        international_remote_allowed=result.international_remote_allowed,
        work_modes=list(result.work_modes),
        willing_to_relocate=result.relocation_ready,
        shift_ok=result.shift_work_allowed,
        physical_work_ok=result.physical_work_allowed,
        housing_needed=result.housing_needed,
        start_availability=result.availability,
        driving_license=result.driving_license,
        has_car=result.has_car,
        search_query_terms=list(result.search_query_terms),
    )


# ---------------------------------------------------------------------------
# Legacy ProfileParser — thin wrapper kept for backward compat with old tests
# ---------------------------------------------------------------------------

class ProfileParser:
    """Legacy parser. Wraps ConservativeProfileParser for backward compat.

    New code should use IntakeAgentService which orchestrates LLM extractor +
    conservative fallback + post-processor.
    """

    def __init__(self, llm_client: object = None) -> None:
        self._conservative = ConservativeProfileParser()

    def parse(self, free_text: str, followup_answers: dict[str, str] | None = None) -> IntakeProfileDraft:
        result = self._conservative.parse(free_text, followup_answers)
        return extraction_result_to_draft(result)
