"""The looksLikeAddress port against the app repo's own test table (apps/server/src/search.test.ts)."""
import json
import os

from addrlike import looks_like_address, looks_like_phone

HERE = os.path.dirname(os.path.abspath(__file__))


def test_repo_table():
    with open(os.path.join(HERE, "fixtures", "address_cases.json"), encoding="utf-8") as f:
        cases = json.load(f)
    assert len(cases) > 50
    bad = [(q, want) for q, want, _ in cases if looks_like_address(q) != want]
    assert bad == []


def test_phone():
    assert looks_like_phone("+62 812-3456-7890")
    assert looks_like_phone("WA 081234567890")
    assert not looks_like_phone("Taipei 101")
    assert not looks_like_phone("7-Eleven 1234")
