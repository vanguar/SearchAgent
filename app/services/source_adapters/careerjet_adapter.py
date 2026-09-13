from __future__ import annotations

import hashlib
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from typing import Any

from app.core.config import Settings
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.errors import (
    AdapterConfigurationError,
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

# User-Agent конечного пользователя для обязательного параметра user_agent.
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# У Careerjet два разных API, и различие принципиальное:
#
# 1. v4 Publisher API (search.api.careerjet.net/v4/query) — Basic Auth по API-ключу,
#    но фактическая авторизация идёт по ИСХОДЯЩЕМУ IP publisher-аккаунта. С локальной
#    машины он стабильно отвечает 403 "Unauthorized access from IP <...>".
# 2. Публичный partner-эндпоинт (public.api.careerjet.net/search) — авторизация по
#    параметру affid плюс обязательный заголовок Referer. Работает с любого IP.
#
# Адаптер использует (2): это единственный вариант, работающий в local-first режиме.
# Эндпоинт доступен только по HTTP (443 закрыт), поэтому affid уходит открытым текстом —
# это partner-идентификатор выдачи, а не ключ доступа к данным пользователя.
#
# Параметр `user_ip` — IP конечного пользователя (браузера); нужен Careerjet для
# geo-targeting и GDPR, а НЕ для авторизации. Когда реального IP браузера нет,
# используется явный локальный fallback.
_LOCAL_FALLBACK_USER_IP = "127.0.0.1"

# Значение location для поиска по всей стране (и запасной вариант, когда город не распознан).
_COUNTRY_WIDE_LOCATION = "Deutschland"
# Поиск по Германии всегда идёт в немецкий индекс: чужая локаль вернула бы вакансии
# другой страны, а не перевод немецких.
_GERMANY_LOCALE = "de_DE"
_DEFAULT_REMOTE_TARGET = ("en_GB", "Remote")


class CareerjetAdapter(BaseSourceAdapter):
    source_id = "careerjet"
    display_name = "Careerjet"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_transport: HttpJsonTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.http_transport = http_transport or UrllibHttpJsonTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_careerjet_enabled

    def describe(self) -> SourceAdapterDescriptor:
        if not self.is_enabled():
            return SourceAdapterDescriptor(
                source_id=self.source_id,
                display_name=self.display_name,
                enabled=False,
                status_label="Отключен",
                status_kind="disabled",
                status_detail="SOURCE_CAREERJET_ENABLED=false.",
            )
        if not self.settings.source_careerjet_api_key:
            return SourceAdapterDescriptor(
                source_id=self.source_id,
                display_name=self.display_name,
                enabled=True,
                status_label="Не работает",
                status_kind="error",
                status_detail="Нет SOURCE_CAREERJET_API_KEY (affiliate id).",
            )
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=True,
            status_label="Готов",
            status_kind="success",
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()

        if not self.settings.source_careerjet_api_key:
            raise AdapterConfigurationError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=(
                    "Careerjet требует affiliate id. "
                    "Зарегистрируйтесь на https://www.careerjet.de/partners/ "
                    "и задайте SOURCE_CAREERJET_API_KEY в .env."
                ),
            )

        if search_input.search_mode == "remote_worldwide":
            return self._search_remote(search_input)
        return self._search_germany(search_input)

    def _search_germany(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        requested_location = _resolve_location(search_input, remote_mode=False)
        warnings_out: list[str] = []

        payload = self._query(requested_location, search_input, locale_code=_GERMANY_LOCALE)
        response_type = _to_text(payload.get("type"))

        if response_type == "LOCATIONS" and requested_location != _COUNTRY_WIDE_LOCATION:
            # Careerjet не распознал локацию. Пустой ответ здесь неотличим от "ничего не
            # нашлось", поэтому вместо тихого нуля повторяем поиск по всей Германии
            # и говорим об этом явно.
            warnings_out.append(
                f"Careerjet не распознал локацию {requested_location!r} — "
                "поиск выполнен по всей Германии."
            )
            payload = self._query(
                _COUNTRY_WIDE_LOCATION,
                search_input,
                locale_code=_GERMANY_LOCALE,
                with_radius=False,
            )

        return self._build_response(
            search_input,
            payloads=((payload, _GERMANY_LOCALE),),
            warnings_out=warnings_out,
        )

    def _search_remote(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        """Всемирно-удалённый поиск: по одному запросу на языковой индекс.

        У каждой локали своя область поиска, потому что "Remote" — это для Careerjet
        НАЗВАНИЕ МЕСТА, а не признак удалённой работы. Замеры одного и того же запроса
        "Python Developer": en_GB+Remote — 1 вакансия, uk_UA+Remote — 1, uk_UA+Ukraine —
        51, пустая локация — 693, но почти сплошь офисные вакансии в Британии. Поэтому
        область задаётся явно рядом с локалью: SOURCE_CAREERJET_REMOTE_LOCALES.
        """
        payloads: list[tuple[Mapping[str, Any], str]] = []
        for locale_code, location in _remote_targets(self.settings.source_careerjet_remote_locales):
            payload = self._query(
                location, search_input, locale_code=locale_code, with_radius=False
            )
            payloads.append((payload, locale_code))

        return self._build_response(search_input, payloads=tuple(payloads), warnings_out=[])

    def _build_response(
        self,
        search_input: SourceSearchInput,
        *,
        payloads: tuple[tuple[Mapping[str, Any], str], ...],
        warnings_out: list[str],
    ) -> AdapterSearchResponse:
        records: list[SourceRecordPreview] = []
        seen_ids: set[str] = set()
        skipped = 0
        # None означает "источник не сообщил число", а не "ноль" — контракт
        # AdapterSearchResponse.total_count это различает, и различие сохраняем.
        total_hits: int | None = None

        for payload, locale_code in payloads:
            response_type = _to_text(payload.get("type"))
            if response_type == "ERROR":
                # Careerjet умеет отвечать 200 с телом {"type":"ERROR","error":"..."}.
                raise AdapterRequestError(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    message=f"Careerjet отклонил запрос: {_to_text(payload.get('error')) or 'без деталей'}.",
                )
            if response_type == "LOCATIONS":
                message = _to_text(payload.get("message")) or "location mode"
                warnings_out.append(f"Careerjet ({locale_code}): режим локации — {message}.")
                continue

            raw_jobs = payload.get("jobs", [])
            if not isinstance(raw_jobs, list):
                raise AdapterResponseError(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    message="Careerjet ответ не содержит список jobs.",
                )

            reported_hits = _to_int(payload.get("hits"))
            if reported_hits is not None:
                total_hits = reported_hits if total_hits is None else total_hits + reported_hits
            for raw_job in raw_jobs:
                if not isinstance(raw_job, Mapping):
                    skipped += 1
                    continue
                record = _parse_record(self.source_id, self.display_name, raw_job)
                if record is None:
                    skipped += 1
                    continue
                if record.external_id in seen_ids:
                    continue
                seen_ids.add(record.external_id)
                records.append(record)

        if skipped:
            warnings_out.append(f"Careerjet: пропущено {skipped} записей без стабильного ID.")

        if len(payloads) == 1:
            raw_payload: dict[str, Any] = dict(payloads[0][0])
        else:
            raw_payload = {
                "locales": tuple(locale for _, locale in payloads),
                "payloads": tuple(dict(payload) for payload, _ in payloads),
            }

        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=tuple(records),
            # Ни одна локаль не сообщила hits (например режим LOCATIONS) — тогда
            # честное число это то, что реально разобрано, как было и до локалей.
            total_count=total_hits if total_hits is not None else len(records),
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload=raw_payload,
            warnings=tuple(warnings_out),
        )

    def _query(
        self,
        location: str,
        search_input: SourceSearchInput,
        *,
        locale_code: str,
        with_radius: bool = True,
    ) -> Mapping[str, Any]:
        params: dict[str, Any] = {
            "affid": self.settings.source_careerjet_api_key,
            "keywords": search_input.query or None,
            "location": location,
            "locale_code": locale_code,
            "page": search_input.page,
            # Публичный эндпоинт ждёт `pagesize` (v4 использовал `page_size`).
            "pagesize": min(search_input.page_size, 100),
            "sort": "date",
            "fragment_size": self.settings.source_careerjet_fragment_size,
            "user_agent": _DEFAULT_USER_AGENT,
            # Geo-targeting, не авторизация: реальный IP браузера, иначе локальный fallback.
            "user_ip": search_input.user_ip or _LOCAL_FALLBACK_USER_IP,
        }
        # Радиус имеет смысл только вокруг конкретного города, не вокруг страны.
        if (
            with_radius
            and search_input.radius_km is not None
            and location != _COUNTRY_WIDE_LOCATION
        ):
            params["radius"] = search_input.radius_km

        try:
            http_response = self.http_transport.get_json(
                self.settings.source_careerjet_base_url,
                params=params,
                # Без Referer Careerjet отвечает 403 "Undeclared referrer".
                headers={"Referer": self.settings.source_careerjet_referer},
                timeout_seconds=self.settings.source_adapter_timeout_seconds,
            )
        except HttpTransportError as exc:
            status_hint = f" HTTP {exc.status_code}." if exc.status_code is not None else ""
            raise AdapterRequestError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Не удалось получить ответ от Careerjet.{status_hint} {exc.message}".strip(),
            ) from exc
        except HttpDecodeError as exc:
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message=f"Careerjet вернул некорректный JSON: {exc.message}",
            ) from exc

        payload = http_response.payload
        if not isinstance(payload, Mapping):
            raise AdapterResponseError(
                source_id=self.source_id,
                source_name=self.display_name,
                message="Careerjet ответ имеет неожиданный формат верхнего уровня.",
            )
        return payload



def _remote_targets(raw_value: str) -> tuple[tuple[str, str], ...]:
    """Пары "языковой индекс — область поиска" для всемирно-удалённого поиска.

    Формат записи: ``locale`` или ``locale:location``. Без явной области берётся
    "Remote" — так вёл себя адаптер до появления настройки.
    """
    targets: list[tuple[str, str]] = []
    for part in raw_value.split(","):
        entry = part.strip()
        if not entry:
            continue
        locale_code, separator, location = entry.partition(":")
        locale_code = locale_code.strip()
        if not locale_code:
            continue
        resolved_location = location.strip() if separator else _DEFAULT_REMOTE_TARGET[1]
        targets.append((locale_code, resolved_location))
    return tuple(dict.fromkeys(targets)) or (_DEFAULT_REMOTE_TARGET,)


def _resolve_location(search_input: SourceSearchInput, *, remote_mode: bool) -> str:
    """Локация запроса. Пустое поле означает "вся Германия", а не "игнорировать поле"."""
    if remote_mode:
        return "Remote"
    return (search_input.location or "").strip() or _COUNTRY_WIDE_LOCATION


def _parse_record(
    source_id: str,
    source_name: str,
    raw_job: Mapping[str, Any],
) -> SourceRecordPreview | None:
    # API не возвращает поле id — используем хэш URL как стабильный внешний ID
    url = _to_text(raw_job.get("url"))
    if not url:
        return None

    external_id = "cj-" + hashlib.md5(url.encode()).hexdigest()[:16]

    return SourceRecordPreview(
        source_id=source_id,
        source_name=source_name,
        external_id=external_id,
        source_reference=url,
        title=_to_text(raw_job.get("title")) or "Без названия",
        company=_to_text(raw_job.get("company")),
        location=_to_text(raw_job.get("locations")),
        posted_at=_format_posted_at(_to_text(raw_job.get("date"))),
        detail_url=url,
        raw_payload=dict(raw_job),
    )


def _format_posted_at(raw: str | None) -> str | None:
    """Careerjet отдаёт дату в RFC 2822 ("Fri, 11 Sep 2026 07:50:20 GMT").

    Нормализатор понимает только ISO, поэтому приводим здесь — как это уже делает RSS-лейн.
    """
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        return raw


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_int(value: Any) -> int | None:
    text = _to_text(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None
