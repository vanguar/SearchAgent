"""Один работодатель не должен забивать выдачу.

Агентство публикует десятки похожих вакансий, и без свёртки они занимали
весь раздел «горячих». Карточки «горячих» группируются по работодателю:
каноническое имя (employer_identity), общий e-mail или телефон в тексте.

* кластер больше employer_cluster_min_size горячих вакансий показывается одной
  сводной карточкой — лучшей по месту в списке, остальные внутри неё;
* меньший кластер показывает не больше max_cards_per_employer_in_top
  карточек, остальные сворачиваются в первую.

Раздел «на проверку» не сворачивается: там человек сам разбирает варианты.

Ничего не скрывается: свёрнутая вакансия остаётся в сводной карточке со
своей ссылкой.
"""
from __future__ import annotations

import dataclasses
import re
from collections import Counter
from collections.abc import Sequence

from app.core.relevance_config import RelevanceConfig, get_relevance_config
from app.services.employer_identity import employer_key
from app.services.search_models import SearchResultItem

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:\+|\b0)\d[\d\s/()-]{6,}\d")
_MIN_PHONE_DIGITS = 8


def item_contacts(item: SearchResultItem) -> tuple[str, ...]:
    """E-mail и телефоны из текста вакансии, в нормализованном виде."""
    contacts: list[str] = []
    for record in item.canonical_group.source_records:
        text = record.body_text or ""
        contacts.extend(email.casefold() for email in _EMAIL_RE.findall(text))
        for phone in _PHONE_RE.findall(text):
            digits = re.sub(r"\D", "", phone)
            if digits.startswith("49"):
                digits = "0" + digits[2:]
            if len(digits) >= _MIN_PHONE_DIGITS:
                contacts.append(digits)
    return tuple(dict.fromkeys(contacts))


def item_employer_key(item: SearchResultItem) -> str:
    return employer_key(item.primary_record.original_company or item.canonical_group.company_name)


def collapse_employer_clusters(
    items: Sequence[SearchResultItem],
    *,
    config: RelevanceConfig | None = None,
) -> tuple[SearchResultItem, ...]:
    """Тот же упорядоченный список, но кластеры работодателей в «горячих» свёрнуты."""
    resolved = config or get_relevance_config()
    hot = [item for item in items if item.bucket == "hot"]
    cluster_of = _cluster_ids(hot)
    cluster_sizes = Counter(cluster_of.values())

    collapsed_into: dict[str, str] = {}
    members: dict[str, list[SearchResultItem]] = {}
    shown: dict[int, list[str]] = {}
    for item in hot:
        cluster = cluster_of[_key(item)]
        limit = 1 if cluster_sizes[cluster] > resolved.employer_cluster_min_size else (
            resolved.max_cards_per_employer_in_top
        )
        already = shown.setdefault(cluster, [])
        if len(already) < limit:
            already.append(_key(item))
            continue
        host = already[0]
        collapsed_into[_key(item)] = host
        members.setdefault(host, []).append(item)

    result: list[SearchResultItem] = []
    for item in items:
        key = _key(item)
        if key in collapsed_into:
            continue
        hosted = members.get(key)
        if hosted:
            cluster_members = tuple(
                dataclasses.replace(member, collapsed_into=key) for member in hosted
            )
            item = dataclasses.replace(
                item,
                cluster_members=cluster_members,
                cluster_size=cluster_sizes[cluster_of[key]],
                cluster_contacts=_shared_contacts((item, *hosted)),
            )
        result.append(item)
    return tuple(result)


def _key(item: SearchResultItem) -> str:
    return item.canonical_group.canonical_key


def _cluster_ids(items: Sequence[SearchResultItem]) -> dict[str, int]:
    """Союз по общему работодателю, e-mail или телефону."""
    parent = list(range(len(items)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    owner: dict[str, int] = {}
    for index, item in enumerate(items):
        markers = [f"employer:{key}"] if (key := item_employer_key(item)) else []
        markers.extend(f"contact:{contact}" for contact in item_contacts(item))
        for marker in markers:
            if marker in owner:
                parent[find(index)] = find(owner[marker])
            else:
                owner[marker] = index
    return {_key(item): find(index) for index, item in enumerate(items)}


def _shared_contacts(items: Sequence[SearchResultItem]) -> tuple[str, ...]:
    counts: Counter[str] = Counter(contact for item in items for contact in item_contacts(item))
    return tuple(contact for contact, _ in counts.most_common(2))
