"""Python port of looksLikeAddress() from apps/server/src/search.ts, used to drop place names that are really an
address (spec section 1.3 step 2). Each pattern is a direct translation:

- JavaScript \\d and \\b are ASCII-only, so they become [0-9] and an explicit ASCII word boundary.
- \\p{L} becomes [^\\W\\d_] (any letter); \\p{L}|\\p{N} becomes [^\\W_].
- \\p{sc=Han|Hiragana|Katakana} uses the ranges in textnorm.
- $ (end of input) becomes \\Z.

tests/test_addrlike.py runs the repo's own test table, and the build compares the port with the TypeScript on real
names when the app repo is available (see README).
"""
import re
import unicodedata

from textnorm import HAN, HIRAGANA, KATAKANA


def _cls(*groups):
    return "".join(f"\\U{a:08X}-\\U{b:08X}" for g in groups for a, b in g)


CJK = _cls(HAN, HIRAGANA, KATAKANA)
D = "[0-9]"
W = "[A-Za-z0-9_]"
B = f"(?:(?<={W})(?!{W})|(?<!{W})(?={W}))"   # JavaScript's ASCII \b
L = r"[^\W\d_]"                               # \p{L}
LN = r"[^\W_]"                                # [\p{L}\p{N}]
I = re.IGNORECASE

JP_BLOCK = re.compile(f"[{CJK}]\\s*{D}{{1,4}}(?:\\s*[-ー]\\s*{D}{{1,4}}){{1,3}}")
ZH_NO = re.compile(f"[路街道段巷弄]\\s*(?:[〇一二三四五六七八九十百]+段)?\\s*(?:{D}|[〇一二三四五六七八九十百])+\\s*(?:[號号]|巷|弄)")
JP_CHOME = re.compile(f"(?:{D}+|[一二三四五六七八九十]+)\\s*丁目(?![店駅])|{D}+\\s*番地|{D}+\\s*番\\s*{D}+")
KO_ROAD = re.compile(f"(?:[가-힣]|{D})(?:로|길)\\s*{D}+(?:-{D}+)?(?!\\s*(?:호점|층|호선|번\\s*출구|가?역))")
KO_LOT = re.compile(f"[가-힣](?:동|리|가)\\s*{D}+(?:-{D}+)?(?:\\s*번지)?(?!\\s*(?:호점|층|호선|번\\s*출구|{D}))")
KO_ROMAN = re.compile(f"{B}[a-z]+-(?:ro|gil|daero|dong)\\s*{D}+|{B}{D}+(?:-{D}+)?\\s+[a-z]+-(?:ro|gil|daero){B}", I)
TH_SCRIPT = re.compile(r"(?:ซอย|ถนน|ถ\.|ซ\.)")
THREE_PART = re.compile(f"(?<![0-9-]){D}{{1,4}}-{D}{{1,4}}-{D}{{1,4}}(?![0-9-])")
CHOME_ROMAN = re.compile(f"{B}{D}+\\s*-?\\s*chome{B}", I)
_STATES = "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
POSTAL = re.compile(f"〒\\s*{D}{{3}}-?{D}{{4}}|{B}[A-Z]{{1,2}}{D}(?:[A-Z]|{D})?\\s+{D}[A-Z]{{2}}{B}|{B}(?:{_STATES})\\s+{D}{{5}}(?:-{D}{{4}})?{B}")

TYPE_AFTER_NUMBER = ("street|st|road|rd|avenue|ave|av|boulevard|blvd|bd|bvd|lane|ln|drive|dr|court|ct|place|pl|terrace|"
                     "tce|parade|pde|highway|hwy|way|crescent|cres|close|square|sq|row|mews|grove|gardens|walk|parkway|"
                     "pkwy|circle|cir|trail|esplanade|quay|broadway|loop|plaza|alley|pike|expressway|expy|freeway|rue|"
                     "allée|allee|impasse|chemin|route|quai|cours|passage|faubourg|jalan|jl|jln")
NUMBER_FIRST = re.compile(
    f"(?:^|[\\s,]){D}+[a-z]?(?:[-/]{D}+[a-z]?)?,?\\s+(?:(?:{L}|[0-9'’.-])+\\s+){{0,4}}?(?:{TYPE_AFTER_NUMBER})\\.?(?:\\s|,|\\Z)", I)
TYPE_BEFORE_NAME = ("calle|c/|avenida|avda|av|paseo|plaza|carrer|ronda|via|viale|piazza|corso|vicolo|largo|rua|travessa|"
                    "praça|praca|alameda|estrada|rodovia|jalan|jl|jln|gang|gg|lorong|soi|thanon")
NAME_THEN_NUMBER = re.compile(
    f"(?:^|[\\s,])(?:{TYPE_BEFORE_NAME})\\.?\\s+(?:(?:{L}|['’.-])+\\s+){{1,5}}?(?:no\\.?\\s*|nr\\.?\\s*|n[º°o]\\.?\\s*|#\\s*)?{D}+[a-z]?{B}", I)
EN_NUMBER_LAST = re.compile(
    f"{L}{{2,}}\\s+(?:street|st|road|rd|avenue|ave|lane|ln|drive|dr|boulevard|blvd|way|place|square)\\.?\\s+{D}+[a-eg-z]?(?:\\s*,|\\s*\\Z)", I)
GRID = re.compile(f"{B}(?:calle|carrera|cra|cl|kr|avenida|av|diagonal|transversal)\\.?\\s*{D}+[a-z]?\\s*(?:#|no\\.?|n[º°])\\s*{D}+", I)
COMPOUND = re.compile(
    f"{L}{{2,}}[-\\s]?(?:straße|strasse|str\\.|platz|weg|gasse|allee|damm|ufer|chaussee|straat|gracht|laan|plein|kade|"
    f"singel|gatan|gata|vägen|vej|gade|veien)\\s+{D}+[a-z]?{B}", I)
LOOSE_STREET_WORD = re.compile(f"(?:^|[\\s,])(?:jl|jln|jalan|gg|gang|soi|thanon){B}\\.?", I)
POI_WORDS = re.compile(
    f"(?<!{LN})(?:hotel|hostel|resort|motel|inn|villas?|guest\\s*house|caf[eé]|coffee|kopi|restaurant|resto|warung|bistro|"
    f"brasserie|trattoria|pizzeria|pizza|sushi|ramen|bar|pub|brewery|diner|bakery|club|spa|salon|museum|gallery|temple|"
    f"pura|shrine|mall|store|shop|cinema|theat(?:er|re)|night\\s*market)(?!{LN})", I)
BEONJI = re.compile(f"{D}+\\s*번지")
HAS_DIGIT = re.compile(D)


def _js_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def looks_like_address(raw: str) -> bool:
    q = unicodedata.normalize("NFKC", raw).strip()
    if _js_len(q) < 3:
        return False
    has_digit = bool(HAS_DIGIT.search(q))
    if ZH_NO.search(q) or JP_CHOME.search(q):
        return True
    if not has_digit:
        return False
    if JP_BLOCK.search(q) or KO_ROAD.search(q) or KO_LOT.search(q) or BEONJI.search(q):
        return True
    if TH_SCRIPT.search(q) or THREE_PART.search(q) or CHOME_ROMAN.search(q) or POSTAL.search(q) or GRID.search(q):
        return True
    latin = (KO_ROMAN.search(q) or COMPOUND.search(q) or EN_NUMBER_LAST.search(q) or NUMBER_FIRST.search(q)
             or NAME_THEN_NUMBER.search(q) or LOOSE_STREET_WORD.search(q))
    return bool(latin) and not POI_WORDS.search(q)


def looks_like_phone(name: str) -> bool:
    """A name that is mostly a phone number ("+62 812-3456-7890", "WA 081234567890")."""
    alnum = [ch for ch in name if ch.isalnum()]
    digits = sum(1 for ch in alnum if "0" <= ch <= "9")
    return digits >= 7 and digits >= 0.8 * len(alnum)
