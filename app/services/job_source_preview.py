from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.logging import logger
from app.services.normalization_models import PreviewPipelineResult
from app.services.source_adapters.errors import SourceAdapterError
from app.services.source_adapters.models import (
    AdapterSearchResponse,
    SourceAdapterDescriptor,
    SourceSearchInput,
)
from app.services.source_adapters.registry import SourceAdapterRegistry
from app.services.vacancy_processing import VacancyProcessingService


@dataclass(frozen=True, slots=True)
class SourcePreviewState:
    source: SourceAdapterDescriptor
    response: AdapterSearchResponse | None = None
    error: SourceAdapterError | None = None
    processed_preview: PreviewPipelineResult | None = None


class JobSourcePreviewService:
    """Minimal preview service that fetches raw source data and PHASE 6 processing output."""

    def __init__(
        self,
        *,
        registry: SourceAdapterRegistry | None = None,
        vacancy_processing_service: VacancyProcessingService | None = None,
    ) -> None:
        self.registry = registry or SourceAdapterRegistry()
        self.vacancy_processing_service = vacancy_processing_service or VacancyProcessingService()

    def list_sources(self) -> tuple[SourceAdapterDescriptor, ...]:
        return self.registry.list_sources()

    def fetch_previews(
        self,
        *,
        search_input: SourceSearchInput,
        source_ids: Sequence[str],
    ) -> tuple[SourcePreviewState, ...]:
        preview_states: list[SourcePreviewState] = []

        for source_id in source_ids:
            descriptor: SourceAdapterDescriptor | None = None
            try:
                adapter = self.registry.get(source_id)
                descriptor = adapter.describe()
                response = adapter.search(search_input)
                processed_preview = self.vacancy_processing_service.process_adapter_response(response)
            except SourceAdapterError as exc:
                preview_states.append(
                    SourcePreviewState(
                        source=_build_error_source_descriptor(
                            descriptor=descriptor,
                            source_id=exc.source_id,
                            source_name=exc.source_name,
                        ),
                        error=exc,
                    )
                )
                continue
            except Exception:
                source_name = descriptor.display_name if descriptor is not None else source_id.upper()
                logger.exception("source_preview_unexpected_error source_id=%s", source_id)
                preview_states.append(
                    SourcePreviewState(
                        source=_build_error_source_descriptor(
                            descriptor=descriptor,
                            source_id=source_id,
                            source_name=source_name,
                        ),
                        error=SourceAdapterError(
                            source_id=descriptor.source_id if descriptor is not None else source_id,
                            source_name=source_name,
                            code="preview_failed",
                            message=f"Не удалось получить превью из источника {source_name}.",
                            retryable=False,
                        ),
                    )
                )
                continue

            preview_states.append(
                SourcePreviewState(
                    source=descriptor,
                    response=response,
                    processed_preview=processed_preview,
                )
            )

        return tuple(preview_states)


def _build_error_source_descriptor(
    *,
    descriptor: SourceAdapterDescriptor | None,
    source_id: str,
    source_name: str,
) -> SourceAdapterDescriptor:
    if descriptor is not None:
        return SourceAdapterDescriptor(
            source_id=descriptor.source_id,
            display_name=descriptor.display_name,
            enabled=descriptor.enabled,
            status_label="Ошибка",
        )

    return SourceAdapterDescriptor(
        source_id=source_id,
        display_name=source_name,
        enabled=False,
        status_label="Ошибка",
    )
