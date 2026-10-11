"""Python port of the server's lookup (spec section 2.3), used by the build's smoke and coverage gates.

    q -> full-text query (last-word rule, vocab expansion) -> boxes of 15 km around each centre -> level-12 cells,
    put in rings (<= 3, 8, 15 km) -> each ring's cells merged into consecutive id ranges -> one FTS query per range,
    nearest ring first, rows outside the box dropped -> stop at 600 rows.

Time O(C log C + R + h): C cells (about 35 for a 15 km box), R merged ranges, h rows read. Space O(h).

The server's ranker is not ported. `rank()` here is a simple stand-in (name match, then distance, then confidence) so
the gates can ask "is the expected place in the top 3 / top 12".
"""
import math
import re
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from textnorm import is_split_char, norm, query_tokens, tokens, words  # noqa: E402

PAD_KM = 15.0
RINGS_KM = (3.0, 8.0)          # ring 0: <= 3 km, ring 1: <= 8 km, ring 2: the rest of the box
CAP = 600
VOCAB_EXTRA = 12
KM_PER_DEG = 111.32


def quote(t: str) -> str:
    return '"' + t.replace('"', '""') + '"'


def _next_prefix(w: str) -> str:
    return w[:-1] + chr(ord(w[-1]) + 1)


_ES = re.compile(r"(s|x|z|ch|sh|o)$")
_LATIN4 = re.compile(r"^[a-z]{4,}$")


def number_forms(t: str) -> list:
    """The other number of a Latin word of 4+ letters (FORMAT.md section 5), as the server's numberForms():
    "seafoods" -> "seafood", "glass" -> "glasses", "boxes" -> "box", "joes" -> "joe"."""
    if not _LATIN4.match(t):
        return []
    base = t[:-2]
    if t.endswith("es") and _ES.search(base) and len(base) >= (4 if base.endswith("o") else 3):
        return [base]
    if t.endswith("s") and not t.endswith("ss"):
        return [t[:-1]]
    return [t + "es" if _ES.search(t) else t + "s"]


def _any_of(ts: list) -> str:
    return "(" + " OR ".join(quote(x) for x in ts) + ")" if len(ts) > 1 else quote(ts[0])


def build_query(q: str, db, common=frozenset()):
    """Returns (match, first_letter_filter, tokens) or None when nothing searchable is left.

    The last Latin word: 1 letter -> removed, applied later as a filter; 2-3 letters -> prefix term; 4+ letters ->
    the exact token OR its singular OR up to 12 vocabulary terms that start with it, most frequent first, minus
    common tokens. Every other Latin word of 4+ letters is ORed with its other number (number_forms).
    """
    toks = query_tokens(q)
    if not toks:
        return None
    letter = None
    last = toks[-1]
    last_latin = not is_split_char(last)
    if last_latin and len(last) == 1 and len(toks) > 1:
        letter = last
        toks = toks[:-1]
        last_latin = False
    parts, run = [], []
    for i, t in enumerate(toks):
        if is_split_char(t):
            run.append(t)
            continue
        if run:
            parts.append(quote(" ".join(run)))
            run = []
        if i == len(toks) - 1 and last_latin:
            if len(t) <= 3:
                parts.append(quote(t) + "*")
            else:
                terms = [r[0] for r in db.execute(
                    "SELECT term FROM vocab WHERE term >= ? AND term < ? ORDER BY df DESC LIMIT 13",
                    (t, _next_prefix(t)))]
                extra = [x for x in terms if x != t and x not in common][:VOCAB_EXTRA]
                shorter = [x for x in number_forms(t) if not x.startswith(t)]
                parts.append(_any_of(list(dict.fromkeys([t] + shorter + extra))))
        else:
            parts.append(_any_of([t] + number_forms(t)))
    if run:
        parts.append(quote(" ".join(run)))
    if not parts:
        return None
    return " AND ".join(parts), letter, toks


def eligible(q: str, common=frozenset()) -> bool:
    """Too-short queries and queries made only of common tokens are skipped (spec section 2.2)."""
    n = norm(q)
    toks = query_tokens(q)
    if not toks:
        return False
    cjk = any(is_split_char(t) for t in toks)
    if len(n.replace(" ", "")) < (2 if cjk else 3):
        return False
    one_letter_last = len(toks) > 1 and len(toks[-1]) == 1 and not is_split_char(toks[-1])
    complete = toks[:-1] if one_letter_last else toks
    return not all(t in common for t in complete)


def box_around(lat: float, lng: float, pad_km: float = PAD_KM):
    dlat = pad_km / KM_PER_DEG
    dlng = pad_km / (KM_PER_DEG * max(0.01, math.cos(math.radians(lat))))
    return (lng - dlng, lat - dlat, lng + dlng, lat + dlat)


def split_180(box):
    w, s, e, n = box
    if w < -180:
        return [(w + 360, s, 180.0, n), (-180.0, s, e, n)]
    if e > 180:
        return [(w, s, 180.0, n), (-180.0, s, e - 360, n)]
    return [box]


def _x(lng: float) -> int:
    return min(4095, max(0, math.floor((lng + 180.0) / 360.0 * 4096)))


def _y(lat: float) -> int:
    return min(4095, max(0, math.floor((lat + 90.0) / 180.0 * 4096)))


def interleave(x: int, y: int) -> int:
    c = 0
    for i in range(12):
        c |= ((x >> i) & 1) << (2 * i) | ((y >> i) & 1) << (2 * i + 1)
    return c


def cell_bounds(x: int, y: int):
    return (x / 4096 * 360 - 180, y / 4096 * 180 - 90, (x + 1) / 4096 * 360 - 180, (y + 1) / 4096 * 180 - 90)


def km(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))


def rings(box, centre):
    """Cells covering box, grouped into rings by their nearest point to centre: [[cell, ...], [...], [...]]."""
    w, s, e, n = box
    clat, clng = centre
    out = [[], [], []]
    for x in range(_x(w), _x(e) + 1):
        for y in range(_y(s), _y(n) + 1):
            cw, cs, ce, cn = cell_bounds(x, y)
            d = km(clat, clng, min(max(clat, cs), cn), min(max(clng, cw), ce))
            ring = 0 if d <= RINGS_KM[0] else 1 if d <= RINGS_KM[1] else 2
            out[ring].append((d, interleave(x, y)))
    return [[c for _, c in sorted(r)] for r in out]


def ranges(db, cells):
    """Merge cells whose zcell spans are consecutive (hi + 1 == next lo) into id ranges, in id order."""
    if not cells:
        return []
    spans = []
    for i in range(0, len(cells), 500):
        chunk = cells[i:i + 500]
        spans += db.execute(f"SELECT lo, hi FROM zcell WHERE cell IN ({','.join('?' * len(chunk))})", chunk).fetchall()
    spans.sort()
    merged = []
    for lo, hi in spans:
        if merged and merged[-1][1] + 1 == lo:
            merged[-1][1] = hi
        else:
            merged.append([lo, hi])
    return merged


SQL = ("SELECT p.id, p.name, p.alt, p.cat, p.addr, p.lat, p.lng, p.conf, p.src, p.flags FROM place_fts "
       "JOIN place p ON p.id = place_fts.rowid WHERE place_fts MATCH ? AND place_fts.rowid BETWEEN ? AND ? LIMIT ?")


def lookup(db, q: str, centres, common=frozenset(), cap: int = CAP, stats: dict = None):
    """Rows for q around each (lat, lng) centre, nearest ring first, at most cap rows per call."""
    if not eligible(q, common):
        return []
    built = build_query(q, db, common)
    if not built:
        return []
    match, letter, _ = built
    seen, out = set(), []
    st = stats if stats is not None else {}
    st.setdefault("ranges", 0)
    st.setdefault("statements", 0)
    st["match"] = match
    for clat, clng in centres:
        for box in split_180(box_around(clat, clng)):
            w, s, e, n = box
            for ring in rings(box, (clat, clng)):
                rs = ranges(db, ring)
                st["ranges"] += len(rs)
                for lo, hi in rs:
                    if len(out) >= cap:
                        return out
                    st["statements"] += 1
                    try:
                        rows = db.execute(SQL, (match, lo, hi, cap - len(out))).fetchall()
                    except Exception:   # fts5 syntax error and the like: no rows
                        return []
                    for r in rows:
                        if r[0] in seen or not (s <= r[5] <= n and w <= r[6] <= e):
                            continue
                        if letter and not any(t.startswith(letter) for t in tokens(r[1] + " " + (r[2] or ""))):
                            continue
                        seen.add(r[0])
                        out.append(r)
                        if len(out) >= cap:
                            return out
    return out


def rank(rows, q: str, centres):
    """Stand-in ranking: exact name > all query words in the name (last one as a prefix) > other, then distance."""
    qw = words(q)

    def score(r):
        nw = words(r[1])
        if nw == qw:
            tier = 3
        elif qw and all(t in nw for t in qw[:-1]) and any(t.startswith(qw[-1]) for t in nw):
            tier = 2
        elif qw and all(any(t.startswith(x) for t in nw) for x in qw):
            tier = 2
        else:
            tier = 1
        if any(is_split_char(t) for t in qw):
            joined = "".join(nw)
            if "".join(qw) in joined:
                tier = max(tier, 3 if joined == "".join(qw) else 2)
        d = min(km(r[5], r[6], la, ln) for la, ln in centres)
        return (-tier, d, -r[7])

    return sorted(rows, key=score)
