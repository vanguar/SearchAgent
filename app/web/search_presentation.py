"""Человеческий пересказ технической диагностики прогона поиска.

Слой отображения, и только он. Прогон честно считает всё, что с ним случилось:
сырые записи каждого источника, счётчики после нормализации и дедупликации,
предупреждения адаптеров, текст ошибки вместе с телом HTTP-ответа. Эти данные
нужны для аудита — они остаются в `SearchRunResult`, в экспорте и в логах без
изменений. Но человеку на странице результатов они отвечали не на его вопрос:
он спрашивает «поиск работает?» и «сколько вакансий нашли?», а читал «Сырых: 61
из 61 · Нормализовано: 61» и, когда Jooble отвечал 403, целую страницу HTML из
тела ответа.

Здесь техническая картина превращается в две строки для обычного взгляда и
аккуратный список для раскрываемых подробностей. Ни одно число не считается
заново: всё берётся из уже посчитанного прогоном.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.services.search_models import SearchRunResult, SearchSourceState

SourceOutcome = Literal["ok", "empty", "error", "off"]

# Тело HTTP-ответа начинается сразу за коротким сообщением об ошибке, и именно
# оно выливалось на страницу. Код состояния — единственное, что из ответа
# полезно человеку, поэтому его вынимаем отдельно, а остальное не читаем.
_HTTP_STATUS_PATTERN = re.compile(r"HTTP[\s:]*(\d{3})")

# Признаки того, что фраза пришла не от нас, а из ответа сервера: разметка,
# ссылки, куски JSON. Такую фразу человеку не показываем ни в каком виде.
_FOREIGN_MARKERS = ("<", ">", "://", "{", "}", "&lt;", "&gt;")

_MAX_SENTENCE_LENGTH = 160

_STATUS_LABELS: dict[SourceOutcome, str] = {
    "ok": "Работает",
    "empty": "Нет результатов",
    "error": "Ошибка источника",
    "off": "Отключён",
}

_BADGE_CLASSES: dict[SourceOutcome, str] = {
    "ok": "badge-success",
    "empty": "badge-warning",
    "error": "badge-error",
    "off": "badge-disabled",
}


def _plural(count: int, one: str, few: str, many: str) -> str:
    """Русская форма слова при числе: 1 источник, 2 источника, 5 источников."""
    tail = abs(count) % 100
    if 11 <= tail <= 14:
        return many
    last = tail % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def _http_status(message: str | None) -> str | None:
    if not message:
        return None
    match = _HTTP_STATUS_PATTERN.search(message)
    return match.group(1) if match is not None else None


def _own_first_sentence(message: str | None) -> str | None:
    """Первая фраза сообщения, если она точно наша, а не из ответа сервера.

    Всё от первой угловой скобки отрезается до разбора на фразы: `<!DOCTYPE
    html>` и тело страницы приклеены к сообщению без разделителя, и без этого
    отреза «первая фраза» уже содержала бы разметку.
    """
    if not message:
        return None
    head = message.split("<", 1)[0].strip()
    if not head:
        return None
    sentence = head.split(". ", 1)[0].strip().rstrip(";").strip()
    if not sentence:
        return None
    if len(sentence) > _MAX_SENTENCE_LENGTH:
        return None
    if any(marker in sentence for marker in _FOREIGN_MARKERS):
        return None
    if not sentence.endswith("."):
        sentence = f"{sentence}."
    return sentence


def source_error_sentence(source_name: str, error_message: str | None) -> str:
    """Короткая фраза об ошибке источника — без тела ответа и без трассировок.

    Код состояния HTTP человеку понятен и отличает «сервер отказал» от «сервер
    недоступен», поэтому он остаётся. Всё остальное, что адаптер приложил к
    сообщению, здесь не появляется ни при каких данных.
    """
    status = _http_status(error_message)
    if status is not None:
        return f"{source_name} не ответил на запрос: HTTP {status}."
    own = _own_first_sentence(error_message)
    if own is not None:
        return own
    return f"{source_name} не ответил на запрос."


@dataclass(frozen=True, slots=True)
class SourceRowView:
    """Один источник в раскрытых подробностях."""

    source_name: str
    outcome: SourceOutcome
    status_label_ru: str
    badge_class: str
    detail_ru: str
    technical_ru: str | None = None


@dataclass(frozen=True, slots=True)
class SearchOverview:
    """Всё, что странице результатов нужно знать о прогоне, уже по-русски."""

    sources: tuple[SourceRowView, ...]
    sources_headline_ru: str
    volume_ru: str | None
    error_notes_ru: tuple[str, ...]
    others_kept_working: bool
    found_ru: str
    visible_ru: str
    technical_totals_ru: str
    bucket_breakdown_ru: tuple[str, ...]
    tone: Literal["ok", "warning"]


def _outcome(state: SearchSourceState) -> SourceOutcome:
    if state.status_kind == "disabled":
        return "off"
    if state.status_kind == "error":
        return "error"
    if state.status_kind == "warning" or state.raw_count <= 0:
        return "empty"
    return "ok"


def _detail_ru(state: SearchSourceState, outcome: SourceOutcome) -> str:
    if outcome == "error":
        return source_error_sentence(state.source_name, state.error_message)
    if outcome == "off":
        return "Источник отключён в настройках."
    if outcome == "empty":
        return "По этому запросу вакансий не найдено."
    if state.canonical_count and state.canonical_count != state.raw_count:
        return f"Получено: {state.raw_count} · после обработки: {state.canonical_count}"
    return f"Получено: {state.raw_count}"


def _technical_ru(state: SearchSourceState) -> str | None:
    """Компактная техническая строка — только внутри раскрытых подробностей."""
    parts: list[str] = []
    if state.status_kind != "error":
        total = f" из {state.total_count}" if state.total_count is not None else ""
        parts.append(
            f"raw {state.raw_count}{total} · normalized {state.normalized_count}"
            f" · canonical {state.canonical_count}"
        )
    parts.extend(state.warnings)
    return " · ".join(parts) if parts else None


def _sources_headline_ru(counts: dict[SourceOutcome, int]) -> str:
    if not any(counts.values()):
        return "В этом прогоне источники не опрашивались."
    segments: list[str] = []
    ok = counts["ok"]
    if ok:
        segments.append(
            f"{ok} {_plural(ok, 'источник', 'источника', 'источников')}"
            f" {_plural(ok, 'работает', 'работают', 'работают')}"
        )
    if counts["empty"]:
        segments.append(f"{counts['empty']} без результатов")
    if counts["error"]:
        errors = counts["error"]
        segments.append(f"{errors} {_plural(errors, 'с ошибкой', 'с ошибками', 'с ошибками')}")
    if counts["off"]:
        off = counts["off"]
        segments.append(f"{off} {_plural(off, 'отключён', 'отключены', 'отключены')}")
    return " · ".join(segments)


def _volume_ru(result: SearchRunResult) -> str | None:
    raw = result.total_raw_records
    canonical = result.total_canonical_results
    if not raw and not canonical:
        return None
    records = _plural(raw, "запись", "записи", "записей")
    vacancies = _plural(canonical, "вакансия", "вакансии", "вакансий")
    return f"Получено {raw} {records} · после обработки {canonical} {vacancies}"


def _found_ru(canonical: int) -> str:
    if not canonical:
        return "Уникальных вакансий не найдено"
    unique = _plural(canonical, "уникальная", "уникальные", "уникальных")
    vacancies = _plural(canonical, "вакансия", "вакансии", "вакансий")
    return f"Найдено {canonical} {unique} {vacancies}"


def _visible_ru(visible: int) -> str:
    if not visible:
        return "Ни одна пока не прошла фильтры"
    verb = _plural(visible, "подходит", "подходят", "подходят")
    return f"{visible} {verb} для просмотра"


def search_overview(result: SearchRunResult) -> SearchOverview:
    """Собрать пользовательский взгляд на прогон из его технических счётчиков."""
    counts: dict[SourceOutcome, int] = {"ok": 0, "empty": 0, "error": 0, "off": 0}
    rows: list[SourceRowView] = []
    error_notes: list[str] = []
    for state in result.source_states:
        outcome = _outcome(state)
        counts[outcome] += 1
        detail = _detail_ru(state, outcome)
        if outcome == "error":
            error_notes.append(detail)
        rows.append(
            SourceRowView(
                source_name=state.source_name,
                outcome=outcome,
                status_label_ru=_STATUS_LABELS[outcome],
                badge_class=_BADGE_CLASSES[outcome],
                detail_ru=detail,
                technical_ru=_technical_ru(state),
            )
        )

    visible = len(result.hot_results) + len(result.maybe_results)
    hidden = len(result.hidden_filtered_items)

    return SearchOverview(
        sources=tuple(rows),
        sources_headline_ru=_sources_headline_ru(counts),
        volume_ru=_volume_ru(result),
        error_notes_ru=tuple(error_notes),
        # Отказ одного источника не должен читаться как провал всего поиска:
        # пока остальные приносят вакансии, блок остаётся спокойным, а про
        # упавший источник сказано одной строкой.
        others_kept_working=bool(error_notes) and counts["ok"] > 0,
        found_ru=_found_ru(result.total_canonical_results),
        visible_ru=_visible_ru(visible),
        technical_totals_ru=(
            f"raw {result.total_raw_records}"
            f" · normalized {result.total_normalized_records}"
            f" · canonical {result.total_canonical_results}"
        ),
        bucket_breakdown_ru=(
            f"Горячие: {len(result.hot_results)}",
            f"На проверку: {len(result.maybe_results)}",
            f"Отклонены: {len(result.rejected_results)}",
            f"Показано пользователю: {visible}",
            f"Скрыто жёсткими фильтрами: {hidden}",
        ),
        tone="ok" if (visible or counts["ok"]) else "warning",
    )
