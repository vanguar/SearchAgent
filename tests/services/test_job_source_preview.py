from app.services.job_source_preview import JobSourcePreviewService
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.errors import AdapterRequestError
from app.services.source_adapters.models import (
    AdapterSearchResponse,
    SourceRecordPreview,
    SourceSearchInput,
)
from app.services.source_adapters.registry import SourceAdapterRegistry


class SuccessAdapter(BaseSourceAdapter):
    source_id = "ok"
    display_name = "Success Source"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=(
                SourceRecordPreview(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    external_id="ok-1",
                    source_reference="ok-1",
                    title="Lagermitarbeiter/in",
                    company="Test Lager GmbH",
                    location="Berlin",
                    posted_at="2026-04-16",
                    detail_url=None,
                    raw_payload={"external_id": "ok-1"},
                ),
            ),
            total_count=1,
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload={"source": self.source_id},
        )


class FailingAdapter(BaseSourceAdapter):
    source_id = "broken"
    display_name = "Broken Source"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        raise AdapterRequestError(
            source_id=self.source_id,
            source_name=self.display_name,
            message="Временный сбой у источника.",
        )


class ExplodingAdapter(BaseSourceAdapter):
    source_id = "boom"
    display_name = "Boom Source"

    def is_enabled(self) -> bool:
        return True

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        raise RuntimeError("unexpected failure")


def test_job_source_preview_service_keeps_other_results_when_one_adapter_fails() -> None:
    registry = SourceAdapterRegistry(adapters=(SuccessAdapter(), FailingAdapter()))
    service = JobSourcePreviewService(registry=registry)

    preview_states = service.fetch_previews(
        search_input=SourceSearchInput(query="lager", page=1, page_size=5),
        source_ids=("ok", "broken"),
    )

    assert len(preview_states) == 2
    assert preview_states[0].response is not None
    assert preview_states[0].error is None
    assert preview_states[0].response.records[0].external_id == "ok-1"
    assert preview_states[0].processed_preview is not None
    assert len(preview_states[0].processed_preview.normalized_records) == 1
    assert len(preview_states[0].processed_preview.canonical_groups) == 1
    assert preview_states[0].processed_preview.cache_decisions[0].source_record_action == "create"

    assert preview_states[1].response is None
    assert preview_states[1].error is not None
    assert preview_states[1].error.code == "request_failed"
    assert preview_states[1].error.message == "Временный сбой у источника."
    assert preview_states[1].source.status_label == "Ошибка"


def test_job_source_preview_service_wraps_unexpected_exception_into_typed_error() -> None:
    registry = SourceAdapterRegistry(adapters=(ExplodingAdapter(),))
    service = JobSourcePreviewService(registry=registry)

    preview_states = service.fetch_previews(
        search_input=SourceSearchInput(query="lager", page=1, page_size=5),
        source_ids=("boom",),
    )

    assert len(preview_states) == 1
    assert preview_states[0].response is None
    assert preview_states[0].error is not None
    assert preview_states[0].error.code == "preview_failed"
    assert preview_states[0].error.message == "Не удалось получить превью из источника Boom Source."
    assert preview_states[0].source.display_name == "Boom Source"
    assert preview_states[0].source.status_label == "Ошибка"
