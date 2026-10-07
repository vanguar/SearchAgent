from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.relevance_config import get_relevance_config
from app.services.ai_tools_profile import is_ai_tools_profile
from app.services.commute_signals import has_early_shift
from app.services.driver_license_signal_extractor import extract_driver_license_requirements
from app.services.employer_branch import branch_location_conflict
from app.services.employment_signal_extractor import extract_employment_signals
from app.services.geo_distance import (
    GeoPoint,
    distance_km,
    location_matches_city,
    names_other_country,
    resolve_point,
)
from app.services.hashers import normalize_text_for_fingerprint
from app.services.language_signal_extractor import (
    ENGLISH_BENEFIT_PATTERNS,
    ENGLISH_PREFERRED_PATTERNS,
    ENGLISH_REQUIRED_PATTERNS,
)
from app.services.license_requirement_signals import extract_license_requirement_signals
from app.services.light_goods_transport import (
    employment_evidence_text,
    mandatory_vehicle_evidence_text,
    match_light_goods_transport,
)
from app.services.normalization_models import CanonicalVacancyGroup
from app.services.role_family import (
    RoleFamily,
    classify_role_families,
    classify_vacancy_de,
    families_are_compatible,
    is_specific_family,
)
from app.services.salary_signal_extractor import extract_canonical_salary_signals
from app.services.search_models import (
    RuleHit,
    SearchProfileContext,
    VacancySignalSnapshot,
    normalize_profile_text,
    profile_role_texts,
)
from app.services.search_normalizer import is_remote_worldwide_location
from app.services.signal_negation import mask_negated_signals, normalize_signal_text
from app.services.transport_mode_signals import extract_transport_mode_signals
from app.services.vehicle_class_signal_extractor import extract_vehicle_class_signals
from app.services.work_authorization_signals import (
    extract_work_authorization_requirements,
    unmet_work_authorization,
)

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
        code="vehicle_logistics_family",
        label_ru="роль в автомобильной логистике",
        # Перегон и перестановка автомобилей. Только однозначные составные формы:
        # голое "fahrzeug" стоит и в "Fahrzeugbau" (производство), и в
        # "Fahrzeugtechniker" (автомеханик) — это другая работа.
        patterns=(
            r"\bfahrzeug(?:uberfuhr|verbring|ruckfuhr|umsetz|logistik|rangier|transfer)\w*",
            r"\buberfuhrungsfahrer\w*",
            r"\b(?:mietwagen)?uberfuhrer\w*",
            r"\bruckfuhrungsfahrer\w*",
            r"\bumsetzfahrer\w*",
            r"\brangierfahrer\w*",
            r"\b(?:pkw|kfz|auto)\s+rangierer\w*",
            r"\brangierer\b",
            r"\bwerkstattfahrer\w*",
            r"\bhol\s+und\s+bringservice\b",
            r"\bhol\s+und\s+bringfahrer\b",
            r"\bhol\s+und\s+bring\s+service\b",
            r"\bautologistik\w*",
            r"\b(?:fahrzeug|auto)transport\w*",
        ),
    ),
    TextRule(
        code="delivery_driving_family",
        label_ru="роль в доставке или вождении",
        # Погрузчик исключён явно: "Staplerfahrer" кончается на "-fahrer", но это
        # складская работа, а не дорожная. Без исключения водительский профиль
        # считал бы складские вакансии своими. По той же причине исключены
        # операторы техники: "Baggerfahrer", "Kranfahrer", "Radladerfahrer" водят
        # машину, которая не выезжает со стройки, поля или площадки.
        patterns=(
            r"\b(?!\w*(?:stapler|schubmast))"
            r"(?!\w*(?:bagger|radlader|kran|walzen|raupen|maschinen|traktor|schlepper|mahdrescher)fahrer)"
            r"\w*fahrer\w*",
            # Составные названия доставки: "Paketzusteller", "Briefzusteller",
            # "Expresskurier". С "\b" перед корнем они не находились, и самые
            # частые курьерские заголовки держались только на словах из тела.
            r"\b\w*zusteller\w*",
            r"\b(?:paket|brief|post|zeitungs|express)zustellung\w*",
            r"\b(?:paket|brief|post)bote\w*",
            r"\bausliefer\w*",
            r"\b\w*kurier\w*",
            r"\blieferfahrer\w*",
            r"\bkraftfahrer\w*",
            r"\bfahrzeugfuhrer\w*",
            # Английские заголовки той же работы. Только составные формы: голое
            # "driver" в IT-объявлении — это драйвер устройства.
            r"\b(?:delivery|van|courier|parcel)\s+drivers?\b",
            r"\bcouriers?\b",
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
    "vehicle_logistics_family": RoleFamily.VEHICLE_LOGISTICS,
}

# Правила по коду. Раньше алиасы профиля ссылались на POSITIVE_ROLE_FAMILIES по
# НОМЕРУ в кортеже, и добавление нового семейства в середину списка молча
# переназначало «водитель» на чужое правило. Имя не сдвигается.
POSITIVE_ROLE_RULES_BY_CODE: dict[str, TextRule] = {rule.code: rule for rule in POSITIVE_ROLE_FAMILIES}


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
        r"\bdeutschkenntnisse\b(?!\s+(?:auf\s+)?a[12]\b)(?!\s*.{0,30}\b(?:nicht|keine)\s+(?:erforderlich|notwendig)\b).{0,80}\b(?:erforderlich|vorausgesetzt|zwingend|required|mandatory|must)\b",
        # Уверенный уровень, записанный без слова "Kenntnisse". Эти формы
        # встречаются в объявлениях не реже канонических и пропускались целиком.
        r"(?<!kein )(?<!keine )(?<!nicht )(?<!ohne )\bdeutsch\s+flie(?:ss|s)end\b",
        r"\bmuttersprach\w*\s+(?:niveau\s+)?deutsch\b",
        r"\bdeutsch\s+(?:auf\s+)?muttersprach\w*(?:\s+niveau)?\b",
        r"\bsichere[rn]?\s+umgang\s+mit\s+der\s+deutschen\s+sprache\b",
        r"\bexzellente\s+deutschkenntnisse\b",
        r"(?<!basic )\bgerman\b[^;]*\b(?:required|must|mandatory)\b",
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
        r"\bdeutschkenntnisse\b(?!\s+(?:auf\s+)?a[12]\b)(?!\s*.{0,30}\b(?:nicht|keine)\s+(?:erforderlich|notwendig)\b)"
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
    patterns=ENGLISH_REQUIRED_PATTERNS,
)
ENGLISH_PREFERRED_RULE = TextRule(
    code="english_preferred_signal",
    label_ru="английский указан как пожелание",
    patterns=ENGLISH_PREFERRED_PATTERNS,
)
ENGLISH_BENEFIT_RULE = TextRule(
    code="english_benefit_signal",
    label_ru="английский упомянут как соцпакет",
    patterns=ENGLISH_BENEFIT_PATTERNS,
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
        r"\bgrundkenntnisse\s+(?:in\s+)?(?:der\s+)?deutsch\w*(?:\s+sprache)?\b",
        r"\bdeutsch\w*\s+grundkenntnisse\b",
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
        r"\b(?:nur\s+)?grundkenntnisse\s+(?:in\s+)?(?:der\s+)?deutsch\w*(?:\s+sprache)?\b",
        r"\bdeutsch\w*\s+(?:grund|basis)kenntnisse\b",
        r"\b(?:einfache|geringe) deutschkenntnisse\b",
        r"\bbasiskenntnisse\s+(?:in\s+)?(?:der\s+)?deutsch\w*(?:\s+sprache)?\b",
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


_ENGLISH_MENTION_RE = re.compile(r"\benglish\b")


def _english_requirement_is_softened_everywhere(text: str) -> bool:
    """Все упоминания английского поданы как пожелание или как соцпакет.

    Нужна отдельно от _requirement_is_mandatory по той же причине, что и немецкий
    аналог: часть сигнала приходит готовым флагом из language_signal_extractor, где
    позиция совпадения уже потеряна. Дополнительно к обычному смягчению здесь
    учитывается benefit-контекст: "english courses with a native speaker" — это
    корпоративная плюшка, а не языковой барьер.
    """
    mentions = list(_ENGLISH_MENTION_RE.finditer(text))
    if not mentions:
        return False
    return all(
        _english_mention_is_soft(
            text[max(0, match.start() - _QUALIFICATION_OPTIONALITY_WINDOW) : match.end() + _QUALIFICATION_OPTIONALITY_WINDOW]
        )
        for match in mentions
    )


def _english_mention_is_soft(window: str) -> bool:
    return _window_softens_requirement(window) or _matches_rule(window, ENGLISH_BENEFIT_RULE)


def _english_requirement_is_mandatory(text: str) -> bool:
    """Требование по английскому найдено и хотя бы одно упоминание не смягчено.

    Отличается от общего _requirement_is_mandatory тем, что окно проверяется ещё и
    на benefit-контекст: без этого "english courses" в блоке бонусов читалось бы как
    обязательный язык.
    """
    for pattern in ENGLISH_REQUIRED_RULE.patterns:
        for match in re.finditer(pattern, text):
            start = max(text.rfind(";", 0, match.start()) + 1, match.start() - _QUALIFICATION_OPTIONALITY_WINDOW)
            boundary = text.find(";", match.end())
            end = boundary if boundary >= 0 else len(text)
            window = text[start : min(end, match.end() + _QUALIFICATION_OPTIONALITY_WINDOW)]
            if not _english_mention_is_soft(window):
                return True
    return False


def _requirement_is_mandatory(text: str, rule: TextRule) -> bool:
    """Требование найдено И хотя бы одно упоминание не смягчено соседними словами.

    Проверять наличие слова недостаточно: "Berufsausbildung wünschenswert" — это
    приглашение, а не барьер, и отклонять по нему вакансию значит терять
    подходящую работу. Обратная ошибка не менее дорога: пропущенное обязательное
    требование выводит в горячие работу, на которую не возьмут.
    """
    for pattern in rule.patterns:
        for match in re.finditer(pattern, text):
            start = max(text.rfind(";", 0, match.start()) + 1, match.start() - _QUALIFICATION_OPTIONALITY_WINDOW)
            boundary = text.find(";", match.end())
            end = boundary if boundary >= 0 else len(text)
            window = text[start : min(end, match.end() + _QUALIFICATION_OPTIONALITY_WINDOW)]
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

_ROLE_SUFFIX_TOKENS = frozenset({
    "developer",
    "entwickler",
    "entwicklerin",
    "softwareentwickler",
    "softwareentwicklerin",
})

_POSITIVE_ROLE_PROFILE_ALIASES: dict[str, tuple[TextRule, ...]] = {
    alias: (POSITIVE_ROLE_RULES_BY_CODE[code],)
    for alias, code in (
        ("склад", "warehouse_family"),
        ("warehouse", "warehouse_family"),
        ("lager", "warehouse_family"),
        ("логист", "logistics_family"),
        ("logistik", "logistics_family"),
        ("упаков", "packaging_family"),
        ("verpack", "packaging_family"),
        ("packer", "packaging_family"),
        ("packing", "packaging_family"),
        ("производ", "production_family"),
        ("produktion", "production_family"),
        ("помощ", "helper_family"),
        ("helper", "helper_family"),
        ("helfer", "helper_family"),
        ("курьер", "delivery_driving_family"),
        ("водитель", "delivery_driving_family"),
        ("fahrer", "delivery_driving_family"),
        ("kurier", "delivery_driving_family"),
        ("zusteller", "delivery_driving_family"),
        ("lieferfahrer", "delivery_driving_family"),
        # Автомобильная логистика — своё направление, а не доставка.
        ("перегон", "vehicle_logistics_family"),
        ("перегонщик", "vehicle_logistics_family"),
        ("автологистик", "vehicle_logistics_family"),
        ("uberfuhr", "vehicle_logistics_family"),
        ("uberfuhrung", "vehicle_logistics_family"),
        ("fahrzeuglogistik", "vehicle_logistics_family"),
        ("rangierer", "vehicle_logistics_family"),
        ("umsetzfahrer", "vehicle_logistics_family"),
        ("werkstattfahrer", "vehicle_logistics_family"),
    )
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
        if record.description_complete is not True:
            continue
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
    # A specific contrary title outweighs incidental transfer vocabulary in the body.
    # Mixed transfer/delivery titles still classify as vehicle logistics.
    title_family = classify_vacancy_de(title_text)
    role_text = combined_text
    if (
        classify_role_families(profile_role_texts(profile)) == frozenset({RoleFamily.VEHICLE_LOGISTICS})
        and is_specific_family(title_family)
        and title_family is not RoleFamily.VEHICLE_LOGISTICS
    ):
        role_text = title_text
    positive_role_hits = _keep_profile_relevant_role_hits(
        _match_rules(role_text, POSITIVE_ROLE_FAMILIES),
        profile,
    )
    positive_role_hits_in_title = _keep_profile_relevant_role_hits(
        _match_rules(title_text, POSITIVE_ROLE_FAMILIES),
        profile,
    )
    negative_role_hits = _match_rules(combined_text, NEGATIVE_ROLE_FAMILIES)

    desired_role_hits = _dedupe_hits([
        *_match_profile_roles(
            combined_text=role_text,
            raw_roles=profile.desired_roles,
            alias_catalog=_POSITIVE_ROLE_PROFILE_ALIASES,
            fallback_code="desired_role_match",
            fallback_label="совпадает с профилем поиска",
        ),
        *_match_profile_roles(
            combined_text=role_text,
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
    # Generic aliases such as "fahrer" inside "Überführungsfahrer" must not
    # reintroduce the delivery family that the positive-role gate already removed.
    desired_role_hits = _keep_profile_relevant_role_hits(desired_role_hits, profile)
    desired_role_hits_in_title = _keep_profile_relevant_role_hits(desired_role_hits_in_title, profile)
    light_match = None
    if classify_role_families(profile.desired_roles or profile.search_query_terms) == frozenset({RoleFamily.LIGHT_GOODS_TRANSPORT}):
        light_match = match_light_goods_transport(
            canonical.normalized_title, "\n".join(record.body_text or "" for record in canonical.source_records),
        )
        if title_family not in {RoleFamily.GENERIC, RoleFamily.DRIVING, RoleFamily.LIGHT_GOODS_TRANSPORT}:
            light_match = "none"
        hit = RuleHit(code="light_goods_transport_family", label_ru="перевозка грузов на лёгком автомобиле")
        positive_role_hits = (hit,) if light_match != "none" else ()
        positive_role_hits_in_title = (hit,) if light_match == "target" else ()
        desired_role_hits = positive_role_hits
        desired_role_hits_in_title = positive_role_hits_in_title
    excluded_role_hits = _match_excluded_profile_roles(
        title_text=title_text,
        combined_text=combined_text,
        raw_roles=profile.excluded_roles,
    )
    license_text = build_driver_license_text(canonical)
    vehicle_text = build_vehicle_class_text(canonical)
    if light_match is not None:
        license_text = mandatory_vehicle_evidence_text(license_text)
        vehicle_text = mandatory_vehicle_evidence_text(vehicle_text)
    driver_license_requirement = extract_driver_license_requirements(license_text)
    license_signals = extract_license_requirement_signals(
        title=title_text,
        text=license_text,
        company=" ".join(
            dict.fromkeys(
                name for name in (canonical.company_name, *(r.original_company for r in canonical.source_records)) if name
            )
        ),
    )
    transport_signals = extract_transport_mode_signals(title=title_text, text=license_text)
    vehicle_class_signals = extract_vehicle_class_signals(vehicle_text)
    # Форма занятости, самозанятость и нагрузка читаются по ИСХОДНОМУ тексту:
    # combined_text уже свёрнут для поиска слов, а извлекателям нужны свои
    # нормализации (и оригинальная пунктуация — для суммы вида "15,50 €").
    raw_text = build_raw_analysis_text(canonical)
    employment_signals = extract_employment_signals(employment_evidence_text(raw_text) if light_match is not None else raw_text)
    salary_signals = extract_canonical_salary_signals(canonical, analysis_text=raw_text)
    location_match, location_hits = _match_profile_locations(canonical, profile)
    distance_from_home = _measure_home_distance(canonical, profile)
    matched_search_city, outside_requested_cities, distance_to_search_city = _match_requested_cities(
        canonical, profile
    )

    # Немецкий проверяется так же, как квалификация: важно не наличие слова, а
    # есть ли рядом смягчение. Без этого "Deutsch B1 wünschenswert" читалось как
    # жёсткое требование и отсекало вакансию, куда берут и без B1.
    german_text = mask_negated_signals(
        normalize_signal_text(raw_text), re.compile(r"\b(?:deutsch\w*|german)\b")
    )
    german_requirement_softened = _german_requirement_is_softened_everywhere(german_text)
    strong_german_required = _requirement_is_mandatory(german_text, STRONG_GERMAN_REQUIREMENT_RULE) or (
        canonical.language_signals.strong_german_required and not german_requirement_softened
    )
    german_any_required = _requirement_is_mandatory(german_text, GERMAN_ANY_REQUIRED_RULE)
    analyzable_body = _has_analyzable_body(canonical)
    # Тексты описаний к этому моменту очищены от шаблонных абзацев работодателя
    # (employer_boilerplate), поэтому облегчающие признаки опираются только на
    # текст конкретной вакансии.
    german_not_required_signal = (
        not strong_german_required and _matches_rule(combined_text, GERMAN_NOT_REQUIRED_RULE)
    )
    basic_german_signal = not strong_german_required and _matches_rule(combined_text, BASIC_GERMAN_RULE)
    low_language_signal = canonical.language_signals.low_language_signal or _matches_rule(
        combined_text, LOW_LANGUAGE_RULE
    )
    # Облегчение подтверждено, если оно видно в заголовке или описание целиком.
    # По вырезке оно правдоподобно, но не доказано: рядом может стоять «B2
    # erforderlich», которого во фрагмент не попало.
    language_relief_confirmed = analyzable_body or any(
        _matches_rule(title_text, rule) for rule in (GERMAN_NOT_REQUIRED_RULE, BASIC_GERMAN_RULE, LOW_LANGUAGE_RULE)
    )
    english_required_signal = _english_requirement_is_mandatory(combined_text) or (
        canonical.language_signals.english_required
        and not _english_requirement_is_softened_everywhere(combined_text)
    )
    english_preferred_signal = (
        not english_required_signal
        and (canonical.language_signals.english_preferred or _matches_rule(combined_text, ENGLISH_PREFERRED_RULE))
    )
    # Требование не найдено И судить об его отсутствии не по чему: описание слишком
    # короткое или это вырезка. Careerjet отдаёт фрагмент вокруг поискового слова,
    # Adzuna режет на 500 символах — там "не сказано про английский" ничего не значит.
    english_requirement_unknown = (
        not english_required_signal and not english_preferred_signal and not analyzable_body
    )
    ai_tools_language_fit_signal = (
        is_ai_tools_profile(profile)
        and not english_required_signal
        # Английский "желателен" — это уже языковая нагрузка, бонуса за её отсутствие
        # быть не должно: иначе упоминание английского поднимало бы вакансию выше той,
        # где английский не упомянут вовсе.
        and not english_preferred_signal
        # Бонус за "язык не требуется" можно давать только по цельному описанию:
        # по вырезке отсутствие требования не доказано.
        and not english_requirement_unknown
        and (not german_any_required or basic_german_signal)
        and not strong_german_required
    )
    work_authorization_risks = tuple(
        requirement.label_ru
        for requirement in unmet_work_authorization(
            extract_work_authorization_requirements(raw_text), work_authorized=profile.work_authorized
        )
    )
    role_confirmed = _role_confirmation(
        canonical,
        profile,
        light_match=light_match,
        title_confirmed=bool(positive_role_hits_in_title or desired_role_hits_in_title),
    )
    return VacancySignalSnapshot(
        role_confirmed=role_confirmed,
        early_shift_signal=has_early_shift(raw_text),
        employer_branch_conflict=_employer_branch_conflict(canonical),
        apprenticeship_signal=_is_apprenticeship_title(title_text),
        light_goods_transport_match=light_match,
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
        negated_driver_license_categories=driver_license_requirement.negated,
        title_driver_license_categories=license_signals.title_categories,
        requires_p_schein=license_signals.requires_p_schein,
        p_schein_optional=license_signals.p_schein_optional,
        medical_transport_required=license_signals.medical_transport_required,
        license_risk_markers=license_signals.risk_markers,
        title_transport_modes=transport_signals.title_modes,
        body_transport_modes=transport_signals.body_modes,
        car_in_title=transport_signals.car_in_title,
        car_mentioned=transport_signals.car_mentioned,
        press_distribution_signal=transport_signals.press_distribution,
        distance_from_home_km=distance_from_home,
        matched_search_city=matched_search_city,
        outside_requested_cities=outside_requested_cities,
        distance_to_search_city_km=distance_to_search_city,
        employment_types=employment_signals.employment_types,
        self_employment_signals=employment_signals.self_employment_signals,
        requires_self_employment=employment_signals.requires_self_employment,
        employed_contract_signal=bool(employment_signals.employed_contract_signals) or any(
            isinstance(record.raw_payload, dict) and record.raw_payload.get("contract_type") == "permanent"
            for record in canonical.source_records
        ),
        heavy_physical_signals=employment_signals.heavy_physical_signals,
        salary_mentioned=salary_signals.mentioned,
        salary_hourly_eur=salary_signals.hourly_eur,
        salary_period=salary_signals.period,
        salary_is_net=salary_signals.is_net,
        salary_is_comparable=salary_signals.is_comparable,
        salary_conflict=salary_signals.conflict,
        salary_evidence=salary_signals.evidence,
        salary_doubtful=salary_signals.doubtful,
        salary_doubtful_amounts=salary_signals.doubtful_amounts,
        salary_upper_bound=salary_signals.upper_bound_amount if salary_signals.upper_bound_only else None,
        light_commercial_vehicle_signals=vehicle_class_signals.light_commercial,
        heavy_vehicle_signals=vehicle_class_signals.heavy_vehicle,
        heavy_vehicle_context_signals=vehicle_class_signals.heavy_vehicle_context,
        heavy_driver_qualification_signals=vehicle_class_signals.heavy_qualification,
        location_match=location_match,
        location_hits=location_hits,
        strong_german_required=strong_german_required,
        german_any_required=german_any_required,
        low_language_signal=low_language_signal,
        language_relief_confirmed=language_relief_confirmed,
        german_not_required_signal=german_not_required_signal,
        basic_german_signal=basic_german_signal,
        description_insufficient=not analyzable_body,
        no_mandatory_german_mentioned=(
            not strong_german_required
            and not german_any_required
            and analyzable_body
        ),
        english_required_signal=english_required_signal,
        english_preferred_signal=english_preferred_signal,
        english_requirement_unknown=english_requirement_unknown,
        ai_tools_language_fit_signal=ai_tools_language_fit_signal,
        ukrainian_welcome_signal=_matches_rule(combined_text, UKRAINIAN_WELCOME_RULE),
        shift_signal=canonical.language_signals.shift_signal or _matches_rule(combined_text, SHIFT_RULE),
        relocation_signal=_matches_rule(combined_text, RELOCATION_RULE),
        immediate_start_signal=_matches_rule(combined_text, IMMEDIATE_START_RULE),
        degree_required=_requirement_is_mandatory(combined_text, DEGREE_REQUIRED_RULE),
        vocational_training_required=_requirement_is_mandatory(combined_text, VOCATIONAL_REQUIRED_RULE),
        strong_experience_required=_matches_rule(combined_text, STRONG_EXPERIENCE_RULE),
        entry_level_signal=_matches_rule(combined_text, ENTRY_LEVEL_RULE),
        sponsorship_ambiguity=bool(work_authorization_risks),
        work_authorization_risks=work_authorization_risks,
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


def build_raw_analysis_text(canonical: CanonicalVacancyGroup) -> str:
    """Исходный текст объявления без свёртывания.

    Нужен извлекателям, у которых своя нормализация: сумма «15,50 €» без запятой
    и знака валюты теряет смысл, а combined_text убирает всю пунктуацию.
    """
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
            if isinstance(main_occupation, str):
                parts.append(main_occupation)
    return "\n".join(part for part in parts if part)


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
        for family in classify_role_families(profile_role_texts(profile))
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


def _role_confirmation(
    canonical: CanonicalVacancyGroup,
    profile: SearchProfileContext,
    *,
    light_match: str | None,
    title_confirmed: bool,
) -> bool | None:
    """Названа ли искомая работа: в заголовке или неоднократно в описании.

    Одно упоминание в описании — не подтверждение: «Berliner Kurier» в подписи
    издательства превращал вакансию редактора в курьерскую. Смены, совпавший
    город и указанная оплата сами по себе роль не подтверждают.
    """
    if light_match is not None:
        return None
    query_families = {
        family for family in classify_role_families(profile_role_texts(profile)) if is_specific_family(family)
    }
    role_families = set(POSITIVE_ROLE_HIT_FAMILIES.values())
    if not query_families or not any(
        families_are_compatible(query_family, role_family)
        for query_family in query_families
        for role_family in role_families
    ):
        return None
    if title_confirmed:
        return True
    body = " ".join(
        normalize_text_for_fingerprint(record.body_text) for record in canonical.source_records if record.body_text
    )
    if not body:
        return False
    relevant_rules = [
        rule for rule in POSITIVE_ROLE_FAMILIES
        if _keep_profile_relevant_role_hits((RuleHit(code=rule.code, label_ru=rule.label_ru),), profile)
        and rule.code != "helper_family"
    ]
    mentions = sum(
        len(re.findall(pattern, body)) for rule in relevant_rules for pattern in rule.patterns
    )
    return mentions >= get_relevance_config().role_body_confirmation_min_mentions


def _is_apprenticeship_title(title_text: str) -> bool:
    return any(re.search(pattern, title_text) for pattern in get_relevance_config().apprenticeship_title_patterns)


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
        if role_tokens and all(_title_names_excluded_token(token, title_text) for token in role_tokens):
            hits.append(RuleHit(code="excluded_role_match", label_ru="роль исключена профилем"))
    return _dedupe_hits(hits)


# Исключённая доставка и тот, кто ею занят. Профиль пишет "Paketzustellung",
# заголовок — "Paketzusteller" или "Postbote", и подстрокой они не совпадают.
# Перечислены только эти формы: общее отсечение "-ung" превращало бы
# исключённую "Lieferung" в "liefer" и скрывало бы всех Lieferfahrer.
_EXCLUDED_ROLE_TITLE_FORMS: dict[str, tuple[str, ...]] = {
    "paketzustellung": ("paketzusteller",),
    "briefzustellung": ("briefzusteller",),
    "postzustellung": ("postzusteller", "postbote"),
}


def _title_names_excluded_token(token: str, title_text: str) -> bool:
    return any(form in title_text for form in (token, *_EXCLUDED_ROLE_TITLE_FORMS.get(token, ())))


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


def _measure_home_distance(
    canonical: CanonicalVacancyGroup,
    profile: SearchProfileContext,
) -> float | None:
    """Дорога от места жительства до вакансии в километрах.

    Отвечает на вопрос "сколько реально ездить" и работает штрафом в скоринге.
    Где именно искать, задаёт список городов, а не расстояние, — см.
    _match_requested_cities.
    """
    if is_remote_worldwide_location(profile.preferred_locations):
        return None
    if not profile.home_city:
        return None

    vacancy_point = _resolve_vacancy_point(canonical)
    if vacancy_point is None:
        return None
    return distance_km(resolve_point(city=profile.home_city), vacancy_point)


def _employer_branch_conflict(canonical: CanonicalVacancyGroup) -> tuple[str, float] | None:
    """Филиал работодателя далеко от места, указанного источником (см. employer_branch)."""
    for record in canonical.source_records:
        conflict = branch_location_conflict(
            record.original_company or record.normalized_company,
            city=record.normalized_location.city,
            postal_code=record.normalized_location.postal_code,
            location_text=record.normalized_location.raw_text or record.original_location,
        )
        if conflict is not None:
            return conflict
    return None


def _resolve_vacancy_point(canonical: CanonicalVacancyGroup) -> GeoPoint | None:
    vacancy_point = resolve_point(
        city=canonical.city,
        location_text=canonical.location_text,
    )
    if vacancy_point is not None:
        return vacancy_point
    # Каноническая запись хранит уже урезанную локацию; исходная строка
    # источника часто богаче ("Brinckmansdorf, Rostock" против "Brinckmansdorf").
    for record in canonical.source_records:
        vacancy_point = resolve_point(
            city=record.normalized_location.city,
            postal_code=record.normalized_location.postal_code,
            location_text=record.normalized_location.raw_text or record.original_location,
        )
        if vacancy_point is not None:
            return vacancy_point
    return None


def _match_requested_cities(
    canonical: CanonicalVacancyGroup,
    profile: SearchProfileContext,
) -> tuple[str | None, bool, float | None]:
    """Который из заказанных городов — город этой вакансии.

    Возвращает (город, "точно не наш", расстояние до ближайшего заказанного
    города). Третье состояние первых двух значений — (None, False) — означает
    "по локации не понять": работодатель не назвал место или назвал его так,
    чего нет в справочнике. Молчание не приравнивается к чужому городу:
    источник вернул вакансию по запросу конкретного города, и выбрасывать её
    только за неуказанный адрес значит терять живые вакансии.

    Радиус. Когда профиль задал search_radius_km, «наш город» — это не только
    город из списка, но и всё в пределах радиуса от него: человек, живущий в
    Нойштрелице и готовый ездить 50 км, хочет видеть и Нойбранденбург. Без
    радиуса (None или 0) правило работает как прежде — строго по названиям
    городов, и заказ «Berlin, Rostock» означает ровно эти два города.
    """
    if not profile.search_cities:
        return None, False, None

    location_texts = [canonical.location_text, canonical.city]
    location_texts.extend(
        text
        for record in canonical.source_records
        for text in (record.normalized_location.raw_text, record.original_location)
    )

    recognized_elsewhere = False
    for city in profile.search_cities:
        for text in location_texts:
            verdict = location_matches_city(text, city)
            if verdict:
                return city, False, 0.0
            if verdict is False:
                recognized_elsewhere = True

    # Названа другая страна — это однозначный ответ «не наш город», даже если
    # самого города нет в немецком справочнике. Без этой проверки «Kyiv, Ukraine»
    # и «Wien, AT» проходили как «по локации не понять» и оставались в выдаче по
    # Берлину.
    if any(
        names_other_country(text, country_code=canonical.country_code)
        for text in location_texts
        if text
    ):
        return None, True, None

    nearest_city, nearest_distance = _nearest_requested_city(canonical, profile)
    radius_km = profile.search_radius_km or 0
    if radius_km > 0 and nearest_distance is not None:
        if nearest_distance <= radius_km:
            return nearest_city, False, nearest_distance
        # Место известно и оно дальше радиуса — это уже точно не наш район, даже
        # если название города в справочнике не нашлось.
        return None, True, nearest_distance

    return None, recognized_elsewhere, nearest_distance


def _nearest_requested_city(
    canonical: CanonicalVacancyGroup,
    profile: SearchProfileContext,
) -> tuple[str | None, float | None]:
    """Ближайший из заказанных городов и расстояние до него в километрах."""
    vacancy_point = _resolve_vacancy_point(canonical)
    if vacancy_point is None:
        return None, None

    best_city: str | None = None
    best_distance: float | None = None
    for city in profile.search_cities:
        city_point = resolve_point(city=city)
        if city_point is None:
            continue
        distance = distance_km(city_point, vacancy_point)
        if distance is None:
            continue
        if best_distance is None or distance < best_distance:
            best_city, best_distance = city, distance
    return best_city, best_distance


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

    if not location_text:
        # Источник не сообщил место вообще. Это «не понять», а не «чужой город»:
        # False здесь означал жёсткое отклонение по location_mismatch, и вакансии
        # без локации молча выбрасывались у любого профиля с relocation_ready=False
        # — притом что источник вернул их именно по запросу нужного города.
        # Тот же принцип уже соблюдает _match_requested_cities.
        return None, ()

    # Те же строки, по которым города проверяет _match_requested_cities. Раньше
    # здесь брались только урезанные canonical-поля, и «Friedrichshain, Berlin»
    # превращалось в «friedrichshain»: токен «berlin» в тексте не находился, и
    # берлинская вакансия жёстко отклонялась по location_mismatch — при том что
    # проверка заказанных городов ту же вакансию принимала. Две географические
    # проверки обязаны отвечать одинаково.
    raw_location_texts = [
        text
        for text in (
            canonical.location_text,
            canonical.city,
            *(
                value
                for record in canonical.source_records
                for value in (record.normalized_location.raw_text, record.original_location)
            ),
        )
        if text
    ]

    matched_locations: list[str] = []
    for preferred in profile.preferred_locations:
        normalized_preferred = normalize_profile_text(preferred)
        if not normalized_preferred:
            continue
        if normalized_preferred in {"deutschland", "germany", "germania", "германия"} and canonical.country_code == "DE":
            matched_locations.append(preferred)
            continue

        # Сначала — та же проверка города, что и у заказанных городов: она знает
        # про районы и про почтовые индексы.
        if any(location_matches_city(text, preferred) for text in raw_location_texts):
            matched_locations.append(preferred)
            continue

        # Земли и страны в городском справочнике отсутствуют, поэтому для них
        # остаётся проверка по словам.
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
