"""Build the release: extract, clean, write and verify each region, then write the manifest and licence files.

    python build/build.py                       # all regions, latest Overture release, as the workflow runs it
    python build/build.py --regions bali taiwan --out out/dev
    python build/build.py --from-parquet bali=path/to/bali.parquet --out out/dev   # no S3 download

Exit code 0 when built or skipped (out/status.json says which), 1 when a gate failed or a step crashed. Nothing in
out/ is published by this script; the workflow uploads it.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time
import traceback
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clean  # noqa: E402
import extract  # noqa: E402
import labels as labels_mod  # noqa: E402
import manifest as manifest_mod  # noqa: E402
import verify  # noqa: E402
import write_index  # noqa: E402
from regions import ORDER, REGIONS, bboxes, box_predicate  # noqa: E402
from sets import SET_NAME  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_REPO = "eugene08976/tripweave-places"


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def previous_manifest(spec: str):
    """The manifest of the last published release, or None (first build, or 'none')."""
    if not spec or spec == "none":
        return None
    if os.path.exists(spec):
        return verify.load_json(spec)
    try:
        req = urllib.request.Request(spec, headers={"User-Agent": extract.USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def adapt_parquet(con, path: str, region: str, work: str) -> str:
    """Accept the 2026-10-10 measurement extracts (older column names) for --from-parquet."""
    cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()}
    if {"gers", "name_en", "contacts", "lng", "sources"} <= cols:
        return path
    if not {"id", "name", "lon", "lat", "sources", "confidence"} <= cols:
        raise SystemExit(f"{path}: unknown parquet layout")
    out = os.path.join(work, f"{region}.adapted.parquet")
    contacts = " + ".join(f"coalesce({c}, 0)" for c in ("n_web", "n_soc", "n_ph", "n_em") if c in cols) or "0"
    addr_region = "region" if "region" in cols else "NULL::VARCHAR"
    cf = REGIONS[region]["country_filter"]
    country = f"AND {cf.replace('addresses[1].country', 'country')}" if cf else ""
    con.execute(f"""COPY (
      SELECT id AS gers, name, []::VARCHAR[] AS name_en, basic_category, tax_primary, tax_hierarchy, confidence,
             operating_status, addr, locality, {addr_region} AS addr_region, country, brand, brand_wikidata,
             [struct_pack(dataset := s.dataset, license := s.license) FOR s IN coalesce(sources, [])] AS sources,
             {contacts} AS contacts, lon AS lng, lat
      FROM read_parquet('{path}')
      WHERE {box_predicate(region, 'lon', 'lat')} {country}
        AND name IS NOT NULL AND confidence >= {extract.MIN_CONF} AND coalesce(operating_status, 'open') = 'open'
    ) TO '{out}' (FORMAT parquet, COMPRESSION zstd)""")
    return out


def parse_from_parquet(items):
    out = {}
    for it in items or []:
        if "=" in it:
            r, p = it.split("=", 1)
        else:
            base = os.path.basename(it).lower()
            r = next((k for k in REGIONS if k in base), None)
            p = it
        if r not in REGIONS:
            raise SystemExit(f"--from-parquet {it}: can't tell the region; use region=path")
        out[r] = os.path.abspath(os.path.expanduser(p))
    return out


def write_status(out_dir: str, status: dict) -> None:
    with open(os.path.join(out_dir, "status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)
        f.write("\n")
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            for k in ("result", "tag", "release"):
                if status.get(k):
                    f.write(f"{k}={status[k]}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regions", nargs="+", choices=ORDER, default=None)
    ap.add_argument("--release", default=None, help="Overture release (default: STAC latest)")
    ap.add_argument("--from-parquet", nargs="+", default=None, metavar="[REGION=]PATH",
                    help="build from local parquet extracts instead of S3 (emergency fallback, dev builds)")
    ap.add_argument("--work", default=os.path.join(ROOT, "work"))
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--memory", default="6GB")
    ap.add_argument("--previous", default=None,
                    help="previous manifest (URL or path, 'none'); default: the repo's latest release")
    ap.add_argument("--build-number", type=int, default=1, help="n in the tag places-<release>-b<n>")
    ap.add_argument("--force", action="store_true", help="build even if this Overture release is already published")
    ap.add_argument("--allow-count-change", action="store_true", help="publish a row-count change beyond +-15%%")
    ap.add_argument("--labels-offline", action="store_true", help="don't call Wikidata; use labels/brands.json")
    ap.add_argument("--no-gates", action="store_true", help="dev builds only: report gates but never fail on them")
    ap.add_argument("--keep-work", action="store_true", help="keep the parquet extracts in --work")
    a = ap.parse_args()

    t_start = time.time()
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    from_pq = parse_from_parquet(a.from_parquet)
    regions = a.regions or (sorted(from_pq, key=ORDER.index) if from_pq else list(ORDER))
    repo = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)
    status = {"result": "failed", "regions": regions}
    report = {"regions": {}, "gates": {}}
    try:
        release = a.release or (None if from_pq else extract.latest_release())
        if release is not None:
            extract.check_release(release)
        if release is None:
            raise SystemExit("--release is required with --from-parquet")
        status["release"] = release
        prev_spec = a.previous if a.previous is not None else \
            f"https://github.com/{repo}/releases/latest/download/manifest.json"
        prev = previous_manifest(prev_spec)
        status["previous_release"] = prev.get("overture_release") if prev else None
        if prev and prev.get("overture_release") == release and not a.force:
            status.update(result="skipped", reason=f"Overture {release} is already published as {prev.get('tag')}")
            write_status(a.out, status)
            log(status["reason"])
            return 0
        tag = f"places-{release}-b{a.build_number}"
        status["tag"] = tag
        built_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        log(f"building {tag} for {', '.join(regions)} (previous: {status['previous_release']})")

        files, all_licences = [], {}
        for region in regions:
            rt = {"t0": time.time()}
            con = extract.connect(a.threads, a.memory, os.path.join(a.work, "tmp"))
            if region in from_pq:
                pq = adapt_parquet(con, from_pq[region], region, a.work)
                rt["extract"] = {"from_parquet": from_pq[region]}
            else:
                pq = os.path.join(a.work, f"{region}.parquet")
                log(f"{region}: extracting from s3 ({a.threads} threads, {a.memory})")
                rt["extract"] = extract.extract(region, release, pq, a.threads, a.memory, os.path.join(a.work, "tmp"))
                log(f"{region}: extracted {rt['extract']['rows']:,} rows in {rt['extract']['seconds']} s")
            t1 = time.time()
            labels = None
            if region == "taiwan":
                ids = clean.taiwan_label_ids(con, pq)
                path = os.path.join(ROOT, "labels", "brands.json")
                if a.labels_offline:
                    labels, lst = labels_mod.load(path), {"ids": len(ids), "offline": True}
                else:
                    labels, lst = labels_mod.refresh(ids, path, log=log)
                rt["labels"] = lst
                log(f"taiwan: brand labels {lst}")
            st = clean.clean(con, pq, region, a.work, labels=labels, log=log)
            t2 = time.time()
            if st["licence_gate"] != "ok":
                raise RuntimeError(f"{region}: licence gate failed: {st['licence_gate']}")
            out_path = os.path.join(a.out, f"{region}.sqlite")
            meta = {"region": region, "country": REGIONS[region]["country"], "overture_release": release,
                    "built_at": built_at, "min_conf": f"{extract.MIN_CONF:.2f}", "set": SET_NAME,
                    "bboxes": json.dumps(bboxes(region)), "datasets": json.dumps(st["datasets"], ensure_ascii=False)}
            facts = write_index.write(con, out_path, meta, log=log)
            con.close()
            t3 = time.time()
            chk = verify.check_file(out_path, region, st["rows"])
            if facts["integrity_check"] != "ok":
                chk["ok"] = False
                chk["problems"].append(f"integrity_check {facts['integrity_check']}")
            sha = manifest_mod.sha256(out_path)
            files.append({"region": region, "name": f"{region}.sqlite", "bytes": facts["bytes"], "sha256": sha,
                          "rows": st["rows"], "country": REGIONS[region]["country"], "bboxes": bboxes(region),
                          "datasets": st["datasets"]})
            for k, v in st["licences"].items():
                all_licences[k] = all_licences.get(k, 0) + v
            rt.update(clean=st, write={k: v for k, v in facts.items() if k != "common_tokens"},
                      common_tokens=facts["common_tokens"], file_check=chk, sha256=sha,
                      seconds={"extract": round(t1 - rt["t0"], 1), "clean": round(t2 - t1, 1),
                               "write": round(t3 - t2, 1), "total": round(t3 - rt["t0"], 1)})
            rt.pop("t0")
            report["regions"][region] = rt
            log(f"{region}: file check {'ok' if chk['ok'] else chk['problems']}; sha256 {sha}")
            if not a.keep_work and region not in from_pq and os.path.exists(pq):
                os.remove(pq)
        shutil.rmtree(os.path.join(a.work, "tmp"), ignore_errors=True)

        # gates
        g = report["gates"]
        g["files"] = {r: report["regions"][r]["file_check"]["ok"] for r in regions}
        prev_rows = {f["region"]: f["rows"] for f in (prev or {}).get("files", [])}
        g["counts"] = verify.check_counts({f["region"]: f["rows"] for f in files},
                                          {r: REGIONS[r]["floor"] for r in regions}, prev_rows, a.allow_count_change)
        g["licence"] = {r: report["regions"][r]["clean"]["licence_gate"] for r in regions}
        g["norm_vectors"] = verify.check_vectors()
        smoke_doc = verify.load_json(os.path.join(HERE, "smoke.json"))
        cases = smoke_doc["cases"]
        paths = {f["region"]: os.path.join(a.out, f["name"]) for f in files}
        picked = False
        for c in cases:
            if c["expect"] is None and c["region"] in paths:
                c["expect"] = verify.pick_minneapolis_joes(paths[c["region"]], tuple(c["at"]))
                picked = c["expect"] is not None
                log(f"smoke: picked {c['expect']!r} for {c['q']!r} at {c['at']}")
        if picked:
            with open(os.path.join(HERE, "smoke.json"), "w", encoding="utf-8") as f:
                json.dump(smoke_doc, f, ensure_ascii=False, indent=1)
                f.write("\n")
        g["smoke"] = verify.run_smoke(paths, cases)
        cov = verify.run_coverage(paths, verify.load_json(os.path.join(HERE, "coverage.json")))
        g["owner_six"] = cov["owner_six"]
        g["coverage_summary"] = cov.get("summary")
        report["coverage"] = cov["places"]

        failures = []
        failures += [f"file {r}" for r, ok in g["files"].items() if not ok]
        failures += [f"count {r}" for r, c in g["counts"].items() if not c["ok"]]
        failures += [f"licence {r}" for r, v in g["licence"].items() if v != "ok"]
        if not g["norm_vectors"]["ok"]:
            failures.append("norm vectors differ from norm-vectors.json")
        failures += [f"smoke {c['region']} {c['q']!r}" for c in g["smoke"] if not c["ok"]]
        failures += [f"owner place {o['id']}" for o in g["owner_six"] if not o["ok"]]
        g["failures"] = failures
        for c in g["smoke"]:
            log(f"smoke {c['region']} {c['q']!r}: {'ok' if c['ok'] else 'FAIL'} top3={c['top3']}")
        for o in g["owner_six"]:
            log(f"owner place {o['name']} ({o['primary']!r}): {'ok' if o['ok'] else 'FAIL'} "
                f"{ {q: v['pos'] for q, v in o['queries'].items()} }")
        log(f"coverage: {g['coverage_summary']}")
        log(f"counts: { {r: (c['rows'], c.get('change')) for r, c in g['counts'].items()} }")

        smoke_ok = [c for c in g["smoke"] if c["ok"]]
        man = manifest_mod.manifest(tag, release, built_at, files, smoke_ok if a.no_gates else g["smoke"])
        att = manifest_mod.attribution(tag, release, built_at, files, all_licences)
        assets = manifest_mod.write_release_files(a.out, man, att)
        report["assets"] = assets
        report["seconds_total"] = round(time.time() - t_start, 1)
        with open(os.path.join(a.out, "build-report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1, default=str)
        if failures and not a.no_gates:
            status.update(result="failed", reason="; ".join(failures))
            write_status(a.out, status)
            log(f"GATES FAILED: {status['reason']}")
            return 1
        status.update(result="built", files={f["name"]: {"rows": f["rows"], "bytes": f["bytes"]} for f in files},
                      gate_failures_ignored=failures if a.no_gates else [], seconds=report["seconds_total"])
        write_status(a.out, status)
        log(f"built {tag} in {report['seconds_total']:.0f} s; assets: {', '.join(assets)}")
        return 0
    except Exception as e:
        status.update(result="failed", reason=f"{type(e).__name__}: {e}")
        write_status(a.out, status)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
