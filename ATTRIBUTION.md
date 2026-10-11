# Attribution

The place files published in this repository's Releases are derived from **Overture Maps** data,
© Overture Maps Foundation, https://overturemaps.org.

Each release has its own `ATTRIBUTION.md` with the exact Overture release, the number of rows from every source
dataset and the files' checksums. This page is the template's summary.

## Sources and terms

Credit lines from Overture's attribution page (https://docs.overturemaps.org/attribution/):

- Meta, Microsoft, BrightQuery, DAC, PinMeTo, RenderSEO and Krick: "Data from ... Available under CDLA Permissive 2.0."
  The text of the agreement is in `licenses/CDLA-Permissive-2.0.txt` and in every release.
- Foursquare: "Copyright 2024 Foursquare Labs, Inc. All rights reserved. Available under Apache 2.0." The licence is
  in `licenses/Apache-2.0.txt` and Foursquare's NOTICE, with our notice of changes, in `licenses/foursquare-NOTICE.txt`.
- AllThePlaces: CC0 1.0.
- English brand names for some Taiwan rows: Wikidata, CC0 1.0.

Overture's places theme contains no OpenStreetMap data.

## Changes made

Derived from Overture Maps data: filtered, de-duplicated, re-indexed by Tripweave. In detail:

- kept only places with a name, a confidence of at least 0.70 and no closed status, inside each region's boxes;
- kept only categories useful to travelers and removed vehicle, hardware, parking and transit categories, and in the
  United States also park, museum, historic site, monument, plaza, bridge, national park and beach categories (trails
  stay);
- removed names that are only digits or punctuation, single letters, addresses, phone numbers and "we moved" notices,
  and pages that put a person's name in front of a nearby place's name;
- removed company, union and supplier rows (business-register names ending in a legal form such as LLC), and cut names
  stuffed with keywords to their first part;
- merged rows that describe the same place within 100 m into one row, keeping the other names as alternates;
- removed rows named like another row plus a place word that are pinned away from that place;
- added English brand names from Wikidata to some Taiwan rows;
- dropped most columns, joined each street address with its town (and US state) and shortened it to 80 characters,
  normalized names for search and re-indexed everything into SQLite files.

Every file also carries this notice in its `meta.notice` value.
