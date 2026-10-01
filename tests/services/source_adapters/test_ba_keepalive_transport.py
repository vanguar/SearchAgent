"""BA keep-alive transport: one reused connection, bounded retries, its own connect timeout."""
from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from app.services.source_adapters.ba_adapter import BAAdapter
from app.services.source_adapters.errors import AdapterRequestError, HttpTransportError
from app.services.source_adapters.http import (
    HttpJsonResponse,
    KeepAliveHttpJsonTransport,
    RetryPolicy,
    UrllibHttpJsonTransport,
)
from app.services.source_adapters.models import SourceSearchInput
from app.services.source_adapters.registry import SourceAdapterRegistry, shared_ba_transport


def _policy(max_retries: int = 1) -> tuple[RetryPolicy, list[float]]:
    slept: list[float] = []
    return RetryPolicy(max_retries=max_retries, sleep=slept.append, jitter=lambda _lo, hi: hi), slept


class _Server:
    """Local HTTP/1.1 server that records which TCP connection served each request."""

    def __init__(self, drop_first_request: bool = False) -> None:
        self.ports: list[int] = []
        self.requests = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                server.requests += 1
                if drop_first_request and server.requests == 1:
                    self.close_connection = True  # hang up without answering: a broken connection
                    return
                server.ports.append(self.client_address[1])
                body = json.dumps({"path": self.path}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                return None

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server() -> Iterator[_Server]:
    srv = _Server()
    yield srv
    srv.close()


def test_sequential_requests_reuse_one_connection(server: _Server) -> None:
    transport = KeepAliveHttpJsonTransport(retry_policy=_policy()[0])
    for query in ("Lagerarbeiter", "Fahrer", "Python"):
        transport.get_json(f"{server.url}/jobs", params={"was": query})
    transport.close()
    assert len(server.ports) == 3
    assert len(set(server.ports)) == 1  # one TCP (and, over HTTPS, one TLS) connection


def test_success_keeps_http_json_response_contract(server: _Server) -> None:
    transport = KeepAliveHttpJsonTransport(retry_policy=_policy()[0])
    response = transport.get_json(f"{server.url}/jobs", params={"was": "Fahrer", "wo": None, "page": 1})
    transport.close()
    assert isinstance(response, HttpJsonResponse)
    assert response.url == f"{server.url}/jobs?was=Fahrer&page=1"
    assert response.status_code == 200
    assert response.payload == {"path": "/jobs?was=Fahrer&page=1"}


def test_broken_connection_is_replaced_on_retry() -> None:
    srv = _Server(drop_first_request=True)
    policy, slept = _policy()
    transport = KeepAliveHttpJsonTransport(retry_policy=policy)
    try:
        response = transport.get_json(f"{srv.url}/jobs")
    finally:
        transport.close()
        srv.close()
    assert response.status_code == 200
    assert srv.requests == 2 and len(slept) == 1  # the retry reconnected and succeeded


def test_transport_error_is_retried_then_succeeds() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("_ssl.c:983: The handshake operation timed out", request=request)
        return httpx.Response(200, json={"ok": True})

    policy, slept = _policy()
    transport = KeepAliveHttpJsonTransport(retry_policy=policy, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert transport.get_json("https://example.test/jobs").payload == {"ok": True}
    assert len(calls) == 2 and len(slept) == 1


def test_ba_gives_up_after_two_attempts_with_the_usual_timeout_message(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectTimeout("handshake timed out", request=request)

    transport = KeepAliveHttpJsonTransport(
        retry_policy=RetryPolicy(max_retries=shared_ba_transport()._retry_policy.max_retries, sleep=lambda _s: None),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setenv("SOURCE_BA_ENABLED", "true")
    adapter = BAAdapter(http_transport=transport)
    with pytest.raises(AdapterRequestError) as raised:
        adapter.search(SourceSearchInput("Fahrer", location="Berlin"))
    assert len(calls) == 2
    assert raised.value.message == "Истекло время ожидания ответа BA API."


def test_non_retryable_status_is_not_retried() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(403, text="forbidden")

    transport = KeepAliveHttpJsonTransport(retry_policy=_policy()[0], client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(HttpTransportError) as raised:
        transport.get_json("https://example.test/jobs")
    assert raised.value.status_code == 403 and len(calls) == 1


def test_ba_has_its_own_connect_timeout_and_keeps_the_read_timeout() -> None:
    seen: list[dict[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={})

    shared = shared_ba_transport()
    transport = KeepAliveHttpJsonTransport(
        connect_timeout_seconds=shared._connect_timeout_seconds,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    transport.get_json("https://example.test/jobs", timeout_seconds=10.0)
    assert shared._connect_timeout_seconds == 30.0
    assert shared._keepalive_expiry_seconds == 14.0  # just under BA's `Keep-Alive: timeout=15`
    assert shared._retry_policy.max_retries == 1  # two attempts in total
    assert seen == [{"connect": 30.0, "read": 10.0, "write": 10.0, "pool": 10.0}]


def test_ba_client_outlives_a_registry_and_other_sources_keep_urllib() -> None:
    first, second = SourceAdapterRegistry(), SourceAdapterRegistry()
    ba_transport = first.get("ba").http_transport
    assert ba_transport is shared_ba_transport()
    assert second.get("ba").http_transport is ba_transport
    for source_id in ("careerjet", "adzuna", "jooble", "arbeitnow", "remotive", "remotejobs", "eures", "greenhouse",
                      "lever", "hh"):
        assert isinstance(first.get(source_id).http_transport, UrllibHttpJsonTransport), source_id


def test_explicit_transport_still_reaches_ba() -> None:
    explicit = UrllibHttpJsonTransport()
    assert SourceAdapterRegistry(http_transport=explicit).get("ba").http_transport is explicit
