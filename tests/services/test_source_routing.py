"""Which sources a normal (non-Russian) search runs in each mode."""
from __future__ import annotations

from app.services.search_service import SearchService
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.models import SourceAdapterDescriptor
from app.services.source_adapters.registry import SourceAdapterRegistry
from app.web.routes.jobs import _build_source_status_rows


class _Stub(BaseSourceAdapter):
    def __init__(self, source_id: str, *, global_remote: bool) -> None:
        self.source_id = self.display_name = source_id
        self.global_remote = global_remote

    def is_enabled(self) -> bool:
        return True

    def describe(self) -> SourceAdapterDescriptor:
        return SourceAdapterDescriptor(self.source_id, self.display_name, True, "Готов", global_remote=self.global_remote)

    def search(self, search_input):  # noqa: ANN001, ANN201
        raise AssertionError("routing must not fetch")


def _service() -> SearchService:
    adapters = (
        _Stub("ba", global_remote=False),
        _Stub("adzuna", global_remote=False),
        _Stub("jooble", global_remote=False),
        _Stub("arbeitnow", global_remote=False),  # the real adapter is not marked global_remote
        _Stub("remotive", global_remote=True),
    )
    return SearchService(registry=SourceAdapterRegistry(adapters))


def test_arbeitnow_runs_only_for_remote_searches() -> None:
    service = _service()
    assert service.resolve_source_ids_for_search(search_mode="germany_local") == ("ba", "adzuna", "jooble")
    assert service.resolve_source_ids_for_search(search_mode="remote_worldwide") == ("jooble", "arbeitnow", "remotive")


def test_status_panel_marks_arbeitnow_as_remote_only() -> None:
    rows = {row["descriptor"].source_id: row["mode_scope"] for row in _build_source_status_rows(_service().list_sources())}
    assert rows["arbeitnow"] == "remote_worldwide"
    assert rows["ba"] == "germany_local"
