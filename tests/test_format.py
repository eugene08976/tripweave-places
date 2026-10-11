"""Format 1 (spec section 1.4, FORMAT.md): DDL, journal mode, meta, zcell, vocab, common tokens, Morton parity."""
import json
import os

import apsw
import pytest

import fixture
import lookup
import write_index

FOOD = ["food_and_drink", "restaurant"]


def rows(n=1200):
    out = []
    for i in range(n):
        name = "Warung Sika" if i == 7 else f"Warung {i}" if i % 2 else f"Cafe Number {i}"
        out.append({"gers": f"00000000-0000-4000-8000-{i:012d}", "name": name, "basic_category": "restaurant",
                    "tax_primary": "restaurant", "tax_hierarchy": FOOD,
                    "lat": -8.60 - (i % 40) * 0.004, "lng": 115.10 + (i // 40) * 0.004})
    return out


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("fmt") / "bali.sqlite")
    res = fixture.build_region(rows(), "bali", path, release="2026-09-23.1")
    db = apsw.Connection(path, flags=apsw.SQLITE_OPEN_READONLY)
    yield res, db, path
    db.close()


def test_tables_and_pragmas(built):
    _, db, _ = built
    names = {n for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table')")}
    assert {"meta", "place", "place_fts", "zcell", "vocab"} <= names
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert db.execute("PRAGMA page_size").fetchone()[0] == 4096
    sql = db.execute("SELECT sql FROM sqlite_master WHERE name = 'place_fts'").fetchone()[0]
    assert "content=''" in sql and "unicode61 remove_diacritics 2" in sql and "prefix='2 3'" in sql
    with open(built[2], "rb") as f:
        head = f.read(20)
    assert head[18] == 1 and head[19] == 1          # legacy (rollback journal) file format, never WAL


def test_meta(built):
    _, db, _ = built
    m = dict(db.execute("SELECT k, v FROM meta"))
    assert m["format"] == "1" and m["norm"] == "1" and m["region"] == "bali" and m["country"] == "ID"
    assert m["sqlite_version"] == apsw.sqlite_lib_version()
    assert m["level"] == "12" and m["min_conf"] == "0.70" and m["set"] == "traveler-v2"
    assert m["rows"] == "1200" and json.loads(m["bboxes"]) == [[114.42, -8.87, 115.72, -8.04]]
    assert json.loads(m["datasets"]) == {"meta": 1200}
    assert "Overture Maps" in m["notice"]
    for k in ("overture_release", "built_at", "common_tokens"):
        assert k in m


def test_common_tokens_and_vocab(built):
    res, db, _ = built
    m = dict(db.execute("SELECT k, v FROM meta"))
    common = json.loads(m["common_tokens"])
    assert common == ["cafe", "number", "warung"]          # df >= 500 and >= 1% of 1,200 rows; ties by term
    assert db.execute("SELECT df FROM vocab WHERE term = 'sika'").fetchone()[0] == 1
    assert db.execute("SELECT df FROM vocab WHERE term = 'warung'").fetchone()[0] == 600


def test_zcell_spans_cover_ids_in_order(built):
    _, db, _ = built
    spans = db.execute("SELECT cell, lo, hi FROM zcell ORDER BY lo").fetchall()
    assert spans[0][1] == 1 and spans[-1][2] == 1200
    for (c1, lo1, hi1), (c2, lo2, hi2) in zip(spans, spans[1:]):
        assert hi1 + 1 == lo2 and c1 < c2
    for pid, lat, lng in db.execute("SELECT id, lat, lng FROM place"):
        c = write_index.cell_of(lat, lng)
        lo, hi = db.execute("SELECT lo, hi FROM zcell WHERE cell = ?", (c,)).fetchone()
        assert lo <= pid <= hi


def test_morton_edges():
    assert write_index.cell_of(-90, -180) == 0
    assert write_index.cell_of(90, 180) == (1 << 24) - 1          # clamped to 4095, 4095
    assert write_index.cell_of(53.0, 179.99) == lookup.interleave(4095, lookup._y(53.0))
    assert write_index.cell_of(53.0, 179.9) == lookup.interleave(4094, lookup._y(53.0))
    assert write_index.cell_of(71.0, -156.8) == lookup.interleave(lookup._x(-156.8), lookup._y(71.0))
    # the x bit lands on even positions, the y bit on odd positions
    assert lookup.interleave(1, 0) == 1 and lookup.interleave(0, 1) == 2


def test_fts_query_shapes(built):
    _, db, _ = built
    common = frozenset(["warung", "cafe", "number"])
    assert lookup.build_query("warung sika", db, common)[0] == '("warung" OR "warungs") AND "sika"'
    assert lookup.build_query("warung si", db, common)[0] == '("warung" OR "warungs") AND "si"*'
    assert lookup.build_query("cafe x", db, common)[:2] == ('("cafe" OR "cafes")', "x")
    assert lookup.build_query('sika"bar', db, common)[0] == '("sika" OR "sikas") AND "bar"*'
    assert lookup.build_query("sia seafoods", db, common)[0] == '"sia" AND ("seafoods" OR "seafood")'
    assert [lookup.number_forms(w) for w in ("seafoods", "glass", "boxes", "joes", "shoes", "places", "sia")] == [
        ["seafood"], ["glasses"], ["box"], ["joe"], ["shoe"], ["place"], []]
    assert lookup.quote('a"b') == '"a""b"'
    assert lookup.build_query("鼎泰豐", db, common)[0] == '"鼎 泰 豐"'
    assert not lookup.eligible("warung", common) and lookup.eligible("warung sika", common)
    rows = lookup.lookup(db, "warung sika", [(-8.62, 115.12)], common)
    assert [r[1] for r in rows] == ["Warung Sika"]
    for bad in ('"', "*", "NEAR(", "-", "AND", "^", "!!!"):
        lookup.lookup(db, bad, [(-8.62, 115.12)], common)        # never raises
