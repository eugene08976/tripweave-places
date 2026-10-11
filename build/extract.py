"""Step 1: read one region from Overture's public S3 bucket into a local parquet file (spec section 1.2).

No account is needed: the bucket is public and read anonymously. A column that disappears from the release makes the
query fail, so the run stops and nothing is published. That is how a breaking schema change shows up.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from regions import REGIONS, box_predicate  # noqa: E402

STAC = "https://stac.overturemaps.org/catalog.json"
MIN_CONF = 0.70
SOURCE = "s3://overturemaps-us-west-2/release/{rel}/theme=places/type=place/*"
USER_AGENT = "tripweave-places builder (https://github.com/eugene08976/tripweave-places)"


RELEASE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.\d+$")


def check_release(rel) -> str:
    """An Overture release name ("2026-09-23.1") and nothing else: it goes into an S3 path, SQL, file names and the
    workflow's outputs."""
    if not isinstance(rel, str) or not RELEASE_RE.match(rel):
        raise RuntimeError(f"not an Overture release name: {str(rel)[:40]!r}")
    return rel


def latest_release(timeout: float = 30) -> str:
    req = urllib.request.Request(STAC, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        cat = json.load(r)
    if isinstance(cat.get("latest"), str):
        return check_release(cat["latest"])
    for link in cat.get("links", []):
        if link.get("rel") == "child" and link.get("latest"):
            return check_release(link["href"].rstrip("/").split("/")[-2])
    raise RuntimeError("STAC catalog has no release marked latest")


def connect(threads: int, memory: str, temp_dir: str):
    import duckdb
    os.makedirs(temp_dir, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    con.execute(f"SET threads={int(threads)}; SET memory_limit='{memory}'; SET temp_directory='{temp_dir}'; "
                "SET preserve_insertion_order=false;")
    return con


def extract_sql(region: str, release: str, out_path: str) -> str:
    cf = REGIONS[region]["country_filter"]
    country = f"AND {cf}" if cf else ""
    src = SOURCE.format(rel=check_release(release))
    return f"""COPY (
  SELECT id AS gers, names.primary AS name,
         [r.value FOR r IN coalesce(names.rules, []) IF r.language = 'en'] AS name_en,
         basic_category, taxonomy.primary AS tax_primary, taxonomy.hierarchy AS tax_hierarchy,
         confidence, operating_status,
         addresses[1].freeform AS addr, addresses[1].locality AS locality, addresses[1].region AS addr_region,
         addresses[1].country AS country,
         brand.names.primary AS brand, brand.wikidata AS brand_wikidata,
         [struct_pack(dataset := s.dataset, license := s.license) FOR s IN coalesce(sources, [])] AS sources,
         len(coalesce(websites, [])) + len(coalesce(phones, [])) + len(coalesce(socials, []))
           + len(coalesce(emails, [])) AS contacts,
         bbox.xmin AS lng, bbox.ymin AS lat
  FROM read_parquet('{src}', hive_partitioning=0)
  WHERE {box_predicate(region)} {country}
    AND names.primary IS NOT NULL AND confidence >= {MIN_CONF}
    AND coalesce(operating_status, 'open') = 'open'
) TO '{out_path}' (FORMAT parquet, COMPRESSION zstd)"""


def extract(region: str, release: str, out_path: str, threads: int, memory: str, temp_dir: str) -> dict:
    con = connect(threads, memory, temp_dir)
    tmp = out_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    t = time.time()
    con.execute(extract_sql(region, release, tmp))
    os.replace(tmp, out_path)
    rows = con.execute(f"SELECT count(*) FROM '{out_path}'").fetchone()[0]
    con.close()
    return {"region": region, "rows": rows, "seconds": round(time.time() - t, 1), "bytes": os.path.getsize(out_path)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("regions", nargs="+", choices=sorted(REGIONS))
    ap.add_argument("--release", default=None, help="Overture release; default: the STAC catalog's latest")
    ap.add_argument("--work", default="work")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--memory", default="6GB")
    a = ap.parse_args()
    rel = a.release or latest_release()
    os.makedirs(a.work, exist_ok=True)
    for r in a.regions:
        info = extract(r, rel, os.path.join(a.work, f"{r}.parquet"), a.threads, a.memory, os.path.join(a.work, "tmp"))
        print(json.dumps({"release": rel, **info}), flush=True)


if __name__ == "__main__":
    main()
