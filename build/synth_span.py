"""Synthetic US file for the server's range-scan tests (spec section 4.1, us-span.sqlite).

About 5,000 rows: 800 around each of Minneapolis, New Orleans, Memphis, Atlanta and Anaheim (the cities where one
[min lo, max hi] span covered 99 to 352 times the rows of the box's cells), plus 1,000 spread over the lower 48 so
that the cells of one city box map to id ranges with other rows between them. Rare words ("joes", "tacos", "sushi")
sit at fixed, known spots; `truth.json` next to the file lists them so a test can check that every one is found.

    python build/synth_span.py --out apps/server/src/overture/fixture/us-span.sqlite
"""
import argparse
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixture  # noqa: E402

CITIES = {
    "minneapolis": (44.9778, -93.2650),
    "new_orleans": (29.9511, -90.0715),
    "memphis": (35.1495, -90.0490),
    "atlanta": (33.7490, -84.3880),
    "anaheim": (33.8366, -117.9143),
}
PER_CITY = 800
BACKGROUND = 1000
RARE = ("joes", "tacos", "sushi")
FILLER = ("store", "market", "deli", "diner", "grill", "bakery", "books", "outfitters", "salon", "gallery")


def _row(i: int, name: str, lat: float, lng: float) -> dict:
    return {"gers": f"00000000-0000-4000-8000-{i:012d}", "name": name, "basic_category": "restaurant",
            "tax_primary": "restaurant", "tax_hierarchy": ["food_and_drink", "restaurant"], "confidence": 0.9,
            "country": "US", "lat": round(lat, 6), "lng": round(lng, 6)}


def rows(seed: int = 42):
    rnd = random.Random(seed)
    out, truth, i = [], [], 0
    for city, (clat, clng) in CITIES.items():
        dlat = 14 / 111.32
        dlng = 14 / (111.32 * math.cos(math.radians(clat)))
        for k in range(PER_CITY):
            i += 1
            lat = clat + rnd.uniform(-dlat, dlat)
            lng = clng + rnd.uniform(-dlng, dlng)
            if k < 6 * len(RARE):
                word = RARE[k % len(RARE)]
                name = f"{city.replace('_', ' ').title()} {word.title()} {k}"
                truth.append({"city": city, "q": word, "name": name, "lat": round(lat, 6), "lng": round(lng, 6)})
            else:
                name = f"{rnd.choice(FILLER).title()} {city[:3].title()} {k}"
            out.append(_row(i, name, lat, lng))
    for k in range(BACKGROUND):
        i += 1
        out.append(_row(i, f"{rnd.choice(FILLER).title()} Background {k}", rnd.uniform(25.0, 49.0),
                        rnd.uniform(-124.0, -67.5)))
    return out, truth


def build(out_path: str) -> dict:
    rs, truth = rows()
    res = fixture.build_region(rs, "us", out_path, release="synthetic-us-span")
    with open(os.path.splitext(out_path)[0] + ".truth.json", "w", encoding="utf-8") as f:
        json.dump({"cities": {k: list(v) for k, v in CITIES.items()}, "pad_km": 15, "rare": truth}, f, indent=1)
        f.write("\n")
    return {"rows": res["write"]["rows"], "bytes": res["write"]["bytes"], "rare": len(truth)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(json.dumps(build(a.out)))


if __name__ == "__main__":
    main()
