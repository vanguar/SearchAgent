from __future__ import annotations

import atexit
import threading
from collections.abc import Sequence

from app.core.config import Settings
from app.services.source_adapters.adzuna_adapter import AdzunaAdapter
from app.services.source_adapters.arbeitnow_adapter import ArbeitnowAdapter
from app.services.source_adapters.ba_adapter import BAAdapter
from app.services.source_adapters.base import SourceAdapter
from app.services.source_adapters.careerjet_adapter import CareerjetAdapter
from app.services.source_adapters.djinni_rss_adapter import DjinniRssAdapter
from app.services.source_adapters.dou_rss_adapter import DouRssAdapter
from app.services.source_adapters.errors import AdapterConfigurationError
from app.services.source_adapters.eures_adapter import EURESAdapter
from app.services.source_adapters.greenhouse_adapter import GreenhouseAdapter
from app.services.source_adapters.hh_adapter import HHAdapter
from app.services.source_adapters.http import (
    DEFAULT_GET_CACHE_TTL_SECONDS,
    HttpJsonTransport,
    KeepAliveHttpJsonTransport,
    RetryPolicy,
    UrllibHttpJsonTransport,
)
from app.services.source_adapters.jooble_adapter import JoobleAdapter
from app.services.source_adapters.lever_adapter import LeverAdapter
from app.services.source_adapters.models import SourceAdapterDescriptor
from app.services.source_adapters.remotejobs_adapter import RemoteJobsOrgAdapter
from app.services.source_adapters.remotive_adapter import RemotiveAdapter

_ba_transport: KeepAliveHttpJsonTransport | None = None
_ba_transport_lock = threading.Lock()


def shared_ba_transport() -> KeepAliveHttpJsonTransport:
    """One keep-alive BA client per process.

    A registry is built per search request, so a per-registry client would pay BA's slow
    TLS handshake on every search. Two attempts: a 30 s handshake makes a third one rarely
    useful and only stretches the worst case. BA announces `Keep-Alive: timeout=15`; idle
    connections are dropped just before that, never reused after the server closed them.
    """
    global _ba_transport
    with _ba_transport_lock:
        if _ba_transport is None:
            _ba_transport = KeepAliveHttpJsonTransport(
                connect_timeout_seconds=30.0, keepalive_expiry_seconds=14.0, retry_policy=RetryPolicy(max_retries=1)
            )
            atexit.register(_ba_transport.close)
        return _ba_transport


class SourceAdapterRegistry:
    """Ordered registry that keeps adapters isolated and explicit."""

    def __init__(
        self,
        adapters: Sequence[SourceAdapter] | None = None,
        *,
        settings: Settings | None = None,
        http_transport: HttpJsonTransport | None = None,
    ) -> None:
        if adapters is None:
            resolved_settings = settings or Settings()
            # Shared transport with a short GET cache so repeated identical fetches across
            # keyword attempts in one search run hit the network only once (prevents 429s
            # from full-list sources like Arbeitnow that filter client-side).
            resolved_transport = http_transport or UrllibHttpJsonTransport(
                cache_ttl_seconds=DEFAULT_GET_CACHE_TTL_SECONDS
            )
            adapters = (
                BAAdapter(settings=resolved_settings, http_transport=http_transport or shared_ba_transport()),
                CareerjetAdapter(settings=resolved_settings, http_transport=resolved_transport),
                EURESAdapter(settings=resolved_settings, http_transport=resolved_transport),
                RemotiveAdapter(settings=resolved_settings, http_transport=resolved_transport),
                AdzunaAdapter(settings=resolved_settings, http_transport=resolved_transport),
                JoobleAdapter(settings=resolved_settings, http_transport=resolved_transport),
                ArbeitnowAdapter(settings=resolved_settings, http_transport=resolved_transport),
                RemoteJobsOrgAdapter(settings=resolved_settings, http_transport=resolved_transport),
                GreenhouseAdapter(settings=resolved_settings, http_transport=resolved_transport),
                LeverAdapter(settings=resolved_settings, http_transport=resolved_transport),
                HHAdapter(settings=resolved_settings, http_transport=resolved_transport),
                DouRssAdapter(settings=resolved_settings),
                DjinniRssAdapter(settings=resolved_settings),
            )

        adapter_map: dict[str, SourceAdapter] = {}
        for adapter in adapters:
            if adapter.source_id in adapter_map:
                raise ValueError(f"Duplicate source adapter id: {adapter.source_id}")
            adapter_map[adapter.source_id] = adapter

        self._adapters = adapter_map

    def list_sources(self) -> tuple[SourceAdapterDescriptor, ...]:
        return tuple(adapter.describe() for adapter in self._adapters.values())

    def get(self, source_id: str) -> SourceAdapter:
        adapter = self._adapters.get(source_id)
        if adapter is None:
            raise AdapterConfigurationError(
                source_id=source_id,
                source_name=source_id.upper(),
                message=f"Источник '{source_id}' не зарегистрирован в adapter registry.",
            )
        return adapter
