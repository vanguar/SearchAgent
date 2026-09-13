from __future__ import annotations

import re
from typing import Any

from app.core.config import Settings
from app.services.source_adapters.base import BaseSourceAdapter
from app.services.source_adapters.errors import AdapterRequestError, HttpTransportError
from app.services.source_adapters.http import HttpTextTransport, UrllibHttpTextTransport
from app.services.source_adapters.models import (
    AdapterSearchResponse,
    SourceAdapterDescriptor,
    SourceRecordPreview,
    SourceSearchInput,
)
from app.services.source_adapters.rss import parse_rss_items

# Djinni принимает `primary_keyword` ТОЛЬКО из закрытого списка рубрик. Значение вне
# списка не вызывает ошибку — фид молча отдаёт общий нефильтрованный топ-100. Из-за
# этого поиск по "AI Automation Specialist" подмешивал в выдачу "Head of Business
# Development" и "Chief Marketing Officer" и ранжировал их как релевантные.
#
# Список проверен живыми запросами: рубрика считается валидной, если её выдача не
# совпадает с нефильтрованным фидом. Проверка воспроизводится скриптом
# scripts/probe_djinni_keywords.py.
DJINNI_PRIMARY_KEYWORDS: tuple[str, ...] = (
    "Android",
    "Architect",
    "Business Analyst",
    "C++",
    "Data Analyst",
    "Data Engineer",
    "Data Science",
    "Design",
    "DevOps",
    "ERP",
    "Embedded",
    "Flutter",
    "Golang",
    "HR",
    "Java",
    "JavaScript",
    "Lead",
    "Marketing",
    "Node.js",
    "Other",
    "PHP",
    "Product Manager",
    "Project Manager",
    "Python",
    "QA",
    "QA Automation",
    "React Native",
    "Ruby",
    "Rust",
    "SEO",
    "SQL",
    "Salesforce",
    "Scala",
    "Security",
    "Support",
    "Sysadmin",
    "Unity",
    "iOS",
)

_KEYWORD_BY_NORMALIZED: dict[str, str] = {
    keyword.casefold(): keyword for keyword in DJINNI_PRIMARY_KEYWORDS
}

# Термин профиля -> рубрика Djinni. Ключ ищется как подстрока нормализованного запроса,
# поэтому порядок важен: более длинные и специфичные записи стоят выше.
# Значение — кортеж: у AI/LLM-запросов на Djinni нет своей рубрики, и роли такого рода
# лежат сразу в двух ("Data Science" и "Python"), поэтому опрашиваются обе.
_QUERY_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("qa automation", ("QA Automation",)),
    ("automation qa", ("QA Automation",)),
    ("test automation", ("QA Automation",)),
    ("data scien", ("Data Science",)),
    ("data engineer", ("Data Engineer",)),
    ("data analyst", ("Data Analyst",)),
    ("business analyst", ("Business Analyst",)),
    ("project manager", ("Project Manager",)),
    ("product manager", ("Product Manager",)),
    ("react native", ("React Native",)),
    ("node js", ("Node.js",)),
    ("nodejs", ("Node.js",)),
    ("prompt engineer", ("Data Science", "Python")),
    ("machine learning", ("Data Science", "Python")),
    ("deep learning", ("Data Science", "Python")),
    ("generative ai", ("Data Science", "Python")),
    ("ai automation", ("Data Science", "Python")),
    ("ai integration", ("Data Science", "Python")),
    ("ai implementation", ("Data Science", "Python")),
    ("ai workflow", ("Data Science", "Python")),
    ("ai tools", ("Data Science", "Python")),
    ("ai agent", ("Data Science", "Python")),
    ("ai coding", ("Data Science", "Python")),
    ("ai engineer", ("Data Science", "Python")),
    ("llm", ("Data Science", "Python")),
    ("claude code", ("Data Science", "Python")),
    ("openai codex", ("Data Science", "Python")),
    ("нейросет", ("Data Science", "Python")),
    ("ai интегратор", ("Data Science", "Python")),
    ("ki automatisierung", ("Data Science", "Python")),
    ("fastapi", ("Python",)),
    ("django", ("Python",)),
    ("flask", ("Python",)),
    ("python", ("Python",)),
    ("golang", ("Golang",)),
    ("javascript", ("JavaScript",)),
    ("typescript", ("JavaScript",)),
    ("devops", ("DevOps",)),
    ("sysadmin", ("Sysadmin",)),
    ("support", ("Support",)),
    ("security", ("Security",)),
    ("embedded", ("Embedded",)),
    ("salesforce", ("Salesforce",)),
    ("flutter", ("Flutter",)),
    ("android", ("Android",)),
    ("ios", ("iOS",)),
    ("unity", ("Unity",)),
    ("scala", ("Scala",)),
    ("rust", ("Rust",)),
    ("ruby", ("Ruby",)),
    ("php", ("PHP",)),
    ("java", ("Java",)),
    ("design", ("Design",)),
    ("marketing", ("Marketing",)),
    ("seo", ("SEO",)),
)

_NON_WORD_RE = re.compile(r"[^0-9a-zа-яёіїєґ+#]+")


def _normalize_query(value: str) -> str:
    return _NON_WORD_RE.sub(" ", value.casefold()).strip()


def resolve_djinni_primary_keywords(query: str | None) -> tuple[str, ...]:
    """Рубрики Djinni для запроса. Пустой кортеж — запрос рубрике не соответствует.

    Пустой кортеж означает "не спрашивать Djinni", а НЕ "спросить без фильтра":
    запрос без валидной рубрики вернул бы общий фид, не имеющий отношения к поиску.
    """
    normalized = _normalize_query(query or "")
    if not normalized:
        return ()

    exact = _KEYWORD_BY_NORMALIZED.get(normalized)
    if exact is not None:
        return (exact,)

    for fragment, keywords in _QUERY_ALIASES:
        if fragment in normalized:
            return keywords

    # Многословный запрос вида "Senior Python Developer": совпадение по отдельному слову
    # допустимо только по точному названию рубрики, иначе снова получим общий фид.
    for token in normalized.split():
        keyword = _KEYWORD_BY_NORMALIZED.get(token)
        if keyword is not None:
            return (keyword,)
    return ()

class DjinniRssAdapter(BaseSourceAdapter):
    source_id = "djinni_rss"
    display_name = "Djinni Jobs RSS"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        text_transport: HttpTextTransport | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.text_transport = text_transport or UrllibHttpTextTransport()

    def is_enabled(self) -> bool:
        return self.settings.source_djinni_enabled

    def describe(self) -> SourceAdapterDescriptor:
        enabled = self.is_enabled()
        return SourceAdapterDescriptor(
            source_id=self.source_id,
            display_name=self.display_name,
            enabled=enabled,
            status_label="Готов" if enabled else "Отключен",
            status_kind="success" if enabled else "disabled",
            status_detail=(
                "Украинский/remote IT-источник через RSS Djinni. В фиде есть только заголовок, "
                "ссылка и описание: компанию и город Djinni не отдаёт, поэтому на карточках "
                "этих вакансий они остаются пустыми."
                if enabled
                else "SOURCE_DJINNI_ENABLED=false."
            ),
            global_remote=True,
        )

    def search(self, search_input: SourceSearchInput) -> AdapterSearchResponse:
        self.ensure_enabled()

        keywords = resolve_djinni_primary_keywords(search_input.query)
        if search_input.query and not keywords:
            # Рубрики нет — Djinni вернул бы общий фид вместо результатов поиска.
            # Пустой ответ честнее: он не подмешивает в выдачу посторонние вакансии.
            return AdapterSearchResponse(
                source_id=self.source_id,
                source_name=self.display_name,
                records=(),
                total_count=0,
                page=search_input.page,
                page_size=search_input.page_size,
                raw_payload={"feed_url": self.settings.source_djinni_feed_url, "skipped_query": search_input.query},
                warnings=(
                    f"Djinni: запрос {search_input.query!r} не соответствует ни одной рубрике "
                    "Djinni — источник пропущен, чтобы не подмешивать нерелевантный общий фид.",
                ),
            )

        records: list[SourceRecordPreview] = []
        seen_ids: set[str] = set()
        warnings: list[str] = []
        feed_urls: list[str] = []
        total_items = 0

        for keyword in keywords or (None,):
            params: dict[str, Any] = {}
            if keyword is not None:
                params["primary_keyword"] = keyword

            try:
                response = self.text_transport.get_text(
                    self.settings.source_djinni_feed_url,
                    params=params,
                    timeout_seconds=self.settings.source_adapter_timeout_seconds,
                )
            except HttpTransportError as exc:
                status_hint = f" HTTP {exc.status_code}." if exc.status_code is not None else ""
                raise AdapterRequestError(
                    source_id=self.source_id,
                    source_name=self.display_name,
                    message=f"Не удалось получить RSS Djinni.{status_hint} {exc.message}".strip(),
                ) from exc

            feed_urls.append(response.url)
            items = parse_rss_items(response.text, source_id=self.source_id, source_name=self.display_name)
            total_items += len(items)
            for item in items:
                record = _item_to_record(self.source_id, self.display_name, item.raw_payload)
                if record is None or record.external_id in seen_ids:
                    continue
                seen_ids.add(record.external_id)
                records.append(record)

        if len(keywords) > 1:
            warnings.append(
                "Djinni: у AI/LLM-запросов нет своей рубрики, опрошены "
                + " и ".join(repr(keyword) for keyword in keywords)
                + "."
            )

        return AdapterSearchResponse(
            source_id=self.source_id,
            source_name=self.display_name,
            records=tuple(records),
            total_count=len(records),
            page=search_input.page,
            page_size=search_input.page_size,
            raw_payload={
                "feed_url": feed_urls[0] if len(feed_urls) == 1 else tuple(feed_urls),
                "item_count": total_items,
                "primary_keywords": keywords,
            },
            warnings=tuple(warnings),
        )


def _item_to_record(source_id: str, source_name: str, raw_item: dict[str, Any]) -> SourceRecordPreview | None:
    external_id = _to_text(raw_item.get("guid")) or _to_text(raw_item.get("link"))
    if external_id is None:
        return None
    return SourceRecordPreview(
        source_id=source_id,
        source_name=source_name,
        external_id=external_id,
        source_reference=external_id,
        title=_to_text(raw_item.get("title")) or "Без названия",
        company=None,
        location=None,
        posted_at=_to_text(raw_item.get("pub_date")),
        detail_url=_to_text(raw_item.get("link")),
        raw_payload=dict(raw_item),
    )


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
