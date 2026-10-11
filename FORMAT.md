# Format 1, norm 1: the contract between this builder and the Tripweave server

This file is the reader's specification. The server (`apps/server/src/overture/`) implements against it. Anything
not written here is not promised.

- **`format`** changes when a table, a column, the tokenizer, the cell scheme or the meaning of a meta key changes.
  A reader refuses a file or manifest whose `format` it doesn't know.
- **`norm`** changes when the text normalization changes (section 4). A reader refuses a file whose `norm` it doesn't
  know, and refuses a manifest whose `norm_vectors` its own normalizer doesn't reproduce exactly.

Current values: `format = 1`, `norm = 1`.

## 1. Release files

A release is a GitHub Release in `eugene08976/tripweave-places`, tag `places-<overture_release>-b<n>`, for example
`places-2026-09-23.1-b1`. Assets:

| Asset | What |
|---|---|
| `bali.sqlite`, `taiwan.sqlite`, `us.sqlite` | One SQLite file per region (sections 2 and 3) |
| `manifest.json` | Uploaded last, before the release leaves draft (section 6) |
| `ATTRIBUTION.md` | Sources, row counts per dataset, the change notice |
| `CDLA-Permissive-2.0.txt`, `Apache-2.0.txt`, `foursquare-NOTICE.txt` | Licence texts |

- Latest manifest: `https://github.com/eugene08976/tripweave-places/releases/latest/download/manifest.json`
- Files of a release: `https://github.com/eugene08976/tripweave-places/releases/download/<tag>/<name>`.
  Always download by tag, never from `latest/`, so files from two releases can't mix.
- The last 3 releases are kept; older ones are deleted.

## 2. SQLite file

- Written by SQLite 3.53.2 (the version is in `meta.sqlite_version`), page size 4096, rollback-journal file format
  (header bytes 18 and 19 are 1, never WAL). `PRAGMA journal_mode` reads `delete`.
- After writing: FTS5 `optimize`, `PRAGMA integrity_check` = ok, FTS5 `integrity-check`, `ANALYZE` (so
  `sqlite_stat1` and `sqlite_stat4` exist), `VACUUM`, then reopened read-only.
- The file never changes after it is published. Open it read-only (`{ readonly: true, fileMustExist: true }` in
  better-sqlite3). The server's choice of `mmap_size` and `cache_size` doesn't affect correctness.

### Tables

```sql
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
```

### `place`

| Column | Meaning |
|---|---|
| `id` | Position in z-order, 1..rows, no gaps (section 3). Equals `place_fts.rowid`. |
| `gers` | Overture GERS id as 16 raw UUID bytes. The canonical string is the lower-case hyphenated UUID (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`, 36 characters), so `externalId = "overture:" + uuid` is 45 characters. GERS ids can change between releases: an opaque reference only. |
| `name` | Overture `names.primary`, trimmed. Shown as is. |
| `alt` | Other names, joined with `\|`, or NULL: the brand (when not already in the name), Overture's English name variants (`names.rules` with language `en`), names of duplicate rows merged into this one, and, in Taiwan, the brand's English Wikidata label for names with no Latin letter. A part whose normalized text is already inside the normalized name, or repeats an earlier part, is left out. |
| `cat` | Overture `basic_category` (for example `restaurant`, `beach`, `gym`), or NULL. Stored only, not in the full-text index. |
| `addr` | The street address and the town: `addresses[1].freeform`, then `, ` and `addresses[1].locality` (in the US `locality, XX` with the state from `addresses[1].region` when it is a 2-letter code). A Chinese street address (starting with a Han character) gets the town in front with no separator (`台北市信義路二段194號`). The town is left out when the street address already contains it. Whitespace collapsed; at most 80 characters, the street part cut first (at a space when possible, trailing `,;-` removed) so the town stays. Only one of the two: that one. Neither: NULL. The country is not included. |
| `lat`, `lng` | WGS84 degrees, from `bbox.ymin` / `bbox.xmin` (a place is a point). |
| `conf` | Overture confidence × 100, rounded, 70..100. |
| `src` | The provider's licence class: `0` meta, `1` Microsoft, `2` Foursquare (Apache-2.0), `3` AllThePlaces (CC0-1.0), `4` another CDLA-Permissive-2.0 provider (BrightQuery, DAC, PinMeTo, RenderSEO, ...). |
| `flags` | Bit 0: the row has a brand (a chain branch). Other bits are 0 in format 1. |

### `place_fts`

- Contentless FTS5 (`content=''`): the index holds terms only; read columns from `place` with
  `JOIN place p ON p.id = place_fts.rowid`.
- Column `name` = `norm(place.name)`. Column `alt` = the `norm()` of each `alt` part, joined with single spaces.
- The indexed text is already normalized (section 4): lower case, accents off, tokens separated by one space. The
  tokenizer `unicode61 remove_diacritics 2` then sees exactly those tokens.
- `prefix='2 3'`: prefix queries of 2 and 3 characters (`"co"*`) are served by the prefix index. A longer prefix
  (`"stor"*`) reads the whole term range of the file: use the vocab expansion in section 5 instead.
- `cat` is not indexed.

### `zcell`

- `cell`: a level-12 cell id (section 3). One row per non-empty cell.
- `lo`, `hi`: the first and last `place.id` in that cell. Every id in `[lo, hi]` belongs to the cell; the spans of all
  cells are disjoint, and ordered by `cell` they tile `1..rows` without gaps.

### `vocab`

- Every term in `place_fts` (both columns) with `df`, the number of rows containing it. Built with `fts5vocab` (the
  `row` type), so a term is exactly a tokenizer output: one normalized token.
- Used for the last-word expansion (section 5). Half-typed words match few terms: in the US file "stor" 207 terms.

### `meta`

All values are TEXT.

| Key | Value |
|---|---|
| `format` | `"1"` |
| `norm` | `"1"` |
| `region` | `bali`, `taiwan` or `us` |
| `country` | `ID`, `TW`, `US` |
| `overture_release` | for example `2026-09-23.1` |
| `built_at` | UTC, ISO 8601 with `Z`, for example `2026-10-11T00:09:48Z` |
| `sqlite_version` | the writing SQLite, for example `3.53.2` |
| `level` | `"12"` |
| `min_conf` | `"0.70"` |
| `set` | `traveler-v2` |
| `rows` | row count of `place` (also `max(zcell.hi)`) |
| `bboxes` | JSON `[[west, south, east, north], ...]` in degrees: the region's extract boxes (section 7) |
| `common_tokens` | JSON array of terms, most frequent first (ties by term): every vocab term with `df >= max(500, ceil(rows / 100))`, **except single Han, kana or Hangul characters** (see below) |
| `datasets` | JSON `{dataset: rows}`, the provider dataset named in each row's sources |
| `notice` | `Derived from Overture Maps data: filtered, de-duplicated, re-indexed by Tripweave` |

Why single CJK characters are not in `common_tokens`: a CJK word is a phrase of one-character tokens. In Taiwan
茶, 湯, 會, 全 and 家 each pass the frequency rule, so with them in the list a search for 茶湯會 (a tea chain) or
全家 (FamilyMart) would be skipped as if it were a bare category word.

## 3. Cells and z-order

```
x12 = clamp(floor((lng + 180) / 360 * 4096), 0, 4095)
y12 = clamp(floor((lat +  90) / 180 * 4096), 0, 4095)
cell = interleave(x12, y12)      // bit i of x12 -> bit 2i, bit i of y12 -> bit 2i+1; 24 bits, safe in 32-bit JS ops
```

```ts
function interleave(x: number, y: number): number {
  let c = 0;
  for (let i = 0; i < 12; i++) c |= (((x >> i) & 1) << (2 * i)) | (((y >> i) & 1) << (2 * i + 1));
  return c;
}
```

- A cell is 360/4096 = 0.0879° of longitude by 180/4096 = 0.0439° of latitude, about 7 × 5 km at mid latitudes.
- Rows are sorted by the 62-bit Morton code of the point (31 bits per axis, same interleave with
  `floor((lng + 180) / 360 * 2^31)` and `floor((lat + 90) / 180 * 2^31)`, clamped), then by GERS id. The cell id is
  the top 24 bits of that code, so each cell's rows are one contiguous id span, recorded in `zcell`. Ids within a
  cell follow the finer Morton order; a reader needs only the cell.
- Parity checks a reader can run on fixtures: `cell(-90, -180) = 0`; `cell(90, 180) = 2^24 - 1` (clamped);
  `x12(179.9) = 4094`, `x12(179.99) = 4095`; for every row, `zcell[cell(lat, lng)].lo <= id <= .hi`.

## 4. Text normalization, norm 1

The TypeScript in the app repo is the reference: `words()` in `apps/server/src/rank.ts` up to (not including) its
stop-word filter, `fold()` and `tokens()` in `apps/server/src/search.ts`, plus two additions. In order:

1. `s.normalize("NFKC")`
2. Possessive: `replace(/(\p{L})['’`]s(?![\p{L}\p{N}])/gu, "$1s")` ("Finn's" → "Finns"). As in the TypeScript, only
   a lower-case `s` counts: "JOE'S PIZZA" becomes "joe s pizza". Then every remaining `'`, `’` or `` ` `` becomes a
   space ("dell'Arte" → "dell Arte").
3. `fold()`: `normalize("NFKD")`, remove U+0300–U+036F, `normalize("NFKC")`, `toLowerCase()`, `ß` → `ss`,
   **`臺` → `台` (addition 1)**, then every run of characters that are not `\p{L}` or `\p{N}` becomes one space;
   trim.
4. `tokens()`: split with `CJK_RUN` (runs of Han, Hiragana, Katakana, Hangul, Thai or `ー`, or runs of other letters
   and digits), then map each token through `ABBR` (`st` → `street`, `rd` → `road`, `jl` → `jalan`, ...).
5. **Addition 2:** every Han, Hiragana, Katakana or Hangul character, and `ー`, becomes its own token. A Thai run stays
   one token.
6. `norm(s)` = the tokens joined with single spaces. **No stop words are removed** for the index.

Query side: the same tokens, minus the `STOP` set of `search.ts` (`the of and no nr n de del della di da do la le
les el los las der die das`), unless that removes every token. Note that `n` is a stop word, so "in-n-out" searches
`in out`.

Examples (all are in the manifest's `norm_vectors` and in `norm-vectors.json`):

| Input | norm |
|---|---|
| `Joe's Pizza` | `joes pizza` |
| `ＡＢＣ` | `abc` |
| `臺北101` | `台 北 101` |
| `A4鼎泰豐` | `a4 鼎 泰 豐` |
| `dell'Arte` | `dell arte` |
| `O'Brien's Pub` | `o briens pub` |
| `Main St` | `main street` |
| `セブン-イレブン` | `セ ブ ン イ レ ブ ン` |
| `JOE'S PIZZA` | `joe s pizza` |

The Python port (`build/textnorm.py`) was compared with the TypeScript pipeline on 349,775 real names (all Bali
names, 150,000 Taiwan and 150,000 US names, all Taiwan brands, the vectors and edge cases): 0 differences.

## 5. Lookup (what the builder's port does; the server's `store.ts` follows spec section 2.3)

The builder runs this in `build/lookup.py` for its smoke and coverage gates.

**Skip** when the normalized query has fewer than 3 characters (2 when it has a CJK token), or when every remaining
token (after a 1-letter last word is set aside) is in `common_tokens`.

**MATCH string**

- Tokens = query tokens (section 4). Each token is written as an FTS5 string: `"` + token with `"` doubled + `"`.
- A run of consecutive single CJK characters becomes one phrase: `"鼎 泰 豐"`.
- The last token, when it is not a CJK character:
  - 1 letter (and not the only token): removed from the MATCH and applied afterwards as a filter: some token of the
    row's name or alt must start with it.
  - 2 or 3 characters: a prefix term, `"pi"*`.
  - 4 or more: `("word" OR "t1" OR ... )` with up to 12 extra terms from
    `SELECT term FROM vocab WHERE term >= ?w AND term < ?w_next ORDER BY df DESC LIMIT 13`, where `w_next` is `w`
    with its last character incremented, minus `w` itself and minus `common_tokens`, plus the word's singular when
    it has one (below). No extra terms: just `"word"`.
- Every other Latin token of 4+ letters (`[a-z]{4,}`) is ORed with its other number, since the ranker folds
  plurals: `"seafoods"` → `("seafoods" OR "seafood")`, `"grill"` → `("grill" OR "grills")`. The rule: a word
  ending in `es` whose stem ends in `s`, `x`, `z`, `ch`, `sh` or `o` (stem at least 3 letters, 4 when it ends in
  `o`) loses the `es` (`boxes` → `box`, `joes` → `joe`); else a word ending in `s` but not `ss` loses the `s`;
  else it gains `es` after `s`, `x`, `z`, `ch`, `sh` or `o`, `s` otherwise.
- **Parts are joined with ` AND `.** FTS5 rejects implicit AND in front of a parenthesised OR group:
  `"sia" ("grill" OR "grills")` is `fts5: syntax error near "OR"`, while `"sia" AND ("grill" OR "grills")` works.
- Normalized tokens contain only letters and digits, so `"`, `*`, `(` and `^` never reach the MATCH string, and
  `AND`, `OR`, `NEAR` inside quotes are plain terms. Catch FTS5 errors anyway and return no rows.

Examples from the files of 2026-09-23.1:

| Query | MATCH |
|---|---|
| `sia` | `"sia"*` |
| `obsid` | `("obsid" OR "obsidian")` |
| `warung si` | `("warung" OR "warungs") AND "si"*` |
| `joe's pizza` | `("joes" OR "joe") AND ("pizza" OR "pizzaria" OR "pizzas" OR ...)` |
| `sia grill & seafoods` | `"sia" AND ("grill" OR "grills") AND ("seafoods" OR "seafood")` |
| `臺北101` | `"台 北" AND "101"*` |

**Scan**

1. Box: 15 km around each centre (`dlat = 15 / 111.32`, `dlng = 15 / (111.32 cos(lat))`), split at ±180°.
2. Cells: every cell whose `x12` and `y12` fall in the box's ranges.
3. Rings by the distance from the centre to the cell's nearest point: ≤ 3 km, ≤ 8 km, the rest.
4. Per ring: look up the cells in `zcell`, sort the spans by `lo`, merge spans where `hi + 1 = next lo`.
5. Ring by ring, nearest first, range by range:
   `SELECT p.* FROM place_fts JOIN place p ON p.id = place_fts.rowid WHERE place_fts MATCH ? AND place_fts.rowid BETWEEN ? AND ? LIMIT ?`
   (limit = rows still allowed). Keep rows inside the box; stop at 600 rows.

Measured on this Mac with a warm cache (2026-09-23.1 files): 1 to 6 ms per lookup, 13 to 30 statements for a
15 km box ("coffee" at Times Square reaches the 600 cap after 3 statements).

## 6. `manifest.json`

```json
{
  "format": 1, "norm": 1,
  "tag": "places-2026-09-23.1-b1", "overture_release": "2026-09-23.1", "built_at": "2026-10-11T00:09:48Z",
  "files": [
    { "region": "bali", "name": "bali.sqlite", "bytes": 5541888,
      "sha256": "56ce3f011ef3d573a1c22ac308d85a740d27d64000dc201263b3f1956f046506", "rows": 33643,
      "country": "ID", "bboxes": [[114.42, -8.87, 115.72, -8.04]],
      "datasets": { "meta": 31585, "Foursquare": 1151, "Microsoft": 700, "AllThePlaces": 202, "PinMeTo": 5 } }
  ],
  "norm_vectors": [["Joe's Pizza", "joes pizza"], ["ＡＢＣ", "abc"], ...],
  "smoke": [{ "region": "bali", "q": "sia grill", "at": [-8.6478, 115.1385], "expect": "SIA - Grill & Seafood Bar" }, ...]
}
```

- `format`, `norm`: numbers here, strings in `meta`.
- `files[].bytes` and `sha256` are of the file as uploaded; `rows` equals `meta.rows`.
- `norm_vectors`: `[input, expected]` pairs; the reader's `norm(input)` must equal `expected` for every pair.
- `smoke`: `at` is `[lat, lng]`. A case passes when a row whose `norm(name)` equals `norm(expect)` is among the
  first 3 results of a lookup around `at`. The builder ranks with a simple stand-in (all words matched, then
  distance); the server runs the cases through its own lookup and ranking.

## 7. Regions

| Region | Boxes `[west, south, east, north]` | Country filter |
|---|---|---|
| bali | `[114.42, -8.87, 115.72, -8.04]` | none (every row is Indonesian) |
| taiwan | `[118.1, 21.8, 122.2, 26.5]` | `addresses[1].country = 'TW'` |
| us | `[-125.0, 24.3, -66.8, 49.5]`, `[-180.0, 51.0, -129.9, 71.6]`, `[172.0, 51.0, 180.0, 53.1]`, `[-160.7, 18.8, -154.7, 22.4]` | `'US'` or null |

A box tells the reader which files a search box can touch. A region's rows are inside its boxes.

## 8. Extract columns (input of `build/fixture.py`)

The S3 query writes these columns, and hand-written fixture rows (`rows.json`) use the same names:

| Column | Type | From |
|---|---|---|
| `gers` | VARCHAR (UUID) | `id` |
| `name` | VARCHAR | `names.primary` |
| `name_en` | VARCHAR[] | `names.rules[].value` where `language = 'en'` |
| `basic_category`, `tax_primary`, `tax_hierarchy` | VARCHAR, VARCHAR, VARCHAR[] | `basic_category`, `taxonomy.primary`, `taxonomy.hierarchy` |
| `confidence` | DOUBLE | `confidence` |
| `operating_status` | VARCHAR | `operating_status` |
| `addr`, `locality`, `addr_region`, `country` | VARCHAR | `addresses[1]` (`freeform`, `locality`, `region`, `country`) |
| `brand`, `brand_wikidata` | VARCHAR | `brand.names.primary`, `brand.wikidata` |
| `sources` | STRUCT(dataset, license)[] | `sources`, in order |
| `contacts` | INTEGER | number of websites + phones + socials + emails |
| `lng`, `lat` | DOUBLE | `bbox.xmin`, `bbox.ymin` |

Fixture defaults: confidence 0.9, open, sources `[Overture, meta]` (CDLA-Permissive-2.0), country = the region's.
