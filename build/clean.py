"""Step 2: clean-up and dedupe (spec section 1.3), in DuckDB with a few Python passes.

Order: 0 provider and licence, 1 traveler set v2 and the trims, 2 junk names, 3 agent pages, 4 duplicates,
4b misplaced named copies, 5 English names (labels). Steps 6 and 7 (common tokens, vocabulary) run in write_index.py
on the finished file. Every rule logs how many rows it removed.

Complexity: steps 0-2 are O(n). Steps 3 and 4 join each row only with rows in its own and the 8 neighbouring grid
cells (200 m and 100 m), so they cost O(n * k), where k is the number of rows per neighbourhood. Step 4b joins names
by a hash of their words (O(n + c), c candidate pairs) and counts address words around each candidate in a 2 km grid
(O(c * a), a rows per cell whose address holds the candidate's word).
"""
import csv
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rapidfuzz import fuzz  # noqa: E402

import sets  # noqa: E402
from addrlike import looks_like_address, looks_like_phone  # noqa: E402
from textnorm import has_latin_letter, norm, words  # noqa: E402

LICENCES = ("CDLA-Permissive-2.0", "Apache-2.0", "CC0-1.0")
OVERTURE_DATASETS = ("Overture", "Overture-signals")
# src column: 0 meta, 1 microsoft, 2 foursquare (Apache-2.0), 3 alltheplaces (CC0), 4 other CDLA
SRC_SQL = """CASE WHEN license = 'Apache-2.0' THEN 2
                 WHEN license = 'CC0-1.0' THEN 3
                 WHEN lower(dataset) = 'meta' THEN 0
                 WHEN lower(dataset) = 'microsoft' THEN 1
                 ELSE 4 END"""

# Area and category words removed for the "core" name comparison (dedupe rule 2).
AREA = {"bali", "indonesia", "seminyak", "canggu", "kuta", "legian", "ubud", "uluwatu", "denpasar", "nusa", "dua",
        "jimbaran", "sanur", "badung", "berawa", "pererenan", "bingin", "pecatu", "taiwan", "taipei", "台", "北", "高",
        "雄", "new", "york", "nyc", "manhattan", "brooklyn", "la", "sf", "usa"}
SOFT = AREA | {"beach", "club", "bar", "restaurant", "resto", "cafe", "coffee", "kitchen", "grill", "the", "and", "co",
               "hotel", "villa", "villas", "resort", "spa", "gym", "fitness", "studio", "store", "shop", "店", "門", "市",
               "party", "vip", "official", "branch"}
WE_MOVED = re.compile(r"\bwe('ve| have)? moved\b", re.IGNORECASE)
# Company rows: a registered business name (a business register's rows end in a legal form: "Boba King Llc", "The
# Georgian Motel Corporation"), holding companies and unions ("Grand Central Holdings", "Hotel Motel Trades Council"),
# and suppliers ("Taiwan Bubble Tea Supplier"). Shops, cafés and bars elsewhere often carry "LLC" too and stay.
LEGAL_FORM = re.compile(r"\b(llc|l\.l\.c|inc|incorporated|corp|corporation|ltd|llp|pllc|plc|gmbh)\b\.?\s*$|\b(llc|corporation|ltd)\b",
                        re.IGNORECASE)
REGISTER_DATASETS = ("BrightQuery",)
COMPANY = re.compile(r"\b(holdings|trades council)\b", re.IGNORECASE)
SUPPLIER = re.compile(r"\b(suppliers?|distributors?|manufacturers?|manufacturing|wholesalers?)\b", re.IGNORECASE)
# Keyword-stuffed names: 2+ "|" separators ("Analog Uluwatu | Cafe | Bar | Bistro"). The name is cut to its first part.
STUFFED_SEP = re.compile(r"[|｜]")

AGENT_M = 200.0
DUP_M = 100.0
M_PER_DEG = 111_320.0


def core(key: str) -> str:
    return " ".join(t for t in key.split() if t not in SOFT)


def destuff(name: str) -> str:
    """A keyword-stuffed name cut to its first part ("Analog Uluwatu | Cafe | Bar | Bistro" -> "Analog Uluwatu");
    any other name unchanged."""
    s = (name or "").strip()
    if len(STUFFED_SEP.findall(s)) < 2:
        return s
    return STUFFED_SEP.split(s, 1)[0].strip(" -–—/·,")


def company_reason(name: str, dataset=None):
    """Why a row is a company rather than a place to go, or None."""
    s = (name or "").strip()
    if dataset in REGISTER_DATASETS and LEGAL_FORM.search(s):
        return "junk_company"
    if COMPANY.search(s):
        return "junk_company"
    if SUPPLIER.search(s):
        return "junk_supplier"
    return None


def junk_reason(name: str, dataset=None):
    """Why a name is junk, or None. Rule 2 of the spec, plus company and supplier rows."""
    s = (name or "").strip()
    if not any(unicodedata.category(ch)[0] == "L" for ch in s):
        return "junk_no_letter"            # only digits, punctuation or symbols
    alnum = [ch for ch in s if unicodedata.category(ch)[0] in "LN"]
    if len(alnum) == 1 and has_latin_letter(alnum[0]):
        return "junk_single_latin"         # "A", "B." (a single CJK character is a real name and stays)
    if WE_MOVED.search(s):
        return "junk_we_moved"
    if looks_like_phone(s):
        return "junk_phone"
    if looks_like_address(s):
        return "junk_address"
    return company_reason(s, dataset)


def short_diff_blocks(ka: str, kb: str) -> bool:
    """True when two similar names differ only in a digit or one letter of a short word ("SMA" / "SMP", "1" / "2")."""
    ta, tb = ka.split(), kb.split()
    da = [t for t in ta if t not in tb]
    db = [t for t in tb if t not in ta]
    if any(any(ch.isdigit() for ch in t) for t in da + db):
        return True
    for x in da:
        for y in db:
            if len(x) == len(y) and len(x) <= 4 and sum(1 for p, q in zip(x, y) if p != q) == 1:
                return True
    return False


def fuzzy_same(ka: str, kb: str) -> bool:
    if len(ka) < 12 or len(kb) < 12:
        return False
    if fuzz.ratio(ka, kb) < 90:
        return False
    return not short_diff_blocks(ka, kb)


def same_name(ka: str, kb: str, ca: str, cb: str) -> bool:
    if ka == kb:
        return True
    if ca and ca == cb and len(ca.replace(" ", "")) >= 3:
        return True
    return fuzzy_same(ka, kb)


def _grid(cell_m: float) -> str:
    """Grid columns for a neighbourhood join: 1-degree latitude band, row and column of a cell_m metre cell.

    Longitude cells are widened by 1/cos(latitude) of the band so that a cell is about cell_m wide everywhere, and all
    cells get a 10% margin so that the band's cos(latitude) error never makes a cell narrower than cell_m. Pairs that
    straddle a whole-degree latitude line are not compared (about 0.1% of pairs).
    """
    d = 1.1 * cell_m / M_PER_DEG
    return (f"floor(lat)::INTEGER AS band, floor(lat / {d})::BIGINT AS cy, "
            f"floor(lng * cos(radians(floor(lat) + 0.5)) / {d})::BIGINT AS cx")


HAV = ("2 * 6371000 * asin(sqrt(pow(sin(radians(b.lat - a.lat) / 2), 2) + cos(radians(a.lat)) * cos(radians(b.lat)) "
       "* pow(sin(radians(b.lng - a.lng) / 2), 2)))")


def _neighbours(table: str, cond: str, cols: str) -> str:
    return f"""SELECT {cols} FROM {table} a
      JOIN (SELECT t.*, dy, dx FROM {table} t, (SELECT unnest([-1, 0, 1]) dy), (SELECT unnest([-1, 0, 1]) dx)) b
        ON b.band = a.band AND b.cy + b.dy = a.cy AND b.cx + b.dx = a.cx
      WHERE {cond}"""


def _py_pass(con, select_sql: str, fn, out_cols: list, work: str, name: str):
    """Run fn over rows of select_sql in Python and load the results back as table `name` (via a temp CSV)."""
    path = os.path.join(work, f"{name}.csv")
    cur = con.execute(select_sql)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        while True:
            rows = cur.fetchmany(100_000)
            if not rows:
                break
            for r in rows:
                out = fn(r)
                if out is not None:
                    w.writerow(out)
    types = ", ".join(f"'{c}': '{t}'" for c, t in out_cols)
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM read_csv('{path}', header=false, "
                f"columns={{{types}}}, quote='\"', escape='\"', auto_detect=false)")
    os.remove(path)


def clean(con, parquet: str, region: str, work: str, labels: dict = None, log=print) -> dict:
    """Leaves the cleaned rows in table `fin`. Returns the per-step counts."""
    st = {}
    cols = {c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet}')").fetchall()}
    addr_region = "addr_region" if "addr_region" in cols else "NULL::VARCHAR AS addr_region"  # extracts before 2026-10-10
    con.execute(f"""CREATE OR REPLACE TABLE r AS
      SELECT row_number() OVER () AS rid, gers, trim(name) AS name, name_en, basic_category, tax_primary,
             tax_hierarchy, tax_hierarchy[1] AS top, confidence, addr, locality, {addr_region}, country,
             nullif(trim(brand), '') AS brand, brand_wikidata, contacts, lng, lat,
             list_filter(sources, s -> s.dataset NOT IN {OVERTURE_DATASETS})[1] AS prov, sources
      FROM read_parquet('{parquet}')""")
    con.execute("""CREATE OR REPLACE TABLE r AS SELECT * EXCLUDE (prov, sources),
        coalesce(prov.dataset, sources[1].dataset) AS dataset, coalesce(prov.license, sources[1].license) AS license
      FROM r""")
    st["extracted"] = con.execute("SELECT count(*) FROM r").fetchone()[0]
    st["no_provider_entry"] = con.execute(
        f"SELECT count(*) FROM r WHERE dataset IN {OVERTURE_DATASETS} OR dataset IS NULL").fetchone()[0]
    log(f"{region}: extracted {st['extracted']:,} (confidence >= 0.70, open, named)")

    # 1. traveler set v2, trim, no category
    con.execute(f"""CREATE OR REPLACE TABLE r AS SELECT *,
        CASE WHEN name = '' THEN 'junk_empty'
             WHEN {sets.no_category_sql()} THEN CASE WHEN {sets.no_category_kept_sql()} THEN NULL ELSE 'no_category' END
             WHEN NOT {sets.traveler_sql()} THEN 'not_traveler'
             WHEN {sets.trimmed_sql()} THEN 'trim'
             WHEN {'TRUE' if region == 'us' else 'FALSE'} AND {sets.us_landmark_sql()} THEN 'trim_us_landmark'
             ELSE NULL END AS reason,
        (basic_category IN {sets._in(sets.TRIM)}) AS in_trim_list
      FROM r""")
    for reason, n in con.execute("SELECT reason, count(*) FROM r WHERE reason IS NOT NULL GROUP BY 1").fetchall():
        st[f"removed_{reason}"] = n
    st["traveler_set"] = con.execute(
        "SELECT count(*) FROM r WHERE reason IS NULL OR reason LIKE 'trim%'").fetchone()[0]
    st["trim_exceptions_kept"] = con.execute("SELECT count(*) FROM r WHERE reason IS NULL AND in_trim_list").fetchone()[0]
    st["no_category_kept"] = con.execute(
        f"SELECT count(*) FROM r WHERE reason IS NULL AND {sets.no_category_sql()}").fetchone()[0]
    con.execute("CREATE OR REPLACE TABLE s AS SELECT * EXCLUDE (reason, in_trim_list) FROM r WHERE reason IS NULL")
    con.execute("DROP TABLE r")
    st["after_traveler"] = con.execute("SELECT count(*) FROM s").fetchone()[0]
    log(f"{region}: traveler set {st['traveler_set']:,}; trim removed {st.get('removed_trim', 0):,} "
        f"(kept {st['trim_exceptions_kept']:,} rentals/post offices); US landmarks {st.get('removed_trim_us_landmark', 0):,}; "
        f"not traveler {st.get('removed_not_traveler', 0):,}; "
        f"no category {st.get('removed_no_category', 0):,} (kept {st['no_category_kept']:,}); "
        f"left {st['after_traveler']:,}")

    # 2. junk names, plus the name keys the next steps compare
    def keys(r):
        rid, name, dataset = r
        short = destuff(name)
        why = junk_reason(short, dataset) or (company_reason(name, dataset) if short != name.strip() else None)
        if why:
            return (rid, why, None, None, 0, None)
        k = " ".join(words(short))
        return (rid, None, k, core(k), len(k.split()), short if short != name else None)
    _py_pass(con, "SELECT rid, name, dataset FROM s", keys,
             [("rid", "BIGINT"), ("why", "VARCHAR"), ("k", "VARCHAR"), ("core", "VARCHAR"), ("nw", "INTEGER"),
              ("short", "VARCHAR")],
             work, "keys")
    for why, n in con.execute("SELECT why, count(*) FROM keys WHERE why IS NOT NULL GROUP BY 1").fetchall():
        st[f"removed_{why}"] = n
    st["names_destuffed"] = con.execute("SELECT count(*) FROM keys WHERE why IS NULL AND short IS NOT NULL").fetchone()[0]
    con.execute(f"""CREATE OR REPLACE TABLE s AS SELECT s.* REPLACE (coalesce(keys.short, s.name) AS name),
        keys.k, keys.core, keys.nw, {_grid(AGENT_M)}
      FROM s JOIN keys USING (rid) WHERE keys.why IS NULL""")
    con.execute("DROP TABLE keys")
    st["after_junk"] = con.execute("SELECT count(*) FROM s").fetchone()[0]
    log(f"{region}: junk names removed {sum(v for k, v in st.items() if k.startswith('removed_junk')):,} "
        f"(companies {st.get('removed_junk_company', 0):,}, suppliers {st.get('removed_junk_supplier', 0):,}); "
        f"stuffed names cut {st['names_destuffed']:,}; left {st['after_junk']:,}")

    # 3. agent pages: "<1-2 words> <full name of another row within 200 m>", same basic_category,
    #    the other row's confidence >= this row's - 0.2
    con.execute("CREATE OR REPLACE TABLE agent AS " + _neighbours("s", f"""a.rid <> b.rid
        AND b.nw >= 2 AND a.nw - b.nw BETWEEN 1 AND 2 AND suffix(a.k, ' ' || b.k)
        AND a.basic_category = b.basic_category AND b.confidence >= a.confidence - 0.2
        AND {HAV} <= {AGENT_M}""", "DISTINCT a.rid AS rid"))
    st["removed_agent_page"] = con.execute("SELECT count(*) FROM agent").fetchone()[0]
    con.execute("CREATE OR REPLACE TABLE s AS SELECT * FROM s WHERE rid NOT IN (SELECT rid FROM agent)")
    con.execute("DROP TABLE agent")
    st["after_agent"] = con.execute("SELECT count(*) FROM s").fetchone()[0]
    log(f"{region}: agent pages removed {st['removed_agent_page']:,}; left {st['after_agent']:,}")

    # 4. duplicates within 100 m: candidates in SQL (exact key, core key, or a loose edit-distance prefilter that is a
    #    superset of fuzz.ratio >= 90), the exact rule and the greedy absorb in Python.
    con.execute(f"CREATE OR REPLACE TABLE s AS SELECT * EXCLUDE (band, cy, cx), {_grid(DUP_M)} FROM s")
    con.execute("CREATE OR REPLACE TABLE pairs AS " + _neighbours("s", f"""a.rid < b.rid
        AND (a.top IS NULL OR b.top IS NULL OR a.top = b.top)
        AND (a.k = b.k OR (a.core = b.core AND length(replace(a.core, ' ', '')) >= 3)
             OR (length(a.k) >= 12 AND length(b.k) >= 12
                 AND levenshtein(a.k, b.k) <= 0.2 * greatest(length(a.k), length(b.k))))
        AND {HAV} <= {DUP_M}""", "DISTINCT a.rid AS ra, b.rid AS rb"))
    cand = con.execute("""SELECT p.ra, p.rb, a.k, b.k, a.core, b.core,
          a.confidence, a.contacts, lower(a.dataset) = 'meta', b.confidence, b.contacts, lower(b.dataset) = 'meta',
          b.name
        FROM pairs p JOIN s a ON a.rid = p.ra JOIN s b ON b.rid = p.rb""").fetchall()
    con.execute("DROP TABLE pairs")
    st["dedupe_candidate_pairs"] = len(cand)
    rank, partners = {}, {}
    names = {}
    for ra, rb, ka, kb, ca, cb, conf_a, cont_a, meta_a, conf_b, cont_b, meta_b, name_b in cand:
        if not same_name(ka, kb, ca or "", cb or ""):
            continue
        rank[ra] = (-conf_a, -(cont_a or 0), bool(meta_a), ra)
        rank[rb] = (-conf_b, -(cont_b or 0), bool(meta_b), rb)
        partners.setdefault(ra, []).append(rb)
        partners.setdefault(rb, []).append(ra)
    if cand:
        for ra, rb, *_rest, name_b in cand:
            names[rb] = name_b
    gone, absorbed = set(), []
    for i in sorted(rank, key=rank.get):        # highest confidence, then most contacts, then non-meta
        if i in gone:
            continue
        for j in partners.get(i, ()):
            if j not in gone and rank[j] > rank[i]:
                gone.add(j)
                absorbed.append((i, j))
    st["removed_duplicate"] = len(gone)
    need = {j for _, j in absorbed if j not in names}
    if need:
        con.execute("CREATE OR REPLACE TEMP TABLE needn AS SELECT unnest(?::BIGINT[]) AS rid", [sorted(need)])
        names.update(dict(con.execute("SELECT rid, name FROM s JOIN needn USING (rid)").fetchall()))
    path = os.path.join(work, "absorbed.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for i, j in absorbed:
            w.writerow((i, j, names[j]))
    con.execute(f"""CREATE OR REPLACE TABLE absorbed AS SELECT * FROM read_csv('{path}', header=false,
        columns={{'keep': 'BIGINT', 'gone': 'BIGINT', 'name': 'VARCHAR'}}, quote='"', escape='"', auto_detect=false)""")
    os.remove(path)
    con.execute("""CREATE OR REPLACE TABLE s AS
      SELECT s.*, ab.names AS absorbed FROM s
      LEFT JOIN (SELECT keep, list(name ORDER BY gone) AS names FROM absorbed GROUP BY keep) ab ON ab.keep = s.rid
      WHERE s.rid NOT IN (SELECT gone FROM absorbed)""")
    con.execute("DROP TABLE absorbed")
    st["after_dedupe"] = con.execute("SELECT count(*) FROM s").fetchone()[0]
    log(f"{region}: duplicates absorbed {st['removed_duplicate']:,} "
        f"({100 * st['removed_duplicate'] / max(1, st['after_agent']):.2f}%); left {st['after_dedupe']:,}")

    # 4b. misplaced named copies
    st["removed_misplaced_copy"], st["misplaced_copy_examples"] = misplaced_copies(
        con, os.path.join(work, f"misplaced-{region}.tsv"))
    st["after_misplaced"] = con.execute("SELECT count(*) FROM s").fetchone()[0]
    log(f"{region}: misplaced named copies removed {st['removed_misplaced_copy']:,}; left {st['after_misplaced']:,}"
        + (f" (e.g. {st['misplaced_copy_examples'][:3]})" if st["misplaced_copy_examples"] else ""))

    # 0 (gate). Licence of every kept row
    bad = con.execute(f"SELECT license, count(*) FROM s WHERE license IS NULL OR license NOT IN {LICENCES} "
                      "GROUP BY 1").fetchall()
    st["licence_gate"] = "ok" if not bad else f"FAIL {bad}"
    st["datasets"] = {d: n for d, n in con.execute(
        "SELECT dataset, count(*) FROM s GROUP BY 1 ORDER BY 2 DESC, 1").fetchall()}
    st["licences"] = {d: n for d, n in con.execute(
        "SELECT license, count(*) FROM s GROUP BY 1 ORDER BY 2 DESC").fetchall()}

    # 5. English names for Taiwan brands (labels passed in by build.py)
    st["label_rows"] = 0
    if labels:
        con.execute("CREATE OR REPLACE TEMP TABLE lab(qid VARCHAR, label VARCHAR)")
        con.executemany("INSERT INTO lab VALUES (?, ?)", [(q, l) for q, l in labels.items() if l])
    else:
        con.execute("CREATE OR REPLACE TEMP TABLE lab(qid VARCHAR, label VARCHAR)")
    con.execute(f"""CREATE OR REPLACE TABLE fin AS
      SELECT gers, name, name_en, brand, absorbed,
             CASE WHEN brand_wikidata IS NOT NULL AND NOT regexp_matches(strip_accents(name), '[A-Za-z]')
                  THEN lab.label END AS label,
             basic_category AS cat,
             nullif(trim(addr), '') AS addr, nullif(trim(locality), '') AS locality,
             nullif(trim(addr_region), '') AS addr_region,
             lat, lng, CAST(round(confidence * 100) AS INTEGER) AS conf, {SRC_SQL} AS src, dataset, license
      FROM s LEFT JOIN lab ON lab.qid = s.brand_wikidata""")
    con.execute("DROP TABLE s")
    st["label_rows"] = con.execute("SELECT count(*) FROM fin WHERE label IS NOT NULL").fetchone()[0]
    st["rows"] = con.execute("SELECT count(*) FROM fin").fetchone()[0]
    log(f"{region}: licence gate {st['licence_gate']}; English brand labels on {st['label_rows']:,} rows; "
        f"final {st['rows']:,}")
    return st


# Words a place name ends with that say what it is rather than where it is ("Joe's Pizza Express").
NOT_PLACE_WORDS = {"restaurant", "resto", "cafe", "coffee", "kitchen", "grill", "bistro", "express", "store", "shop",
                   "shoppe", "market", "branch", "outlet", "center", "centre", "plaza", "mall", "bar", "club", "lounge",
                   "house", "hotel", "motel", "hostel", "villa", "villas", "resort", "suites", "beach", "street", "road",
                   "avenue", "jalan", "gang", "official", "original", "new", "north", "south", "east", "west",
                   "downtown", "uptown", "midtown", "city", "town", "place", "station", "service", "services", "school",
                   "studio", "salon", "spa", "gym", "fitness", "garden", "gardens", "park", "lodge", "inn", "tavern",
                   "pizza", "pizzeria", "bakery", "deli", "diner", "cantina", "taqueria", "grocery", "mart", "market",
                   "foods", "food", "wine", "wines", "liquor", "liquors", "pub", "brewery", "tours", "tour", "travel",
                   "rental", "rentals", "boutique", "gallery", "museum", "church", "temple", "warung", "hall", "room",
                   "rooms", "too", "two", "express", "plus", "group", "company", "team", "bros", "brothers", "sons",
                   "family", "kids", "dog", "dogs", "pets", "auto", "cars", "nails", "beauty", "hair", "barber",
                   "barbershop", "tattoo", "clinic", "dental", "medical", "pharmacy", "bank", "atm", "cinema", "theater",
                   "theatre", "arena", "stadium", "field", "court", "courts", "pool", "academy", "institute", "island",
                   "islands", "lake", "river", "bay", "point", "hills", "heights", "valley", "springs", "falls",
                   "boardwalk", "marina", "harbor", "harbour", "pier", "wharf", "crossing", "landing", "king", "queen",
                   "palace", "square", "trail", "mountain", "peak", "creek"}
COPY_CELL_DEG = 0.009     # about 1 km; the neighbourhood of a point is the 5 x 5 cells around it (about 5 km across)
COPY_MIN_KM = 3.0         # a copy is at least this far from every row of its base name
COPY_MAX_KM = 50.0        # ... and its base name has a row within this distance
COPY_BASE_SHARE = 0.05    # 5%+ of the addresses around the base row hold the word
COPY_OWN_RATIO = 20       # ... and at most 1/20 of that share around the copy


def misplaced_copies(con, audit_path=None):
    """Step 4b: drop "<another row's name> <place word>" pinned away from that place.

    "Single Fin Uluwatu" sits in Kuta, 14 km from Single Fin in Uluwatu: 25% of the addresses around Single Fin say
    Uluwatu, 0.2% of those around the Kuta pin do. A row qualifies when its name is the name of another row (2+ words)
    plus one Latin word w of 4+ letters that isn't a category, direction or business word, no row of the base name lies
    within 3 km, the nearest lies within 50 km, w isn't in the row's own address, at least 5% of the addresses around the
    base row hold w (3 or more), and around the row at most a twentieth of that share. A branch that really is in
    "Seminyak" has neighbours whose addresses say Seminyak, and stays; a common street word ("Sari", "Rama") is in too
    few addresses around the base row. Chain branches (a brand with 3+ rows in the region) stay.
    Returns (removed, a few "name -> base name, km" examples); every removal goes to audit_path.
    """
    con.execute(f"""CREATE OR REPLACE TEMP TABLE mc_r AS
      SELECT rid, k, lat, lng,
             regexp_extract(k, '^(.*) ([a-z]{{4,}})$', 1) AS prefix, regexp_extract(k, ' ([a-z]{{4,}})$', 1) AS w,
             regexp_split_to_array(lower(strip_accents(coalesce(addr, '') || ' ' || coalesce(locality, ''))), '[^a-z0-9]+') AS own
      FROM s WHERE nw >= 3 AND regexp_matches(k, ' [a-z]{{4,}}$')
        AND (brand IS NULL OR brand NOT IN (SELECT brand FROM s WHERE brand IS NOT NULL GROUP BY 1 HAVING count(*) > 2))""")
    con.execute("DELETE FROM mc_r WHERE list_contains(own, w) OR w IN (SELECT unnest(?::VARCHAR[]))",
                [sorted(NOT_PLACE_WORDS)])
    hav = HAV.replace("a.", "r.")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE mc_pairs AS
      SELECT r.rid, r.w, r.lat, r.lng, b.rid AS bid, b.lat AS blat, b.lng AS blng, {hav} AS d
      FROM mc_r r JOIN s b ON b.k = r.prefix AND b.rid <> r.rid AND b.nw >= 2
      WHERE abs(b.lat - r.lat) < {COPY_MAX_KM / 111.0}""")
    con.execute("""CREATE OR REPLACE TEMP TABLE mc_best AS
      SELECT rid, w, lat, lng, bid, blat, blng, d FROM mc_pairs
      QUALIFY row_number() OVER (PARTITION BY rid ORDER BY d) = 1""")
    con.execute(f"DELETE FROM mc_best WHERE d < {COPY_MIN_KM * 1000} OR d > {COPY_MAX_KM * 1000}")
    n_cand = con.execute("SELECT count(*) FROM mc_best").fetchone()[0]
    removed, examples = 0, []
    if n_cand:
        d = COPY_CELL_DEG

        # addresses per cell, and per cell and candidate word
        con.execute(f"""CREATE OR REPLACE TEMP TABLE mc_cells AS
          SELECT floor(lat / {d})::BIGINT AS cy, floor(lng * cos(radians(lat)) / {d})::BIGINT AS cx, count(*) AS n
          FROM s WHERE addr IS NOT NULL GROUP BY 1, 2""")
        con.execute(f"""CREATE OR REPLACE TEMP TABLE mc_words AS
          SELECT w, floor(lat / {d})::BIGINT AS cy, floor(lng * cos(radians(lat)) / {d})::BIGINT AS cx, count(*) AS n FROM (
            SELECT lat, lng, unnest(regexp_split_to_array(lower(strip_accents(coalesce(addr, ''))), '[^a-z0-9]+')) AS w
            FROM s WHERE addr IS NOT NULL)
          WHERE w IN (SELECT DISTINCT w FROM mc_best) GROUP BY 1, 2, 3""")

        def around(lat: str, lng: str, table: str, word: bool) -> str:
            cy, cx = f"floor(c.{lat} / {d})::BIGINT", f"floor(c.{lng} * cos(radians(c.{lat})) / {d})::BIGINT"
            return (f"(SELECT coalesce(sum(n), 0) FROM {table} x WHERE x.cy BETWEEN {cy} - 2 AND {cy} + 2 "
                    f"AND x.cx BETWEEN {cx} - 2 AND {cx} + 2{' AND x.w = c.w' if word else ''})")
        con.execute(f"""CREATE OR REPLACE TEMP TABLE mc_ev AS
          SELECT c.*, {around('lat', 'lng', 'mc_cells', False)} AS own_n, {around('lat', 'lng', 'mc_words', True)} AS own_w,
                 {around('blat', 'blng', 'mc_cells', False)} AS base_n, {around('blat', 'blng', 'mc_words', True)} AS base_w
          FROM mc_best c""")
        con.execute(f"""CREATE OR REPLACE TEMP TABLE mc_gone AS SELECT rid, bid, d, own_n, own_w, base_n, base_w FROM mc_ev
          WHERE base_w >= 3 AND base_w >= {COPY_BASE_SHARE} * base_n
            AND own_w * {COPY_OWN_RATIO} * base_n <= base_w * own_n""")
        gone = con.execute("SELECT r.name, b.name, g.d, r.lat, r.lng, b.lat, b.lng, g.own_w, g.own_n, g.base_w, g.base_n "
                           "FROM mc_gone g JOIN s r ON r.rid = g.rid JOIN s b ON b.rid = g.bid ORDER BY g.d").fetchall()
        removed = len(gone)
        examples = [f"{a} -> {b}, {dist / 1000:.1f} km" for a, b, dist, *_ in gone[:12]]
        if audit_path:
            with open(audit_path, "w", encoding="utf-8") as f:
                f.write("name\tbase name\tkm\tlat\tlng\tbase lat\tbase lng\tword around it\tword around base\n")
                for a, b, dist, la, lo, bla, blo, ow, on, bw, bn in gone:
                    f.write(f"{a}\t{b}\t{dist / 1000:.1f}\t{la:.5f}\t{lo:.5f}\t{bla:.5f}\t{blo:.5f}\t{ow}/{on}\t{bw}/{bn}\n")
        con.execute("CREATE OR REPLACE TABLE s AS SELECT * FROM s WHERE rid NOT IN (SELECT rid FROM mc_gone)")
        for t in ("mc_cells", "mc_words", "mc_ev", "mc_gone"):
            con.execute(f"DROP TABLE {t}")
    for t in ("mc_r", "mc_pairs", "mc_best"):
        con.execute(f"DROP TABLE {t}")
    return removed, examples


def taiwan_label_ids(con, parquet: str) -> list:
    """Brand ids of rows whose name has no Latin letter: the only rows that get a Wikidata label."""
    return [r[0] for r in con.execute(f"""SELECT DISTINCT brand_wikidata FROM read_parquet('{parquet}')
        WHERE brand_wikidata IS NOT NULL AND NOT regexp_matches(strip_accents(name), '[A-Za-z]')""").fetchall()]


def build_alt(name: str, name_en, brand, absorbed, label) -> list:
    """alt = brand (if not already in the name) + English name variants + absorbed names + Wikidata label.

    Parts whose normalized text is already in the name, or repeats an earlier part, are left out.
    """
    nn = f" {norm(name)} "
    seen, out = set(), []

    def add(s):
        if not s:
            return
        s = s.strip()
        n = norm(s)
        if not n or n in seen or f" {n} " in nn:
            return
        seen.add(n)
        out.append(s)

    add(brand)
    for v in name_en or []:
        add(v)
    for v in absorbed or []:
        add(v)
    add(label)
    return out
