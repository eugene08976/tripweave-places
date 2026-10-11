"""The synthetic us-span file: under 1 MB, about 5k rows, every rare word found in its city box."""
import json
import os

import apsw

import lookup
import synth_span


def test_us_span(tmp_path):
    path = str(tmp_path / "us-span.sqlite")
    info = synth_span.build(path)
    assert 4800 <= info["rows"] <= 5200 and info["bytes"] < 1_000_000
    truth = json.load(open(os.path.splitext(path)[0] + ".truth.json"))
    db = apsw.Connection(path, flags=apsw.SQLITE_OPEN_READONLY)
    common = frozenset(json.loads(dict(db.execute("SELECT k, v FROM meta"))["common_tokens"]))
    for city, (lat, lng) in truth["cities"].items():
        for word in synth_span.RARE:
            want = {t["name"] for t in truth["rare"] if t["city"] == city and t["q"] == word}
            got = {r[1] for r in lookup.lookup(db, word, [(lat, lng)], common)}
            assert want <= got, (city, word)
    db.close()
