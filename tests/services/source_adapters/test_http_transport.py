"""Unit tests for the shared HTTP transport: User-Agent, retries, backoff, Retry-After."""
from __future__ import annotations

import io
from urllib.error import HTTPError, URLError

import pytest
from app.services.source_adapters import http as http_module
from app.services.source_adapters.errors import HttpTransportError
from app.services.source_adapters.http import (
    DEFAULT_USER_AGENT,
    RetryPolicy,
    UrllibHttpJsonTransport,
    _parse_retry_after,
)


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _no_sleep_policy(max_retries: int = 2) -> tuple[RetryPolicy, list[float]]:
    slept: list[float] = []
    policy = RetryPolicy(
        max_retries=max_retries,
        sleep=slept.append,
        jitter=lambda _lo, hi: hi,  # deterministic: use the full capped delay
    )
    return policy, slept


def test_sends_default_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):  # noqa: ANN001
        captured["headers"] = request.headers
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(http_module, "urlopen", fake_urlopen)
    transport = UrllibHttpJsonTransport()

    response = transport.get_json("https://example.org/api")

    assert response.payload == {"ok": True}
    # urllib capitalizes header keys
    assert captured["headers"]["User-agent"] == DEFAULT_USER_AGENT


def test_retries_transient_url_error_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_urlopen(request, timeout):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            raise URLError("temporary dns failure")
        return _FakeResponse(b'{"ok": 1}')

    monkeypatch.setattr(http_module, "urlopen", fake_urlopen)
    policy, slept = _no_sleep_policy()
    transport = UrllibHttpJsonTransport(retry_policy=policy)

    response = transport.get_json("https://example.org/api")

    assert response.payload == {"ok": 1}
    assert calls["n"] == 2
    assert len(slept) == 1  # one backoff before the successful retry


def test_does_not_retry_non_retryable_status(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_urlopen(request, timeout):  # noqa: ANN001
        calls["n"] += 1
        raise HTTPError("https://example.org/api", 403, "Forbidden", {}, io.BytesIO(b"forbidden"))

    monkeypatch.setattr(http_module, "urlopen", fake_urlopen)
    policy, slept = _no_sleep_policy()
    transport = UrllibHttpJsonTransport(retry_policy=policy)

    with pytest.raises(HttpTransportError) as exc_info:
        transport.get_json("https://example.org/api")

    assert exc_info.value.status_code == 403
    assert calls["n"] == 1  # 403 is terminal, no retries
    assert slept == []


def test_retries_429_then_exhausts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_urlopen(request, timeout):  # noqa: ANN001
        calls["n"] += 1
        raise HTTPError("https://example.org/api", 429, "Too Many", {"Retry-After": "2"}, io.BytesIO(b""))

    monkeypatch.setattr(http_module, "urlopen", fake_urlopen)
    policy, slept = _no_sleep_policy(max_retries=2)
    transport = UrllibHttpJsonTransport(retry_policy=policy)

    with pytest.raises(HttpTransportError) as exc_info:
        transport.get_json("https://example.org/api")

    assert exc_info.value.status_code == 429
    assert calls["n"] == 3  # 1 initial + 2 retries
    assert slept and all(delay >= 2 for delay in slept)  # Retry-After honored


def test_get_cache_collapses_repeated_identical_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_urlopen(request, timeout):  # noqa: ANN001
        calls["n"] += 1
        return _FakeResponse(b'{"hit": 1}')

    monkeypatch.setattr(http_module, "urlopen", fake_urlopen)
    transport = UrllibHttpJsonTransport(cache_ttl_seconds=60.0)

    first = transport.get_json("https://example.org/all-jobs?page=1")
    second = transport.get_json("https://example.org/all-jobs?page=1")
    transport.get_json("https://example.org/all-jobs?page=2")  # different URL → refetch

    assert first.payload == second.payload == {"hit": 1}
    assert calls["n"] == 2  # same URL fetched once (cached), different URL fetched again


def test_get_cache_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_urlopen(request, timeout):  # noqa: ANN001
        calls["n"] += 1
        return _FakeResponse(b'{"ok": 1}')

    monkeypatch.setattr(http_module, "urlopen", fake_urlopen)
    transport = UrllibHttpJsonTransport()  # no cache_ttl → no caching

    transport.get_json("https://example.org/api")
    transport.get_json("https://example.org/api")

    assert calls["n"] == 2


def test_error_detail_is_truncated_and_single_line(monkeypatch: pytest.MonkeyPatch) -> None:
    huge_html = "<html>\n  <body>" + ("A" * 5000) + "\n</body></html>"

    def fake_urlopen(request, timeout):  # noqa: ANN001
        raise HTTPError("https://example.org/api", 429, "Too Many", {}, io.BytesIO(huge_html.encode()))

    monkeypatch.setattr(http_module, "urlopen", fake_urlopen)
    policy, _ = _no_sleep_policy(max_retries=0)
    transport = UrllibHttpJsonTransport(retry_policy=policy)

    with pytest.raises(HttpTransportError) as exc_info:
        transport.get_json("https://example.org/api")

    message = exc_info.value.message
    assert "\n" not in message  # collapsed to a single line
    assert len(message) <= 301  # capped (+ ellipsis)


def test_parse_retry_after_handles_seconds_and_garbage() -> None:
    assert _parse_retry_after("5") == 5.0
    assert _parse_retry_after(" 3 ") == 3.0
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert _parse_retry_after("-1") is None
