"""Frozen API corpus: independent relevance labels and unchanged existing-profile scores."""
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from app.services.filter_engine import explain_filter_decision
from scripts.diagnose_light_transport import PROFILE, group_records

ROOT = Path(__file__).parents[2]
CAPTURE = json.loads((ROOT / "tests/fixtures/light_transport_live_probes.json").read_text(encoding="utf-8"))
LABELS = json.loads((ROOT / "docs/diagnostics/light_transport_probe_labels.json").read_text(encoding="utf-8"))
BEFORE = json.loads((ROOT / "docs/diagnostics/light_transport_live_before.json").read_text(encoding="utf-8"))
GROUPS = group_records([r for probe in CAPTURE["probes"] for r in probe["records"]])


@pytest.fixture(autouse=True)
def freeze_scoring_date(monkeypatch):
    monkeypatch.setattr("app.services.scorer.utc_now", lambda: datetime.fromisoformat(CAPTURE["timestamp"]))


def test_target_recall_and_noise_protection_on_captured_api_records():
    for label, group in zip(LABELS, GROUPS, strict=True):
        assert label["title"] == group.normalized_title
        decision = explain_filter_decision(group, PROFILE)["final_decision"]
        if label["label"] == "TARGET":
            assert decision in {"hot", "maybe"}, group.normalized_title
        elif label["label"] in {"NOISE", "UNKNOWN"}:
            assert decision == "hard_hidden", group.normalized_title
        else:
            assert decision != "hot", group.normalized_title


@pytest.mark.parametrize("name,role,term", [
    ("courier", "Курьер", "Kurier"),
    ("fahrer_b", "Fahrer B", "Fahrer Klasse B"),
    ("vehicle_logistics", "Fahrzeugüberführer", "Fahrzeugüberführer"),
    ("warehouse", "Lagerarbeiter", "Lagerarbeiter"),
])
def test_existing_profiles_preserve_frozen_decisions_and_scores(name, role, term):
    profile = replace(PROFILE, desired_roles=(role,), search_query_terms=(term,))
    for expected, group in zip(BEFORE["evaluations"][name], GROUPS, strict=True):
        actual = explain_filter_decision(group, profile)
        assert (actual["final_decision"], actual["score"]) == (expected["final_decision"], expected["score"]), group.normalized_title
