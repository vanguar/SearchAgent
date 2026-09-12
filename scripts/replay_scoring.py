"""Проигрывание фильтра и скоринга на РЕАЛЬНЫХ вакансиях из локальной базы.

Юнит-тесты проверяют правила на синтетических строках. Этот стенд проверяет
другое: что на тех самых объявлениях, которые уже лежат в smartjob.local.sqlite3
и попали в выгрузки, решение изменилось именно так, как задумано.

Использование:

    python scripts/replay_scoring.py --profile 4 --title-like "kraftfahrer"
    python scripts/replay_scoring.py --profile 2 --limit 40

Стенд ничего не пишет в базу и не ходит в сеть.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from app.services.filter_engine import FilterEngine
from app.services.hashers import fingerprint_tokens, normalize_text_for_fingerprint
from app.services.language_signal_extractor import LanguageSignalExtractor
from app.services.location_normalizer import LocationNormalizer
from app.services.normalization_models import CanonicalVacancyGroup, NormalizedVacancyRecord
from app.services.rule_catalog import HOT_BUCKET_MIN_SCORE, MAYBE_BUCKET_MIN_SCORE, inspect_vacancy
from app.services.scorer import VacancyScorer
from app.services.search_models import SearchProfileContext
from app.services.search_profile_resolver import interpret_legal_status

_DB_PATH = Path(__file__).resolve().parent.parent / "smartjob.local.sqlite3"
_LOCATION_NORMALIZER = LocationNormalizer()
_LANGUAGE_EXTRACTOR = LanguageSignalExtractor()


def _km(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f} км"


def extract_language_signals(text: str):
    """Языковые сигналы по слитому тексту заголовка и тела."""
    return _LANGUAGE_EXTRACTOR.extract(title=text, body_text=None)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[: len(value)], pattern).date()
        except ValueError:
            continue
    return None


def _build_record(row: sqlite3.Row, canonical: sqlite3.Row) -> NormalizedVacancyRecord:
    try:
        payload = json.loads(row["raw_payload"]) if row["raw_payload"] else {}
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    title = canonical["normalized_title"] or ""
    body = canonical["description_text"] or ""
    location_text = canonical["location_text"]
    return NormalizedVacancyRecord(
        source_id=str(row["source_name"] or "").lower(),
        source_name=row["source_name"] or "",
        external_id=row["external_id"] or "",
        source_reference=row["external_id"],
        source_url=row["source_url"],
        raw_payload=payload,
        original_title=title,
        original_company=canonical["company_name"],
        original_location=location_text,
        original_posted_at=None,
        posted_date=_parse_date(canonical["source_posted_at"]),
        normalized_title=title,
        normalized_company=normalize_text_for_fingerprint(canonical["company_name"]) or None,
        normalized_location=_LOCATION_NORMALIZER.normalize(location_text),
        body_text=body,
        normalized_body_text=normalize_text_for_fingerprint(body),
        title_tokens=fingerprint_tokens(title),
        content_tokens=fingerprint_tokens(body),
        title_fingerprint="",
        content_fingerprint="",
        language_signals=extract_language_signals(f"{title}\n{body}"),
    )


def load_canonicals(
    *,
    title_like: str | None = None,
    company_like: str | None = None,
    limit: int = 25,
) -> list[CanonicalVacancyGroup]:
    connection = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    query = "select * from vacancy_canonicals where 1=1"
    params: list[str] = []
    if title_like:
        query += " and lower(normalized_title) like ?"
        params.append(f"%{title_like.lower()}%")
    if company_like:
        query += " and lower(coalesce(company_name,'')) like ?"
        params.append(f"%{company_like.lower()}%")
    query += " order by id desc limit ?"
    params.append(str(limit))

    groups: list[CanonicalVacancyGroup] = []
    for canonical in connection.execute(query, params):
        source_rows = connection.execute(
            "select * from vacancy_source_records where canonical_id = ?", (canonical["id"],)
        ).fetchall()
        if not source_rows:
            continue
        records = tuple(_build_record(row, canonical) for row in source_rows)
        title = canonical["normalized_title"] or ""
        body = canonical["description_text"] or ""
        groups.append(
            CanonicalVacancyGroup(
                canonical_key=f"db-{canonical['id']}",
                normalized_title=title,
                company_name=canonical["company_name"],
                location_text=canonical["location_text"],
                country_code=canonical["country_code"],
                city=canonical["city"],
                posted_date=_parse_date(canonical["source_posted_at"]),
                language_signals=extract_language_signals(f"{title}\n{body}"),
                source_records=records,
                provenance=tuple(row["source_name"] or "" for row in source_rows),
            )
        )
    connection.close()
    return groups


def load_profile_context(search_profile_id: int) -> SearchProfileContext:
    """Тот же контекст, что строит боевой резолвер, но напрямую из sqlite."""
    connection = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    search_profile = connection.execute(
        "select * from search_profiles where id = ?", (search_profile_id,)
    ).fetchone()
    if search_profile is None:
        raise SystemExit(f"search_profile {search_profile_id} не найден")
    user_profile = connection.execute(
        "select * from user_profiles where id = ?", (search_profile["user_profile_id"],)
    ).fetchone()
    connection.close()

    def as_tuple(raw: str | None) -> tuple[str, ...]:
        try:
            value = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            return ()
        return tuple(value) if isinstance(value, list) else ()

    legal_status = interpret_legal_status(user_profile["legal_status"] if user_profile else None)
    return SearchProfileContext(
        profile_label=search_profile["name"] or "",
        profile_source="saved",
        legal_status=user_profile["legal_status"] if user_profile else None,
        work_authorized=bool(user_profile["work_authorized"]) if user_profile else True,
        german_level=user_profile["german_level"] if user_profile else None,
        english_level=user_profile["english_level"] if user_profile else None,
        desired_roles=as_tuple(search_profile["desired_roles"]),
        excluded_roles=as_tuple(search_profile["excluded_roles"]),
        preferred_locations=as_tuple(search_profile["preferred_locations"]),
        relocation_ready=search_profile["relocation_ready"],
        shift_ok=search_profile["shift_ok"],
        physical_work_ok=search_profile["physical_work_ok"],
        housing_needed=search_profile["housing_needed"],
        start_availability_text=search_profile["start_availability_text"],
        driver_license=search_profile["driver_license"],
        no_german_required=bool(search_profile["no_german_required"]),
        section24_interpreted=legal_status.section24,
        search_query_terms=as_tuple(search_profile["search_query_terms"]),
        home_city=user_profile["city"] if user_profile else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=int, required=True, help="id из таблицы search_profiles")
    parser.add_argument("--title-like", default=None)
    parser.add_argument("--company-like", default=None)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--search-mode", default="germany_local")
    parser.add_argument("--home", default=None, help="город проживания (перекрывает профиль)")
    parser.add_argument("--search-location", default=None, help="город поиска из формы")
    parser.add_argument("--radius", type=int, default=None, help="радиус поиска, км")
    args = parser.parse_args()

    profile = load_profile_context(args.profile)
    profile = dataclasses.replace(
        profile,
        home_city=args.home or profile.home_city,
        search_location=args.search_location,
        search_radius_km=args.radius,
    )
    groups = load_canonicals(title_like=args.title_like, company_like=args.company_like, limit=args.limit)
    filter_engine = FilterEngine()
    scorer = VacancyScorer()

    print(f"профиль #{args.profile}: {profile.profile_label} | вакансий: {len(groups)}")
    print("=" * 110)
    for group in groups:
        signals = inspect_vacancy(group, profile)
        verdict = filter_engine.evaluate(group, profile, signals=signals, search_mode=args.search_mode)
        score = scorer.score(group, profile, signals=signals, search_mode=args.search_mode)
        reasons = ", ".join(hit.code for hit in verdict.rejection_hits) or "-"
        bucket = (
            "hot" if score.score >= HOT_BUCKET_MIN_SCORE
            else "maybe" if score.score >= MAYBE_BUCKET_MIN_SCORE
            else "rejected"
        )
        if verdict.hard_reject:
            bucket = "HIDDEN"
        print(
            f"{verdict.decision:<7} {bucket:<8} {score.score:>4}  "
            f"{(group.normalized_title or '')[:44]:<44} | {(group.city or '')[:16]:<16} | "
            f"{_km(signals.distance_from_home_km):>7} | {reasons}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
