"""Text normalization, norm version 1.

The reference is the app server's TypeScript: `words()` in apps/server/src/rank.ts and `fold()` / `tokens()` in
apps/server/src/search.ts, plus two additions this format defines: 臺 is folded to 台, and every Han, kana or Hangul
character becomes its own token. The server checks this port against its own code with the manifest's
`norm_vectors` before it uses a file, so the two can't drift apart silently.

    norm("Joe's Pizza")  -> "joes pizza"
    norm("臺北101")       -> "台 北 101"
    norm("Main St")      -> "main street"

Stop words stay in the indexed text. Queries drop them (`query_tokens`), as the server's `queryTokens` does.
"""
import unicodedata

NORM_VERSION = 1

# Code point ranges of the scripts the server's CJK_RUN pattern names, generated from Node's Unicode 16.0 data
# (\p{sc=Han} and so on). Python's re module has no script property, so the ranges are spelled out.
HAN = ((0x2E80, 0x2E99), (0x2E9B, 0x2EF3), (0x2F00, 0x2FD5), (0x3005, 0x3005), (0x3007, 0x3007), (0x3021, 0x3029),
       (0x3038, 0x303B), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFA6D), (0xFA70, 0xFAD9), (0x16FE2, 0x16FE3),
       (0x16FF0, 0x16FF1), (0x20000, 0x2A6DF), (0x2A700, 0x2B739), (0x2B740, 0x2B81D), (0x2B820, 0x2CEA1),
       (0x2CEB0, 0x2EBE0), (0x2EBF0, 0x2EE5D), (0x2F800, 0x2FA1D), (0x30000, 0x3134A), (0x31350, 0x323AF))
HIRAGANA = ((0x3041, 0x3096), (0x309D, 0x309F), (0x1B001, 0x1B11F), (0x1B132, 0x1B132), (0x1B150, 0x1B152),
            (0x1F200, 0x1F200))
KATAKANA = ((0x30A1, 0x30FA), (0x30FD, 0x30FF), (0x31F0, 0x31FF), (0x32D0, 0x32FE), (0x3300, 0x3357), (0xFF66, 0xFF6F),
            (0xFF71, 0xFF9D), (0x1AFF0, 0x1AFF3), (0x1AFF5, 0x1AFFB), (0x1AFFD, 0x1AFFE), (0x1B000, 0x1B000),
            (0x1B120, 0x1B122), (0x1B155, 0x1B155), (0x1B164, 0x1B167))
HANGUL = ((0x1100, 0x11FF), (0x302E, 0x302F), (0x3131, 0x318E), (0x3200, 0x321E), (0x3260, 0x327E), (0xA960, 0xA97C),
          (0xAC00, 0xD7A3), (0xD7B0, 0xD7C6), (0xD7CB, 0xD7FB), (0xFFA0, 0xFFBE), (0xFFC2, 0xFFC7), (0xFFCA, 0xFFCF),
          (0xFFD2, 0xFFD7), (0xFFDA, 0xFFDC))
THAI = ((0xE01, 0xE3A), (0xE40, 0xE5B))
PROLONGED = 0x30FC  # ー, listed on its own in CJK_RUN (script Common)


def _table(*groups):
    s = set()
    for g in groups:
        for a, b in g:
            s.update(range(a, b + 1))
    return frozenset(s)


_SPLIT = _table(HAN, HIRAGANA, KATAKANA, HANGUL) | {PROLONGED}   # one token per character
_THAI = _table(THAI)                                             # a Thai run stays one token, as in CJK_RUN
_CJK_CLASS = _SPLIT | _THAI

# Copied from apps/server/src/search.ts (ABBR, STOP) and rank.ts (PLACE_STOP). Changing any of them is a norm change.
ABBR = {
    "st": "street", "rd": "road", "ave": "avenue", "av": "avenue", "blvd": "boulevard", "bd": "boulevard",
    "bvd": "boulevard", "dr": "drive", "ln": "lane", "ct": "court", "pl": "place", "sq": "square", "hwy": "highway",
    "pkwy": "parkway", "tce": "terrace", "pde": "parade", "cres": "crescent", "jl": "jalan", "jln": "jalan",
    "gg": "gang", "avda": "avenida",
}
STOP = frozenset(["the", "of", "and", "no", "nr", "n", "de", "del", "della", "di", "da", "do", "la", "le", "les", "el",
                  "los", "las", "der", "die", "das"])
PLACE_STOP = frozenset(["the", "of", "and", "et", "de", "du", "d", "des", "la", "le", "les", "l", "del", "della", "di",
                        "da", "do", "dos", "das", "el", "los", "las", "der", "die", "den", "al"])
APOSTROPHES = "'’`"


def _is_ln(ch: str) -> bool:
    c = unicodedata.category(ch)
    return c[0] == "L" or c[0] == "N"


def _is_letter(ch: str) -> bool:
    return unicodedata.category(ch)[0] == "L"


def _apostrophes(s: str) -> str:
    """rank.ts words(): a possessive 's is joined ("Finn's" -> "Finns"); every other apostrophe becomes a space.

    Mirrors /(\\p{L})['’`]s(?![\\p{L}\\p{N}])/gu then /['’`]/g. Like the TypeScript, only a lower-case s counts.
    """
    out = []
    i, n = 0, len(s)
    free = 0  # a regex match can't reuse characters an earlier match consumed
    while i < n:
        ch = s[i]
        if ch in APOSTROPHES:
            if (i - 1 >= free and _is_letter(s[i - 1]) and i + 1 < n and s[i + 1] == "s"
                    and (i + 2 >= n or not _is_ln(s[i + 2]))):
                out.append("s")
                i += 2
                free = i
                continue
            out.append(" ")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def fold(s: str) -> str:
    """search.ts fold(), plus 臺 -> 台: accents off, NFKC, lower case, ß -> ss, non letters/digits -> one space."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not (0x300 <= ord(ch) <= 0x36F))
    s = unicodedata.normalize("NFKC", s).lower().replace("ß", "ss").replace("臺", "台")
    out, space = [], False
    for ch in s:
        if _is_ln(ch):
            out.append(ch)
            space = False
        elif not space:
            out.append(" ")
            space = True
    return "".join(out).strip()


def _runs(chunk: str):
    """Split a space-free chunk the way CJK_RUN does, then one token per Han/kana/Hangul character."""
    cur, cur_cjk = [], None
    for ch in chunk:
        cjk = ord(ch) in _CJK_CLASS
        if cur and cjk != cur_cjk:
            yield "".join(cur), cur_cjk
            cur = []
        cur.append(ch)
        cur_cjk = cjk
    if cur:
        yield "".join(cur), cur_cjk


def tokens(s: str) -> list:
    """All tokens of a text, stop words included."""
    if not s:
        return []
    t = _apostrophes(unicodedata.normalize("NFKC", s))
    out = []
    for chunk in fold(t).split():
        for run, cjk in _runs(chunk):
            if not cjk:
                out.append(ABBR.get(run, run))
                continue
            thai = []
            for ch in run:
                if ord(ch) in _THAI:
                    thai.append(ch)
                    continue
                if thai:
                    out.append("".join(thai))
                    thai = []
                out.append(ch)
            if thai:
                out.append("".join(thai))
    return out


def norm(s: str) -> str:
    """The text written to the full-text index, and the left side of every norm vector."""
    return " ".join(tokens(s))


def query_tokens(q: str) -> list:
    """Query side: stop words dropped (search.ts STOP), unless that leaves nothing."""
    all_ = tokens(q)
    kept = [t for t in all_ if t not in STOP]
    return kept or all_


def words(s: str) -> list:
    """rank.ts words(): tokens without PLACE_STOP words, unless that leaves nothing. Used for name comparisons."""
    all_ = tokens(s)
    kept = [t for t in all_ if t not in PLACE_STOP]
    return kept or all_


def is_split_char(t: str) -> bool:
    """A single Han, kana or Hangul character token."""
    return len(t) == 1 and ord(t) in _SPLIT


def has_latin_letter(s: str) -> bool:
    return any(("a" <= ch <= "z") or ("A" <= ch <= "Z") for ch in unicodedata.normalize("NFKD", s or ""))


# The norm vectors published in every manifest and committed as norm-vectors.json. The server refuses a file whose
# vectors its own normalizer doesn't reproduce.
VECTOR_INPUTS = [
    "Joe's Pizza", "ＡＢＣ", "臺北101", "A4鼎泰豐", "dell'Arte", "O'Brien's Pub", "Main St", "Finn's Beach Club",
    "SIA - Grill & Seafood Bar", "Café Ãçaí", "Straße", "L&L Hawaiian Barbecue", "In-N-Out Burger",
    "St. Regis Bali", "Jl. Pantai Berawa", "鼎泰豐 Din Tai Fung", "セブン-イレブン", "스타벅스 강남점",
    "ร้านกาแฟ", "Ｔａｉｐｅｉ　１０１", "7-ELEVEN 統一超商", "JOE'S PIZZA", "Bar d’Italia", "the Lawn",
    "Pura Luhur Uluwatu", "McDonald's", "Ōkura", "Ꭰ", "x́y", "²nd", "ﬁsh",
]


def vectors() -> list:
    return [[s, norm(s)] for s in VECTOR_INPUTS]
