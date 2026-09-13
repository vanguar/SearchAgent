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

    def request_fingerprint(self, search_input: SourceSearchInput) -> str | None:
        """Отпечаток фактического запроса к источнику, либо None.

        Один поиск делает много попыток с разными ключевыми словами. У источника,
        который сводит разные слова к одному и тому же запросу, все эти попытки
        приносят один и тот же набор записей, и конвейер разбирает его заново.

        Одинаковый отпечаток означает "этот запрос уже обслужен в этом прогоне,
        записи уже в общем котле". None (по умолчанию) означает "свести нельзя,
        спрашивать каждый раз" — так ведут себя источники, у которых своя выдача
        на каждое слово.
        """
        return None


class BaseSourceAdapter(SourceAdapter):
    def ensure_enabled(self) -> None:
        if not self.is_enabled():
            raise AdapterDisabledError(source_id=self.source_id, source_name=self.display_name)
