"""Narrow evidence for transporting goods in light vehicles, independent of profile scoring."""
from __future__ import annotations

import re
from typing import Literal

from app.services.signal_negation import is_negated_signal, normalize_signal_text

LightTransportMatch = Literal["target", "adjacent", "none"]

_LIGHT = re.compile(
    r"\b(?:sprinter\w*|transporter\w*|kleintransporter\w*|kastenwagen|pkw)\b"
    r"|\b3\s+5\s*(?:t|tonnen?|tonner)\b"
    r"|\b(?:fuhrerschein(?:klasse)?|fahrerlaubnis(?:klasse)?|klasse)\s+(?:der\s+klasse\s+)?b\b"
)
_VAN = re.compile(r"\b(?:sprinter\w*|transporter\w*|kleintransporter\w*|kastenwagen)\b|\b3\s+5\s*(?:t|tonnen?|tonner)\b")
_DRIVER = re.compile(r"\b(?:\w*fahrer(?:in)?|driver)\b")
_GOODS = re.compile(
    r"\b(?:guter\w*|waren\w*|stuckgut\w*|transportgut\w*|lieferung\w*|ausliefer\w*|"
    r"direktfahrt\w*|sonderfahrt\w*|depotverkehr)\b"
    r"|\bzwischen\b.{0,45}\b(?:standort\w*|filial\w*|lager\w*|niederlassung\w*|depot\w*)\b"
)
_WRONG_TITLE = re.compile(
    r"\b(?:mull\w*fahrer|abfall\w*fahrer|baggerfahrer|\w*staplerfahrer|radladerfahrer|kranfahrer|"
    r"maschinenfahrer|busfahrer|taxifahrer|lokfuhrer|triebfahrzeugfuhrer|verkaufsfahrer|"
    r"sicherheits\w*|redakteur\w*|journalist\w*|lagermitarbeiter|lagerhelfer|kommissionierer|"
    r"disponent\w*|mechaniker\w*|mechatroniker\w*|monteur\w*)\b"
)
_PASSENGERS = re.compile(
    r"\b(?:personenbeforder\w*|personentransport\w*|patient\w*|krankenfahr\w*|"
    r"fahrgast\w*|fahrgaste\w*|schulerbeforder\w*|schulbus\w*|taxi\w*)\b"
    r"|\b(?:beforder\w*|transportier\w*)\b.{0,35}\b(?:personen|mitarbeiter|mitarbeitenden)\b"
)
_PARCEL = re.compile(r"\b(?:paket\w*|amazon\s+dsp|hermes\s+zustellung)\b")
_TRANSFER = re.compile(
    r"\b(?:\w*uberfuhr\w*|fahrzeugtransfer\w*|fahrzeugverbring\w*|fahrzeuglogistik|"
    r"fahrzeugumsetz\w*|rangier\w*|neufahrzeug\w*)\b|\bauslieferungsfahrer\s+(?:pkw|fahrzeuge)\b"
)
_DUTY = re.compile(
    r"\b(?:sie|du)\s+(?:transportieren|transportierst|fahren|fahrst|liefern|lieferst|befordern|beforderst)\b"
    r"|\b(?:transport|auslieferung|beforderung)\s+von\s+(?:waren|gutern)\b"
)
_REGULAR = re.compile(r"\b(?:taglich|hauptaufgabe|hauptsachlich|regelma[ßs]+ig|feste\w*\s+touren)\b")
_INCIDENTAL = re.compile(r"\b(?:gelegentlich|gelegentliche\w*|bei\s+bedarf|aushilfsweise|manchmal)\b")


def _positive(pattern: re.Pattern[str], text: str) -> bool:
    return any(
        not is_negated_signal(text, match)
        and not re.search(r"\b(?:nicht|statt\s+reiner)\s+$", text[max(0, match.start() - 30):match.start()])
        and not re.search(r"\bkeine\s+\d+\s+stopps\s+pro\s+tag\s+wie\s+im\s+klassischen\s+$", text[max(0, match.start() - 90):match.start()])
        for match in pattern.finditer(text)
    )


def has_light_transport_title(title: str) -> bool:
    """A driving title plus a van/mass or explicit goods service AND class-B evidence."""
    text = normalize_signal_text(title)
    if _positive(_WRONG_TITLE, text) or any(_positive(p, text) for p in (_PASSENGERS, _PARCEL, _TRANSFER)):
        return False
    if re.search(r"\b(?:service|shuttle)fahrer", text):
        return bool(_positive(_LIGHT, text) and _positive(_GOODS, text))
    return bool(_DRIVER.search(text) and _positive(_LIGHT, text) and (_positive(_VAN, text) or _positive(_GOODS, text)))


def is_light_transport_role(text: str) -> bool:
    """Recognize explicit profile intent; plain Fahrer B remains generic driving."""
    normalized = normalize_signal_text(text)
    return has_light_transport_title(text) or (
        bool(re.fullmatch(r"(?:transporter|sprinter)(?:\s+(?:transporter|sprinter))?\s+klasse\s+b", normalized))
    ) or "light_goods_transport" == text.strip().casefold() or (
        "перевозка грузов" in text.casefold() and any(word in text.casefold() for word in ("sprinter", "transporter", "категория b"))
    )


def match_light_goods_transport(title: str, body: str = "") -> LightTransportMatch:
    """Body evidence must describe actual duties; incidental words never rescue contrary titles."""
    title_text = normalize_signal_text(title)
    body_text = normalize_signal_text(body)
    combined = f"{title_text} {body_text}"
    if _positive(_WRONG_TITLE, title_text):
        return "none"
    if any(_positive(pattern, combined) for pattern in (_PASSENGERS, _PARCEL, _TRANSFER)):
        return "none"
    if has_light_transport_title(title):
        return "target"
    if _DRIVER.search(title_text) and _positive(_LIGHT, combined) and _positive(_GOODS, combined):
        # Service and shuttle titles need actual transport duties, not company vocabulary.
        if re.search(r"\b(?:service|shuttle)fahrer", title_text) and not _DUTY.search(body_text):
            return "none"
        if not _INCIDENTAL.search(body_text):
            return "adjacent"
    # Narrow title allowlist prevents body-only rescues of unknown editorial/security jobs.
    generic_logistics = re.fullmatch(r"(?:mitarbeiter\s+logistik|logistikmitarbeiter|mitarbeiter)(?:\s+m\s+w\s+d)?", title_text)
    if generic_logistics:
        # All evidence must co-occur in one duty sentence, not across company boilerplate.
        for sentence in re.split(r"[.!?;\n]", body):
            text = normalize_signal_text(sentence)
            if (_DUTY.search(text) and _REGULAR.search(text) and _positive(_GOODS, text)
                    and _positive(_LIGHT, text) and not _INCIDENTAL.search(text)):
                return "adjacent"
    return "none"


def employment_evidence_text(text: str) -> str:
    """Remove work-style adjectives, preserving genuine independent-contractor requirements."""
    normalized = normalize_signal_text(text)
    normalized = re.sub(
        r"\bselbst(?:st)?andig\w*\s+(?:(?:und|sorgfaltige|zuverlassige|eigenverantwortliche)\s+){0,3}arbeitsweise\b",
        "arbeitsweise", normalized,
    )
    return re.sub(r"\b(?:arbeitest|arbeiten|arbeitet)\s+selbst(?:st)?andig\b", "arbeiten", normalized)


def mandatory_vehicle_evidence_text(text: str) -> str:
    """Mask explicitly optional qualification phrases only for this specialization.

    Handles advertised light vans with optional old class-2/C1 and BKrFQG modules.
    Mandatory language inside the same phrase always prevents masking.
    """
    pattern = re.compile(
        r"\b(?:Führerschein|Führerscheinklasse|Klasse|Code|Schlüsselzahl|Module|Fahrerkarte)\b"
        r"[^.!?;\n]{0,140}?\b(?:von Vorteil|wünschenswert|optional)\b", re.I,
    )

    def mask(match: re.Match[str]) -> str:
        phrase = match.group()
        if re.search(r"\b(?:erforderlich|zwingend|Pflicht|benötigt|Voraussetzung|muss)\b", phrase, re.I):
            return phrase
        return " "

    # Preserve sentence boundaries; a following "Deutsch von Vorteil" cannot waive CE.
    return pattern.sub(mask, re.sub(r"\binkl\.", "inkl", text, flags=re.I))
