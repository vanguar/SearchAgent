from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.ai_tools_profile import is_ai_tools_profile
from app.services.driver_license_signal_extractor import extract_driver_license_requirements
from app.services.geo_distance import distance_km, resolve_point
from app.services.hashers import normalize_text_for_fingerprint
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.role_family import (
    RoleFamily,
    classify_desired_roles,
    families_are_compatible,
    is_specific_family,
)
from app.services.search_models import RuleHit, SearchProfileContext, VacancySignalSnapshot, normalize_profile_text
from app.services.search_normalizer import is_remote_worldwide_location
from app.services.vehicle_class_signal_extractor import extract_vehicle_class_signals

HOT_BUCKET_MIN_SCORE = 70
MAYBE_BUCKET_MIN_SCORE = 45
BASE_SCORE = 35


@dataclass(frozen=True, slots=True)
class TextRule:
    code: str
    label_ru: str
    patterns: tuple[str, ...]


POSITIVE_ROLE_FAMILIES: tuple[TextRule, ...] = (
    TextRule(
        code="warehouse_family",
        label_ru="складская роль",
        # NB: "warehouse" and "picker" are ambiguous outside blue-collar ads. A "data
        # warehouse" is a database, not a building — an AI Engineer ad that mentioned
        # "databases, data warehouses, file stores" was scored as a warehouse job and
        # explained itself on the card as "складская роль". A "date/color/file picker"
        # is a UI widget, not an order picker. Excluded by context, not dropped.
        patterns=(
            r"\blager\w*",
            r"(?<!data )\bwarehouse\w*",
            r"\bkommissionier\w*",
            r"(?<!date )(?<!time )(?<!color )(?<!colour )(?<!file )(?<!image )(?<!emoji )\bpicker\b",
            # Погрузчик и высотный склад — складская работа. Без этих форм
            # "Staplerfahrer" попадал только в водительскую семью, и складской
            # профиль терял свою же профильную вакансию (91 -> 61 баллов).
            r"\b\w*stapler\w*",
            r"\bhubwagen\w*",
            r"\bflurforderzeug\w*",
            r"\bhochregal\w*",
            r"\bintralogistik\w*",
            # "\blager\w*" не ловит формы, где "lager" стоит внутри слова.
            r"\bfachlagerist\w*",
            r"\bkuhlhaus\w*",
            r"\bwareneingang\w*",
            r"\bwarenausgang\w*",
        ),
    ),
    TextRule(
        code="logistics_family",
        label_ru="логистическая роль",
        # NB: bare "distribution" is not a logistics signal — startup ads use it for
        # go-to-market ("strong distribution and real credibility behind the company"),
        # software ads for package/content distribution. Only the logistics compounds
        # and explicit logistics phrases count.
        patterns=(
            r"\blogistik\w*",
            r"\bversand\w*",
            r"\bfulfillment\b",
            r"\bdistributions(?:zentrum|zentren|lager|center)\w*",
            r"\bdistribution (?:cent(?:er|re)|warehouse|hub|logistics)\b",
        ),
    ),
    TextRule(
        code="packaging_family",
        label_ru="роль в упаковке",
        patterns=(r"\bverpack\w*", r"\bpackaging\b", r"\bpacking\b", r"\bpacker\b", r"\bsortier\w*"),
    ),
    TextRule(
        code="production_family",
        label_ru="производственная роль",
        # NB: bare "montage"/"assembly" intentionally NOT used — they collide with the
        # English film/video term "montage" and software/meeting "assembly", which made
        # creative roles (e.g. "Cinematic Video Editor") false-match as production work.
        # Restricted to unambiguous German assembly compounds + explicit manufacturing phrases.
        patterns=(
            r"\bproduktion\w*", r"\bfertigung\w*",
            r"\bmontagemitarbeiter\w*", r"\bmontagehelfer\w*", r"\bmontagearbeit\w*",
            r"\bendmontage\w*", r"\bvormontage\w*", r"\bmontagelinie\w*", r"\bfliessband\w*",
            r"\bassembly line\b", r"\bassembly worker\b",
        ),
    ),
    TextRule(
        code="helper_family",
        label_ru="простая вспомогательная роль",
        patterns=(r"\bhelfer\w*", r"\bhilfskraft\w*", r"\baushilfe\w*"),
    ),
    TextRule(
        code="delivery_driving_family",
        label_ru="роль в доставке или вождении",
        # Погрузчик исключён явно: "Staplerfahrer" кончается на "-fahrer", но это
        # складская работа, а не дорожная. Без исключения водительский профиль
        # считал бы складские вакансии своими.
        patterns=(
            r"\b(?!\w*stapler)\w*fahrer\w*",
            r"\bzusteller\w*",
            r"\bkurier\w*",
            r"\blieferfahrer\w*",
            r"\bkraftfahrer\w*",
        ),
    ),
)

# Role family behind each positive blue-collar rule. A hit only counts as a positive
# signal for a profile that actually looks for that kind of work: for an IT profile a
# "warehouse role" is never a reason to rank a vacancy higher, it is noise from a word
# that happens to appear in the ad (data warehouse, distribution, packaging of a build).
# helper_family is deliberately unmapped — "Helfer"/"Aushilfe" spans every manual family
# and carries no family of its own.
POSITIVE_ROLE_HIT_FAMILIES: dict[str, RoleFamily] = {
    "warehouse_family": RoleFamily.WAREHOUSE,
    "logistics_family": RoleFamily.WAREHOUSE,
    "packaging_family": RoleFamily.WAREHOUSE,
    "production_family": RoleFamily.PRODUCTION,
    "delivery_driving_family": RoleFamily.DRIVING,
}


NEGATIVE_ROLE_FAMILIES: tuple[TextRule, ...] = (
    TextRule(
        code="healthcare_family",
        label_ru="роль из медицины или ухода",
        patterns=(r"\bpflege\w*", r"\bmedizin\w*", r"\bkrank\w*", r"\barzt\w*", r"\bnurse\b"),
    ),
    TextRule(
        code="engineering_it_family",
        label_ru="роль из IT или инженерии",
        patterns=(r"\bsoftware\w*", r"\bentwickler\w*", r"\bdeveloper\b", r"\bingenieur\w*", r"\bdevops\b"),
    ),
    TextRule(
        code="office_admin_family",
        label_ru="офисная или административная роль",
        patterns=(
            r"\bbuchhalt\w*", r"\bcontrolling\b", r"\bassistenz\b", r"\bsachbearbeit\w*",
            r"\bcustomer support\b", r"\bcustomer service\b", r"\bsupport specialist\b",
            r"\bkundenservice\w*", r"\bkundenbetreuung\w*",
        ),
    ),
    TextRule(
        code="sales_family",
        label_ru="роль в продажах",
        patterns=(
            r"\bvertrieb\w*", r"\bsales\b", r"\baccount manager\b", r"\bkundenberater\w*",
            r"\bcustomer success\b", r"\bclient success\b", r"\baccount executive\b",
            r"\bbusiness development\b",
        ),
    ),
    TextRule(
        code="education_social_family",
        label_ru="роль в образовании или соцсфере",
        patterns=(r"\berzieher\w*", r"\blehrer\w*", r"\bpadagog\w*", r"\bsozialarbeiter\w*"),
    ),
)

STRONG_GERMAN_REQUIREMENT_RULE = TextRule(
    code="strong_german_requirement",
    label_ru="явно требуют хороший немецкий",
    patterns=(
        r"\b(?:sehr gute|gute|fliessend(?:e|er|es|en)?|verhandlungssicher(?:e|er|es|en)?|sichere|b1|b2|c1|c2)\s+deutsch",
        r"\bdeutsch(?:kenntnisse)?\s+(?:mindestens\s+)?(?:b1|b2|c1|c2)\b",
        r"\bdeutschkenntnisse\b(?!\s*.{0,30}\b(?:nicht|keine)\s+(?:erforderlich|notwendig)\b).{0,80}\b(?:erforderlich|vorausgesetzt|zwingend|required|mandatory|must)\b",
        # Уверенный уровень, записанный без слова "Kenntnisse". Эти формы
        # встречаются в объявлениях не реже канонических и пропускались целиком.
        r"(?<!kein )(?<!keine )(?<!nicht )(?<!ohne )\bdeutsch\s+flie(?:ss|s)end\b",
        r"\bmuttersprach\w*\s+(?:niveau\s+)?deutsch\b",
        r"\bdeutsch\s+(?:auf\s+)?muttersprach\w*(?:\s+niveau)?\b",
        r"\bsichere[rn]?\s+umgang\s+mit\s+der\s+deutschen\s+sprache\b",
        r"\bexzellente\s+deutschkenntnisse\b",
        r"\bgerman\b.*\b(?:required|must|mandatory)\b",
    ),
)
GERMAN_ANY_REQUIRED_RULE = TextRule(
    code="german_any_required",
    label_ru="вакансия требует знание немецкого",
    patterns=(
        # Level A2 or above explicitly mentioned (B1/A2 not caught by strong rule)
        r"\b(?:a1|a2|b1|b2|c1|c2)\s+deutsch\b",
        r"\bdeutsch\s+(?:a1|a2|b1|b2|c1|c2)\b",
        # "Deutschkenntnisse" only when paired with a mandatory marker (≤80 chars apart)
        # "Deutschkenntnisse von Vorteil" and similar soft phrases are intentionally NOT caught
        # Match "Deutschkenntnisse" only when a mandatory marker appears within 80 chars.
        # Note: normalize_text_for_fingerprint removes all punctuation, so sentence boundaries
        # are not preserved — cross-sentence matching is a known limitation.
        # Защита от отрицания: «Deutschkenntnisse sind nicht erforderlich» — это НЕ требование.
        # Такая же защита уже стоит у строгого правила; здесь её не было.
        r"\bdeutschkenntnisse\b(?!\s*.{0,30}\b(?:nicht|keine)\s+(?:erforderlich|notwendig)\b)"
        r".{0,80}\b(?:erforderlich|vorausgesetzt|zwingend|pflicht|muss|required|mandatory)\b",
        # Qualified German knowledge — adjective signals it is required, not optional
        r"\b(?:gute|sehr\s+gute|fliessende|fliessend|verhandlungssichere|verhandlungssicher|sichere)\s+deutschkenntnisse\b",
        # Глагольные формулировки на «ты» и «вы». Низкопороговые объявления (DHL,
        # Zeitarbeit, розница) почти не пишут «Deutschkenntnisse erforderlich» — они
        # пишут «Du kannst dich auf Deutsch unterhalten». Это такое же требование,
        # и раньше оно полностью пропускалось: плашка «немецкий не указан» врала.
        # Отрицания рядом («kein Deutsch sprechen») гасят срабатывание.
        r"(?<!kein )(?<!keine )(?<!nicht )(?<!ohne )\b(?:du\s+sprichst|sie\s+sprechen|sprichst\s+du)\s+(?:flie(?:ss|s)end\s+|gut\s+|gutes\s+|etwas\s+)?deutsch\b",
        r"(?<!kein )(?<!keine )(?<!nicht )(?<!ohne )\bauf\s+deutsch\s+(?:unterhalten|verstandigen|kommunizieren|sprechen)\b",
        r"(?<!kein )(?<!keine )(?<!nicht )(?<!ohne )\bdich\s+auf\s+deutsch\b",
        r"(?<!kein )(?<!keine )(?<!nicht )(?<!ohne )\b(?:verstandigst|verstandigen)\s+dich\s+auf\s+deutsch\b",
        r"(?<!kein )(?<!keine )(?<!nicht )(?<!ohne )\bdeutsch\s+in\s+wort\s+und\s+schrift\b",
        r"(?<!kein )(?<!keine )(?<!nicht )(?<!ohne )\bbeherrschst\s+(?:die\s+)?deutsche?\s+sprache\b",
        r"(?<!kein )(?<!keine )(?<!nicht )(?<!ohne )\bdeutsche\s+sprache\s+in\s+wort\s+und\s+schrift\b",
        # Working language is German
        r"\barbeitssprache\s+deutsch\b",
        # German explicitly required / expected
        r"\bdeutsch\s+(?:vorausgesetzt|erforderlich|notwendig|erwartet|pflicht)\b",
        r"\bdeutsch\s+(?:ist|als)\s+(?:pflicht|voraussetzung|anforderung|muss)\b",
        # Communication in German
        r"\bkommunikation\s+(?:auf\s+)?deutsch\b",
        # English patterns for German requirement.
        # NB: soft/optional phrasings ("German is an asset", "German is a plus",
        # "German nice to have") must NOT match — they signal German is optional, not required.
        r"\bgerman\s+(?:required|must|mandatory|needed|proficiency|language skills)\b",
        r"\bgerman\s+(?:is|as)\s+(?:required|mandatory|a must)\b",
        # Требование, записанное без маркера обязательности: в немецком само
        # "Du verfügst über Deutschkenntnisse" в разделе требований и есть
        # требование, отдельного "erforderlich" там не пишут.
        r"\b(?:du\s+verfugst|sie\s+verfugen)\s+uber\s+.{0,20}deutschkenntnisse\b",
        r"\bgrundkenntnisse\s+(?:der\s+)?deutsch\w*(?:\s+sprache)?\b",
        r"\b(?:du\s+solltest|sie\s+sollten)\s+deutsch\s+sprechen\b",
        r"\bdeutsch\s+sprechen\s+konnen\b",
        r"\bsprachkenntnisse\s*:?\s*deutsch\b",
    ),
)
ENGLISH_REQUIRED_RULE = TextRule(
    code="english_required_signal",
    label_ru="английский явно обязателен",
    patterns=(
        r"\benglish\b.{0,50}\b(?:required|mandatory|essential|must have)\b",
        r"\b(?:fluent|business|professional|advanced|excellent) english\b.{0,40}"
        r"\b(?:required|mandatory|essential|must)\b",
        r"\b(?:english\s+(?:b2|c1|c2)|(?:b2|c1|c2)\s+english)\b",
        r"\bmust\b.{0,40}\b(?:speak|write|communicate in) english\b",
    ),
)
ENGLISH_PREFERRED_RULE = TextRule(
    code="english_preferred_signal",
    label_ru="английский указан как пожелание",
    patterns=(
        r"\benglish\b.{0,30}\b(?:preferred|a plus|an asset|nice to have|desirable|advantage)\b",
        r"\b(?:preferred|desirable)\b.{0,20}\benglish\b",
    ),
)
LOW_LANGUAGE_RULE = TextRule(
    code="low_language_signal",
    label_ru="языковой барьер невысокий",
    patterns=(
        r"\bohne deutsch",
        r"\bkeine deutschkenntnisse",
        r"\b(?:deutsch|deutschkenntnisse)\s+nicht\s+(?:erforderlich|notwendig)\b",
        r"\bgerman\s+(?:is\s+)?not\s+(?:required|necessary|mandatory)\b",
        r"\b(?:kein|keine|no)\s+(?:deutsch|deutschkenntnisse|german)\s+(?:erforderlich|required|necessary)\b",
        r"\bgrundkenntnisse(?: in deutsch)?\b",
        r"\beinfache deutschkenntnisse\b",
        r"\b(?:deutsch|deutschkenntnisse)\s+(?:auf\s+)?a1(?:\s+niveau)?\b",
        r"\ba1(?:\s+niveau)?\s+(?:deutsch|deutschkenntnisse)\b",
        r"\bbasic german\b",
        r"\benglish only\b",
    ),
)
GERMAN_NOT_REQUIRED_RULE = TextRule(
    code="german_not_required_signal",
    label_ru="немецкий не требуется",
    patterns=(
        r"\bohne (?:deutsch|deutschkenntnisse)\b",
        r"\bkeine deutschkenntnisse\b",
        r"\b(?:deutsch|deutschkenntnisse)\s+nicht\s+(?:erforderlich|notwendig)\b",
        r"\bkein deutsch\s+(?:erforderlich|notwendig)\b",
        r"\bgerman\s+(?:is\s+)?not\s+(?:required|necessary|mandatory)\b",
        r"\bno german\s+(?:required|necessary|mandatory)\b",
        r"\bwithout german\b",
        r"\benglish only\b",
    ),
)
BASIC_GERMAN_RULE = TextRule(
    code="basic_german_signal",
    label_ru="достаточно базового немецкого",
    patterns=(
        r"\b(?:nur\s+)?grundkenntnisse(?: in deutsch)?\b",
        r"\b(?:einfache|geringe) deutschkenntnisse\b",
        r"\bbasiskenntnisse(?: in deutsch)?\b",
        r"\b(?:deutsch|deutschkenntnisse)\s+(?:auf\s+)?a1(?:\s+niveau)?\b",
        r"\ba1(?:\s+niveau)?\s+(?:deutsch|deutschkenntnisse)\b",
        r"\bbasic german\b",
    ),
)
UKRAINIAN_WELCOME_RULE = TextRule(
    code="ukrainian_welcome_signal",
    label_ru="украинцев явно приглашают откликаться",
    patterns=(
        r"\bukrainer(?:innen)?\b.{0,60}\b(?:willkommen|bevorzugt|gesucht)\b",
        r"\bukrainische\s+(?:bewerber|bewerberinnen|kandidaten|mitarbeiter|gefluchtete)\b.{0,60}"
        r"\b(?:willkommen|bevorzugt|gesucht)\b",
        r"\b(?:bewerber|bewerbungen|menschen|gefluchtete)\b.{0,60}\baus der ukraine\b.{0,60}"
        r"\b(?:willkommen|bevorzugt|begrussen)\b",
        r"\b(?:ukrainians?|ukrainian (?:applicants|candidates|refugees))\b.{0,60}"
        r"\b(?:welcome|preferred|encouraged to apply)\b",
        r"\b(?:applicants|candidates|refugees) from ukraine\b.{0,60}\b(?:welcome|preferred)\b",
    ),
)
SHIFT_RULE = TextRule(
    code="shift_signal",
    label_ru="есть смены",
    patterns=(r"\bschicht\w*", r"\bnachtarbeit\b", r"\bwochenend\w*", r"\b3 schicht\b"),
)
DEGREE_REQUIRED_RULE = TextRule(
    code="degree_requirement",
    label_ru="нужен обязательный профильный диплом",
    patterns=(
        r"\b(?:bachelor|master|studium|hochschulabschluss|universitatsabschluss)\b",
        r"\bdegree\b.*\b(?:required|mandatory)\b",
    ),
)
VOCATIONAL_REQUIRED_RULE = TextRule(
    code="vocational_requirement",
    label_ru="нужен обязательный Ausbildung",
    patterns=(
        # Между "abgeschlossene" и "Ausbildung" почти всегда стоит уточнение
        # специальности: "abgeschlossene kaufmännische Ausbildung",
        # "abgeschlossene technische Ausbildung". Без допуска на эти слова правило
        # пропускало требование, и диспетчер автопарка с обязательным Ausbildung
        # висел в горячих.
        r"\babgeschlossene\w*(?:\s+\w+){0,2}\s+(?:berufs)?ausbildung\b",
        r"\b(?:berufs)?ausbildung\b(?:\s+\w+){0,3}\s+(?:erforderlich|zwingend|vorausgesetzt|notwendig|pflicht)\b",
        r"\bausgebildete[rn]?\s+\w+\b",
        r"\bgelernte[rn]?\s+\w+\b",
        r"\b(?:completed|vocational)\s+(?:apprenticeship|vocational training)\b",
    ),
)

# Слова, которые превращают требование в пожелание. Немецкие объявления почти
# никогда не пишут "необязательно" прямо: они пишут "wünschenswert", "von
# Vorteil", "idealerweise". Без этой проверки вакансия, куда берут и без
# Ausbildung, жёстко отсекалась наравне с той, куда без него не берут.
_QUALIFICATION_OPTIONAL_MARKERS: tuple[str, ...] = (
    "wunschenswert",
    "von vorteil",
    "vorteilhaft",
    "erwunscht",
    "nicht zwingend",
    "nicht erforderlich",
    "nicht notwendig",
    "kein muss",
    "keine ausbildung",
    "keine berufsausbildung",
    "ohne ausbildung",
    "kein abschluss",
    "ohne abschluss",
    "oder vergleichbare",
    "oder ahnliche",
    "quereinsteiger",
    "auch ohne",
    "nice to have",
    "a plus",
    "an asset",
    "preferred",
    "desirable",
)

# Слова, которые смягчают требование НЕ всегда. "Idealerweise" перед названием
# области уточняет специальность, а не отменяет диплом: в "abgeschlossene
# kaufmännische Ausbildung, idealerweise im Bereich Logistik" Ausbildung нужен
# обязательно, и просто её отрасль предпочтительна.
_CONDITIONAL_OPTIONAL_MARKERS: tuple[str, ...] = (
    "idealerweise",
    "vorzugsweise",
    "gerne",
    "bevorzugt",
)
# Продолжения, после которых смягчающее слово относится к области, а не к
# самому требованию.
_DOMAIN_CONTINUATION_RE = re.compile(
    r"^\s*(?:im\s+bereich|im\s+umfeld|mit\s+schwerpunkt|in\s+der|in\s+den|im\s+"
    r"|als\s|aus\s+dem\s+bereich|richtung)\b"
)
# Насколько близко к упоминанию квалификации должно стоять смягчение, чтобы
# относиться именно к нему, а не к другому пункту списка требований.
_QUALIFICATION_OPTIONALITY_WINDOW = 60


def _window_softens_requirement(window: str) -> bool:
    """Есть ли рядом с требованием слово, превращающее его в пожелание."""
    if any(marker in window for marker in _QUALIFICATION_OPTIONAL_MARKERS):
        return True
    for marker in _CONDITIONAL_OPTIONAL_MARKERS:
        for match in re.finditer(rf"\b{marker}\b", window):
            if not _DOMAIN_CONTINUATION_RE.match(window[match.end() :]):
                return True
    return False


_GERMAN_MENTION_RE = re.compile(r"\bdeutsch\w*")


def _german_requirement_is_softened_everywhere(text: str) -> bool:
    """Все упоминания немецкого в тексте поданы как пожелание.

    Нужна отдельно от _requirement_is_mandatory, потому что часть сигналов о
    немецком приходит готовым флагом из language_signal_extractor, и позиции
    совпадения там уже нет — проверить окно можно только по самому тексту.
    """
    mentions = list(_GERMAN_MENTION_RE.finditer(text))
    if not mentions:
        return False
    return all(
        _window_softens_requirement(
            text[max(0, match.start() - _QUALIFICATION_OPTIONALITY_WINDOW) : match.end() + _QUALIFICATION_OPTIONALITY_WINDOW]
        )
        for match in mentions
    )


def _requirement_is_mandatory(text: str, rule: TextRule) -> bool:
    """Требование найдено И хотя бы одно упоминание не смягчено соседними словами.

    Проверять наличие слова недостаточно: "Berufsausbildung wünschenswert" — это
    приглашение, а не барьер, и отклонять по нему вакансию значит терять
    подходящую работу. Обратная ошибка не менее дорога: пропущенное обязательное
    требование выводит в горячие работу, на которую не возьмут.
    """
    for pattern in rule.patterns:
        for match in re.finditer(pattern, text):
            start = max(0, match.start() - _QUALIFICATION_OPTIONALITY_WINDOW)
            window = text[start : match.end() + _QUALIFICATION_OPTIONALITY_WINDOW]
            if not _window_softens_requirement(window):
                return True
    return False
STRONG_EXPERIENCE_RULE = TextRule(
    code="experience_requirement",
    label_ru="просят заметный профильный опыт",
    patterns=(
        r"\bmehrjahrige erfahrung\b",
        r"\bmindestens\s+[23]\s+jahre\b",
        r"\b(?:3|5)\s+years? of experience\b",
        r"\bberufserfahrung\b.*\b(?:erforderlich|required|must)\b",
    ),
)
ENTRY_LEVEL_RULE = TextRule(
    code="entry_level_signal",
    label_ru="без жесткого упора на опыт",
    patterns=(
        r"\bquereinsteiger\w*",
        r"\bberufseinsteiger\w*",
        r"\bkeine erfahrung\b",
        r"\bohne erfahrung\b",
        r"\bentry level\b",
        r"\btraining on the job\b",
    ),
)
RELOCATION_RULE = TextRule(
    code="relocation_signal",
    label_ru="есть помощь с переездом или жильем",
    patterns=(
        r"\bunterkunft\w*",
        r"\bwohnheim\b",
        r"\bwohnung\b",
        r"\baccommodation\b",
        r"\brelocation\b",
        r"\bumzug\w*",
    ),
)
IMMEDIATE_START_RULE = TextRule(
    code="immediate_start_signal",
    label_ru="можно быстро выйти",
    patterns=(r"\bab sofort\b", r"\bsofortiger eintritt\b", r"\bimmediate start\b", r"\bstart sofort\b"),
)
SPONSORSHIP_REVIEW_RULE = TextRule(
    code="sponsorship_review",
    label_ru="есть вопросы по допуску к работе",
    patterns=(
        r"\bvisa\w*",
        r"\bsponsorship\b",
        r"\barbeitserlaubnis\b",
        r"\bwork permit\b",
        r"\bresidence permit\b",
        r"\beu citizenship\b",
    ),
)

_ROLE_SUFFIX_TOKENS = frozenset({
    "developer",
    "entwickler",
    "entwicklerin",
    "softwareentwickler",
    "softwareentwicklerin",
})

_POSITIVE_ROLE_PROFILE_ALIASES: dict[str, tuple[TextRule, ...]] = {
    "склад": (POSITIVE_ROLE_FAMILIES[0],),
    "warehouse": (POSITIVE_ROLE_FAMILIES[0],),
    "lager": (POSITIVE_ROLE_FAMILIES[0],),
    "логист": (POSITIVE_ROLE_FAMILIES[1],),
    "logistik": (POSITIVE_ROLE_FAMILIES[1],),
    "упаков": (POSITIVE_ROLE_FAMILIES[2],),
    "verpack": (POSITIVE_ROLE_FAMILIES[2],),
    "packer": (POSITIVE_ROLE_FAMILIES[2],),
    "packing": (POSITIVE_ROLE_FAMILIES[2],),
    "производ": (POSITIVE_ROLE_FAMILIES[3],),
    "produktion": (POSITIVE_ROLE_FAMILIES[3],),
    "помощ": (POSITIVE_ROLE_FAMILIES[4],),
    "helper": (POSITIVE_ROLE_FAMILIES[4],),
    "helfer": (POSITIVE_ROLE_FAMILIES[4],),
    "курьер": (POSITIVE_ROLE_FAMILIES[5],),
    "водитель": (POSITIVE_ROLE_FAMILIES[5],),
    "fahrer": (POSITIVE_ROLE_FAMILIES[5],),
    "kurier": (POSITIVE_ROLE_FAMILIES[5],),
    "zusteller": (POSITIVE_ROLE_FAMILIES[5],),
    "lieferfahrer": (POSITIVE_ROLE_FAMILIES[5],),
}
_NEGATIVE_ROLE_PROFILE_ALIASES: dict[str, tuple[TextRule, ...]] = {
    "мед": (NEGATIVE_ROLE_FAMILIES[0],),
    "pflege": (NEGATIVE_ROLE_FAMILIES[0],),
    "уход": (NEGATIVE_ROLE_FAMILIES[0],),
    "it": (NEGATIVE_ROLE_FAMILIES[1],),
    "програм": (NEGATIVE_ROLE_FAMILIES[1],),
    "software": (NEGATIVE_ROLE_FAMILIES[1],),
    "офис": (NEGATIVE_ROLE_FAMILIES[2],),
    "admin": (NEGATIVE_ROLE_FAMILIES[2],),
    "продаж": (NEGATIVE_ROLE_FAMILIES[3],),
    "sales": (NEGATIVE_ROLE_FAMILIES[3],),
}


# Ниже этого объёма текста описания утверждать «обязательный немецкий не указан» нельзя:
# это не вывод из текста, а отсутствие текста. Часть источников отдаёт вакансию вообще без
# описания, и тогда сигнал срабатывал на одном заголовке — а в карточке показывался как
# «Главный приоритет».
_MIN_BODY_CHARS_FOR_ABSENCE_CLAIM = 120

# Признаки того, что перед нами не всё объявление, а вырезка из него.
# Careerjet отдаёт фрагмент, ЦЕНТРИРОВАННЫЙ вокруг поискового слова, поэтому окно
# сдвигается от запроса к запросу и требования часто остаются за его краем. Adzuna
# режет описание на 500 символах и ставит многоточие.
#
# Реальный случай: у вакансии почтальона DHL строка «Du kannst dich auf Deutsch
# unterhalten» стояла ВЫШЕ начала вырезки, движок получил 651 символ с середины фразы
# — и карточка уверенно показывала «обязательный немецкий не указан».
_EXCERPT_TAIL_MARKERS = ("…", "...")


def _looks_like_partial_excerpt(body: str) -> bool:
    """Вырезка из объявления, а не всё объявление целиком.

    Эвристика намеренно простая: обрыв в конце и начало с середины фразы. Она не ловит
    вырезку, которая случайно начинается и заканчивается по границам предложений —
    в этом случае утверждение об отсутствии требования всё ещё может быть неверным.
    """
    stripped = body.strip()
    if not stripped:
        return True
    if stripped.endswith(_EXCERPT_TAIL_MARKERS):
        return True
    first = stripped[0]
    # Немецкое объявление начинается с заглавной буквы: существительные и первое слово
    # предложения пишутся с большой. Строчная в начале — верный признак обрыва.
    return first.isalpha() and first.islower()


def _has_analyzable_body(canonical: CanonicalVacancyGroup) -> bool:
    """Есть ли текст, по которому вообще можно судить об ОТСУТСТВИИ требования.

    Утверждать «в вакансии не сказано про немецкий» можно только по цельному описанию.
    По вырезке можно утверждать обратное — что требование НАЙДЕНО, — и это работает как
    прежде: найденное в куске текста остаётся найденным.
    """
    for record in canonical.source_records:
        body = (record.body_text or "").strip()
        if len(body) < _MIN_BODY_CHARS_FOR_ABSENCE_CLAIM:
            continue
        if _looks_like_partial_excerpt(body):
            continue
        return True
    return False


def inspect_vacancy(canonical: CanonicalVacancyGroup, profile: SearchProfileContext) -> VacancySignalSnapshot:
    combined_text = build_combined_text(canonical)
    title_text = build_title_text(canonical)
    positive_role_hits = _keep_profile_relevant_role_hits(
        _match_rules(combined_text, POSITIVE_ROLE_FAMILIES),
        profile,
    )
    positive_role_hits_in_title = _keep_profile_relevant_role_hits(
        _match_rules(title_text, POSITIVE_ROLE_FAMILIES),
        profile,
    )
    negative_role_hits = _match_rules(combined_text, NEGATIVE_ROLE_FAMILIES)

    desired_role_hits = _dedupe_hits([
        *_match_profile_roles(
            combined_text=combined_text,
            raw_roles=profile.desired_roles,
            alias_catalog=_POSITIVE_ROLE_PROFILE_ALIASES,
            fallback_code="desired_role_match",
            fallback_label="совпадает с профилем поиска",
        ),
        *_match_profile_roles(
            combined_text=combined_text,
            raw_roles=profile.search_query_terms,
            alias_catalog={},
            fallback_code="search_query_term_match",
            fallback_label="совпадает с поисковыми терминами профиля",
        ),
    ])
    desired_role_hits_in_title = _dedupe_hits([
        *_match_profile_roles(
            combined_text=title_text,
            raw_roles=profile.desired_roles,
            alias_catalog=_POSITIVE_ROLE_PROFILE_ALIASES,
            fallback_code="desired_role_match",
            fallback_label="совпадает с профилем поиска",
        ),
        *_match_profile_roles(
            combined_text=title_text,
            raw_roles=profile.search_query_terms,
            alias_catalog={},
            fallback_code="search_query_term_match",
            fallback_label="совпадает с поисковыми терминами профиля",
        ),
    ])
    excluded_role_hits = _match_excluded_profile_roles(
        title_text=title_text,
        combined_text=combined_text,
        raw_roles=profile.excluded_roles,
    )
    driver_license_requirement = extract_driver_license_requirements(build_driver_license_text(canonical))
    vehicle_class_signals = extract_vehicle_class_signals(build_vehicle_class_text(canonical))
    location_match, location_hits = _match_profile_locations(canonical, profile)
    distance_from_home, distance_from_search = _measure_distances(canonical, profile)

    # Немецкий проверяется так же, как квалификация: важно не наличие слова, а
    # есть ли рядом смягчение. Без этого "Deutsch B1 wünschenswert" читалось как
    # жёсткое требование и отсекало вакансию, куда берут и без B1.
    german_requirement_softened = _german_requirement_is_softened_everywhere(combined_text)
    strong_german_required = _requirement_is_mandatory(combined_text, STRONG_GERMAN_REQUIREMENT_RULE) or (
        canonical.language_signals.strong_german_required and not german_requirement_softened
    )
    german_any_required = _requirement_is_mandatory(combined_text, GERMAN_ANY_REQUIRED_RULE)
    german_not_required_signal = (
        not strong_german_required and _matches_rule(combined_text, GERMAN_NOT_REQUIRED_RULE)
    )
    basic_german_signal = not strong_german_required and _matches_rule(combined_text, BASIC_GERMAN_RULE)
    english_required_signal = canonical.language_signals.english_required or _matches_rule(
        combined_text,
        ENGLISH_REQUIRED_RULE,
    )
    english_preferred_signal = (
        not english_required_signal
        and (canonical.language_signals.english_preferred or _matches_rule(combined_text, ENGLISH_PREFERRED_RULE))
    )
    ai_tools_language_fit_signal = (
        is_ai_tools_profile(profile)
        and not english_required_signal
        and (not german_any_required or basic_german_signal)
        and not strong_german_required
    )

    return VacancySignalSnapshot(
        combined_text=combined_text,
        positive_role_hits=positive_role_hits,
        positive_role_hits_in_title=positive_role_hits_in_title,
        desired_role_hits_in_title=desired_role_hits_in_title,
        negative_role_hits=negative_role_hits,
        desired_role_hits=desired_role_hits,
        excluded_role_hits=excluded_role_hits,
        required_driver_license_categories=driver_license_requirement.required,
        allowed_driver_license_categories=driver_license_requirement.allowed,
        optional_driver_license_categories=driver_license_requirement.optional,
        mentioned_driver_license_categories=driver_license_requirement.mentioned,
        distance_from_home_km=distance_from_home,
        distance_from_search_location_km=distance_from_search,
        light_commercial_vehicle_signals=vehicle_class_signals.light_commercial,
        heavy_vehicle_signals=vehicle_class_signals.heavy_vehicle,
        heavy_vehicle_context_signals=vehicle_class_signals.heavy_vehicle_context,
        heavy_driver_qualification_signals=vehicle_class_signals.heavy_qualification,
        location_match=location_match,
        location_hits=location_hits,
        strong_german_required=strong_german_required,
        german_any_required=german_any_required,
        low_language_signal=canonical.language_signals.low_language_signal or _matches_rule(combined_text, LOW_LANGUAGE_RULE),
        german_not_required_signal=german_not_required_signal,
        basic_german_signal=basic_german_signal,
        no_mandatory_german_mentioned=(
            not strong_german_required
            and not german_any_required
            and _has_analyzable_body(canonical)
        ),
        english_required_signal=english_required_signal,
        english_preferred_signal=english_preferred_signal,
        ai_tools_language_fit_signal=ai_tools_language_fit_signal,
        ukrainian_welcome_signal=_matches_rule(combined_text, UKRAINIAN_WELCOME_RULE),
        shift_signal=canonical.language_signals.shift_signal or _matches_rule(combined_text, SHIFT_RULE),
        relocation_signal=_matches_rule(combined_text, RELOCATION_RULE),
        immediate_start_signal=_matches_rule(combined_text, IMMEDIATE_START_RULE),
        degree_required=_requirement_is_mandatory(combined_text, DEGREE_REQUIRED_RULE),
        vocational_training_required=_requirement_is_mandatory(combined_text, VOCATIONAL_REQUIRED_RULE),
        strong_experience_required=_matches_rule(combined_text, STRONG_EXPERIENCE_RULE),
        entry_level_signal=_matches_rule(combined_text, ENTRY_LEVEL_RULE),
        sponsorship_ambiguity=_matches_rule(combined_text, SPONSORSHIP_REVIEW_RULE),
    )


def build_combined_text(canonical: CanonicalVacancyGroup) -> str:
    parts: list[str] = [
        canonical.normalized_title,
        canonical.company_name or "",
        canonical.location_text or "",
        canonical.city or "",
        canonical.country_code or "",
    ]
    for record in canonical.source_records:
        parts.extend(
            (
                record.original_title,
                record.original_company or "",
                record.original_location or "",
                record.body_text or "",
            )
        )
    return normalize_text_for_fingerprint(" ".join(part for part in parts if part))


def build_title_text(canonical: CanonicalVacancyGroup) -> str:
    parts: list[str] = [canonical.normalized_title]
    parts.extend(record.original_title for record in canonical.source_records)
    return normalize_text_for_fingerprint(" ".join(part for part in parts if part))


def build_driver_license_text(canonical: CanonicalVacancyGroup) -> str:
    parts: list[str] = [canonical.normalized_title]
    for record in canonical.source_records:
        parts.extend((record.original_title, record.body_text or ""))
    return "\n".join(part for part in parts if part)


def build_vehicle_class_text(canonical: CanonicalVacancyGroup) -> str:
    parts: list[str] = [canonical.normalized_title]
    for record in canonical.source_records:
        parts.extend((record.original_title, record.body_text or ""))
        if isinstance(record.raw_payload, dict):
            main_occupation = record.raw_payload.get("hauptberuf")
            if isinstance(main_occupation, str) and _should_include_main_occupation(
                main_occupation=main_occupation,
                vacancy_text="\n".join(part for part in parts if part),
            ):
                parts.append(main_occupation)
    return "\n".join(part for part in parts if part)


def _should_include_main_occupation(*, main_occupation: str, vacancy_text: str) -> bool:
    vacancy_signals = extract_vehicle_class_signals(vacancy_text)
    if not vacancy_signals.light_commercial:
        return True

    occupation_signals = extract_vehicle_class_signals(main_occupation)
    return occupation_signals.heavy_vehicle != ("Berufskraftfahrer",)


def _keep_profile_relevant_role_hits(
    hits: tuple[RuleHit, ...],
    profile: SearchProfileContext,
) -> tuple[RuleHit, ...]:
    """Drop blue-collar role hits that no desired role of the profile is asking for.

    Only families the profile itself declares are used, so a profile whose roles do not
    classify into any specific family keeps every hit — the gate stays conservative.
    """
    query_families = {
        family
        for family in classify_desired_roles(profile.desired_roles)
        if is_specific_family(family)
    }
    if not query_families:
        return hits

    return tuple(
        hit
        for hit in hits
        if (hit_family := POSITIVE_ROLE_HIT_FAMILIES.get(hit.code)) is None
        or any(families_are_compatible(query_family, hit_family) for query_family in query_families)
    )


def _match_rules(text: str, rules: tuple[TextRule, ...]) -> tuple[RuleHit, ...]:
    hits: list[RuleHit] = []
    for rule in rules:
        if _matches_rule(text, rule):
            hits.append(RuleHit(code=rule.code, label_ru=rule.label_ru))
    return tuple(hits)


def _matches_rule(text: str, rule: TextRule) -> bool:
    return any(re.search(pattern, text) for pattern in rule.patterns)


def _match_profile_roles(
    *,
    combined_text: str,
    raw_roles: tuple[str, ...],
    alias_catalog: dict[str, tuple[TextRule, ...]],
    fallback_code: str,
    fallback_label: str,
) -> tuple[RuleHit, ...]:
    hits: list[RuleHit] = []
    for raw_role in raw_roles:
        normalized_role = normalize_profile_text(raw_role)
        if not normalized_role:
            continue

        matched_alias = False
        for alias, rules in alias_catalog.items():
            if alias not in normalized_role:
                continue
            matched_alias = True
            for rule in rules:
                if _matches_rule(combined_text, rule):
                    hits.append(RuleHit(code=rule.code, label_ru=fallback_label))
                    break

        if matched_alias:
            continue

        role_tokens = tuple(
            token
            for token in normalized_role.split()
            if len(token) >= 3 and token not in _ROLE_SUFFIX_TOKENS
        )
        if role_tokens and all(token in combined_text for token in role_tokens):
            hits.append(RuleHit(code=fallback_code, label_ru=fallback_label))

    return _dedupe_hits(hits)


def _match_excluded_profile_roles(
    *,
    title_text: str,
    combined_text: str,
    raw_roles: tuple[str, ...],
) -> tuple[RuleHit, ...]:
    hits: list[RuleHit] = []
    for raw_role in raw_roles:
        normalized_role = normalize_profile_text(raw_role)
        if not normalized_role:
            continue
        if _is_language_requirement_exclusion(normalized_role):
            continue
        if _is_physical_domain_exclusion(normalized_role) and _looks_like_it_software_role(combined_text):
            continue
        role_tokens = tuple(token for token in normalized_role.split() if len(token) >= 3)
        if role_tokens and all(token in title_text for token in role_tokens):
            hits.append(RuleHit(code="excluded_role_match", label_ru="роль исключена профилем"))
    return _dedupe_hits(hits)


def _is_language_requirement_exclusion(normalized_role: str) -> bool:
    language_tokens = ("english", "german", "deutsch", "английск", "немецк")
    level_tokens = ("advanced", "fluent", "c1", "c2", "b2", "свобод", "продвинут")
    return any(token in normalized_role for token in language_tokens) and any(
        token in normalized_role for token in level_tokens
    )


def _is_physical_domain_exclusion(normalized_role: str) -> bool:
    return any(token in normalized_role for token in ("warehouse", "lager", "production", "produktion", "manual physical"))


def _looks_like_it_software_role(text: str) -> bool:
    return any(
        token in text
        for token in (
            "software",
            "developer",
            "entwickler",
            "python",
            "backend",
            "fastapi",
            "api",
            "machine learning",
            " ml ",
            "automation",
        )
    )


def _measure_distances(
    canonical: CanonicalVacancyGroup,
    profile: SearchProfileContext,
) -> tuple[float | None, float | None]:
    """Дорога до вакансии от дома и от города поиска, в километрах.

    Два разных расстояния, потому что это два разных вопроса. От дома — сколько
    реально ездить; это штраф в скоринге. От города поиска — попадает ли вакансия
    в радиус, который пользователь явно задал в форме; это жёсткий фильтр.
    """
    if is_remote_worldwide_location(profile.preferred_locations):
        return None, None

    vacancy_point = resolve_point(
        city=canonical.city,
        location_text=canonical.location_text,
    )
    if vacancy_point is None:
        return None, None

    home_point = resolve_point(city=profile.home_city) if profile.home_city else None
    search_point = resolve_point(city=profile.search_location) if profile.search_location else None
    return distance_km(home_point, vacancy_point), distance_km(search_point, vacancy_point)


def _match_profile_locations(
    canonical: CanonicalVacancyGroup,
    profile: SearchProfileContext,
) -> tuple[bool | None, tuple[str, ...]]:
    if not profile.preferred_locations:
        return None, ()

    location_text = normalize_text_for_fingerprint(
        " ".join(part for part in (canonical.location_text, canonical.city, canonical.country_code) if part)
    )
    if is_remote_worldwide_location(profile.preferred_locations):
        if any(token in location_text for token in ("remote", "worldwide", "anywhere", "global", "emea", "europe")):
            return True, ("worldwide remote",)
        return None, ()

    matched_locations: list[str] = []
    for preferred in profile.preferred_locations:
        normalized_preferred = normalize_profile_text(preferred)
        if not normalized_preferred:
            continue
        if normalized_preferred in {"deutschland", "germany", "germania", "германия"} and canonical.country_code == "DE":
            matched_locations.append(preferred)
            continue
        tokens = tuple(token for token in normalized_preferred.split() if len(token) >= 2)
        if tokens and all(token in location_text for token in tokens):
            matched_locations.append(preferred)

    return bool(matched_locations), tuple(matched_locations)


def _dedupe_hits(hits: list[RuleHit]) -> tuple[RuleHit, ...]:
    seen: set[str] = set()
    ordered: list[RuleHit] = []
    for hit in hits:
        if hit.code in seen:
            continue
        seen.add(hit.code)
        ordered.append(hit)
    return tuple(ordered)
