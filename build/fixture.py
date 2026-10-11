"""Build small region files from hand-written rows, through the real pipeline (extract filters, clean, write).

    python build/fixture.py rows.json --out apps/server/src/overture/fixture

rows.json: {"bali": [row, ...], "taiwan": [...], "us": [...]}. A row uses the extract's column names (see FORMAT.md,
"Extract columns"); anything left out gets a default: confidence 0.9, open, sources [meta CDLA-Permissive-2.0].
Rows outside the region's boxes, closed, unnamed or under confidence 0.70 are dropped exactly as the S3 query would.
The server's test fixtures ({bali,taiwan,us}-mini.sqlite) are built this way.
"""
import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clean  # noqa: E402
import extract  # noqa: E402
import labels as labels_mod  # noqa: E402
import write_index  # noqa: E402
from regions import REGIONS, bboxes, box_predicate  # noqa: E402
from sets import SET_NAME  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS = {
    "name_en": [], "basic_category": None, "tax_primary": None, "tax_hierarchy": None, "confidence": 0.9,
    "operating_status": None, "addr": None, "locality": None, "addr_region": None, "country": None, "brand": None, "brand_wikidata": None,
    "sources": [{"dataset": "Overture", "license": "CDLA-Permissive-2.0"},
                {"dataset": "meta", "license": "CDLA-Permissive-2.0"}],
    "contacts": 0,
}
COLUMNS = ("gers VARCHAR, name VARCHAR, name_en VARCHAR[], basic_category VARCHAR, tax_primary VARCHAR, "
           "tax_hierarchy VARCHAR[], confidence DOUBLE, operating_status VARCHAR, addr VARCHAR, locality VARCHAR, "
           "addr_region VARCHAR, country VARCHAR, brand VARCHAR, brand_wikidata VARCHAR, "
           "sources STRUCT(dataset VARCHAR, license VARCHAR)[], contacts INTEGER, lng DOUBLE, lat DOUBLE")


def rows_to_parquet(con, rows: list, region: str, path: str) -> int:
    con.execute(f"CREATE OR REPLACE TEMP TABLE raw ({COLUMNS})")
    for r in rows:
        full = {**DEFAULTS, **r}
        if full.get("country") is None and REGIONS[region]["country_filter"]:
            full["country"] = REGIONS[region]["country"]
        con.execute("INSERT INTO raw VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [full["gers"], full["name"], full["name_en"], full["basic_category"], full["tax_primary"],
                     full["tax_hierarchy"], full["confidence"], full["operating_status"], full["addr"],
                     full["locality"], full["addr_region"], full["country"], full["brand"], full["brand_wikidata"], full["sources"],
                     full["contacts"], full["lng"], full["lat"]])
    cf = REGIONS[region]["country_filter"]
    country = f"AND {cf.replace('addresses[1].country', 'country')}" if cf else ""
    con.execute(f"""COPY (SELECT * FROM raw WHERE {box_predicate(region, 'lng', 'lat')} {country}
        AND name IS NOT NULL AND confidence >= {extract.MIN_CONF} AND coalesce(operating_status, 'open') = 'open')
        TO '{path}' (FORMAT parquet)""")
    return con.execute(f"SELECT count(*) FROM '{path}'").fetchone()[0]


def build_region(rows: list, region: str, out_path: str, release: str = "fixture", built_at: str = "1970-01-01T00:00:00Z",
                 labels: dict = None, log=lambda *_: None) -> dict:
    import duckdb
    with tempfile.TemporaryDirectory() as work:
        con = duckdb.connect()
        con.execute("SET threads=1; SET preserve_insertion_order=false;")
        pq = os.path.join(work, f"{region}.parquet")
        kept = rows_to_parquet(con, rows, region, pq)
        st = clean.clean(con, pq, region, work, labels=labels, log=log)
        st["extract_kept"] = kept
        if st["licence_gate"] != "ok":
            raise RuntimeError(f"{region}: licence gate failed: {st['licence_gate']}")
        meta = {"region": region, "country": REGIONS[region]["country"], "overture_release": release,
                "built_at": built_at, "min_conf": f"{extract.MIN_CONF:.2f}", "set": SET_NAME,
                "bboxes": json.dumps(bboxes(region)), "datasets": json.dumps(st["datasets"], ensure_ascii=False)}
        facts = write_index.write(con, out_path, meta, log=log)
        con.close()
    return {"clean": st, "write": facts}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rows")
    ap.add_argument("--out", required=True)
    ap.add_argument("--suffix", default="-mini")
    a = ap.parse_args()
    with open(a.rows, encoding="utf-8") as f:
        data = json.load(f)
    os.makedirs(a.out, exist_ok=True)
    labels = labels_mod.load(os.path.join(ROOT, "labels", "brands.json"))
    for region, rows in data.items():
        if region not in REGIONS:
            continue
        path = os.path.join(a.out, f"{region}{a.suffix}.sqlite")
        res = build_region(rows, region, path, labels=labels)
        print(json.dumps({"region": region, "file": path, "rows": res["write"]["rows"],
                          "bytes": res["write"]["bytes"]}))


if __name__ == "__main__":
    main()
