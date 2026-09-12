from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.hashers import normalize_text_for_fingerprint


@dataclass(frozen=True, slots=True)
class VehicleClassSignals:
    light_commercial: tuple[str, ...] = ()
    heavy_vehicle: tuple[str, ...] = ()
    heavy_qualification: tuple[str, ...] = ()
    # Признаки тяжёлого транспорта, которые НЕ являются доказательством сами по себе:
    # объявление на Sprinter вполне может называть водителя "Kraftfahrer". Их перебивает
    # любой явный сигнал лёгкого транспорта, поэтому они живут отдельно от heavy_vehicle.
    heavy_vehicle_context: tuple[str, ...] = ()


_LIGHT_COMMERCIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Planensprinter", re.compile(r"\bplanensprinter\b")),
    ("Koffersprinter", re.compile(r"\bkoffersprinter\b")),
    ("Kleintransporter", re.compile(r"\bkleintransporter\b")),
    ("Kastenwagen", re.compile(r"\bkastenwagen\b")),
    ("Sprinter", re.compile(r"\bsprinter\w*\b")),
    ("Transporter", re.compile(r"\btransporter\w*\b")),
    ("PKW", re.compile(r"\bpkw\b")),
    ("bis 3,5 t", re.compile(r"\bbis\s+3\s+5\s*(?:t|tonnen?)\b")),
    ("3,5-Tonner", re.compile(r"\b3\s+5\s+tonner\b")),
    ("Klasse B", re.compile(r"\b(?:fuhrerschein\s+)?klasse\s+b\b")),
)

_HEAVY_VEHICLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Berufskraftfahrer", re.compile(r"\bberufskraftfahrer(?:in)?\b")),
    ("LKW", re.compile(r"\b(?:lkw|lastkraftwagen|truck(?:\s+driver)?|trucker)\b")),
    ("Sattelzug", re.compile(r"\bsattel(?:zug(?:maschine)?|auflieger)\w*\b")),
    ("Zugmaschine", re.compile(r"\bzugmaschine\w*\b")),
    ("Gliederzug", re.compile(r"\bgliederzug\w*\b")),
    ("Lastzug", re.compile(r"\blastzug\w*\b")),
    ("Schwerlastverkehr", re.compile(r"\bschwerlastverkehr\w*\b")),
    ("Schwertransport", re.compile(r"\bschwertransport\w*\b")),
    ("Fahrmischer", re.compile(r"\b(?:fahrmischer|betonfahrmischer)\w*\b")),
    ("Betonmischer", re.compile(r"\bbetonmisch(?:er|fahrzeug)\w*\b")),
    ("Kipper", re.compile(r"\b(?:kipper(?:fahrer)?|kippsattel|kippsilo|muldenkipper)\w*\b")),
    (
        "Silofahrzeug",
        re.compile(
            r"\b(?:silofahrzeug|silowagen|silofahrer)\w*\b|"
            r"\bsilo\s+lkw\b|\b(?:kraftfahrer|fahrer)\w*\s+(?:fur\s+)?silo\b|"
            r"\bsilo\s+(?:fahrzeug|wagen|fahrer|verkehr|transport)\w*\b"
        ),
    ),
    ("Tankwagen", re.compile(r"\b(?:tankwagen|tankfahrzeug|tanklastwagen|tankzug)\w*\b")),
    ("Abrollkipper", re.compile(r"\babrollkipper\w*\b")),
    ("Absetzkipper", re.compile(r"\babsetzkipper\w*\b")),
    ("Container-LKW", re.compile(r"\bcontainer\s+(?:lkw|lastwagen|fahrzeug)\w*\b")),
    ("Müllfahrzeug", re.compile(r"\b(?:mullfahrzeug|mullwagen)\w*\b")),
    ("Baustellen-LKW", re.compile(r"\bbaustellen\s+(?:lkw|lastwagen|fahrzeug)\w*\b")),
    ("Wechselbrücke", re.compile(r"\bwechselbruck(?:e|enverkehr|en)\w*\b")),
    ("Kran-LKW", re.compile(r"\bkran\s+(?:lkw|lastwagen)\b|\bladekran\w*\b")),
    ("Bus", re.compile(r"\b(?:busfahrer|omnibus|linienbus|reisebus)\w*\b")),
)

# Марки и модели, которые выпускаются только как грузовики: объявление, называющее их,
# не про Sprinter. Требуют транспортного контекста (как и тоннаж), потому что короткие
# токены совпадают с аббревиатурами из других областей ("daf" — Deutsch als Fremdsprache).
_HEAVY_TRUCK_BRAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("DAF", re.compile(r"\bdaf\b")),
    ("Scania", re.compile(r"\bscania\b")),
    ("Mercedes Actros", re.compile(r"\b(?:actros|arocs|atego|axor)\b")),
    ("MAN TG", re.compile(r"\btg[lmsx]\b")),
    ("Iveco", re.compile(r"\b(?:stralis|eurocargo)\b")),
    ("Volvo FH/FM", re.compile(r"\bvolvo\s+f[hm]\b")),
    ("Renault Trucks", re.compile(r"\brenault\s+trucks?\b")),
)

# "Kraftfahrer" без приставки "Berufs-" — формальное немецкое название водителя грузовика,
# но им изредка называют и водителя до 3,5 т. Поэтому сигнал слабый: он отсекает вакансию
# только тогда, когда в тексте нет ни одного упоминания лёгкого транспорта.
_HEAVY_CONTEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Kraftfahrer", re.compile(r"\bkraftfahrer(?:in)?\b")),
)

_HEAVY_QUALIFICATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Berufskraftfahrerqualifikation",
        re.compile(r"\bberufskraftfahrerqualifikation\w*\b|\bbkrfqg\b|\bgrundqualifikation\w*\b"),
    ),
    ("Code 95", re.compile(r"\b(?:schlusselzahl|kennziffer|code)\s+95\b")),
    ("Fahrerkarte", re.compile(r"\b(?:digitale\s+)?fahrerkarte\w*\b|\btachographenkarte\w*\b")),
    (
        "ADR-Schein",
        re.compile(
            r"\badr\s+(?:schein|bescheinigung|fahrer)\w*\b|"
            r"\bfahrer\w*\s+(?:mit\s+)?adr\b|\bgefahrgutfuhrerschein\w*\b"
        ),
    ),
)

_HEAVY_MASS_RE = re.compile(r"\b(?P<mass>7\s+5|12|18|40)\s*(?:t|tonnen?|tonner)\b")
_DRIVER_OR_VEHICLE_CONTEXT_RE = re.compile(
    r"\b(?:fahrer|fahrerin|kraftfahrer|berufskraftfahrer|fahrzeug|fahrzeuge|fahrzeugen|"
    r"lkw|lastwagen|transporter|tonner|zugmaschine)\w*\b"
)
_NEGATED_HEAVY_PREFIX_RE = re.compile(
    r"(?:"
    r"\b(?:kein|keine|keinen|keinem|keiner|keines|ohne|weder|statt)\s+|"
    r"\banstelle\s+von\s+|"
    r"\bnicht\s+(?:mit|auf|als)\s+|"
    r"\b(?:kein|keine|keinen)\s+(?:einsatz|fahrt|fahrten)\s+(?:mit|auf|als)\s+|"
    r"\b(?:kein|keine|keinen)\s+fahrzeug\w*\s+(?:uber|mit|ab)\s+"
    r")$"
)


def extract_vehicle_class_signals(text: str | None) -> VehicleClassSignals:
    normalized = normalize_text_for_fingerprint(text)
    if not normalized:
        return VehicleClassSignals()

    light_commercial = _matching_labels(normalized, _LIGHT_COMMERCIAL_PATTERNS)
    heavy_vehicle = list(_matching_labels(normalized, _HEAVY_VEHICLE_PATTERNS, ignore_negated=True))
    heavy_qualification = _matching_labels(normalized, _HEAVY_QUALIFICATION_PATTERNS, ignore_negated=True)
    heavy_context = _matching_labels(normalized, _HEAVY_CONTEXT_PATTERNS, ignore_negated=True)

    if _DRIVER_OR_VEHICLE_CONTEXT_RE.search(normalized):
        for label in _matching_labels(normalized, _HEAVY_TRUCK_BRAND_PATTERNS, ignore_negated=True):
            if label not in heavy_vehicle:
                heavy_vehicle.append(label)
        for match in _HEAVY_MASS_RE.finditer(normalized):
            if _is_negated_heavy_match(normalized, match):
                continue
            raw_mass = match.group("mass")
            mass = "7,5" if raw_mass == "7 5" else raw_mass
            label = f"{mass} t"
            if label not in heavy_vehicle:
                heavy_vehicle.append(label)

    return VehicleClassSignals(
        light_commercial=light_commercial,
        heavy_vehicle=tuple(heavy_vehicle),
        heavy_qualification=heavy_qualification,
        heavy_vehicle_context=heavy_context,
    )


def _matching_labels(
    normalized_text: str,
    patterns: tuple[tuple[str, re.Pattern[str]], ...],
    *,
    ignore_negated: bool = False,
) -> tuple[str, ...]:
    labels: list[str] = []
    for label, pattern in patterns:
        matches = pattern.finditer(normalized_text)
        if any(not ignore_negated or not _is_negated_heavy_match(normalized_text, match) for match in matches):
            labels.append(label)
    return tuple(labels)


def _is_negated_heavy_match(normalized_text: str, match: re.Match[str]) -> bool:
    prefix = normalized_text[max(0, match.start() - 80) : match.start()]
    return _NEGATED_HEAVY_PREFIX_RE.search(prefix) is not None
