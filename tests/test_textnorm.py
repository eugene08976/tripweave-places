"""Norm 1 (spec section 1.4): the vectors, and norm-vectors.json in step with the code."""
import json
import os

import textnorm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_spec_vectors():
    v = dict(textnorm.vectors())
    assert v["Joe's Pizza"] == "joes pizza"
    assert v["ＡＢＣ"] == "abc"
    assert v["臺北101"] == "台 北 101"
    assert v["A4鼎泰豐"] == "a4 鼎 泰 豐"
    assert v["dell'Arte"] == "dell arte"
    assert v["O'Brien's Pub"] == "o briens pub"
    assert v["Main St"] == "main street"
    assert v["Straße"] == "strasse"
    assert v["セブン-イレブン"] == "セ ブ ン イ レ ブ ン"
    assert v["JOE'S PIZZA"] == "joe s pizza"      # like rank.ts words(): only a lower-case 's is joined


def test_norm_vectors_file_matches(tmp_path):
    """Writes norm-vectors.json when NORM_WRITE=1; otherwise checks the committed file."""
    path = os.path.join(ROOT, "norm-vectors.json")
    mine = textnorm.vectors()
    if os.environ.get("NORM_WRITE") == "1":
        with open(path, "w", encoding="utf-8") as f:
            json.dump(mine, f, ensure_ascii=False, indent=1)
            f.write("\n")
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == mine


def test_query_tokens_drop_stop_words_unless_nothing_left():
    assert textnorm.query_tokens("the lawn") == ["lawn"]
    assert textnorm.query_tokens("the") == ["the"]
    assert textnorm.tokens("Torre de Belém") == ["torre", "de", "belem"]


def test_split_chars():
    assert textnorm.is_split_char("鼎") and not textnorm.is_split_char("a")
    assert textnorm.tokens("台北101") == ["台", "北", "101"]
