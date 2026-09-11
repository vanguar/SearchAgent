"""
Тесты OpenAILLMClient: сконфигурированный путь, деградированный режим, ошибка провайдера.
Все тесты используют моки — реальных вызовов к OpenAI нет.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.services.llm_client import (
    LLMStatus,
    OpenAILLMClient,
    _set_runtime_status,
    build_llm_client,
    get_runtime_status,
)

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------

def _make_client(*, model: str = "gpt-4o-mini") -> OpenAILLMClient:
    """Создать клиента с замоканным OpenAI SDK."""
    with patch("app.services.llm_client._openai_sdk") as mock_sdk:
        mock_sdk.OpenAI.return_value = MagicMock()
        client = OpenAILLMClient(api_key="sk-test", model=model, timeout_seconds=5.0)
    client._client = MagicMock()
    return client


def _make_openai_response(text: str) -> MagicMock:
    """Создать объект ответа, совместимый с openai.ChatCompletion."""
    choice = MagicMock()
    choice.message.content = text
    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# build_llm_client
# ---------------------------------------------------------------------------

def test_build_llm_client_returns_none_when_api_key_missing() -> None:
    with patch.dict("os.environ", {}, clear=True):
        settings = Settings()
        client = build_llm_client(settings)
    assert client is None


def test_build_llm_client_returns_openai_client_when_api_key_set() -> None:
    with patch("app.services.llm_client._openai_sdk") as mock_sdk:
        mock_sdk.OpenAI.return_value = MagicMock()
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-key"}):
            settings = Settings()
            client = build_llm_client(settings)
    assert client is not None
    assert isinstance(client, OpenAILLMClient)


def test_build_llm_client_uses_configured_model() -> None:
    with patch("app.services.llm_client._openai_sdk") as mock_sdk:
        mock_sdk.OpenAI.return_value = MagicMock()
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-x", "OPENAI_MODEL": "gpt-4o"}):
            settings = Settings()
            client = build_llm_client(settings)
    assert client is not None
    assert client._model == "gpt-4o"


def test_build_llm_client_sets_runtime_status_missing_when_no_key() -> None:
    _set_runtime_status(LLMStatus.CONFIGURED)  # задаём "грязное" состояние
    with patch.dict("os.environ", {}, clear=True):
        settings = Settings()
        build_llm_client(settings)
    assert get_runtime_status() == LLMStatus.MISSING_CONFIG


# ---------------------------------------------------------------------------
# get_runtime_status
# ---------------------------------------------------------------------------

def test_get_runtime_status_missing_config_when_no_key() -> None:
    with patch.dict("os.environ", {}, clear=True):
        settings = Settings()
        build_llm_client(settings)
    assert get_runtime_status() == LLMStatus.MISSING_CONFIG


def test_get_runtime_status_configured_after_successful_call() -> None:
    client = _make_client()
    client._client.chat.completions.create.return_value = _make_openai_response("Сотрудник склада")

    client.translate_title("Lagermitarbeiter")

    assert get_runtime_status() == LLMStatus.CONFIGURED


def test_get_runtime_status_provider_error_after_failed_call() -> None:
    client = _make_client()
    client._client.chat.completions.create.side_effect = Exception("connection refused")

    client.translate_title("Lagermitarbeiter")

    assert get_runtime_status() == LLMStatus.PROVIDER_ERROR


def test_extract_profile_fields_v2_sets_extraction_succeeded_status() -> None:
    client = _make_client()
    payload = {"desired_roles": ["Python Developer"]}
    client._client.chat.completions.create.return_value = _make_openai_response(json.dumps(payload))

    result = client.extract_profile_fields_v2("Ищу Python Developer", "{text}")

    assert result == payload
    assert get_runtime_status() == LLMStatus.EXTRACTION_SUCCEEDED


def test_settings_prefers_openai_timeout_seconds() -> None:
    with patch.dict(
        "os.environ",
        {
            "OPENAI_API_KEY": "sk-test-key",
            "OPENAI_TIMEOUT_SECONDS": "45",
            "LLM_TIMEOUT_SECONDS": "15",
        },
        clear=True,
    ):
        settings = Settings()

    assert settings.llm_timeout_seconds == 45.0


# ---------------------------------------------------------------------------
# extract_profile_fields
# ---------------------------------------------------------------------------

def test_extract_profile_fields_parses_valid_json() -> None:
    client = _make_client()
    payload = {
        "current_country": "Germany",
        "legal_status": "section_24",
        "work_authorized": True,
        "german_level": "none",
        "desired_roles": ["Склад", "Упаковка"],
    }
    client._client.chat.completions.create.return_value = _make_openai_response(json.dumps(payload))

    result = client.extract_profile_fields("Я в Германии по 24 параграфу, ищу склад")

    assert result["current_country"] == "Germany"
    assert result["legal_status"] == "section_24"
    assert result["work_authorized"] is True
    assert "Склад" in result["desired_roles"]


def test_extract_profile_fields_returns_empty_dict_on_provider_error() -> None:
    client = _make_client()
    client._client.chat.completions.create.side_effect = Exception("timeout")

    result = client.extract_profile_fields("любой текст")

    assert result == {}
    assert get_runtime_status() == LLMStatus.PROVIDER_ERROR


def test_extract_profile_fields_returns_empty_dict_on_invalid_json() -> None:
    client = _make_client()
    client._client.chat.completions.create.return_value = _make_openai_response("не JSON")

    result = client.extract_profile_fields("любой текст")

    assert result == {}


def test_extract_profile_fields_strips_markdown_fences() -> None:
    client = _make_client()
    payload = {"german_level": "basic"}
    wrapped = f"```json\n{json.dumps(payload)}\n```"
    client._client.chat.completions.create.return_value = _make_openai_response(wrapped)

    result = client.extract_profile_fields("немецкий слабый")

    assert result["german_level"] == "basic"


def test_extract_profile_fields_v2_uses_json_mode_and_larger_budget() -> None:
    client = _make_client()
    payload = {"desired_roles": ["Python Developer"]}
    client._client.chat.completions.create.return_value = _make_openai_response(json.dumps(payload))

    result = client.extract_profile_fields_v2("Ищу Python Developer", "{text}")

    assert result == payload
    kwargs = client._client.chat.completions.create.call_args.kwargs
    assert kwargs["max_tokens"] == 3000
    assert kwargs["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------------------
# translate_title
# ---------------------------------------------------------------------------

def test_translate_title_returns_translation() -> None:
    client = _make_client()
    client._client.chat.completions.create.return_value = _make_openai_response("Сотрудник склада")

    result = client.translate_title("Lagermitarbeiter")

    assert result == "Сотрудник склада"


def test_translate_title_returns_none_on_provider_error() -> None:
    client = _make_client()
    client._client.chat.completions.create.side_effect = Exception("error")

    result = client.translate_title("Lagermitarbeiter")

    assert result is None


def test_translate_title_returns_none_for_empty_input() -> None:
    client = _make_client()

    result = client.translate_title("")

    assert result is None
    client._client.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

def test_summarize_returns_short_russian_text() -> None:
    client = _make_client()
    client._client.chat.completions.create.return_value = _make_openai_response(
        "Работа на складе в Берлине, без знания немецкого, сменный график."
    )

    result = client.summarize(
        "Lagermitarbeiter (m/w/d) in Berlin gesucht. Keine Deutschkenntnisse erforderlich. "
        "Wir bieten Schichtarbeit im Zwei-Schicht-System, puenktliche Bezahlung nach Tarif "
        "und eine bezahlte Einarbeitung. Du kannst sofort in Vollzeit starten."
    )

    assert result is not None
    assert len(result) > 5


def test_summarize_returns_none_for_short_text() -> None:
    client = _make_client()

    result = client.summarize("short")

    assert result is None
    client._client.chat.completions.create.assert_not_called()


def test_summarize_returns_none_on_provider_error() -> None:
    client = _make_client()
    client._client.chat.completions.create.side_effect = Exception("error")

    result = client.summarize("Lagermitarbeiter gesucht. Keine Deutschkenntnisse erforderlich.")

    assert result is None


# ---------------------------------------------------------------------------
# explain_match
# ---------------------------------------------------------------------------

def test_explain_match_returns_enriched_explanation() -> None:
    client = _make_client()
    client._client.chat.completions.create.return_value = _make_openai_response(
        "Подходит: склад без немецкого, смены доступны, можно выйти сразу."
    )

    result = client.explain_match(
        deterministic_explanation="Подходит: складская роль, смены допустимы.",
        body_text="Lagermitarbeiter ohne Deutschkenntnisse. 3-Schicht. Ab sofort.",
    )

    assert result is not None
    assert len(result) > 10


def test_explain_match_returns_none_for_short_body_text() -> None:
    client = _make_client()

    result = client.explain_match(
        deterministic_explanation="Подходит.",
        body_text="short",
    )

    assert result is None
    client._client.chat.completions.create.assert_not_called()


def test_explain_match_returns_none_on_provider_error() -> None:
    client = _make_client()
    client._client.chat.completions.create.side_effect = Exception("error")

    result = client.explain_match(
        deterministic_explanation="Подходит: складская роль.",
        body_text="Lagermitarbeiter ohne Deutschkenntnisse. 3-Schicht. Ab sofort.",
    )

    assert result is None


def test_summarize_skips_text_too_short_to_summarize() -> None:
    """На огрызке текста модель начинает достраивать вакансию по общим знаниям.

    Реальный случай: Adzuna обрезает описание, в тексте не было ни слова про немецкий,
    а в резюме пользователю появилось «Требуется знание немецкого языка».
    """
    client = _make_client()

    assert client.summarize("Zusteller gesucht.") is None
    client._client.chat.completions.create.assert_not_called()
