"""Step 3: write one region's SQLite file, format 1 (spec section 1.4; the full contract is FORMAT.md).

Rows are written in z-order: sorted by the Morton code of the point, so every level-12 cell is one contiguous run of
place ids and `zcell` stores its first and last id. A map box is then a handful of id ranges, and the full-text
query is limited to those ranges with `rowid BETWEEN lo AND hi`.

Written with apsw, which bundles its own SQLite (with FTS5), so the file doesn't depend on the runner's system
SQLite. The SQLite version used is stored in meta.sqlite_version.
"""
import json
import os
import re
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apsw  # noqa: E402

from clean import build_alt  # noqa: E402
from textnorm import NORM_VERSION, is_split_char, norm  # noqa: E402

FORMAT_VERSION = 1
LEVEL = 12                 # cell level: 4096 x 4096 cells over the world
ZBITS = 31                 # bits per axis of the full-precision Morton code (62-bit code, fits a signed int64)
CELL_SHIFT = 2 * (ZBITS - LEVEL)
ADDR_MAX = 80
NOTICE = "Derived from Overture Maps data: filtered, de-duplicated, re-indexed by Tripweave"

DDL = """
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID;
CREATE TABLE place(
  id    INTEGER PRIMARY KEY,
  gers  BLOB NOT NULL,
  name  TEXT NOT NULL,
  alt   TEXT,
  cat   TEXT,
  addr  TEXT,
  lat REAL NOT NULL, lng REAL NOT NULL,
  conf  INTEGER NOT NULL,
  src   INTEGER NOT NULL,
  flags INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE place_fts USING fts5(name, alt, content='',
  tokenize='unicode61 remove_diacritics 2', prefix='2 3');
CREATE TABLE zcell(cell INTEGER PRIMARY KEY, lo INTEGER NOT NULL, hi INTEGER NOT NULL);
CREATE TABLE vocab(term TEXT PRIMARY KEY, df INTEGER NOT NULL) WITHOUT ROWID;
"""


def morton_sql(zbits: int = ZBITS) -> str:
    """DuckDB expression: the Morton code of (lng, lat); x bits at even positions, y bits at odd positions."""
    scale = 1 << zbits
    x = f"least({scale - 1}, greatest(0, floor((lng + 180.0) / 360.0 * {scale})))::BIGINT"
    y = f"least({scale - 1}, greatest(0, floor((lat + 90.0) / 180.0 * {scale})))::BIGINT"
    return (f"list_sum([((({x} >> i) & 1) << (2 * i)) | ((({y} >> i) & 1) << (2 * i + 1)) "
            f"FOR i IN range({zbits})])::BIGINT")


def cell_of(lat: float, lng: float) -> int:
    """Level-12 cell id; must equal the reader's computation (FORMAT.md, 'Cells')."""
    x = min(4095, max(0, int((lng + 180.0) / 360.0 * 4096 // 1)))
    y = min(4095, max(0, int((lat + 90.0) / 180.0 * 4096 // 1)))
    c = 0
    for i in range(12):
        c |= ((x >> i) & 1) << (2 * i) | ((y >> i) & 1) << (2 * i + 1)
    return c


def short_addr(a, limit: int = ADDR_MAX):
    if not a:
        return None
    a = " ".join(a.split())
    if len(a) <= limit:
        return a
    cut = a[:limit]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > limit // 2 else cut).rstrip(" ,;-")


_HAN_FIRST = re.compile(r"^[\u3400-\u9fff\uf900-\ufaff]")
_US_STATE = re.compile(r"^(?:US-)?([A-Z]{2})$")


def compose_addr(street, town, state, region: str):
    """addr (FORMAT.md): the street address and the town, in that order ("Jl. Pantai Bingin No.8, Pecatu"); a
    Chinese street address gets the town in front, as written in Taiwan ("高雄市路竹區路科十路一號"); in the US the
    town carries its state ("1545 S Belcher Rd, Clearwater, FL"). A town already in the street address isn't
    repeated. At most ADDR_MAX characters: the street part is cut so the town stays."""
    street = " ".join(street.split()) if street else ""
    town = " ".join(town.split()) if town else ""
    if region == "us" and town and state:
        m = _US_STATE.match(state.strip().upper())
        if m:
            town = f"{town}, {m.group(1)}"
    if not street:
        return short_addr(town)
    if not town or town.split(",")[0].lower() in street.lower():
        return short_addr(street)
    if _HAN_FIRST.match(street):
        return short_addr(town + street)
    room = ADDR_MAX - len(town) - 2
    if room < 24:
        return short_addr(f"{street}, {town}")
    return f"{short_addr(street, room)}, {town}"


def write(con, out_path: str, meta: dict, log=print, cache_mb: int = 512) -> dict:
    """Write table `fin` (from clean.py) to out_path. Returns build facts."""
    t0 = time.time()
    tmp = out_path + ".tmp"
    for p in (tmp, tmp + "-journal"):
        if os.path.exists(p):
            os.remove(p)
    db = apsw.Connection(tmp)
    db.execute(f"PRAGMA page_size=4096; PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; "
               f"PRAGMA cache_size=-{cache_mb * 1024};")
    db.execute(DDL)

    cur = con.execute(f"""SELECT gers, name, name_en, brand, absorbed, label, cat, addr, locality, addr_region, lat, lng, conf,
               src, z
        FROM (SELECT *, {morton_sql()} AS z FROM fin) ORDER BY z, gers""")
    z_cells = {}
    n = 0
    db.execute("BEGIN")
    while True:
        rows = cur.fetchmany(50_000)
        if not rows:
            break
        P, F = [], []
        for gers, name, name_en, brand, absorbed, label, cat, addr, locality, addr_region, lat, lng, conf, src, z in rows:
            n += 1
            alt = build_alt(name, name_en, brand, absorbed, label)
            flags = 1 if brand else 0
            P.append((n, uuid.UUID(gers).bytes, name, "|".join(alt) or None, cat,
                      compose_addr(addr, locality, addr_region, meta["region"]), lat, lng, conf,
                      src, flags))
            F.append((n, norm(name), " ".join(norm(a) for a in alt)))
            c = z >> CELL_SHIFT
            span = z_cells.get(c)
            if span is None:
                z_cells[c] = [n, n]
            else:
                if span[1] != n - 1:
                    raise RuntimeError(f"cell {c} is not contiguous at row {n}")
                span[1] = n
        db.executemany("INSERT INTO place VALUES (?,?,?,?,?,?,?,?,?,?,?)", P)
        db.executemany("INSERT INTO place_fts(rowid, name, alt) VALUES (?,?,?)", F)
    db.executemany("INSERT INTO zcell VALUES (?,?,?)", [(c, lo, hi) for c, (lo, hi) in sorted(z_cells.items())])
    db.execute("COMMIT")
    t_load = time.time()

    # 6-7. vocabulary with document frequency, and the common tokens
    db.execute("INSERT INTO place_fts(place_fts) VALUES('optimize')")
    db.execute("CREATE VIRTUAL TABLE temp.fv USING fts5vocab(main, place_fts, row)")
    db.execute("INSERT INTO vocab(term, df) SELECT term, doc FROM temp.fv")
    db.execute("DROP TABLE temp.fv")
    threshold = max(500, n // 100 + (1 if n % 100 else 0))
    # Single Han/kana/Hangul characters are left out: CJK words are phrases of such characters, and treating 茶, 湯
    # and 會 as common would skip a search for 茶湯會 (a tea chain) or 全家 (FamilyMart) as a bare category word.
    common = [t for (t,) in db.execute("SELECT term FROM vocab WHERE df >= ? ORDER BY df DESC, term", (threshold,))
              if not is_split_char(t)]
    vocab_terms = db.execute("SELECT count(*) FROM vocab").fetchone()[0]

    m = dict(meta)
    m.update({
        "format": str(FORMAT_VERSION), "norm": str(NORM_VERSION), "level": str(LEVEL),
        "sqlite_version": apsw.sqlite_lib_version(), "rows": str(n),
        "common_tokens": json.dumps(common, ensure_ascii=False), "notice": NOTICE,
    })
    db.executemany("INSERT INTO meta(k, v) VALUES (?, ?)", sorted(m.items()))
    t_vocab = time.time()

    ic = db.execute("PRAGMA integrity_check").fetchall()
    db.execute("INSERT INTO place_fts(place_fts, rank) VALUES('integrity-check', 0)")
    db.execute("ANALYZE")
    db.execute("VACUUM")
    db.close()
    os.replace(tmp, out_path)
    t_end = time.time()

    facts = {
        "rows": n, "cells": len(z_cells), "vocab_terms": vocab_terms, "common_tokens": common,
        "common_threshold": threshold, "integrity_check": ic[0][0] if len(ic) == 1 else [r[0] for r in ic],
        "bytes": os.path.getsize(out_path), "load_s": round(t_load - t0, 1), "vocab_s": round(t_vocab - t_load, 1),
        "check_vacuum_s": round(t_end - t_vocab, 1),
    }
    log(f"{meta['region']}: wrote {n:,} rows, {len(z_cells):,} cells, {vocab_terms:,} terms, "
        f"{facts['bytes'] / 1e6:.1f} MB in {t_end - t0:.0f} s (load {facts['load_s']} s, vocab {facts['vocab_s']} s, "
        f"check+vacuum {facts['check_vacuum_s']} s); common tokens {common}")
    return facts
