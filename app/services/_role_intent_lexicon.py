"""Lexicon data for role-intent normalization.

Contains the RoleIntent type definition, the multilingual lookup map, and the
precomputed sorted keys used by normalize_role_intent in role_intent.py.
This is a data module — no matching logic lives here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.role_family import RoleFamily


@dataclass(frozen=True, slots=True)
class RoleIntent:
    family: RoleFamily
    primary_de: str                  # primary German keyword for source adapters
    synonyms_de: tuple[str, ...]     # ranked fallback keywords (excluding primary_de)
    primary_en: str | None = None    # optional English keyword


# ---------------------------------------------------------------------------
# Multilingual lookup map: lowercase query text → RoleIntent.
# Keys are matched longest-first (see _SORTED_KEYS).
# ---------------------------------------------------------------------------

ROLE_INTENT_MAP: dict[str, RoleIntent] = {

    # ---- DRIVING / DELIVERY ------------------------------------------------
    "водитель-курьер": RoleIntent(
        RoleFamily.DRIVING, "lieferfahrer",
        ("kurier", "fahrer", "zusteller", "kraftfahrer"), "delivery driver",
    ),
    "водій-кур'єр": RoleIntent(
        RoleFamily.DRIVING, "lieferfahrer",
        ("kurier", "fahrer", "zusteller"), "delivery driver",
    ),
    "грузовой водитель": RoleIntent(
        RoleFamily.DRIVING, "lkw-fahrer",
        ("kraftfahrer", "fahrer", "berufskraftfahrer"), "truck driver",
    ),
    "delivery driver": RoleIntent(
        RoleFamily.DRIVING, "lieferfahrer",
        ("kurier", "fahrer", "zusteller", "kraftfahrer"), "delivery driver",
    ),
    "courier driver": RoleIntent(
        RoleFamily.DRIVING, "lieferfahrer",
        ("kurier", "fahrer", "zusteller"), "courier driver",
    ),
    "truck driver": RoleIntent(
        RoleFamily.DRIVING, "lkw-fahrer",
        ("kraftfahrer", "fahrer", "berufskraftfahrer"), "truck driver",
    ),
    "delivery person": RoleIntent(
        RoleFamily.DRIVING, "zusteller",
        ("kurier", "lieferfahrer", "fahrer"), "delivery person",
    ),
    "доставщик": RoleIntent(
        RoleFamily.DRIVING, "zusteller",
        ("kurier", "lieferfahrer", "fahrer"), "delivery person",
    ),
    "доставка": RoleIntent(
        RoleFamily.DRIVING, "zusteller",
        ("kurier", "lieferfahrer", "fahrer"), "delivery",
    ),
    "курьер": RoleIntent(
        RoleFamily.DRIVING, "kurier",
        ("fahrer", "zusteller", "lieferfahrer"), "courier",
    ),
    "водитель": RoleIntent(
        RoleFamily.DRIVING, "fahrer",
        ("kraftfahrer", "lkw-fahrer", "zusteller", "kurier"), "driver",
    ),
    "кур'єр": RoleIntent(
        RoleFamily.DRIVING, "kurier",
        ("fahrer", "zusteller", "lieferfahrer"), "courier",
    ),
    "водій": RoleIntent(
        RoleFamily.DRIVING, "fahrer",
        ("kraftfahrer", "zusteller", "kurier"), "driver",
    ),
    "delivery": RoleIntent(
        RoleFamily.DRIVING, "zusteller",
        ("kurier", "lieferfahrer", "fahrer"), "delivery",
    ),
    "courier": RoleIntent(
        RoleFamily.DRIVING, "kurier",
        ("fahrer", "zusteller", "lieferfahrer"), "courier",
    ),
    "driver": RoleIntent(
        RoleFamily.DRIVING, "fahrer",
        ("kraftfahrer", "zusteller", "kurier"), "driver",
    ),
    "lieferfahrer": RoleIntent(
        RoleFamily.DRIVING, "lieferfahrer",
        ("kurier", "fahrer", "zusteller"), "delivery driver",
    ),
    "zusteller": RoleIntent(
        RoleFamily.DRIVING, "zusteller",
        ("kurier", "lieferfahrer", "fahrer"), "courier",
    ),
    "kraftfahrer": RoleIntent(
        RoleFamily.DRIVING, "kraftfahrer",
        ("fahrer", "lkw-fahrer", "berufskraftfahrer"), "driver",
    ),
    "kurier": RoleIntent(
        RoleFamily.DRIVING, "kurier",
        ("fahrer", "zusteller", "lieferfahrer"), "courier",
    ),
    "fahrer": RoleIntent(
        RoleFamily.DRIVING, "fahrer",
        ("kraftfahrer", "kurier", "zusteller"), "driver",
    ),

    # ---- WAREHOUSE ---------------------------------------------------------
    "складской рабочий": RoleIntent(
        RoleFamily.WAREHOUSE, "lagermitarbeiter",
        ("lagerhelfer", "lager", "lagerist"), "warehouse worker",
    ),
    "рабочий на склад": RoleIntent(
        RoleFamily.WAREHOUSE, "lagermitarbeiter",
        ("lagerhelfer", "lager", "lagerist"), "warehouse worker",
    ),
    "комплектовщик": RoleIntent(
        RoleFamily.WAREHOUSE, "kommissionierer",
        ("lagerhelfer", "lager", "picker"), "picker",
    ),
    "кладовщик": RoleIntent(
        RoleFamily.WAREHOUSE, "lagerist",
        ("lagerverwaltung", "lagermitarbeiter", "lager"), "storeman",
    ),
    "упаковщик": RoleIntent(
        RoleFamily.WAREHOUSE, "verpacker",
        ("verpackung", "lagerhelfer", "lager"), "packer",
    ),
    "грузчик": RoleIntent(
        RoleFamily.WAREHOUSE, "lagerhelfer",
        ("lager", "helfer", "lagermitarbeiter"), "loader",
    ),
    "логистика": RoleIntent(
        RoleFamily.WAREHOUSE, "logistik",
        ("lagerlogistik", "lager", "disponent"), "logistics",
    ),
    "упаковка": RoleIntent(
        RoleFamily.WAREHOUSE, "verpacker",
        ("verpackung", "lagerhelfer", "lager"), "packaging",
    ),
    "склад": RoleIntent(
        RoleFamily.WAREHOUSE, "lager",
        ("lagermitarbeiter", "lagerhelfer", "lagerist", "kommissionierer"), "warehouse",
    ),
    "комірник": RoleIntent(
        RoleFamily.WAREHOUSE, "lagerist",
        ("lagerverwaltung", "lagermitarbeiter", "lager"), "storeman",
    ),
    "вантажник": RoleIntent(
        RoleFamily.WAREHOUSE, "lagerhelfer",
        ("lager", "helfer", "lagermitarbeiter"), "loader",
    ),
    "warehouse worker": RoleIntent(
        RoleFamily.WAREHOUSE, "lagermitarbeiter",
        ("lagerhelfer", "lager", "lagerist"), "warehouse worker",
    ),
    "warehouse": RoleIntent(
        RoleFamily.WAREHOUSE, "lager",
        ("lagermitarbeiter", "lagerhelfer", "lagerist"), "warehouse",
    ),
    "packer": RoleIntent(
        RoleFamily.WAREHOUSE, "verpacker",
        ("verpackung", "lagerhelfer", "lager"), "packer",
    ),
    "packaging": RoleIntent(
        RoleFamily.WAREHOUSE, "verpacker",
        ("verpackung", "lager", "sortierung"), "packaging",
    ),
    "logistics": RoleIntent(
        RoleFamily.WAREHOUSE, "logistik",
        ("lagerlogistik", "lager", "disponent"), "logistics",
    ),
    "picker": RoleIntent(
        RoleFamily.WAREHOUSE, "kommissionierer",
        ("lagerhelfer", "lager", "picker"), "picker",
    ),
    "lagermitarbeiter": RoleIntent(
        RoleFamily.WAREHOUSE, "lagermitarbeiter",
        ("lagerhelfer", "lager", "lagerist"), "warehouse worker",
    ),
    "lagerhelfer": RoleIntent(
        RoleFamily.WAREHOUSE, "lagerhelfer",
        ("lager", "lagermitarbeiter", "helfer"), "warehouse helper",
    ),
    "lagerist": RoleIntent(
        RoleFamily.WAREHOUSE, "lagerist",
        ("lagerverwaltung", "lagermitarbeiter", "lager"), "storeman",
    ),
    "kommissionierer": RoleIntent(
        RoleFamily.WAREHOUSE, "kommissionierer",
        ("lagerhelfer", "lager", "picker"), "picker",
    ),
    "logistik": RoleIntent(
        RoleFamily.WAREHOUSE, "logistik",
        ("lagerlogistik", "lager", "disponent"), "logistics",
    ),
    "verpacker": RoleIntent(
        RoleFamily.WAREHOUSE, "verpacker",
        ("verpackung", "lagerhelfer", "lager"), "packer",
    ),
    "lager": RoleIntent(
        RoleFamily.WAREHOUSE, "lager",
        ("lagermitarbeiter", "lagerhelfer", "lagerist"), "warehouse",
    ),

    # ---- PRODUCTION --------------------------------------------------------
    "производственный рабочий": RoleIntent(
        RoleFamily.PRODUCTION, "produktionsmitarbeiter",
        ("produktionshelfer", "produktion", "fertigung"), "production worker",
    ),
    "оператор станка": RoleIntent(
        RoleFamily.PRODUCTION, "maschinenbediener",
        ("maschinenführer", "produktion", "helfer"), "machine operator",
    ),
    "производство": RoleIntent(
        RoleFamily.PRODUCTION, "produktion",
        ("produktionshelfer", "maschinenbediener", "fertigung"), "production",
    ),
    "сборщик": RoleIntent(
        RoleFamily.PRODUCTION, "montagemitarbeiter",
        ("montage", "fertigung", "produktionshelfer"), "assembler",
    ),
    "оператор": RoleIntent(
        RoleFamily.PRODUCTION, "maschinenführer",
        ("maschinenbediener", "bediener", "helfer"), "operator",
    ),
    "виробництво": RoleIntent(
        RoleFamily.PRODUCTION, "produktion",
        ("produktionshelfer", "fertigung"), "production",
    ),
    "production worker": RoleIntent(
        RoleFamily.PRODUCTION, "produktionsmitarbeiter",
        ("produktionshelfer", "produktion", "fertigung"), "production worker",
    ),
    "production": RoleIntent(
        RoleFamily.PRODUCTION, "produktion",
        ("produktionshelfer", "maschinenbediener", "fertigung"), "production",
    ),
    "assembler": RoleIntent(
        RoleFamily.PRODUCTION, "montagemitarbeiter",
        ("montage", "fertigung", "produktionshelfer"), "assembler",
    ),
    "machine operator": RoleIntent(
        RoleFamily.PRODUCTION, "maschinenbediener",
        ("maschinenführer", "bediener", "produktion"), "machine operator",
    ),
    "produktionsmitarbeiter": RoleIntent(
        RoleFamily.PRODUCTION, "produktionsmitarbeiter",
        ("produktionshelfer", "produktion", "fertigung"), "production worker",
    ),
    "produktionshelfer": RoleIntent(
        RoleFamily.PRODUCTION, "produktionshelfer",
        ("produktion", "fertigung", "helfer"), "production helper",
    ),
    "maschinenbediener": RoleIntent(
        RoleFamily.PRODUCTION, "maschinenbediener",
        ("maschinenführer", "bediener", "produktion"), "machine operator",
    ),
    "produktion": RoleIntent(
        RoleFamily.PRODUCTION, "produktion",
        ("produktionshelfer", "maschinenbediener", "fertigung"), "production",
    ),

    # ---- CLEANING ----------------------------------------------------------
    "уборщица": RoleIntent(
        RoleFamily.CLEANING, "reinigungskraft",
        ("reinigung", "hausreinigung", "housekeeping"), "cleaner",
    ),
    "уборщик": RoleIntent(
        RoleFamily.CLEANING, "reinigungskraft",
        ("reinigung", "hausreinigung", "housekeeping"), "cleaner",
    ),
    "уборка": RoleIntent(
        RoleFamily.CLEANING, "reinigungskraft",
        ("reinigung", "hausreinigung", "housekeeping"), "cleaning",
    ),
    "прибиральниця": RoleIntent(
        RoleFamily.CLEANING, "reinigungskraft",
        ("reinigung", "hausreinigung", "housekeeping"), "cleaner",
    ),
    "прибиральник": RoleIntent(
        RoleFamily.CLEANING, "reinigungskraft",
        ("reinigung", "hausreinigung", "housekeeping"), "cleaner",
    ),
    "cleaner": RoleIntent(
        RoleFamily.CLEANING, "reinigungskraft",
        ("reinigung", "hausreinigung", "housekeeping"), "cleaner",
    ),
    "cleaning": RoleIntent(
        RoleFamily.CLEANING, "reinigungskraft",
        ("reinigung", "hausreinigung", "housekeeping"), "cleaning",
    ),
    "housekeeping": RoleIntent(
        RoleFamily.CLEANING, "housekeeping",
        ("reinigungskraft", "reinigung", "hausreinigung"), "housekeeping",
    ),
    "reinigungskraft": RoleIntent(
        RoleFamily.CLEANING, "reinigungskraft",
        ("reinigung", "hausreinigung", "housekeeping"), "cleaner",
    ),
    "reinigung": RoleIntent(
        RoleFamily.CLEANING, "reinigungskraft",
        ("reinigung", "hausreinigung", "housekeeping"), "cleaner",
    ),

    # ---- KITCHEN -----------------------------------------------------------
    "кухонный работник": RoleIntent(
        RoleFamily.KITCHEN, "küchenhelfer",
        ("küchenhilfe", "gastro", "koch"), "kitchen worker",
    ),
    "шеф-повар": RoleIntent(
        RoleFamily.KITCHEN, "chefkoch",
        ("küchenchef", "koch", "gastro"), "chef",
    ),
    "официант": RoleIntent(
        RoleFamily.KITCHEN, "kellner",
        ("servicekraft", "gastro", "restaurant"), "waiter",
    ),
    "повар": RoleIntent(
        RoleFamily.KITCHEN, "koch",
        ("küchenhelfer", "küchenhilfe", "gastro"), "cook",
    ),
    "офіціант": RoleIntent(
        RoleFamily.KITCHEN, "kellner",
        ("servicekraft", "gastro", "restaurant"), "waiter",
    ),
    "кухар": RoleIntent(
        RoleFamily.KITCHEN, "koch",
        ("küchenhelfer", "gastro"), "cook",
    ),
    "kitchen worker": RoleIntent(
        RoleFamily.KITCHEN, "küchenhelfer",
        ("küchenhilfe", "gastro", "koch"), "kitchen worker",
    ),
    "waitress": RoleIntent(
        RoleFamily.KITCHEN, "kellner",
        ("servicekraft", "gastro", "restaurant"), "waitress",
    ),
    "waiter": RoleIntent(
        RoleFamily.KITCHEN, "kellner",
        ("servicekraft", "gastro", "restaurant"), "waiter",
    ),
    "chef": RoleIntent(
        RoleFamily.KITCHEN, "koch",
        ("küchenhelfer", "gastro", "chefkoch"), "chef",
    ),
    "cook": RoleIntent(
        RoleFamily.KITCHEN, "koch",
        ("küchenhelfer", "gastro"), "cook",
    ),
    "küchenhelfer": RoleIntent(
        RoleFamily.KITCHEN, "küchenhelfer",
        ("küchenhilfe", "gastro", "koch"), "kitchen helper",
    ),
    "küchenhilfe": RoleIntent(
        RoleFamily.KITCHEN, "küchenhilfe",
        ("küchenhelfer", "gastro", "koch"), "kitchen helper",
    ),
    "kellner": RoleIntent(
        RoleFamily.KITCHEN, "kellner",
        ("servicekraft", "gastro", "restaurant"), "waiter",
    ),
    "servicekraft": RoleIntent(
        RoleFamily.KITCHEN, "servicekraft",
        ("kellner", "gastro", "restaurant"), "service staff",
    ),
    "gastro": RoleIntent(
        RoleFamily.KITCHEN, "gastro",
        ("kellner", "küchenhelfer", "restaurant"), "gastronomy",
    ),
    "koch": RoleIntent(
        RoleFamily.KITCHEN, "koch",
        ("küchenhelfer", "gastro"), "cook",
    ),

    # ---- HEALTHCARE --------------------------------------------------------
    "медицинский работник": RoleIntent(
        RoleFamily.HEALTHCARE, "pflegekraft",
        ("pflege", "pflegehelferin", "medizin"), "medical worker",
    ),
    "медсестра": RoleIntent(
        RoleFamily.HEALTHCARE, "pflegekraft",
        ("pflege", "krankenschwester", "pflegehelferin"), "nurse",
    ),
    "медбрат": RoleIntent(
        RoleFamily.HEALTHCARE, "pflegefachkraft",
        ("pflege", "pflegekraft"), "nurse",
    ),
    "санитар": RoleIntent(
        RoleFamily.HEALTHCARE, "pflegehelferin",
        ("pflege", "pflegekraft", "sanitäter"), "medical assistant",
    ),
    "сиделка": RoleIntent(
        RoleFamily.HEALTHCARE, "pflegekraft",
        ("pflege", "altenpflege", "pflegehelferin"), "caregiver",
    ),
    "санітар": RoleIntent(
        RoleFamily.HEALTHCARE, "pflegehelferin",
        ("pflege", "pflegekraft"), "medical assistant",
    ),
    "caregiver": RoleIntent(
        RoleFamily.HEALTHCARE, "pflegekraft",
        ("pflege", "altenpflege", "pflegehelferin"), "caregiver",
    ),
    "healthcare": RoleIntent(
        RoleFamily.HEALTHCARE, "pflegekraft",
        ("pflege", "medizin", "pflegehelferin"), "healthcare",
    ),
    "nurse": RoleIntent(
        RoleFamily.HEALTHCARE, "pflegekraft",
        ("pflege", "krankenschwester", "pflegehelferin"), "nurse",
    ),
    "carer": RoleIntent(
        RoleFamily.HEALTHCARE, "pflegekraft",
        ("pflege", "altenpflege"), "carer",
    ),
    "pflegefachkraft": RoleIntent(
        RoleFamily.HEALTHCARE, "pflegefachkraft",
        ("pflege", "pflegekraft", "altenpflege"), "care specialist",
    ),
    "pflegehelferin": RoleIntent(
        RoleFamily.HEALTHCARE, "pflegehelferin",
        ("pflege", "pflegekraft"), "care helper",
    ),
    "pflegekraft": RoleIntent(
        RoleFamily.HEALTHCARE, "pflegekraft",
        ("pflege", "pflegehelferin", "altenpflege"), "caregiver",
    ),
    "pflege": RoleIntent(
        RoleFamily.HEALTHCARE, "pflegekraft",
        ("pflege", "pflegehelferin", "altenpflege"), "care",
    ),

    # ---- CONSTRUCTION ------------------------------------------------------
    "строитель": RoleIntent(
        RoleFamily.CONSTRUCTION, "bauhelfer",
        ("bau", "montage", "helfer"), "construction worker",
    ),
    "монтажник": RoleIntent(
        RoleFamily.CONSTRUCTION, "monteur",
        ("montage", "techniker", "helfer"), "fitter",
    ),
    "электрик": RoleIntent(
        RoleFamily.CONSTRUCTION, "elektriker",
        ("elektrotechniker", "elektromonteur", "helfer"), "electrician",
    ),
    "сварщик": RoleIntent(
        RoleFamily.CONSTRUCTION, "schweißer",
        ("metallbau", "schlosser", "schweißtechnik"), "welder",
    ),
    "слесарь": RoleIntent(
        RoleFamily.CONSTRUCTION, "schlosser",
        ("mechaniker", "monteur", "instandhalter"), "mechanic",
    ),
    "экспедитор": RoleIntent(
        RoleFamily.WAREHOUSE, "disponent",
        ("spedition", "logistik", "versandmitarbeiter"), "dispatcher",
    ),
    "електрик": RoleIntent(
        RoleFamily.CONSTRUCTION, "elektriker",
        ("elektrotechniker", "helfer"), "electrician",
    ),
    "зварщик": RoleIntent(
        RoleFamily.CONSTRUCTION, "schweißer",
        ("metallbau", "schlosser"), "welder",
    ),
    "слюсар": RoleIntent(
        RoleFamily.CONSTRUCTION, "schlosser",
        ("mechaniker", "monteur"), "mechanic",
    ),
    "construction worker": RoleIntent(
        RoleFamily.CONSTRUCTION, "bauhelfer",
        ("bau", "montage", "helfer"), "construction worker",
    ),
    "construction": RoleIntent(
        RoleFamily.CONSTRUCTION, "bauhelfer",
        ("bau", "montage"), "construction",
    ),
    "electrician": RoleIntent(
        RoleFamily.CONSTRUCTION, "elektriker",
        ("elektrotechniker", "elektromonteur"), "electrician",
    ),
    "welder": RoleIntent(
        RoleFamily.CONSTRUCTION, "schweißer",
        ("metallbau", "schlosser"), "welder",
    ),
    "mechanic": RoleIntent(
        RoleFamily.CONSTRUCTION, "mechaniker",
        ("schlosser", "monteur"), "mechanic",
    ),
    "bauhelfer": RoleIntent(
        RoleFamily.CONSTRUCTION, "bauhelfer",
        ("bau", "montage", "helfer"), "construction helper",
    ),
    "monteur": RoleIntent(
        RoleFamily.CONSTRUCTION, "monteur",
        ("montage", "techniker", "helfer"), "fitter",
    ),
    "elektriker": RoleIntent(
        RoleFamily.CONSTRUCTION, "elektriker",
        ("elektrotechniker", "elektromonteur"), "electrician",
    ),
    "schweißer": RoleIntent(
        RoleFamily.CONSTRUCTION, "schweißer",
        ("metallbau", "schlosser"), "welder",
    ),
    "schlosser": RoleIntent(
        RoleFamily.CONSTRUCTION, "schlosser",
        ("mechaniker", "monteur"), "mechanic",
    ),

    # ---- IT ----------------------------------------------------------------
    # NOTE: "it" is matched with word boundaries in normalize_role_intent.
    "разработчик программного обеспечения": RoleIntent(
        RoleFamily.IT, "softwareentwickler",
        ("developer", "programmierer", "fullstack"), "software developer",
    ),
    "системный администратор": RoleIntent(
        RoleFamily.IT, "systemadministrator",
        ("sysadmin", "administrator", "it-support"), "system administrator",
    ),
    "system administrator": RoleIntent(
        RoleFamily.IT, "systemadministrator",
        ("sysadmin", "administrator", "it-support"), "system administrator",
    ),
    "software developer": RoleIntent(
        RoleFamily.IT, "softwareentwickler",
        ("developer", "programmierer", "fullstack"), "software developer",
    ),
    "software engineer": RoleIntent(
        RoleFamily.IT, "softwareentwickler",
        ("developer", "programmierer", "fullstack"), "software engineer",
    ),
    "it support": RoleIntent(
        RoleFamily.IT, "it-support",
        ("helpdesk", "administrator", "softwareentwickler"), "it support",
    ),
    "разработчик": RoleIntent(
        RoleFamily.IT, "softwareentwickler",
        ("developer", "programmierer", "fullstack"), "developer",
    ),
    "программист": RoleIntent(
        RoleFamily.IT, "softwareentwickler",
        ("developer", "programmierer", "fullstack"), "programmer",
    ),
    "сисадмин": RoleIntent(
        RoleFamily.IT, "systemadministrator",
        ("sysadmin", "administrator", "it-support"), "sysadmin",
    ),
    "програміст": RoleIntent(
        RoleFamily.IT, "softwareentwickler",
        ("developer", "programmierer"), "programmer",
    ),
    "розробник": RoleIntent(
        RoleFamily.IT, "softwareentwickler",
        ("developer", "programmierer"), "developer",
    ),
    "developer": RoleIntent(
        RoleFamily.IT, "softwareentwickler",
        ("developer", "programmierer", "fullstack"), "developer",
    ),
    "programmer": RoleIntent(
        RoleFamily.IT, "softwareentwickler",
        ("developer", "programmierer"), "programmer",
    ),
    "devops": RoleIntent(
        RoleFamily.IT, "devops",
        ("administrator", "systemadministrator", "developer"), "devops",
    ),
    "it": RoleIntent(
        RoleFamily.IT, "softwareentwickler",
        ("developer", "it-support", "administrator"), "it",
    ),
    "softwareentwickler": RoleIntent(
        RoleFamily.IT, "softwareentwickler",
        ("developer", "programmierer", "fullstack"), "software developer",
    ),

    # ---- OFFICE ------------------------------------------------------------
    "менеджер по продажам": RoleIntent(
        RoleFamily.SALES, "vertriebsmitarbeiter",
        ("vertrieb", "kundenberater", "verkäufer"), "sales manager",
    ),
    "бухгалтер": RoleIntent(
        RoleFamily.OFFICE, "buchhalter",
        ("buchhaltung", "sachbearbeiter", "controlling"), "accountant",
    ),
    "менеджер": RoleIntent(
        RoleFamily.OFFICE, "manager",
        ("teamleiter", "sachbearbeiter", "projektmanager"), "manager",
    ),
    "accountant": RoleIntent(
        RoleFamily.OFFICE, "buchhalter",
        ("buchhaltung", "sachbearbeiter", "controlling"), "accountant",
    ),
    "manager": RoleIntent(
        RoleFamily.OFFICE, "manager",
        ("teamleiter", "sachbearbeiter"), "manager",
    ),
    "buchhalter": RoleIntent(
        RoleFamily.OFFICE, "buchhalter",
        ("buchhaltung", "sachbearbeiter"), "accountant",
    ),
    "sachbearbeiter": RoleIntent(
        RoleFamily.OFFICE, "sachbearbeiter",
        ("verwaltungsmitarbeiter", "assistenz", "buchhalter"), "clerk",
    ),

    # ---- SALES -------------------------------------------------------------
    "продавец": RoleIntent(
        RoleFamily.SALES, "verkäufer",
        ("einzelhandel", "kassierer", "kundenberater"), "salesperson",
    ),
    "кассир": RoleIntent(
        RoleFamily.SALES, "kassierer",
        ("verkäufer", "einzelhandel"), "cashier",
    ),
    "продавець": RoleIntent(
        RoleFamily.SALES, "verkäufer",
        ("einzelhandel", "kassierer"), "salesperson",
    ),
    "касир": RoleIntent(
        RoleFamily.SALES, "kassierer",
        ("verkäufer", "einzelhandel"), "cashier",
    ),
    "salesperson": RoleIntent(
        RoleFamily.SALES, "verkäufer",
        ("einzelhandel", "kassierer"), "salesperson",
    ),
    "cashier": RoleIntent(
        RoleFamily.SALES, "kassierer",
        ("verkäufer", "einzelhandel"), "cashier",
    ),
    "sales": RoleIntent(
        RoleFamily.SALES, "vertriebsmitarbeiter",
        ("vertrieb", "verkäufer", "kundenberater"), "sales",
    ),
    "verkäufer": RoleIntent(
        RoleFamily.SALES, "verkäufer",
        ("einzelhandel", "kassierer"), "salesperson",
    ),
    "kassierer": RoleIntent(
        RoleFamily.SALES, "kassierer",
        ("verkäufer", "einzelhandel"), "cashier",
    ),
    "vertrieb": RoleIntent(
        RoleFamily.SALES, "vertriebsmitarbeiter",
        ("vertrieb", "verkäufer"), "sales",
    ),

    # ---- SECURITY ----------------------------------------------------------
    "охранник": RoleIntent(
        RoleFamily.SECURITY, "sicherheitsdienst",
        ("security", "bewachung", "wachschutz"), "security guard",
    ),
    "охоронник": RoleIntent(
        RoleFamily.SECURITY, "sicherheitsdienst",
        ("security", "bewachung"), "security guard",
    ),
    "security guard": RoleIntent(
        RoleFamily.SECURITY, "sicherheitsdienst",
        ("security", "bewachung", "wachschutz"), "security guard",
    ),
    "security": RoleIntent(
        RoleFamily.SECURITY, "sicherheitsdienst",
        ("security", "bewachung"), "security",
    ),
    "sicherheitsdienst": RoleIntent(
        RoleFamily.SECURITY, "sicherheitsdienst",
        ("security", "bewachung", "wachschutz"), "security",
    ),

    # ---- AGRICULTURE -------------------------------------------------------
    "сельское хозяйство": RoleIntent(
        RoleFamily.AGRICULTURE, "landwirtschaft",
        ("ernte", "garten", "gärtner"), "agriculture",
    ),
    "садовник": RoleIntent(
        RoleFamily.AGRICULTURE, "gärtner",
        ("gartenarbeit", "garten", "landwirtschaft"), "gardener",
    ),
    "агроном": RoleIntent(
        RoleFamily.AGRICULTURE, "landwirtschaft",
        ("ernte", "garten", "gärtner"), "agronomist",
    ),
    "gardener": RoleIntent(
        RoleFamily.AGRICULTURE, "gärtner",
        ("gartenarbeit", "garten"), "gardener",
    ),
    "agriculture": RoleIntent(
        RoleFamily.AGRICULTURE, "landwirtschaft",
        ("ernte", "garten"), "agriculture",
    ),

    # ---- GENERIC (low-barrier) ---------------------------------------------
    "рабочий помощник": RoleIntent(
        RoleFamily.GENERIC, "helfer",
        ("aushilfe", "hilfskraft", "lagerhelfer", "produktionshelfer"), "helper",
    ),
    "помощник": RoleIntent(
        RoleFamily.GENERIC, "helfer",
        ("aushilfe", "hilfskraft"), "helper",
    ),
    "general worker": RoleIntent(
        RoleFamily.GENERIC, "helfer",
        ("aushilfe", "hilfskraft"), "general worker",
    ),
    "helper": RoleIntent(
        RoleFamily.GENERIC, "helfer",
        ("aushilfe", "hilfskraft", "lagerhelfer"), "helper",
    ),
    "aushilfe": RoleIntent(
        RoleFamily.GENERIC, "aushilfe",
        ("helfer", "hilfskraft"), "temp worker",
    ),
    "helfer": RoleIntent(
        RoleFamily.GENERIC, "helfer",
        ("aushilfe", "hilfskraft", "lagerhelfer"), "helper",
    ),

    # ---- ADDED: rarer roles (RU/UA/EN → DE). German keywords verified on BA. ----
    # DRIVING
    "таксист": RoleIntent(RoleFamily.DRIVING, "taxifahrer", ("fahrer", "personenbeförderung", "kraftfahrer"), "taxi driver"),
    "таксі": RoleIntent(RoleFamily.DRIVING, "taxifahrer", ("fahrer", "personenbeförderung"), "taxi driver"),
    "taxi driver": RoleIntent(RoleFamily.DRIVING, "taxifahrer", ("fahrer", "personenbeförderung"), "taxi driver"),
    "taxifahrer": RoleIntent(RoleFamily.DRIVING, "taxifahrer", ("fahrer", "personenbeförderung"), "taxi driver"),
    "дальнобойщик": RoleIntent(RoleFamily.DRIVING, "berufskraftfahrer", ("lkw-fahrer", "kraftfahrer", "fahrer"), "truck driver"),
    "далекобійник": RoleIntent(RoleFamily.DRIVING, "berufskraftfahrer", ("lkw-fahrer", "kraftfahrer"), "truck driver"),
    "berufskraftfahrer": RoleIntent(RoleFamily.DRIVING, "berufskraftfahrer", ("lkw-fahrer", "kraftfahrer", "fahrer"), "professional driver"),

    # WAREHOUSE — forklift
    "водитель погрузчика": RoleIntent(RoleFamily.WAREHOUSE, "staplerfahrer", ("stapler", "lagerhelfer", "kommissionierer"), "forklift driver"),
    "оператор погрузчика": RoleIntent(RoleFamily.WAREHOUSE, "staplerfahrer", ("stapler", "lagerhelfer", "kommissionierer"), "forklift operator"),
    "водій навантажувача": RoleIntent(RoleFamily.WAREHOUSE, "staplerfahrer", ("stapler", "lagerhelfer"), "forklift driver"),
    "forklift": RoleIntent(RoleFamily.WAREHOUSE, "staplerfahrer", ("stapler", "lagerhelfer", "kommissionierer"), "forklift driver"),
    "staplerfahrer": RoleIntent(RoleFamily.WAREHOUSE, "staplerfahrer", ("stapler", "lagerhelfer", "kommissionierer"), "forklift driver"),

    # KITCHEN
    "бармен": RoleIntent(RoleFamily.KITCHEN, "barkeeper", ("barmann", "gastro", "kellner"), "bartender"),
    "бариста": RoleIntent(RoleFamily.KITCHEN, "barista", ("gastro", "kellner"), "barista"),
    "bartender": RoleIntent(RoleFamily.KITCHEN, "barkeeper", ("barmann", "gastro", "kellner"), "bartender"),
    "barista": RoleIntent(RoleFamily.KITCHEN, "barista", ("gastro", "kellner"), "barista"),
    "barkeeper": RoleIntent(RoleFamily.KITCHEN, "barkeeper", ("barmann", "gastro", "kellner"), "bartender"),
    "пекарь": RoleIntent(RoleFamily.KITCHEN, "bäcker", ("bäckerei", "backstube", "gastro"), "baker"),
    "пекар": RoleIntent(RoleFamily.KITCHEN, "bäcker", ("bäckerei", "backstube"), "baker"),
    "baker": RoleIntent(RoleFamily.KITCHEN, "bäcker", ("bäckerei", "backstube"), "baker"),
    "bäcker": RoleIntent(RoleFamily.KITCHEN, "bäcker", ("bäckerei", "backstube"), "baker"),
    "кондитер": RoleIntent(RoleFamily.KITCHEN, "konditor", ("konditorei", "bäcker", "gastro"), "confectioner"),
    "konditor": RoleIntent(RoleFamily.KITCHEN, "konditor", ("konditorei", "bäcker"), "confectioner"),
    "мойщик посуды": RoleIntent(RoleFamily.KITCHEN, "spülkraft", ("küchenhilfe", "küchenhelfer", "gastro"), "dishwasher"),
    "посудомойщик": RoleIntent(RoleFamily.KITCHEN, "spülkraft", ("küchenhilfe", "küchenhelfer"), "dishwasher"),
    "dishwasher": RoleIntent(RoleFamily.KITCHEN, "spülkraft", ("küchenhilfe", "küchenhelfer"), "dishwasher"),
    "spülkraft": RoleIntent(RoleFamily.KITCHEN, "spülkraft", ("küchenhilfe", "küchenhelfer"), "dishwasher"),
    "мясник": RoleIntent(RoleFamily.KITCHEN, "metzger", ("fleischer", "fleischerei", "gastro"), "butcher"),
    "м'ясник": RoleIntent(RoleFamily.KITCHEN, "metzger", ("fleischer", "fleischerei"), "butcher"),
    "butcher": RoleIntent(RoleFamily.KITCHEN, "metzger", ("fleischer", "fleischerei"), "butcher"),
    "metzger": RoleIntent(RoleFamily.KITCHEN, "metzger", ("fleischer", "fleischerei"), "butcher"),

    # HEALTHCARE
    "врач": RoleIntent(RoleFamily.HEALTHCARE, "arzt", ("mediziner", "facharzt", "klinik"), "doctor"),
    "лікар": RoleIntent(RoleFamily.HEALTHCARE, "arzt", ("mediziner", "facharzt"), "doctor"),
    "doctor": RoleIntent(RoleFamily.HEALTHCARE, "arzt", ("mediziner", "facharzt"), "doctor"),
    "physician": RoleIntent(RoleFamily.HEALTHCARE, "arzt", ("mediziner", "facharzt"), "physician"),
    "arzt": RoleIntent(RoleFamily.HEALTHCARE, "arzt", ("mediziner", "facharzt"), "doctor"),
    "физиотерапевт": RoleIntent(RoleFamily.HEALTHCARE, "physiotherapeut", ("physiotherapie", "therapeut"), "physiotherapist"),
    "фізіотерапевт": RoleIntent(RoleFamily.HEALTHCARE, "physiotherapeut", ("physiotherapie", "therapeut"), "physiotherapist"),
    "physiotherapist": RoleIntent(RoleFamily.HEALTHCARE, "physiotherapeut", ("physiotherapie", "therapeut"), "physiotherapist"),
    "physiotherapeut": RoleIntent(RoleFamily.HEALTHCARE, "physiotherapeut", ("physiotherapie", "therapeut"), "physiotherapist"),
    "стоматолог": RoleIntent(RoleFamily.HEALTHCARE, "zahnarzt", ("zahnmedizin", "arzt"), "dentist"),
    "dentist": RoleIntent(RoleFamily.HEALTHCARE, "zahnarzt", ("zahnmedizin", "arzt"), "dentist"),
    "zahnarzt": RoleIntent(RoleFamily.HEALTHCARE, "zahnarzt", ("zahnmedizin", "arzt"), "dentist"),

    # CONSTRUCTION / SKILLED TRADES
    "маляр": RoleIntent(RoleFamily.CONSTRUCTION, "maler", ("lackierer", "anstreicher", "bau"), "painter"),
    "painter": RoleIntent(RoleFamily.CONSTRUCTION, "maler", ("lackierer", "anstreicher"), "painter"),
    "maler": RoleIntent(RoleFamily.CONSTRUCTION, "maler", ("lackierer", "anstreicher"), "painter"),
    "плиточник": RoleIntent(RoleFamily.CONSTRUCTION, "fliesenleger", ("fliesen", "bau"), "tiler"),
    "плиточник-облицовщик": RoleIntent(RoleFamily.CONSTRUCTION, "fliesenleger", ("fliesen", "bau"), "tiler"),
    "tiler": RoleIntent(RoleFamily.CONSTRUCTION, "fliesenleger", ("fliesen", "bau"), "tiler"),
    "fliesenleger": RoleIntent(RoleFamily.CONSTRUCTION, "fliesenleger", ("fliesen", "bau"), "tiler"),
    "штукатур": RoleIntent(RoleFamily.CONSTRUCTION, "stuckateur", ("verputzer", "trockenbau", "bau"), "plasterer"),
    "plasterer": RoleIntent(RoleFamily.CONSTRUCTION, "stuckateur", ("verputzer", "trockenbau"), "plasterer"),
    "stuckateur": RoleIntent(RoleFamily.CONSTRUCTION, "stuckateur", ("verputzer", "trockenbau"), "plasterer"),
    "плотник": RoleIntent(RoleFamily.CONSTRUCTION, "zimmermann", ("zimmerer", "tischler", "schreiner"), "carpenter"),
    "столяр": RoleIntent(RoleFamily.CONSTRUCTION, "tischler", ("schreiner", "holz", "möbel"), "joiner"),
    "тесля": RoleIntent(RoleFamily.CONSTRUCTION, "zimmermann", ("zimmerer", "tischler"), "carpenter"),
    "carpenter": RoleIntent(RoleFamily.CONSTRUCTION, "tischler", ("schreiner", "zimmermann"), "carpenter"),
    "joiner": RoleIntent(RoleFamily.CONSTRUCTION, "tischler", ("schreiner", "holz"), "joiner"),
    "tischler": RoleIntent(RoleFamily.CONSTRUCTION, "tischler", ("schreiner", "zimmermann"), "joiner"),
    "schreiner": RoleIntent(RoleFamily.CONSTRUCTION, "schreiner", ("tischler", "holz"), "joiner"),
    "каменщик": RoleIntent(RoleFamily.CONSTRUCTION, "maurer", ("hochbau", "bau"), "mason"),
    "муляр": RoleIntent(RoleFamily.CONSTRUCTION, "maurer", ("hochbau", "bau"), "mason"),
    "mason": RoleIntent(RoleFamily.CONSTRUCTION, "maurer", ("hochbau", "bau"), "mason"),
    "maurer": RoleIntent(RoleFamily.CONSTRUCTION, "maurer", ("hochbau", "bau"), "mason"),
    "кровельщик": RoleIntent(RoleFamily.CONSTRUCTION, "dachdecker", ("dach", "bau"), "roofer"),
    "покрівельник": RoleIntent(RoleFamily.CONSTRUCTION, "dachdecker", ("dach", "bau"), "roofer"),
    "roofer": RoleIntent(RoleFamily.CONSTRUCTION, "dachdecker", ("dach", "bau"), "roofer"),
    "dachdecker": RoleIntent(RoleFamily.CONSTRUCTION, "dachdecker", ("dach", "bau"), "roofer"),
    "сантехник": RoleIntent(RoleFamily.CONSTRUCTION, "anlagenmechaniker", ("installateur", "klempner", "sanitär"), "plumber"),
    "сантехнік": RoleIntent(RoleFamily.CONSTRUCTION, "anlagenmechaniker", ("installateur", "klempner"), "plumber"),
    "plumber": RoleIntent(RoleFamily.CONSTRUCTION, "anlagenmechaniker", ("installateur", "klempner", "sanitär"), "plumber"),
    "installateur": RoleIntent(RoleFamily.CONSTRUCTION, "installateur", ("klempner", "sanitär", "anlagenmechaniker"), "plumber"),
    "anlagenmechaniker": RoleIntent(RoleFamily.CONSTRUCTION, "anlagenmechaniker", ("installateur", "sanitär"), "plumber"),
    "автомеханик": RoleIntent(RoleFamily.CONSTRUCTION, "kfz-mechaniker", ("kfz-mechatroniker", "mechaniker", "werkstatt"), "car mechanic"),
    "автослесарь": RoleIntent(RoleFamily.CONSTRUCTION, "kfz-mechaniker", ("kfz-mechatroniker", "mechaniker", "werkstatt"), "auto mechanic"),
    "автомеханік": RoleIntent(RoleFamily.CONSTRUCTION, "kfz-mechaniker", ("kfz-mechatroniker", "mechaniker"), "car mechanic"),
    "car mechanic": RoleIntent(RoleFamily.CONSTRUCTION, "kfz-mechaniker", ("kfz-mechatroniker", "mechaniker", "werkstatt"), "car mechanic"),
    "kfz-mechaniker": RoleIntent(RoleFamily.CONSTRUCTION, "kfz-mechaniker", ("kfz-mechatroniker", "mechaniker"), "car mechanic"),

    # PRODUCTION — textile
    "швея": RoleIntent(RoleFamily.PRODUCTION, "näherin", ("näher", "textil", "schneider"), "seamstress"),
    "швачка": RoleIntent(RoleFamily.PRODUCTION, "näherin", ("näher", "textil"), "seamstress"),
    "портной": RoleIntent(RoleFamily.PRODUCTION, "schneider", ("näher", "textil"), "tailor"),
    "seamstress": RoleIntent(RoleFamily.PRODUCTION, "näherin", ("näher", "textil"), "seamstress"),
    "tailor": RoleIntent(RoleFamily.PRODUCTION, "schneider", ("näher", "textil"), "tailor"),
    "näherin": RoleIntent(RoleFamily.PRODUCTION, "näherin", ("näher", "textil"), "seamstress"),

    # AGRICULTURE
    "сборщик урожая": RoleIntent(RoleFamily.AGRICULTURE, "erntehelfer", ("ernte", "landwirtschaft"), "harvest worker"),
    "збирач урожаю": RoleIntent(RoleFamily.AGRICULTURE, "erntehelfer", ("ernte", "landwirtschaft"), "harvest worker"),
    "harvest worker": RoleIntent(RoleFamily.AGRICULTURE, "erntehelfer", ("ernte", "landwirtschaft"), "harvest worker"),
    "erntehelfer": RoleIntent(RoleFamily.AGRICULTURE, "erntehelfer", ("ernte", "landwirtschaft"), "harvest worker"),

    # OFFICE — call center
    "оператор колл-центра": RoleIntent(RoleFamily.OFFICE, "callcenter", ("kundenservice", "kundenbetreuung", "telefonist"), "call center agent"),
    "оператор call-центра": RoleIntent(RoleFamily.OFFICE, "callcenter", ("kundenservice", "kundenbetreuung"), "call center agent"),
    "call center": RoleIntent(RoleFamily.OFFICE, "callcenter", ("kundenservice", "kundenbetreuung"), "call center agent"),
    "callcenter": RoleIntent(RoleFamily.OFFICE, "callcenter", ("kundenservice", "kundenbetreuung"), "call center agent"),

    # SECURITY
    "сторож": RoleIntent(RoleFamily.SECURITY, "sicherheitsdienst", ("wachschutz", "security", "wachmann"), "watchman"),
    "watchman": RoleIntent(RoleFamily.SECURITY, "sicherheitsdienst", ("wachschutz", "security", "wachmann"), "watchman"),

    # GENERIC — unskilled labor
    "разнорабочий": RoleIntent(RoleFamily.GENERIC, "helfer", ("aushilfe", "hilfskraft", "bauhelfer", "produktionshelfer"), "general laborer"),
    "різноробочий": RoleIntent(RoleFamily.GENERIC, "helfer", ("aushilfe", "hilfskraft"), "general laborer"),
    "general laborer": RoleIntent(RoleFamily.GENERIC, "helfer", ("aushilfe", "hilfskraft"), "general laborer"),
}

# Sorted keys (longest first) for greedy longest-match lookup.
SORTED_INTENT_KEYS: tuple[str, ...] = tuple(
    sorted(ROLE_INTENT_MAP.keys(), key=len, reverse=True)
)

# Word-boundary regex for the short "it" token.
IT_WORD_RE: re.Pattern[str] = re.compile(r"\bit\b")
