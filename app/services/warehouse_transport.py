"""Warehouse/store routes for an explicitly selected role in strict mixed profiles."""
from __future__ import annotations

import re

from app.services.light_goods_transport import match_light_goods_transport
from app.services.search_models import RuleHit, SearchProfileContext
from app.services.signal_negation import is_negated_signal, normalize_signal_text

WAREHOUSE_ROUTE_ROLE = "Sprinterfahrer Warentransport zwischen Lagern und Filialen Klasse B bis 3.5 t"
_INTENT = re.compile(r"\bzwischen\s+lagern\s+und\s+filialen\b")
_DRIVER = re.compile(r"\b(?:\w*fahrer\w*|\w*zusteller\w*|kurier\w*|courier|driver)\b")
_ROUTE = re.compile(
    r"\b(?:werksverkehr|depotverkehr|filialbelieferung|filialverkehr|linientransport\w*|"
    r"direktfahrt\w*|sonderfahrt\w*|fernverkehr|uberlandfahrt\w*)\b|"
    r"\bzwischen\b.{0,70}\b(?:lager\w*|standort\w*|filial\w*|depot\w*)\b|"
    r"\b(?:beliefer\w*|transport\w*)\b.{0,50}\b(?:filialen|markte|markten|geschaften|supermarkte)\b"
)
_LAST_MILE = re.compile(
    r"\b(?:paketzustell\w*|paketdienst\w*|essensliefer\w*|pizzafahrer|lieferando|"
    r"wohnungstur|privathaushalt\w*|privatkunden|endkunden|mobelauslieferung)\b"
)
_BUSINESS_ROUTE = re.compile(
    r"\b(?:liefer\w*|ausliefer\w*|transportier\w*)\b[^;]{0,100}"
    r"\b(?:waren|produkte|guter)\b[^;]{0,100}\ban\s+(?:unsere\s+)?"
    r"(?:partner|geschaftskunden|gewerbliche\s+kunden)\b"
)


def wants_warehouse_routes(profile: SearchProfileContext) -> bool:
    return profile.strict_match_mode != "off" and any(
        _INTENT.search(normalize_signal_text(role)) for role in profile.desired_roles
    )


def warehouse_route_hits(
    title: str, body: str, profile: SearchProfileContext,
) -> tuple[tuple[RuleHit, ...], tuple[RuleHit, ...]]:
    if not wants_warehouse_routes(profile) or not _DRIVER.search(normalize_signal_text(title)):
        return (), ()
    text = normalize_signal_text(title + "; " + body)
    if any(not is_negated_signal(text, match) for match in _LAST_MILE.finditer(text)):
        return (RuleHit(code="strict_last_mile", label_ru="нужны рейсы между складами/магазинами; адресная доставка исключена"),), ()
    # Mixed driver/warehouse work is allowed by this mixed profile.
    driving_title = re.sub(r"\b(?:lagerhelfer|lagermitarbeiter|kommissionierer)\b", "", title, flags=re.I)
    business_route = any(not is_negated_signal(text, match) for match in _BUSINESS_ROUTE.finditer(text))
    # Products transported to business recipients are goods; normalize this synonym
    # only for this opt-in role, preserving the shared matcher's exclusion rules.
    goods_body = re.sub(r"\bprodukte\b", "Waren", body, flags=re.I) if business_route else body
    if match_light_goods_transport(driving_title, goods_body) == "none":
        return (RuleHit(code="strict_goods_transport", label_ru="не подтверждена перевозка грузов на лёгком автомобиле"),), ()
    if not any(not is_negated_signal(text, match) for match in _ROUTE.finditer(text)):
        if business_route:
            return (), (RuleHit(code="strict_route_review", label_ru="перевозка товаров партнёрам: уточнить склады/магазины, длину рейсов и число остановок"),)
        return (RuleHit(code="strict_route_unconfirmed", label_ru="не подтверждены рейсы между складами/магазинами или прямые грузовые рейсы"),), ()
    return (), ()
