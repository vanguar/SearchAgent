from __future__ import annotations


class SourceAdapterError(RuntimeError):
    """Base typed error for adapter failures surfaced to the service layer."""

    def __init__(
        self,
        *,
        source_id: str,
        source_name: str,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.source_id = source_id
        self.source_name = source_name
        self.code = code
        self.message = message
        self.retryable = retryable


class AdapterDisabledError(SourceAdapterError):
    def __init__(self, *, source_id: str, source_name: str) -> None:
        super().__init__(
            source_id=source_id,
            source_name=source_name,
            code="disabled",
            message=f"Источник {source_name} сейчас отключен конфигурацией.",
            retryable=False,
        )


class AdapterConfigurationError(SourceAdapterError):
    def __init__(self, *, source_id: str, source_name: str, message: str) -> None:
        super().__init__(
            source_id=source_id,
            source_name=source_name,
            code="configuration_invalid",
            message=message,
            retryable=False,
        )


class AdapterRequestError(SourceAdapterError):
    def __init__(
        self,
        *,
        source_id: str,
        source_name: str,
        message: str,
        retryable: bool = True,
        status_code: int | None = None,
        response_message: str | None = None,
    ) -> None:
        super().__init__(
            source_id=source_id,
            source_name=source_name,
            code="request_failed",
            message=message,
            retryable=retryable,
        )
        self.status_code = status_code
        self.response_message = response_message

    @property
    def is_forbidden(self) -> bool:
        return (self.response_message or self.message).casefold().find("forbidden") >= 0


class AdapterResponseError(SourceAdapterError):
    def __init__(self, *, source_id: str, source_name: str, message: str) -> None:
        super().__init__(
            source_id=source_id,
            source_name=source_name,
            code="response_invalid",
            message=message,
            retryable=False,
        )


class HttpTransportError(RuntimeError):
    """Shared HTTP helper error for network or status failures."""

    def __init__(self, *, url: str, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.message = message
        self.status_code = status_code


class HttpDecodeError(RuntimeError):
    """Shared HTTP helper error for malformed JSON responses."""

    def __init__(self, *, url: str, message: str) -> None:
        super().__init__(message)
        self.url = url
        self.message = message
