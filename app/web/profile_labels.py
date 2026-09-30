"""Человеческие подписи к внутренним кодам профиля человека.

Слой отображения, и только он. Правовой статус хранится в базе каноническим
кодом — так его записывает разбор интейка, и на этот код смотрят правила поиска
(см. `interpret_legal_status`). Показывать код человеку нельзя: «section_24» не
говорит ничего никому, кроме кода, а на странице профилей он именно так и
появлялся. Здесь лежит перевод кода в подпись и ничего больше: значение в базе
остаётся прежним, миграция не нужна.
"""
from __future__ import annotations

from app.services.search_profile_resolver import interpret_legal_status

# Канонический словарь статусов — тот же, что нормализует разбор интейка
# (`_LEGAL_STATUS_MAP` в profile_post_processor) и который описан в модели
# извлечения: section_24 | eu_citizen | work_visa | other.
LEGAL_STATUS_LABELS: dict[str, str] = {
    "section_24": "§ 24 AufenthG — временная защита",
    "eu_citizen": "Гражданство ЕС",
    "work_visa": "Рабочая виза или ВНЖ с правом работы",
    "other": "Другой статус",
}


def legal_status_label(value: str | None) -> str:
    """Подпись сохранённого правового статуса.

    Значение вне канонического словаря показывается как есть: туда попадает то,
    что человек написал сам, и подменять его догадкой было бы хуже, чем показать
    его собственные слова.
    """
    if value is None:
        return ""
    stored = value.strip()
    if not stored:
        return ""
    exact = LEGAL_STATUS_LABELS.get(stored.casefold())
    if exact is not None:
        return exact
    # §24 записывают десятком способов, и все их уже узнаёт разбор статуса, на
    # который смотрит поиск. Подпись берётся оттуда же: иначе одно и то же
    # значение называлось бы в поиске и на странице по-разному.
    code = interpret_legal_status(stored).code
    if code is not None and code in LEGAL_STATUS_LABELS:
        return LEGAL_STATUS_LABELS[code]
    return stored
