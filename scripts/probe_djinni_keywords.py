r"""Перепроверка словаря рубрик Djinni живыми запросами.

Djinni на неизвестный ``primary_keyword`` отвечает не ошибкой, а общим нефильтрованным
фидом. Единственный надёжный признак валидной рубрики — её выдача НЕ совпадает с
выдачей заведомо несуществующей рубрики.

Запуск (сеть обязательна, в тесты не входит):

    .\.venv\Scripts\python.exe scripts/probe_djinni_keywords.py
"""

from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request

from app.services.source_adapters.djinni_rss_adapter import DJINNI_PRIMARY_KEYWORDS

FEED_URL = "https://djinni.co/jobs/rss/"
SENTINEL = "__definitely_not_a_djinni_rubric__"
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)


def fetch_titles(primary_keyword: str) -> list[str]:
    url = f"{FEED_URL}?{urllib.parse.urlencode({'primary_keyword': primary_keyword})}"
    request = urllib.request.Request(url, headers={"User-Agent": "SmartJob-SearchAgent/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - фиксированный host
        body = response.read().decode("utf-8", "replace")
    return _TITLE_RE.findall(body)[1:]  # первый <title> — заголовок самого фида


def main() -> int:
    unfiltered = set(fetch_titles(SENTINEL))
    if not unfiltered:
        print("Не удалось получить эталонный нефильтрованный фид — проверка невозможна.")
        return 2

    ignored: list[str] = []
    for keyword in DJINNI_PRIMARY_KEYWORDS:
        titles = fetch_titles(keyword)
        overlap = len(set(titles) & unfiltered)
        looks_ignored = len(titles) >= 99 and overlap > 90
        status = "ИГНОРИРУЕТСЯ" if looks_ignored else "ok"
        print(f"{keyword:18} n={len(titles):3} пересечение={overlap:3} {status}")
        if looks_ignored:
            ignored.append(keyword)
        time.sleep(0.3)

    if ignored:
        print(f"\nDjinni больше не принимает рубрики: {', '.join(ignored)}")
        print("Обновите DJINNI_PRIMARY_KEYWORDS в app/services/source_adapters/djinni_rss_adapter.py.")
        return 1
    print("\nВесь словарь рубрик по-прежнему валиден.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
