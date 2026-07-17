from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.logging import logger
from app.services.source_adapters.errors import HttpDecodeError, HttpTransportError
from app.services.source_adapters.models import RawPayload

# Polite, identifiable client string — several public APIs (e.g. HH) reject or throttle
# anonymous urllib requests that send no User-Agent.
DEFAULT_USER_AGENT = "SmartJob-SearchAgent/0.1 (+https://github.com/; job-search-bot)"

# HTTP statuses worth retrying: explicit rate limiting + transient server errors.
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Error bodies (e.g. a Cloudflare "Just a moment…" challenge HTML page) can be huge.
# Collapse whitespace and cap the detail so it stays readable in the UI / logs.
_MAX_ERROR_DETAIL_CHARS: int = 300

# Default per-instance GET cache TTL used by the registry's shared transport. A single
# orchestrated search now issues many keyword attempts; sources that return the full list
# and filter client-side (e.g. Arbeitnow) would otherwise be re-fetched identically each
# time, tripping rate limits (HTTP 429 / Cloudflare). Caching collapses them into one call.
DEFAULT_GET_CACHE_TTL_SECONDS: float = 120.0


def _short_detail(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > _MAX_ERROR_DETAIL_CHARS:
        return collapsed[:_MAX_ERROR_DETAIL_CHARS].rstrip() + "…"
    return collapsed


class _ResponseCache:
    """Tiny thread-safe TTL cache for idempotent GET responses (per transport instance).

    Only successful responses are cached. Scope is the transport instance, which lives for
    one SearchService (one search run), so there is no cross-request staleness.
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, tuple[float, int, bytes]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> tuple[int, bytes] | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            stored_at, status, body = entry
            if now - stored_at > self._ttl:
                self._entries.pop(key, None)
                return None
            return status, body

    def set(self, key: str, status: int, body: bytes) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), status, body)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Deterministic-ish retry with exponential backoff and full jitter.

    Retries transport errors (no response) and retryable HTTP statuses. A non-retryable
    HTTP status (403, 404, 400, …) is raised immediately so callers see real errors fast.
    """

    max_retries: int = 2  # → up to 3 attempts total
    backoff_base_seconds: float = 0.5
    backoff_cap_seconds: float = 8.0
    sleep: Callable[[float], None] = time.sleep
    jitter: Callable[[float], float] = random.uniform

    def backoff_delay(self, attempt: int, *, retry_after: float | None = None) -> float:
        """Delay before retry `attempt` (1-based). Honors a server Retry-After when larger."""
        capped = min(self.backoff_cap_seconds, self.backoff_base_seconds * (2 ** (attempt - 1)))
        delay = self.jitter(0.0, capped)  # full jitter
        if retry_after is not None:
            delay = max(delay, retry_after)
        return delay


_DEFAULT_RETRY_POLICY = RetryPolicy()


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
    """Small shared GET+POST JSON helper with retry and deterministic error wrapping."""

    def __init__(
        self,
        *,
        retry_policy: RetryPolicy | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        cache_ttl_seconds: float = 0.0,
    ) -> None:
        self._retry_policy = retry_policy or _DEFAULT_RETRY_POLICY
        self._user_agent = user_agent
        self._cache = _ResponseCache(cache_ttl_seconds) if cache_ttl_seconds > 0 else None

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> HttpJsonResponse:
        full_url = _build_url(url, params)
        request_headers = {"Accept": "application/json", "User-Agent": self._user_agent}
        if headers:
            request_headers.update(dict(headers))

        request = Request(full_url, headers=request_headers, method="GET")
        status_code, body = _execute_with_retries(
            request, full_url, timeout_seconds, self._retry_policy, cache=self._cache
        )
        return _decode_json(full_url, status_code, body)

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
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": self._user_agent,
        }
        if headers:
            request_headers.update(dict(headers))

        encoded_body = json.dumps(body if body is not None else {}).encode("utf-8")
        request = Request(full_url, data=encoded_body, headers=request_headers, method="POST")
        status_code, response_body = _execute_with_retries(request, full_url, timeout_seconds, self._retry_policy)
        return _decode_json(full_url, status_code, response_body)


class UrllibHttpTextTransport:
    """Small shared GET text helper for XML/RSS feeds with retry."""

    def __init__(
        self,
        *,
        retry_policy: RetryPolicy | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._retry_policy = retry_policy or _DEFAULT_RETRY_POLICY
        self._user_agent = user_agent

    def get_text(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> HttpTextResponse:
        full_url = _build_url(url, params)
        request_headers = {
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "User-Agent": self._user_agent,
        }
        if headers:
            request_headers.update(dict(headers))

        request = Request(full_url, headers=request_headers, method="GET")
        status_code, body = _execute_with_retries(request, full_url, timeout_seconds, self._retry_policy)
        return HttpTextResponse(url=full_url, status_code=status_code, text=body.decode("utf-8", errors="replace"))


def _execute_with_retries(
    request: Request,
    full_url: str,
    timeout_seconds: float,
    retry_policy: RetryPolicy,
    *,
    cache: _ResponseCache | None = None,
) -> tuple[int, bytes]:
    """Perform the request, retrying transport errors and retryable HTTP statuses.

    Returns (status_code, raw_body_bytes). Raises HttpTransportError on terminal failure.
    Successful GET responses are served from / stored in `cache` when provided.
    """
    is_get = request.get_method() == "GET"
    if cache is not None and is_get:
        cached = cache.get(full_url)
        if cached is not None:
            return cached

    last_error: HttpTransportError | None = None
    for attempt in range(1, retry_policy.max_retries + 2):  # 1 initial + N retries
        retry_after: float | None = None
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                status_code = getattr(response, "status", 200)
                body = response.read()
                if cache is not None and is_get:
                    cache.set(full_url, status_code, body)
                return status_code, body
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            detail = _short_detail(error_body.strip() or str(exc.reason))
            last_error = HttpTransportError(url=full_url, message=detail, status_code=exc.code)
            if exc.code not in _RETRYABLE_STATUS_CODES:
                raise last_error from exc
            retry_after = _parse_retry_after(exc.headers.get("Retry-After") if exc.headers else None)
        except (URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", None)
            message = "Request timed out." if isinstance(exc, TimeoutError) else str(reason or exc)
            last_error = HttpTransportError(url=full_url, message=message, status_code=None)

        if attempt <= retry_policy.max_retries:
            delay = retry_policy.backoff_delay(attempt, retry_after=retry_after)
            logger.warning(
                "http_retry url=%s attempt=%d/%d delay=%.2fs reason=%s",
                full_url, attempt, retry_policy.max_retries, delay,
                last_error.message if last_error else "unknown",
            )
            retry_policy.sleep(delay)

    # The loop always records last_error before exhausting; guard explicitly rather than
    # via assert so the behavior is correct even under `python -O` (asserts stripped).
    if last_error is not None:
        raise last_error
    raise HttpTransportError(url=full_url, message="Request failed without a recorded error.", status_code=None)


def _decode_json(full_url: str, status_code: int, body: bytes) -> HttpJsonResponse:
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HttpDecodeError(url=full_url, message="Response body is not valid JSON.") from exc
    return HttpJsonResponse(url=full_url, status_code=status_code, payload=payload)


def _parse_retry_after(raw: str | None) -> float | None:
    """Parse a Retry-After header value (delta-seconds form only)."""
    if not raw:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None  # HTTP-date form intentionally not supported; fall back to backoff
    return seconds if seconds >= 0 else None


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
