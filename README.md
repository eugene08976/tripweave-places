# tripweave-places

This repository builds the place data that the Tripweave app searches alongside OpenStreetMap: restaurants, cafés,
hotels, villas, gyms, beaches, temples, shops and similar places from [Overture Maps](https://overturemaps.org) for
three regions:

- **Bali**, with Nusa Penida
- **Taiwan**, with Kinmen, Matsu and Penghu
- **the United States**: the 50 states and DC

Once a month a GitHub Actions job downloads the newest Overture release, cleans it, turns each region into one
SQLite file, checks the files, and publishes them as a GitHub Release. The Tripweave server downloads the newest
release by itself. Nobody needs to do anything for a normal month.

This repository holds only the build scripts, the licence texts and the build records. The data files live in the
Releases. No password, token or account is needed to build or to download.

## What happens each month

1. **Wednesdays at 12:17 UTC** the workflow checks Overture's release list. If the newest release is the one already
   published here, it records "skipped" and stops. Overture releases about once a month, so new data is built within
   a week.
2. **Download**: for each region, only the places inside the region's boxes, with a name, still open, and with an
   Overture confidence of at least 0.70. This reads Overture's public bucket on Amazon S3 with no account.
3. **Clean-up**, in this order (every step logs how many places it removed):
   - keep traveler categories (food, lodging, sights, culture, sport, nature, shopping, plus ATMs, banks, pharmacies,
     clinics, post offices and similar) and drop car repair, car dealers, hardware stores, parking and transit, which
     OpenStreetMap covers better. Scooter and car rentals stay.
   - in the United States only, also drop parks, museums, historic sites, monuments, plazas, bridges, national parks
     and beaches (trails and trailheads stay). OpenStreetMap maps these well there, and Overture's rows for them are
     mostly fan pages pinned away from the real place.
   - drop junk names: only digits or punctuation, a single letter, an address or phone number, "we moved".
   - drop company rows: a business register's names ending in "LLC", "Inc", "Corporation" and the like, holding
     companies, unions and suppliers. A name stuffed with keywords ("Analog Uluwatu | Cafe | Bar | Bistro") is cut to
     its first part.
   - drop "agent pages": a person's name in front of the name of a place right next to it.
   - merge duplicates: the same place twice within 100 m.
   - drop misplaced named copies: "Single Fin Uluwatu" pinned in Kuta, 14 km from Single Fin, when the addresses
     around Single Fin say Uluwatu and those around the copy don't. Each run lists them in `work/misplaced-<region>.tsv`.
   - add English brand names from Wikidata to Chinese-named chain branches in Taiwan.
4. **Write** one SQLite file per region, laid out so the server can search a 15 km box with a few quick reads.
5. **Check** every file: database integrity, row counts (above a floor and within 15% of last month), every row's
   licence, a set of test searches ("sia grill" and "obsidian gym" in Canggu, 鼎泰豐 near Taipei 101, "joe's pizza" in
   Times Square, and others), and the owner's six Bali places.
6. **Publish** (a separate job, the only one besides Record that can write to the repo; steps 1 to 5 run with a
   read-only token): a draft release first, then the files, then `manifest.json` last, then the release goes public.
   The server only ever sees a complete release. A failed upload deletes its draft. The newest 3 releases are kept,
   and drafts older than a day are deleted.
7. **Record**: every run, including skipped and failed ones, adds one line to `builds.json` (with
   `publish_failed` when the files were built but not published) and commits it. Those commits also stop GitHub from
   switching off the monthly schedule, which it does after 60 days without activity, so a commit that can't be pushed
   fails the run.

If any check fails, nothing is published, GitHub sends an email about the failed run, and the server keeps using last
month's data. Overture data doesn't expire, so that is safe.

## Running it by hand

On GitHub: **Actions → Build places → Run workflow**. Two switches:

- **force**: build even if this Overture release is already published (for example after a fix here).
- **allow_count_change**: publish even if a region's number of places moved by more than 15%. Look at the numbers
  in the failed run's log first; Overture's US data has grown 7 to 10% in some months.

## Running it on a computer

Needs Python 3.12 and about 8 GB of free memory. The US download takes 15 to 40 minutes and about 5 GB of downloaded
data; the whole build needs about 4 GB of disk.

```sh
python3.12 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.txt
.venv/bin/python -m pytest -q tests
.venv/bin/python build/build.py --threads 8 --memory 8GB          # writes out/
```

Useful options of `build/build.py`:

| Option | What it does |
|---|---|
| `--regions bali taiwan` | Build only some regions |
| `--release 2026-09-23.1` | A specific Overture release instead of the newest |
| `--from-parquet bali=path.parquet` | Build from a file downloaded earlier instead of from S3 |
| `--previous none` | Don't compare row counts with the last published release |
| `--no-gates` | Development builds: report failed checks but still write the files |
| `--keep-work` | Keep the downloaded parquet files in `work/` |

`build/extract.py bali taiwan us` only downloads. `build/fixture.py rows.json --out <dir>` builds tiny test files from
hand-written rows through the same clean-up, and `build/synth_span.py --out <file>` writes the synthetic US test file
the server's tests use.

## When GitHub is down (emergency only)

1. Build on a Mac: `.venv/bin/python build/build.py --threads 8 --memory 8GB --previous none`. If S3 is reachable this
   downloads as usual; if you already have parquet extracts, add `--from-parquet bali=... taiwan=... us=...`.
2. Copy `out/` to the server's volume with `railway volume files upload` into `/data/overture/<tag>/`, then point
   `/data/overture/current.json` at it (the server's `overture:use-local` script writes that file).

## Files

| Path | What |
|---|---|
| `build/` | The builder: `extract.py`, `clean.py`, `write_index.py`, `verify.py`, `manifest.py`, `build.py`, plus the text normalizer, the address detector, the lookup used by the checks, regions and categories |
| `build/smoke.json` | The test searches published in every manifest |
| `build/coverage.json` | The owner's six Bali places and the 86-place coverage test set |
| `FORMAT.md` | The exact file format the Tripweave server reads |
| `norm-vectors.json` | Text-normalization examples the server must reproduce before it uses a file |
| `labels/brands.json` | English brand names from Wikidata (refreshed and committed by each build) |
| `builds.json` | One line per workflow run |
| `tests/` | The builder's tests; `tests/fixtures/server_rows.json` holds the rows for the server's small test files |
| `licenses/` | CDLA-Permissive-2.0, Apache-2.0 and Foursquare's NOTICE |
| `ATTRIBUTION.md` | Where the data comes from and how it was changed |

## Licences

The data is © Overture Maps Foundation and its sources. Most rows are under CDLA-Permissive-2.0; rows from Foursquare
are under Apache-2.0 with Foursquare's NOTICE; rows from AllThePlaces are CC0. Each release carries the licence texts
and an `ATTRIBUTION.md` with the sources and the changes made. Overture's places data contains no OpenStreetMap data.
