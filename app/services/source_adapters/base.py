from __future__ import annotations

from abc import ABC, abstractmethod

from app.services.source_adapters.errors import AdapterDisabledError
from app.services.source_adapters.models import (
    AdapterSearchResponse,
    SourceAdapterDescriptor,
    SourceSearchInput,
)


class SourceAdapter(ABC):
    source_id: str
    display_name: str

    @abstractmethod
    def is_enabled(self) -> bool:
        """Whether the adapter is enabled by local config."""

    @abstractmethod
    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        """Fetch source-native records for a minimal search input."""

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=enabled,
            status_label="Готов" if enabled else "Отключен",
            status_kind="success" if enabled else "disabled",
        )


class BaseSourceAdapter(SourceAdapter):
    def ensure_enabled(self) -> None:
        if not self.is_enabled():
            raise AdapterDisabledError(source_id=self.source_id, source_name=self.display_name)
