"""Снимок выдачи по РЕАЛЬНОМУ прогону — из локальной базы или из фикстуры.

Берёт исходные записи источников, которые видел один поисковый прогон, пропускает
их сырой payload через парсеры тех же адаптеров и дальше через настоящий
SearchService — нормализация, дедупликация, фильтры, скоринг, обратная связь
профиля. Сеть и LLM не используются, в базу ничего не пишется.

Снять фикстуру с прогона в базе (профиль, обратная связь и записи прогона):

    python scripts/relevance_snapshot.py dump --profile 4 --seen-like "2026-10-07 14:36%" \
        --cities "Berlin, Rostock, Stralsund, Greifswald" --out tests/fixtures/run.json

Снимок выдачи по фикстуре:

    python scripts/relevance_snapshot.py run --fixture tests/fixtures/run.json --out after.json
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
_DB_PATH = _ROOT / "smartjob.local.sqlite3"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_PATH.as_posix()}")

from app.services.relevance_replay import (  # noqa: E402
    RUN_FIXTURE_VERSION,
    replay_run_fixture,
    snapshot_of,
)

# Ключи payload, которые парсеры не читают, а места занимают много.
_DROPPED_PAYLOAD_KEYS = frozenset({"adref", "__CLASS__"})


def dump_fixture(*, profile_id: int, seen_like: str, cities: str, query: str) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.services.relevance_memory_service import RelevanceMemoryService
    from app.services.search_profile_resolver import DatabaseSearchProfileResolver

    profile = DatabaseSearchProfileResolver().resolve(profile_id=profile_id)
    session = SessionLocal()
    try:
        from app.db.models.feedback import RelevanceFeedback

        feedback_rows = [
            {
                "canonical_key": row.canonical_key,
                "source_name": row.source_name,
                "normalized_title": row.normalized_title,
                "role_family": row.role_family,
                "feedback_label": row.feedback_label,
            }
            for row in session.query(RelevanceFeedback).filter_by(profile_id=profile_id).order_by(RelevanceFeedback.id)
        ]
        RelevanceMemoryService()  # проверка, что сервис импортируется в окружении снимка
    finally:
        session.close()

    connection = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
    records = []
    for source_name, raw in connection.execute(
        "select source_name, raw_payload from vacancy_source_records where last_seen_at like ? order by id",
        (seen_like,),
    ):
        try:
            payload = json.loads(raw or "null")
        except ValueError:
            continue
        if isinstance(payload, dict):
            records.append({
                "source_name": source_name,
                "payload": {key: value for key, value in payload.items() if key not in _DROPPED_PAYLOAD_KEYS},
            })
    connection.close()
    return {
        "version": RUN_FIXTURE_VERSION,
        "description": f"profile {profile_id}, source records seen {seen_like}",
        "query": query,
        "cities": cities,
        "profile": {
            field.name: getattr(profile, field.name) for field in dataclasses.fields(profile)
        },
        "feedback": feedback_rows,
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    dump = sub.add_parser("dump")
    dump.add_argument("--profile", type=int, required=True)
    dump.add_argument("--seen-like", required=True)
    dump.add_argument("--cities", default="")
    dump.add_argument("--query", default="Доставка")
    dump.add_argument("--out", required=True)
    run = sub.add_parser("run")
    run.add_argument("--fixture", required=True)
    run.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.command == "dump":
        fixture = dump_fixture(
            profile_id=args.profile, seen_like=args.seen_like, cities=args.cities, query=args.query
        )
        Path(args.out).write_text(json.dumps(fixture, ensure_ascii=False, indent=0, default=list), encoding="utf-8")
        print(f"records: {len(fixture['records'])}")
        return 0

    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    snapshot = snapshot_of(replay_run_fixture(fixture))
    Path(args.out).write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(snapshot["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
