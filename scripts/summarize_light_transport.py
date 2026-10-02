"""Score the SAME captured records with any checkout on PYTHONPATH; never call sources/DB."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

from app.services.filter_engine import explain_filter_decision
from diagnose_light_transport import PROFILE, group_records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    captured = json.loads(args.input.read_text(encoding="utf-8"))
    annotations = json.loads(args.labels.read_text(encoding="utf-8"))
    all_records = [record for probe in captured["probes"] for record in probe["records"]]
    groups = group_records(all_records)
    key_to_id = {record.source_record_key: i for i, group in enumerate(groups) for record in group.source_records}
    good = {i for i, annotation in enumerate(annotations) if annotation["label"] == "TARGET"}
    for annotation, group in zip(annotations, groups, strict=True):
        assert annotation["title"] == group.normalized_title
        assert set(annotation["source_keys"]) == {r.source_record_key for r in group.source_records}
    query_ids = {}
    for query in dict.fromkeys(p["query"] for p in captured["probes"]):
        records = [record for p in captured["probes"] if p["query"] == query for record in p["records"]]
        ids = {key_to_id[r.source_record_key] for g in group_records(records) for r in g.source_records}
        query_ids[query] = ids
    metrics = []
    for query, ids in query_ids.items():
        probes = [p for p in captured["probes"] if p["query"] == query]
        others = set().union(*(v for k, v in query_ids.items() if k != query))
        metrics.append({
            "query": query, "raw": sum(len(p["records"]) for p in probes), "canonical": len(ids),
            "relevant": len(ids & good), "unique_relevant": len((ids & good) - others),
            "noise_or_unconfirmed": len(ids - good), "relevant_ids": sorted(ids & good),
            "sources": [{"source": p["source"], "status": p["status"], "raw": len(p["records"])} for p in probes],
        })
    profiles = {
        "light_goods_transport": PROFILE,
        "courier": replace(PROFILE, desired_roles=("Курьер",), search_query_terms=("Kurier",)),
        "fahrer_b": replace(PROFILE, desired_roles=("Fahrer B",), search_query_terms=("Fahrer Klasse B",)),
        "vehicle_logistics": replace(PROFILE, desired_roles=("Fahrzeugüberführer",), search_query_terms=("Fahrzeugüberführer",)),
        "warehouse": replace(PROFILE, desired_roles=("Lagerarbeiter",), search_query_terms=("Lagerarbeiter",)),
    }
    evaluations = {}
    for name, profile in profiles.items():
        evaluations[name] = []
        for i, group in enumerate(groups):
            decision = explain_filter_decision(group, profile)
            evaluations[name].append({
                "id": i, "label": annotations[i]["label"], "title": group.normalized_title,
                **{key: decision[key] for key in ("final_decision", "score", "hard_reject_reason")},
            })
    result = {
        # Universal-newline decoding makes the capture identity stable across Git CRLF conversion.
        "input_sha256": hashlib.sha256(args.input.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
        "timestamp": captured["timestamp"], "city": captured["city"], "page_size": captured["page_size"],
        "canonical_total": len(groups), "raw_total": len(all_records), "query_metrics": metrics,
        "label_counts": dict(Counter(a["label"] for a in annotations)), "evaluations": evaluations,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("frozen sha256", result["input_sha256"], "raw", len(all_records), "canonical", len(groups))


if __name__ == "__main__":
    main()
