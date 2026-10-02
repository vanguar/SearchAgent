"""Read-only diagnostics: direct adapter probes and frozen deterministic scoring. No DB writes."""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from app.services.filter_engine import explain_filter_decision
from app.services.normalizer import VacancyNormalizer
from app.services.search_models import SearchProfileContext
from app.services.source_adapters.models import SourceRecordPreview, SourceSearchInput
from app.services.source_merge import SourceMergeService

QUERIES = (
    "Sprinterfahrer", "Transporterfahrer", "Fahrer Klasse B Transporter", "Fahrer bis 3,5 t",
    "Fahrer Nahverkehr Klasse B", "Fahrer Werksverkehr", "Fahrer Direktfahrten", "Fahrer Sonderfahrten",
    "Kleintransporter Fahrer", "Gütertransport Fahrer Klasse B",
)
PROFILE = SearchProfileContext(
    profile_label="Transporter / Sprinter — категория B", profile_source="fallback",
    desired_roles=("Transporter / Sprinter Klasse B",), search_query_terms=("Sprinterfahrer",),
    driver_license="B", german_level="A1", english_level="нет", self_employment_ok=False,
    shift_ok=True, employment_types=("full_time", "part_time", "mini_job", "temporary"),
)


def group_records(rows):
    normalizer = VacancyNormalizer()
    return SourceMergeService().merge_records(tuple(
        normalizer.normalize_source_record(SourceRecordPreview(**row)) for row in rows
    )).canonical_groups


def synthetic():
    good = [
        "Sprinterfahrer Klasse B", "Transporterfahrer bis 3,5 t", "Fahrer Klasse B Transporter",
        "Sprinterfahrer Nahverkehr", "Fahrer Werksverkehr mit Sprinter", "Fahrer Direktfahrten Klasse B",
        "Fahrer Sonderfahrten Transporter", "Fahrer 3,5 t", "Berufskraftfahrer 3,5 t Klasse B",
        "Kraftfahrer 3,5 t Klasse B", "Transporterfahrer",
    ]
    bad = [
        "LKW-Fahrer CE", "Berufskraftfahrer Fernverkehr", "Kraftfahrer 40 Tonner", "Müllfahrer",
        "Baggerfahrer", "Staplerfahrer", "Busfahrer", "Taxifahrer", "Sicherheitskraft", "Redakteur",
        "Paketzusteller Amazon", "Selbständiger Transporterfahrer mit Gewerbeschein",
        "Fahrzeugüberführer Klasse B",
    ]
    ambig = ["Servicefahrer", "Shuttlefahrer", "Auslieferungsfahrer", "Fahrer im Regionalverkehr"]
    cases = [(label, title, "") for label, titles in [("GOOD", good), ("BAD", bad), ("AMBIG", ambig)] for title in titles]
    cases += [
        ("BAD", "Lagermitarbeiter", "Gelegentlich fahren Sie einen Transporter. Waren im Lager kommissionieren."),
        ("GOOD", "Mitarbeiter Logistik", "Sie transportieren täglich Waren mit einem Sprinter zwischen unseren drei Standorten."),
        ("BAD", "Redakteur", "Wir berichten über Fahrer, Kurier und Sprinterfahrer Klasse B im Gütertransport."),
        ("GOOD", "Fahrer Klasse B im Nahverkehr", "Sie liefern täglich Waren an unsere Filialen. Grundkenntnisse Deutsch."),
    ]
    return [{"label": label, "record": {
        "source_id": "ba", "source_name": "BA", "external_id": f"frozen-{i}", "source_reference": None,
        "title": title, "company": f"Fixture {i}", "location": "Berlin", "posted_at": "2026-10-02",
        "detail_url": f"https://example.org/frozen/{i}", "raw_payload": {"description": body},
    }} for i, (label, title, body) in enumerate(cases)]


def main():
    logging.disable(logging.CRITICAL)  # Adapter retry logs may contain credential-bearing URLs.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("freeze", "score", "probe"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--env", type=Path)
    parser.add_argument("--city", default="Berlin")
    parser.add_argument("--queries", nargs="*")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "freeze":
        result = synthetic()
    elif args.mode == "score":
        cases = json.loads(args.input.read_text(encoding="utf-8"))
        result = [{"label": case["label"], "title": case["record"]["title"],
                   **explain_filter_decision(group_records([case["record"]])[0], PROFILE)} for case in cases]
    else:
        if args.env:
            from dotenv import load_dotenv
            load_dotenv(args.env, override=False)
        from app.core.config import Settings
        from app.services.source_adapters.registry import SourceAdapterRegistry
        settings = Settings()
        registry = SourceAdapterRegistry(settings=settings)
        result = {"timestamp": datetime.now(UTC).isoformat(), "city": args.city, "page_size": 25, "probes": []}
        for query in args.queries or QUERIES:
            for source in ("ba", "adzuna", "careerjet"):
                probe = {"query": query, "source": source, "records": [], "status": "ok"}
                adapter = registry.get(source)
                try:
                    if not adapter.is_enabled():
                        probe["status"] = "disabled"
                    else:
                        response = adapter.search(SourceSearchInput(query, location=args.city, radius_km=50, page_size=25))
                        probe["records"] = [asdict(r) for r in response.records]
                        probe["total_count"] = response.total_count
                except Exception as exc:
                    # Never serialize exception URLs: authenticated sources can put credentials there.
                    probe["status"] = type(exc).__name__
                result["probes"].append(probe)
                args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(query, source, probe["status"], len(probe["records"]), flush=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
