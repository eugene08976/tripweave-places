"""Step 5: manifest.json, ATTRIBUTION.md and the licence files for a release (spec section 1.5)."""
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import textnorm  # noqa: E402
from write_index import FORMAT_VERSION, NOTICE  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LICENCE_FILES = ("CDLA-Permissive-2.0.txt", "Apache-2.0.txt", "foursquare-NOTICE.txt")

# Credit lines from Overture's attribution page (https://docs.overturemaps.org/attribution/), by dataset name as it
# appears in the `sources` column. A dataset not listed here is still credited by name.
CREDITS = {
    "meta": "Data from Meta. Available under CDLA Permissive 2.0.",
    "Microsoft": "Data from Microsoft. Available under CDLA Permissive 2.0.",
    "BrightQuery": "Data from BrightQuery. Available under CDLA Permissive 2.0.",
    "Foursquare": "Copyright 2024 Foursquare Labs, Inc. All rights reserved. Available under Apache 2.0 "
                  "(see Apache-2.0.txt and foursquare-NOTICE.txt).",
    "AllThePlaces": "Data from AllThePlaces. Available under CC0 1.0.",
    "DAC": "Data from DAC. Available under CDLA Permissive 2.0.",
    "PinMeTo": "Data from PinMeTo. Available under CDLA Permissive 2.0.",
    "RenderSEO": "Data from RenderSEO. Available under CDLA Permissive 2.0.",
    "Krick": "Data from Krick. Available under CDLA Permissive 2.0.",
}
REGION_NAMES = {"bali": "Bali (Indonesia)", "taiwan": "Taiwan", "us": "United States (50 states and DC)"}


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(tag: str, release: str, built_at: str, files: list, smoke: list) -> dict:
    return {
        "format": FORMAT_VERSION,
        "norm": textnorm.NORM_VERSION,
        "tag": tag,
        "overture_release": release,
        "built_at": built_at,
        "files": files,
        "norm_vectors": textnorm.vectors(),
        "smoke": [{k: c[k] for k in ("region", "q", "at", "expect")} for c in smoke],
    }


def attribution(tag: str, release: str, built_at: str, files: list, licences: dict) -> str:
    totals = {}
    for f in files:
        for d, n in f["datasets"].items():
            totals[d] = totals.get(d, 0) + n
    lines = [
        "# Tripweave places: attribution and notices",
        "",
        f"Release `{tag}`, built {built_at} from Overture Maps release `{release}`.",
        "",
        "Places in these files: © Overture Maps Foundation, https://overturemaps.org.",
        "",
        "## What changed",
        "",
        NOTICE + ".",
        "",
        "In detail, Tripweave:",
        "",
        "- kept only places with a name, confidence of at least 0.70 and no closed status, in the boxes listed in the "
        "manifest;",
        "- kept only categories useful to travelers (food, lodging, sights, shopping, sport, nature and a short list of "
        "services) and removed vehicle, hardware, parking and transit categories, and in the United States also "
        "park, museum, historic site, monument, plaza, bridge, national park and beach categories (trails stay);",
        "- removed names that are only digits or punctuation, single letters, addresses, phone numbers and "
        "\"we moved\" notices, and pages that repeat a nearby place's name with a person's name in front;",
        "- removed company, union and supplier rows (business-register names ending in a legal form such as LLC), "
        "and cut names stuffed with keywords to their first part;",
        "- merged rows that describe the same place within 100 m into one row, keeping the other names as alternates;",
        "- removed rows named like another row plus a place word that are pinned away from that place;",
        "- added English brand names from Wikidata (CC0) to some Taiwan rows;",
        "- dropped most columns, joined each street address with its town (and US state) and shortened it to 80 "
        "characters, normalized names for search and re-indexed everything into SQLite files.",
        "",
        "## Sources",
        "",
        "Rows per source dataset (the provider named in each row's `sources`):",
        "",
        "| Dataset | Rows | Terms |",
        "|---|---|---|",
    ]
    for d, n in sorted(totals.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {d} | {n:,} | {CREDITS.get(d, f'Data from {d}.')} |")
    lines += [
        "",
        "Rows per licence: " + ", ".join(f"{k} {v:,}" for k, v in sorted(licences.items(), key=lambda kv: -kv[1])) + ".",
        "",
        "## Licences",
        "",
        "- CDLA-Permissive-2.0: `CDLA-Permissive-2.0.txt`. The text of this agreement accompanies the data.",
        "- Apache-2.0 (Foursquare rows, `src` = 2): `Apache-2.0.txt` and `foursquare-NOTICE.txt`, which also records "
        "Tripweave's changes.",
        "- CC0-1.0 (AllThePlaces rows, `src` = 3): no conditions.",
        "- Wikidata labels: CC0-1.0.",
        "",
        "Overture's places theme contains no OpenStreetMap data.",
        "",
        "## Files",
        "",
        "| File | Region | Rows | Bytes | SHA-256 |",
        "|---|---|---|---|---|",
    ]
    for f in files:
        lines.append(f"| {f['name']} | {REGION_NAMES.get(f['region'], f['region'])} | {f['rows']:,} | {f['bytes']:,} | "
                     f"`{f['sha256']}` |")
    lines.append("")
    return "\n".join(lines)


def write_release_files(out_dir: str, man: dict, attribution_md: str) -> list:
    for name in LICENCE_FILES:
        shutil.copyfile(os.path.join(ROOT, "licenses", name), os.path.join(out_dir, name))
    with open(os.path.join(out_dir, "ATTRIBUTION.md"), "w", encoding="utf-8") as f:
        f.write(attribution_md)
    tmp = os.path.join(out_dir, "manifest.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, os.path.join(out_dir, "manifest.json"))
    return [f["name"] for f in man["files"]] + list(LICENCE_FILES) + ["ATTRIBUTION.md", "manifest.json"]
