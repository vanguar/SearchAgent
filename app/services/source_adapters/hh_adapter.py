from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.config import Settings
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.errors import (
    AdapterRequestError,
    AdapterResponseError,
    HttpDecodeError,
    HttpTransportError,
)
from app.services.source_adapters.http import HttpJsonTransport, UrllibHttpJsonTransport
from app.services.source_adapters.models import (
    AdapterSearchResponse,
    SourceAdapterDescriptor,
    SourceRecordPreview,
    SourceSearchInput,
)


class HHAdapter(BaseSourceAdapter):
    source_id = "hh"
    display_name = "HeadHunter API"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_transport: HttpJsonTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.http_transport = http_transport or UrllibHttpJsonTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_hh_enabled

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=enabled,
            status_label="Готов" if enabled else "Отключен",
            status_kind="success" if enabled else "disabled",
            status_detail=(
                "Официальный JSON API HH; страны резолвятся через /areas, Россия/Беларусь не включены."
                if enabled
                else "SOURCE_HH_ENABLED=false."
            ),
            global_remote=True,
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()
        default_base_url = self.settings.source_hh_base_url.rstrip("/")
        target_country_names = _configured_country_names(self.settings.source_hh_country_names)
        warnings: list[str] = []
        area_targets_by_name: dict[str, HHCountryTarget] = {}
        for base_url, country_names in _group_country_names_by_base_url(
            target_country_names, default_base_url
        ).items():
            areas_payload = self._get_json(
                f"{base_url}/areas",
                params=None,
                purpose=f"справочник регионов HH ({base_url})",
            )
            if not isinstance(areas_payload, list):
                raise AdapterResponseError(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    message=f"HH /areas вернул неожиданный формат верхнего уровня для {base_url}.",
                )

            area_ids_by_name = _resolve_country_area_ids(areas_payload, country_names)
            missing = tuple(name for name in country_names if name not in area_ids_by_name)
            if missing:
                warnings.append(f"HH: страны не найдены в /areas на {base_url}: {', '.join(missing)}.")
            for country_name, area_id in area_ids_by_name.items():
                area_targets_by_name[country_name] = HHCountryTarget(
                    country_name=country_name,
                    area_id=area_id,
                    base_url=base_url,
                )

        records_by_key: dict[str, SourceRecordPreview] = {}
        raw_payloads: list[dict[str, Any]] = []
        total_count = 0
        page = max(0, search_input.page - 1)
        for target in area_targets_by_name.values():
            try:
                payload = self._get_json(
                    f"{target.base_url}/vacancies",
                    params={
                        "text": search_input.query,
                        "area": target.area_id,
                        "per_page": search_input.page_size,
                        "page": page,
                    },
                    purpose=f"поиск вакансий HH: {target.country_name}",
                )
            except AdapterRequestError as exc:
                if exc.status_code == 403 and exc.is_forbidden:
                    warnings.append(
                        f"HH: доступ к поиску вакансий для {target.country_name} на {target.base_url} "
                        "заблокирован API (HTTP 403 forbidden)."
                    )
                    continue
                raise
            if not isinstance(payload, Mapping):
                raise AdapterResponseError(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    message="HH /vacancies вернул неожиданный формат верхнего уровня.",
                )
            raw_payloads.append(dict(payload))
            total_count += _to_int(payload.get("found")) or 0
            raw_items = payload.get("items", [])
            if not isinstance(raw_items, list):
                raise AdapterResponseError(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    message="HH /vacancies не содержит список items.",
                )
            for raw_job in raw_items:
                if not isinstance(raw_job, Mapping):
                    continue
                record = _parse_record(self.source_id, self.display_name, raw_job, country_name=target.country_name)
                if record is not None:
                    records_by_key[record.external_id] = record

        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=tuple(records_by_key.values()),
            total_count=total_count,
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload={
                "countries": {
                    name: {"area_id": target.area_id, "base_url": target.base_url}
                    for name, target in area_targets_by_name.items()
                },
                "responses": raw_payloads,
            },
            warnings=tuple(warnings),
        )

    def _get_json(self, url: str, *, params: Mapping[str, Any] | None, purpose: str) -> Any:
        try:
            return self.http_transport.get_json(
                url,
                params=params,
                headers={"User-Agent": "SmartJob SearchAgent (local personal job search)"},
                timeout_seconds=self.settings.source_adapter_timeout_seconds,
            ).payload
        except HttpTransportError as exc:
            status_hint = f" HTTP {exc.status_code}." if exc.status_code is not None else ""
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Не удалось получить {purpose}.{status_hint} {exc.message}".strip(),
                status_code=exc.status_code,
                response_message=exc.message,
            ) from exc
        except HttpDecodeError as exc:
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"HH вернул некорректный JSON для {purpose}: {exc.message}",
            ) from exc


def _configured_country_names(raw: str) -> tuple[str, ...]:
    blocked = {"russia", "россия", "belarus", "беларусь", "белоруссия"}
    names = []
    for part in raw.split(","):
        name = part.strip()
        if name and _normalize_area_name(name) not in blocked:
            names.append(name)
    return tuple(dict.fromkeys(names))


class HHCountryTarget:
    def __init__(self, *, country_name: str, area_id: str, base_url: str) -> None:
        self.country_name = country_name
        self.area_id = area_id
        self.base_url = base_url


def _group_country_names_by_base_url(names: tuple[str, ...], default_base_url: str) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for name in names:
        base_url = _base_url_for_country(name, default_base_url)
        grouped.setdefault(base_url, []).append(name)
    return {base_url: tuple(country_names) for base_url, country_names in grouped.items()}


def _base_url_for_country(name: str, default_base_url: str) -> str:
    normalized = _normalize_area_name(name)
    return _COUNTRY_API_BASE_URLS.get(normalized, default_base_url)


def _resolve_country_area_ids(areas_payload: list[Any], names: tuple[str, ...]) -> dict[str, str]:
    wanted: dict[str, str] = {}
    for name in names:
        for alias in _area_name_aliases(name):
            wanted.setdefault(alias, name)

    resolved: dict[str, str] = {}
    for item in areas_payload:
        if not isinstance(item, Mapping):
            continue
        hh_name = _to_text(item.get("name"))
        hh_id = _to_text(item.get("id"))
        if hh_name is None or hh_id is None:
            continue
        configured_name = wanted.get(_normalize_area_name(hh_name))
        if configured_name is not None:
            resolved[configured_name] = hh_id
    return resolved


_COUNTRY_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "kazakhstan": ("казахстан",),
    "казахстан": ("kazakhstan",),
    "kyrgyzstan": ("кыргызстан", "киргизия"),
    "кыргызстан": ("kyrgyzstan", "киргизия"),
    "киргизия": ("kyrgyzstan", "кыргызстан"),
    "uzbekistan": ("узбекистан",),
    "узбекистан": ("uzbekistan",),
    "georgia": ("грузия",),
    "грузия": ("georgia",),
    "moldova": ("молдова",),
    "молдова": ("moldova",),
}


_COUNTRY_API_BASE_URLS: dict[str, str] = {
    "kazakhstan": "https://api.hh.kz",
    "казахстан": "https://api.hh.kz",
    "kyrgyzstan": "https://api.headhunter.kg",
    "кыргызстан": "https://api.headhunter.kg",
    "киргизия": "https://api.headhunter.kg",
    "uzbekistan": "https://api.hh.uz",
    "узбекистан": "https://api.hh.uz",
    "georgia": "https://api.headhunter.ge",
    "грузия": "https://api.headhunter.ge",
}


def _area_name_aliases(name: str) -> tuple[str, ...]:
    normalized = _normalize_area_name(name)
    aliases = _COUNTRY_NAME_ALIASES.get(normalized, ())
    return (normalized, *aliases)


def _normalize_area_name(name: str) -> str:
    return " ".join(name.casefold().replace("ё", "е").split())


def _parse_record(
    source_id: str,
    source_name: str,
    raw_job: Mapping[str, Any],
    *,
    country_name: str,
) -> SourceRecordPreview | None:
    external_id = _to_text(raw_job.get("id"))
    if external_id is None:
        return None
    employer = raw_job.get("employer")
    company = _to_text(employer.get("name")) if isinstance(employer, Mapping) else None
    area = raw_job.get("area")
    location = _to_text(area.get("name")) if isinstance(area, Mapping) else None
    salary_text = _format_salary(raw_job.get("salary"))
    raw_payload = dict(raw_job)
    raw_payload["country"] = country_name
    if salary_text:
        raw_payload["salary"] = salary_text

    return SourceRecordPreview(
        source_id=source_id,
        source_name=source_name,
        external_id=external_id,
        source_reference=external_id,
        title=_to_text(raw_job.get("name")) or "Без названия",
        company=company,
        location=location,
        posted_at=_date_part(_to_text(raw_job.get("published_at"))),
        detail_url=_to_text(raw_job.get("alternate_url")) or _to_text(raw_job.get("url")),
        raw_payload=raw_payload,
    )


def _format_salary(raw_salary: Any) -> str | None:
    if not isinstance(raw_salary, Mapping):
        return None
    salary_from = raw_salary.get("from")
    salary_to = raw_salary.get("to")
    currency = _to_text(raw_salary.get("currency")) or ""
    if salary_from is not None and salary_to is not None:
        return f"{int(salary_from)}-{int(salary_to)} {currency}".strip()
    if salary_from is not None:
        return f"от {int(salary_from)} {currency}".strip()
    if salary_to is not None:
        return f"до {int(salary_to)} {currency}".strip()
    return None


def _date_part(value: str | None) -> str | None:
    if value and "T" in value:
        return value.split("T", 1)[0]
    return value


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
