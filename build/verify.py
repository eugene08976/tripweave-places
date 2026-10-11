"""Step 4: checks a finished region file must pass before anything is published (spec sections 1.4 and 1.5).

File checks: integrity_check ok (run in write_index), the file reopens read-only, journal_mode reads delete, meta
holds format/norm/region/rows, the file is under 1.9 GiB.
Release checks: row counts above the floors and within +-15% of the previous release (unless allow_count_change),
the licence gate, every smoke case in the top 3 with the builder's lookup, norm vectors equal to norm-vectors.json,
and the owner's six Bali places findable (top 12 of the builder's stand-in ranking) on the Canggu + Uluwatu trip. The 86-place coverage set is measured and reported.
"""
import json

import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apsw  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

import lookup  # noqa: E402
import textnorm  # noqa: E402

MAX_BYTES = int(1.9 * 1024 ** 3)
COUNT_TOLERANCE = 0.15
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def open_ro(path: str):
    return apsw.Connection(path, flags=apsw.SQLITE_OPEN_READONLY)


def meta(db) -> dict:
    return dict(db.execute("SELECT k, v FROM meta").fetchall())


def check_file(path: str, region: str, expect_rows: int) -> dict:
    out = {"bytes": os.path.getsize(path)}
    db = open_ro(path)
    m = meta(db)
    out["journal_mode"] = db.execute("PRAGMA journal_mode").fetchone()[0]
    out["format"], out["norm"], out["region"], out["rows_meta"] = m.get("format"), m.get("norm"), m.get("region"), m.get("rows")
    out["place_rows"] = db.execute("SELECT count(*) FROM place").fetchone()[0]
    out["zcell_max_hi"] = db.execute("SELECT max(hi) FROM zcell").fetchone()[0]
    out["page_size"] = db.execute("PRAGMA page_size").fetchone()[0]
    db.close()
    problems = []
    if out["journal_mode"] != "delete":
        problems.append(f"journal_mode {out['journal_mode']}")
    if out["format"] != "1" or out["norm"] != str(textnorm.NORM_VERSION) or out["region"] != region:
        problems.append("meta format/norm/region")
    if not (int(out["rows_meta"]) == out["place_rows"] == expect_rows == out["zcell_max_hi"]):
        problems.append("row counts disagree")
    if out["bytes"] >= MAX_BYTES:
        problems.append("file over 1.9 GiB")
    if out["page_size"] != 4096:
        problems.append("page size")
    out["ok"] = not problems
    out["problems"] = problems
    return out


def check_counts(rows: dict, floors: dict, previous: dict, allow_change: bool) -> dict:
    out = {}
    for region, n in rows.items():
        r = {"rows": n, "floor": floors[region], "previous": previous.get(region)}
        ok = n >= floors[region]
        if r["previous"]:
            r["change"] = round(n / r["previous"] - 1, 4)
            if abs(r["change"]) > COUNT_TOLERANCE and not allow_change:
                ok = False
        r["ok"] = ok
        out[region] = r
    return out


def check_vectors(path: str = os.path.join(ROOT, "norm-vectors.json")) -> dict:
    with open(path, encoding="utf-8") as f:
        committed = json.load(f)
    mine = textnorm.vectors()
    diff = [c for c, m in zip(committed, mine) if c != m]
    ok = len(committed) == len(mine) and not diff
    return {"ok": ok, "count": len(mine), "diff": diff[:5]}


def _common(db) -> frozenset:
    return frozenset(json.loads(meta(db).get("common_tokens", "[]")))


def run_smoke(files: dict, cases: list) -> list:
    out = []
    dbs = {r: open_ro(p) for r, p in files.items()}
    try:
        for c in cases:
            db = dbs.get(c["region"])
            if db is None:
                continue
            at = [tuple(c["at"])]
            rows = lookup.rank(lookup.lookup(db, c["q"], at, _common(db)), c["q"], at)
            top = [r[1] for r in rows[:3]]
            want = textnorm.norm(c["expect"]) if c.get("expect") else None
            hit = want is not None and any(textnorm.norm(t) == want for t in top)
            d = None
            if rows:
                d = round(lookup.km(rows[0][5], rows[0][6], *c["at"]), 2)
            out.append({**c, "top3": top, "top1_km": d, "ok": hit})
    finally:
        for db in dbs.values():
            db.close()
    return out


def pick_minneapolis_joes(path: str, at=(44.9778, -93.2650)) -> str:
    """The spec leaves the Minneapolis 'joes' smoke case to the first build: the nearest Joe's that ranks first."""
    db = open_ro(path)
    try:
        rows = lookup.rank(lookup.lookup(db, "joes", [at], _common(db)), "joes", [at])
    finally:
        db.close()
    for r in rows:
        if "joes" in textnorm.words(r[1]):
            return r[1]
    return None


# --- coverage: the name matcher from the 2026-10-10 coverage study (map-providers/coverage/scripts/match.py) ---

_STOP = {"the", "a", "o", "de", "da", "do", "dos", "das", "di", "at", "of", "and", "e", "by", "n"}
_AREA = {"lisboa", "lisbon", "porto", "canggu", "ubud", "uluwatu", "seminyak", "jimbaran", "kuta", "bali", "berawa",
         "pecatu", "ungasan", "badung", "chiado", "belem", "indonesia", "portugal"}
_SOFT = _AREA | {"cafe", "coffee", "restaurant", "bar", "club", "coworking", "space", "resort", "hotel", "espresso",
                 "roasters", "co", "beach", "temple", "tower", "station", "bookstore", "market", "monastery", "castle",
                 "lift", "bridge", "eatery", "kitchen", "french", "italian", "spanish", "mexican", "japanese", "seafood",
                 "grill", "bistro", "warung", "gallery", "rooftop", "boutique", "train", "railway", "estacao",
                 "ferroviaria"}
_GENERIC = _SOFT | {"international", "airport", "cultural", "park", "sanctuary", "rice", "terrace", "walk", "ridge",
                    "tram", "stop", "cellars", "port", "shop", "surf", "shopping", "center", "centre", "villas"}


def _cnorm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().replace("&", " and ")
    s = s.replace("'", "").replace("’", "")
    return [t for t in re.sub(r"[^a-z0-9]+", " ", s).split() if t not in _STOP]


def _variants(name):
    out = [name or ""]
    for sep in (" - ", " | ", " (", "(", " @ ", " – ", ","):
        for v in list(out):
            out += [x.strip(" )") for x in v.split(sep) if x.strip(" )")]
    return list(dict.fromkeys(out))


def _covered(t, ct, joined):
    return any(fuzz.ratio(t, c) >= 85 for c in ct) or (len(t) >= 5 and t in joined)


def _strict(q, cand):
    qt, ct = _cnorm(q), _cnorm(cand)
    if not qt or not ct:
        return False
    jq, jc = "".join(qt), "".join(ct)
    if jq == jc:
        return True
    if all(_covered(t, ct, jc) for t in qt) and all(c in _SOFT or _covered(c, qt, jq) for c in ct):
        return True
    core = [c for c in ct if c not in _GENERIC]
    if core and all(_covered(c, qt, jq) for c in ct) and (
            len(core) >= 2 or len(core[0]) >= 6 or len(ct) == len(qt) - 1 and len(core[0]) >= 4):
        return all(t in _SOFT or _covered(t, ct, jc) for t in qt) or len(core) >= 2
    return False


def name_matches(query_names, cand) -> bool:
    return any(_strict(q, v) for q in query_names for v in _variants(cand))


def _m(lat1, lng1, lat2, lng2):
    return lookup.km(lat1, lng1, lat2, lng2) * 1000


def run_coverage(files: dict, cov: dict) -> dict:
    """Owner's six (primary query, top 12 on the Canggu + Uluwatu trip) and the 86-place set (top 12 near its area).

    The server's top-3 gate for the six (spec section 4.4) is measured there, after Photon merging and ranking.
    """
    bali = files.get("bali")
    out = {"owner_six": [], "places": []}
    if not bali:
        return out
    db = open_ro(bali)
    common = _common(db)
    try:
        centres = [tuple(c) for c in cov["owner_trip_centres"]]
        for p in cov["owner_six"]:
            acc = re.compile(p["accept"], re.IGNORECASE)
            per_q = {}
            for q in p["queries"]:
                rows = lookup.rank(lookup.lookup(db, q, centres, common), q, centres)
                pos = None
                for i, r in enumerate(rows[:12]):
                    if acc.search(r[1]) and any(_m(r[5], r[6], la, ln) <= 300 for la, ln in p["truths"]):
                        pos = i + 1
                        break
                per_q[q] = {"pos": pos, "top3": [r[1] for r in rows[:3]]}
            prim = per_q[p["primary"]]["pos"]
            out["owner_six"].append({"id": p["id"], "name": p["name"], "primary": p["primary"], "primary_pos": prim,
                                     "ok": prim is not None, "top3": prim is not None and prim <= 3,
                                     "queries": per_q})
        for p in cov["places"]:
            rec = {"id": p["id"], "group": p["group"], "photon": p["photon_2026_10_10"]}
            if p["group"] == "lisbon":
                rec["overture"] = "outside regions"
            else:
                at = [tuple(p["centre"])]
                names = [p["name"]] + p["aliases"]
                best = None
                for q in names:
                    rows = lookup.rank(lookup.lookup(db, q, at, common), q, at)
                    for i, r in enumerate(rows[:12]):
                        d = _m(r[5], r[6], *p["truth"])
                        if d <= p["tol_m"] and (name_matches(names, r[1]) or name_matches(names, r[2] or "")):
                            if best is None or i + 1 < best[0]:
                                best = (i + 1, q, r[1], round(d))
                            break
                local = None
                if best is None and p["local_names"]:
                    for q in p["local_names"]:
                        rows = lookup.rank(lookup.lookup(db, q, at, common), q, at)
                        for i, r in enumerate(rows[:12]):
                            if _m(r[5], r[6], *p["truth"]) <= p["tol_m"] and name_matches(p["local_names"], r[1]):
                                local = (i + 1, q, r[1])
                                break
                        if local:
                            break
                rec["overture"] = "hit" if best else ("local" if local else "miss")
                rec["detail"] = best or local
            photon_hit = p["photon_2026_10_10"] in ("hit", "hit~")
            rec["found"] = photon_hit or rec.get("overture") == "hit"
            out["places"].append(rec)
    finally:
        db.close()
    pl = out["places"]
    out["summary"] = {
        "owner_six_found": sum(1 for o in out["owner_six"] if o["ok"]),
        "owner_six_top3": sum(1 for o in out["owner_six"] if o["top3"]),
        "bali_overture_hits": sum(1 for r in pl if r["group"] != "lisbon" and r["overture"] == "hit"),
        "bali_photon_hits": sum(1 for r in pl if r["group"] != "lisbon" and r["photon"] in ("hit", "hit~")),
        "bali_found_either": sum(1 for r in pl if r["group"] != "lisbon" and r["found"]),
        "bali_total": sum(1 for r in pl if r["group"] != "lisbon"),
        "lisbon_porto_photon_hits": sum(1 for r in pl if r["group"] == "lisbon" and r["found"]),
        "lisbon_porto_total": sum(1 for r in pl if r["group"] == "lisbon"),
        "all_found": sum(1 for r in pl if r["found"]),
        "all_total": len(pl),
    }
    return out


def load_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


