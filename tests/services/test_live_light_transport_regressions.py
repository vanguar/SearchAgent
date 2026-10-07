"""Frozen API corpus: independent relevance labels and unchanged existing-profile scores."""
import json
import re
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
# Пометка пола из одиночных букв, которую нормализатор заголовков раньше
# оставлял в тексте: «fahrer w d m potsdam» и «fahrer potsdam» — одна вакансия.
_GENDER_LETTERS_RE = re.compile(r"(?:\b[mwdfx]\b\s*){3,4}")


def _title_key(title: str) -> str:
    return " ".join(_GENDER_LETTERS_RE.sub(" ", title).split())


def _aligned(entries: list[dict]) -> list[tuple[list[dict], object]]:
    """Сопоставить снимок корпуса с группами по заголовку, а не по позиции.

    После снимка дедупликация научилась склеивать одинаковые объявления одного
    работодателя (например, две записи P&H «LKW Fahrer 7,5 t C1» из BA), и
    позиционное сравнение сдвигалось. Для склеенной группы допустимо решение
    любой из исходных записей с тем же заголовком.
    """
    by_title: dict[str, list[dict]] = {}
    for entry in entries:
        by_title.setdefault(_title_key(entry["title"]), []).append(entry)
    aligned = [(by_title.get(_title_key(group.normalized_title), []), group) for group in GROUPS]
    assert all(candidates for candidates, _ in aligned)
    return aligned


@pytest.fixture(autouse=True)
def freeze_scoring_date(monkeypatch):
    monkeypatch.setattr("app.services.scorer.utc_now", lambda: datetime.fromisoformat(CAPTURE["timestamp"]))


def test_target_recall_and_noise_protection_on_captured_api_records():
    for labels, group in _aligned(LABELS):
        label = labels[0]
        decision = explain_filter_decision(group, PROFILE)["final_decision"]
        if label["label"] == "TARGET":
            assert decision in {"hot", "maybe"}, group.normalized_title
        elif label["label"] in {"NOISE", "UNKNOWN"}:
            assert decision == "hard_hidden", group.normalized_title
        else:
            assert decision != "hot", group.normalized_title


# Намеренные изменения после исправления курьерского шума; всё остальное обязано
# совпадать с BEFORE до балла. Метки корпуса оценивают перевозку грузов, а не
# доставку: для курьера "Postzustellung / FS B" — профильная работа, которую
# раньше не узнавал заголовок. Охранник остаётся скрытым и лишь теряет бонус за
# водительские слова из тела.
_INTENDED_COURIER_NOISE_FIX = {
    "postzustellung fs b vollzeitanstellung": ("hot", 71),
    "sicherheitsmitarbeiter werkschutz automobilhersteller 15 90 std in berlin id 25911": ("hard_hidden", 27),
}
_INTENDED_CHANGES = {"courier": _INTENDED_COURIER_NOISE_FIX, "fahrer_b": _INTENDED_COURIER_NOISE_FIX}


@pytest.mark.parametrize("name,role,term", [
    ("courier", "Курьер", "Kurier"),
    ("fahrer_b", "Fahrer B", "Fahrer Klasse B"),
    ("vehicle_logistics", "Fahrzeugüberführer", "Fahrzeugüberführer"),
    ("warehouse", "Lagerarbeiter", "Lagerarbeiter"),
])
def test_existing_profiles_preserve_frozen_decisions_and_scores(name, role, term):
    profile = replace(PROFILE, desired_roles=(role,), search_query_terms=(term,))
    intended = _INTENDED_CHANGES.get(name, {})
    for candidates, group in _aligned(BEFORE["evaluations"][name]):
        actual = explain_filter_decision(group, profile)
        expected_pairs = {
            intended.get(group.normalized_title, (expected["final_decision"], expected["score"]))
            for expected in candidates
        }
        assert (actual["final_decision"], actual["score"]) in expected_pairs, group.normalized_title
