"""Сборка офлайн-индекса координат немецких городов из данных GeoNames.

Разовый шаг подготовки данных (бесплатный, дальше всё работает офлайн) —
по образцу scripts/build-index.mjs из App_kurier, но на другом масштабе:
там дом-адресный индекс одного района из Overpass, здесь нужен весь список
населённых пунктов Германии, поэтому источник — почтовый экспорт GeoNames.

Порядок:

1) Скачать https://download.geonames.org/export/zip/DE.zip и распаковать DE.txt.
2) Запустить:  python scripts/build_geo_index.py DE.txt app/data/germany_postal_geo.tsv.gz
3) Закоммитить получившийся .tsv.gz. Пересобирать нужно только когда GeoNames
   выпускает обновление — в рантайме сеть не используется вообще.

Данные GeoNames распространяются по CC BY 4.0, поэтому файл можно держать
в репозитории; ссылка на источник пишется в шапку самого файла.

Формат результата — по строке на запись, tab-separated:

    p<TAB><почтовый индекс><TAB><широта><TAB><долгота>
    c<TAB><нормализованное имя><TAB><широта><TAB><долгота><TAB><вес места>

Одному городу соответствует несколько индексов, одному индексу — несколько
названий, поэтому обе таблицы сводятся к центроиду своих точек.
"""
from __future__ import annotations

import gzip
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

# В почтовом экспорте GeoNames рядом с настоящими населёнными пунктами лежат
# «Großkunden-PLZ» — индексы, выданные отдельным организациям. Их названия это
# названия юрлиц, а не мест, и в таблицу имён они попадать не должны. Сами
# индексы при этом валидны, поэтому из таблицы индексов они не выбрасываются.
_CORPORATE_RE = re.compile(
    r"\b(gmbh|mbh|ag|kg|se|ohg|kgaa|e\.?\s?v|co\.?\s?kg|holding|versicherung|"
    r"bank|verlag|stiftung|postfach|grosskunden)\b",
    re.IGNORECASE,
)

_SOURCE_URL = "https://download.geonames.org/export/zip/"


def ascii_fold(text: str) -> str:
    """Ключ поиска: без умляутов, без пунктуации, в нижнем регистре.

    Должен совпадать с _ascii_fold() из app/services/geo_distance.py, иначе
    собранные ключи не найдутся во время поиска.
    """
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    folded = folded.replace("ß", "ss")
    folded = unicodedata.normalize("NFKD", folded)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    folded = re.sub(r"[^0-9a-zA-Z]+", " ", folded).strip().lower()
    return " ".join(folded.split())


# Места считаются одним и тем же, когда их точки ближе этого порога: почтовые
# индексы одного города дают десятки координат, и все они — этот город.
_SAME_PLACE_DEGREES = 0.15


def _distinct_points(points: list[tuple[float, float]]) -> list[tuple[float, float, int]]:
    """Разложить координаты одного названия на отдельные реальные места.

    Третьим числом идёт вес — сколько почтовых индексов пришлось на это место.
    У города их десятки, у деревни один, и это единственный различитель тёзок,
    доступный офлайн: "Schwerin" — и столица земли, и хутор под Гентином.
    """
    clusters: list[list[tuple[float, float]]] = []
    for point in sorted(points):
        for cluster in clusters:
            anchor = cluster[0]
            if (abs(anchor[0] - point[0]) <= _SAME_PLACE_DEGREES
                    and abs(anchor[1] - point[1]) <= _SAME_PLACE_DEGREES):
                cluster.append(point)
                break
        else:
            clusters.append([point])
    return [(*_centroid(cluster), len(cluster)) for cluster in clusters]


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    return (
        round(sum(point[0] for point in points) / len(points), 4),
        round(sum(point[1] for point in points) / len(points), 4),
    )


def build(source_path: Path, output_path: Path) -> tuple[int, int]:
    postal_points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    place_points: dict[str, list[tuple[float, float]]] = defaultdict(list)

    with source_path.open(encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 11:
                continue
            postal_code, place_name, latitude, longitude = parts[1].strip(), parts[2].strip(), parts[9], parts[10]
            if not (postal_code.isdigit() and len(postal_code) == 5):
                continue
            try:
                point = (float(latitude), float(longitude))
            except ValueError:
                continue

            postal_points[postal_code].append(point)
            if _CORPORATE_RE.search(place_name):
                continue
            key = ascii_fold(place_name)
            if key:
                place_points[key].append(point)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output_path, "wt", encoding="utf-8", newline="\n", compresslevel=9) as handle:
        handle.write(f"# GeoNames postal data for DE (CC BY 4.0) — {_SOURCE_URL}\n")
        handle.write("# kind\tkey\tlat\tlon\n")
        for postal_code in sorted(postal_points):
            latitude, longitude = _centroid(postal_points[postal_code])
            handle.write(f"p\t{postal_code}\t{latitude}\t{longitude}\n")
        # Одно название — несколько строк, когда мест с таким именем несколько.
        # Усреднять их нельзя: два Роггентина лежат в 80 км друг от друга, и
        # центроид указывает в поле между ними. Нужное место выбирает
        # geo_distance по уточнению вида "Roggentin bei Rostock".
        for place in sorted(place_points):
            for latitude, longitude, weight in _distinct_points(place_points[place]):
                handle.write(f"c\t{place}\t{latitude}\t{longitude}\t{weight}\n")

    return len(postal_points), len(place_points)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        print("Usage: python scripts/build_geo_index.py <DE.txt> [out.tsv.gz]", file=sys.stderr)
        return 1

    source_path = Path(argv[1])
    output_path = Path(argv[2]) if len(argv) > 2 else Path("app/data/germany_postal_geo.tsv.gz")
    postal_count, place_count = build(source_path, output_path)
    print(f"{output_path}: {postal_count} индексов, {place_count} названий, {output_path.stat().st_size} байт")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
