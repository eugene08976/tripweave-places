"""The three regions we publish, as Overture bounding boxes plus an optional country filter.

Boxes are (west, south, east, north) in degrees. The filter runs on bbox.xmin / bbox.ymin (a place is a point, so
xmin = lon and ymin = lat), which lets DuckDB skip whole row groups using the parquet statistics.
"""

REGIONS = {
    # The island of Bali with Nusa Penida, Lembongan and Ceningan. The box also takes in a thin strip of East Java's
    # Blambangan national park; every row in it is Indonesian, so no country filter is needed.
    "bali": {
        "boxes": [(114.42, -8.87, 115.72, -8.04)],
        "country": "ID",
        "country_filter": None,
        "floor": 25_000,
    },
    # Taiwan with Kinmen, Matsu and Penghu. The country filter drops the Fujian coast inside the box.
    "taiwan": {
        "boxes": [(118.1, 21.8, 122.2, 26.5)],
        "country": "TW",
        "country_filter": "addresses[1].country = 'TW'",
        "floor": 300_000,
    },
    # The 50 states plus DC. No Puerto Rico or other territories.
    "us": {
        "boxes": [
            (-125.0, 24.3, -66.8, 49.5),    # lower 48
            (-180.0, 51.0, -129.9, 71.6),   # Alaska
            (172.0, 51.0, 180.0, 53.1),     # Aleutians west of 180 degrees
            (-160.7, 18.8, -154.7, 22.4),   # Hawaii
        ],
        "country": "US",
        "country_filter": "(addresses[1].country = 'US' OR addresses[1].country IS NULL)",
        "floor": 4_000_000,
    },
}

ORDER = ("bali", "taiwan", "us")


def box_predicate(region: str, x: str = "bbox.xmin", y: str = "bbox.ymin") -> str:
    parts = [f"({x} BETWEEN {w} AND {e} AND {y} BETWEEN {s} AND {n})" for w, s, e, n in REGIONS[region]["boxes"]]
    return "(" + " OR ".join(parts) + ")"


def bboxes(region: str) -> list:
    return [list(b) for b in REGIONS[region]["boxes"]]
