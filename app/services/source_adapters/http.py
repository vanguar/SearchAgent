from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.services.source_adapters.errors import HttpDecodeError, HttpTransportError
from app.services.source_adapters.models import RawPayload


@dataclass(frozen=True, slots=True)
class HttpJsonResponse:
    url: str
    status_code: int
    payload: RawPayload


@dataclass(frozen=True, slots=True)
class HttpTextResponse:
    url: str
    status_code: int
    text: str


class HttpJsonTransport(Protocol):
    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> HttpJsonResponse: ...

    def post_json(
        self,
        url: str,
        *,
        body: Any | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> HttpJsonResponse: ...


class HttpTextTransport(Protocol):
    def get_text(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> HttpTextResponse: ...


class UrllibHttpJsonTransport:
    """Small shared GET+POST JSON helper with deterministic error wrapping."""

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> HttpJsonResponse:
        full_url = _build_url(url, params)
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(dict(headers))

        request = Request(full_url, headers=request_headers, method="GET")
        return _execute(request, full_url, timeout_seconds)

    def post_json(
        self,
        url: str,
        *,
        body: Any | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> HttpJsonResponse:
        full_url = _build_url(url, params)
        request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if headers:
            request_headers.update(dict(headers))

        encoded_body = json.dumps(body if body is not None else {}).encode("utf-8")
        request = Request(full_url, data=encoded_body, headers=request_headers, method="POST")
        return _execute(request, full_url, timeout_seconds)


class UrllibHttpTextTransport:
    """Small shared GET text helper for XML/RSS feeds."""

    def get_text(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> HttpTextResponse:
        full_url = _build_url(url, params)
        request_headers = {"Accept": "application/rss+xml, application/xml, text/xml, */*"}
        if headers:
            request_headers.update(dict(headers))

        request = Request(full_url, headers=request_headers, method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                status_code = getattr(response, "status", 200)
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            detail = error_body.strip() or str(exc.reason)
            raise HttpTransportError(url=full_url, message=detail, status_code=exc.code) from exc
        except URLError as exc:
            raise HttpTransportError(url=full_url, message=str(exc.reason), status_code=None) from exc
        except TimeoutError as exc:
            raise HttpTransportError(url=full_url, message="Request timed out.", status_code=None) from exc

        return HttpTextResponse(url=full_url, status_code=status_code, text=body)


def _execute(request: Request, full_url: str, timeout_seconds: float) -> HttpJsonResponse:
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
            status_code = getattr(response, "status", 200)
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        detail = error_body.strip() or str(exc.reason)
        raise HttpTransportError(url=full_url, message=detail, status_code=exc.code) from exc
    except URLError as exc:
        raise HttpTransportError(url=full_url, message=str(exc.reason), status_code=None) from exc
    except TimeoutError as exc:
        raise HttpTransportError(url=full_url, message="Request timed out.", status_code=None) from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HttpDecodeError(url=full_url, message="Response body is not valid JSON.") from exc

    return HttpJsonResponse(url=full_url, status_code=status_code, payload=payload)


def _build_url(url: str, params: Mapping[str, Any] | None) -> str:
    if not params:
        return url

    serialized: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            serialized[key] = "true" if value else "false"
        else:
            serialized[key] = str(value)

    if not serialized:
        return url

    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(serialized)}"
