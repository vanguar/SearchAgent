from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

from app.services.source_adapters.errors import AdapterResponseError


@dataclass(frozen=True, slots=True)
class RssItem:
    title: str
    link: str | None
    guid: str | None
    description: str | None
    pub_date: str | None
    raw_payload: dict[str, Any]


def parse_rss_items(
    xml_text: str,
    *,
    source_id: str,
    source_name: str,
) -> tuple[RssItem, ...]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise AdapterResponseError(
            source_id=source_id,
            source_name=source_name,
            message=f"{source_name} вернул некорректный RSS/XML: {exc}.",
        ) from exc

    item_nodes = root.findall(".//item")
    if not item_nodes and _strip_ns(root.tag) == "feed":
        item_nodes = root.findall(".//{*}entry")

    items: list[RssItem] = []
    for node in item_nodes:
        title = _child_text(node, "title") or "Без названия"
        link = _child_text(node, "link")
        if link is None:
            link_node = node.find("{*}link")
            if link_node is not None:
                link = _to_text(link_node.attrib.get("href"))
        guid = _child_text(node, "guid") or _child_text(node, "id")
        description = (
            _child_text(node, "description")
            or _child_text(node, "summary")
            or _child_text(node, "content")
            or _child_text(node, "encoded")
        )
        pub_date = _format_pub_date(_child_text(node, "pubDate") or _child_text(node, "updated"))
        raw_payload = {
            "title": title,
            "link": link,
            "guid": guid,
            "description": description,
            "pub_date": pub_date,
        }
        items.append(
            RssItem(
                title=title,
                link=link,
                guid=guid,
                description=description,
                pub_date=pub_date,
                raw_payload=raw_payload,
            )
        )

    return tuple(items)


def _child_text(node: ElementTree.Element, local_name: str) -> str | None:
    for child in list(node):
        if _strip_ns(child.tag) == local_name:
            return _to_text("".join(child.itertext()))
    return None


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _format_pub_date(raw: str | None) -> str | None:
    if raw is None:
        return None
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return raw[:10] if len(raw) >= 10 else raw


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
