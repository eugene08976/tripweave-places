"""Clean-up rules on small fixtures (spec section 4.2)."""
import json
import os

import apsw
import pytest

import clean
import fixture
import sets
from textnorm import norm

HERE = os.path.dirname(os.path.abspath(__file__))

FOOD = ["food_and_drink", "restaurant"]
SHOP = ["shopping", "fashion_store"]
HOTEL = ["lodging", "hotel"]


def r(i, name, lat, lng, cat="restaurant", tax=FOOD, conf=0.9, **kw):
    return {"gers": f"00000000-0000-4000-8000-{i:012d}", "name": name, "lat": lat, "lng": lng,
            "basic_category": cat, "tax_primary": tax[-1] if tax else None, "tax_hierarchy": tax,
            "confidence": conf, **kw}


def bali_rows():
    m = 1 / 111_320  # one metre in degrees of latitude
    return [
        # agent page: dropped; the shop itself stays
        r(1, "Astra Motor Sangsit", -8.10, 115.10, "fashion_store", SHOP, 0.9),
        r(2, "Budi Astra Motor Sangsit", -8.10 + 40 * m, 115.10, "fashion_store", SHOP, 0.8),
        # different categories: both stay
        r(3, "Discovery Mall", -8.72, 115.17, "shopping_center", ["shopping", "shopping_center"], 0.9),
        r(4, "Burger King Discovery Mall", -8.72 + 30 * m, 115.17, "fast_food_restaurant", FOOD, 0.9),
        # SMA / SMP: two schools, never merged
        r(5, "SMA Negeri 1 Seririt Bali", -8.20, 114.93, "college_university", ["education", "college_university"]),
        r(6, "SMP Negeri 1 Seririt Bali", -8.20 + 20 * m, 114.93, "college_university", ["education", "college_university"]),
        # Grand Hyatt twice: merged, the higher confidence survives
        r(7, "Grand Hyatt Bali", -8.80, 115.23, "hotel", HOTEL, 0.95, contacts=3),
        r(8, "Grand Hyatt Bali", -8.80 + 30 * m, 115.23, "hotel", HOTEL, 0.80),
        # Sanur Beer Garden pair: merged ("the" is a stop word), the other name kept in alt
        r(9, "The Sanur Beer Garden", -8.69, 115.26, "bar", ["food_and_drink", "bar"], 0.9),
        r(10, "Sanur Beer Garden!", -8.69 + 40 * m, 115.26, "bar", ["food_and_drink", "bar"], 0.85),
        # junk names
        r(11, "12345", -8.65, 115.13),
        r(12, "A", -8.65, 115.131),
        r(13, "Jl. Raya Canggu No. 15", -8.65, 115.132),
        r(14, "WA +62 812-3456-7890", -8.65, 115.133),
        r(15, "We moved! Search Arches to find us", -8.65, 115.134),
        # rental filed as a car dealer: kept; a real dealer: trimmed; parking: trimmed
        r(16, "Bali Scooter Rental", -8.66, 115.14, "auto_dealer", ["shopping", "vehicle_dealer", "auto_dealer"]),
        r(17, "Toyota Auto2000", -8.661, 115.14, "auto_dealer", ["shopping", "vehicle_dealer", "auto_dealer"]),
        r(18, "Central Parking", -8.662, 115.14, "parking_lot", ["travel_and_transportation", "parking_lot"]),
        # post office survives the transit trim via tax_primary
        r(19, "Kantor Pos Kuta", -8.72, 115.175, "shipping_or_delivery_service", ["travel_and_transportation", "post_office"],
          tax_primary_override=None),
        # no category: a beach stays, anything else goes
        r(20, "Batu Bolong Beach", -8.659, 115.130, None, None),
        r(21, "Mystery Pin", -8.659, 115.131, None, None),
        # outside the traveler set
        r(22, "PT Sumber Rejeki Finance", -8.67, 115.2, "financial_service", ["financial_service"]),
        # closed and low confidence: removed by the extract filter
        r(23, "Closed Warung", -8.64, 115.1, operating_status="permanently_closed"),
        r(24, "Shaky Warung", -8.64, 115.101, conf=0.5),
        # provider picked from a shuffled source list
        r(25, "Foursquare Cafe", -8.64, 115.102, sources=[{"dataset": "Overture-signals", "license": "CDLA-Permissive-2.0"},
                                                       {"dataset": "Foursquare", "license": "Apache-2.0"},
                                                       {"dataset": "Overture", "license": "CDLA-Permissive-2.0"}]),
        r(26, "Ice Cream Brand", -8.64, 115.103, brand="Gelato Secrets", name_en=["Gelato Secrets Ice Cream"],
          sources=[{"dataset": "AllThePlaces", "license": "CC0-1.0"}, {"dataset": "Overture", "license": "CDLA-Permissive-2.0"}]),
        r(27, "Outside Bali", -7.5, 112.7),
    ]


def _fix(rows):
    for x in rows:
        if "tax_primary_override" in x:
            x.pop("tax_primary_override")
            x["tax_primary"] = "post_office"
    return rows


@pytest.fixture(scope="module")
def bali(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("bali") / "bali-test.sqlite")
    res = fixture.build_region(_fix(bali_rows()), "bali", path)
    db = apsw.Connection(path, flags=apsw.SQLITE_OPEN_READONLY)
    names = {n: (alt, src, flags) for n, alt, src, flags in db.execute("SELECT name, alt, src, flags FROM place")}
    yield res, names, db, path
    db.close()


def test_agent_page_dropped_and_mall_neighbour_kept(bali):
    _, names, _, _ = bali
    assert "Astra Motor Sangsit" in names and "Budi Astra Motor Sangsit" not in names
    assert "Discovery Mall" in names and "Burger King Discovery Mall" in names


def test_schools_not_merged_hotels_and_beer_garden_merged(bali):
    res, names, _, _ = bali
    assert "SMA Negeri 1 Seririt Bali" in names and "SMP Negeri 1 Seririt Bali" in names
    assert sum(1 for n in names if n == "Grand Hyatt Bali") == 1
    assert "The Sanur Beer Garden" in names and "Sanur Beer Garden!" not in names
    assert res["clean"]["removed_duplicate"] == 2


def test_junk_names_dropped(bali):
    res, names, _, _ = bali
    for n in ("12345", "A", "Jl. Raya Canggu No. 15", "WA +62 812-3456-7890", "We moved! Search Arches to find us"):
        assert n not in names
    st = res["clean"]
    assert st["removed_junk_no_letter"] == 1 and st["removed_junk_single_latin"] == 1
    assert st["removed_junk_address"] == 1 and st["removed_junk_phone"] == 1 and st["removed_junk_we_moved"] == 1


def test_trim_and_exceptions(bali):
    res, names, _, _ = bali
    assert "Bali Scooter Rental" in names
    assert "Toyota Auto2000" not in names and "Central Parking" not in names
    assert "Kantor Pos Kuta" in names
    assert "Batu Bolong Beach" in names and "Mystery Pin" not in names
    assert "PT Sumber Rejeki Finance" not in names
    assert "Closed Warung" not in names and "Shaky Warung" not in names and "Outside Bali" not in names
    assert res["clean"]["removed_trim"] == 2


def test_provider_alt_and_flags(bali):
    res, names, _, _ = bali
    assert names["Foursquare Cafe"][1] == 2
    alt, src, flags = names["Ice Cream Brand"]
    assert src == 3 and flags == 1
    assert alt.split("|") == ["Gelato Secrets", "Gelato Secrets Ice Cream"]
    assert names["Grand Hyatt Bali"][0] is None            # the absorbed copy had the same name
    assert res["clean"]["datasets"] == {"meta": 10, "AllThePlaces": 1, "Foursquare": 1}


def test_licence_gate_fails_on_unknown_licence(tmp_path):
    rows = [r(1, "Odd Source Cafe", -8.6, 115.1, sources=[{"dataset": "Overture", "license": "CDLA-Permissive-2.0"},
                                                          {"dataset": "SomeNewProvider", "license": "ODbL-1.0"}])]
    with pytest.raises(RuntimeError, match="licence gate"):
        fixture.build_region(rows, "bali", str(tmp_path / "x.sqlite"))


def test_fuzzy_rule():
    from textnorm import words
    ka, kb = " ".join(words("L and L Hawaiian Barbecue")), " ".join(words("L&L Hawaiian Barbecue"))
    assert ka == kb                                          # equal keys: merged without the fuzzy rule
    assert clean.fuzzy_same("cafe", "cafes") is False        # both names need 12+ characters
    assert clean.fuzzy_same("sma negeri 1 seririt", "smp negeri 1 seririt") is False
    assert clean.fuzzy_same("hotel puri saron seminyak", "hotel puri saron seminyk") is True
    assert clean.fuzzy_same("seririt 1 school", "seririt 2 school") is False
    assert clean.same_name("grand hyatt bali", "grand hyatt bali", "grand hyatt", "grand hyatt")
    assert clean.same_name("sanur beer garden", "sanur beer garden", "sanur beer garden", "sanur beer garden")


def test_trim_list_contents():
    for c in ("parking_lot", "auto_dealer", "airport", "taxi_or_ride_share_service", "hardware_home_and_garden_store"):
        assert c in sets.TRIM
    assert "post_office" in sets.TRAVEL_PRIMARY
    assert "bank_or_credit_union" in sets.TRAVEL_EXTRA


def test_junk_reason_keeps_real_names():
    for n in ("鼎泰豐", "家", "Obsidian Gym Bali", "7-Eleven", "Taipei 101", "41 Street Bar", "Joe's Pizza"):
        assert clean.junk_reason(n) is None, n


def test_build_alt_dedupes_parts():
    assert clean.build_alt("Starbucks Reserve", None, "Starbucks", None, None) == []
    assert clean.build_alt("鼎泰豐", [], "Din Tai Fung", ["鼎泰豐 101店"], "Din Tai Fung") == ["Din Tai Fung", "鼎泰豐 101店"]
    assert norm("Din Tai Fung") == "din tai fung"


def test_server_fixture_rows(tmp_path):
    """tests/fixtures/server_rows.json builds the server's mini files with the expected survivors."""
    with open(os.path.join(HERE, "fixtures", "server_rows.json"), encoding="utf-8") as f:
        data = json.load(f)
    got = {}
    for region in ("bali", "taiwan", "us"):
        path = str(tmp_path / f"{region}-mini.sqlite")
        fixture.build_region(data[region], region, path)
        db = apsw.Connection(path, flags=apsw.SQLITE_OPEN_READONLY)
        got[region] = [n for (n,) in db.execute("SELECT name FROM place")]
        db.close()
    assert got["bali"].count("Grand Hyatt Bali") == 1
    assert "Budi Astra Mart Sangsit" not in got["bali"] and "Canggu Scooter Rental" in got["bali"]
    assert {"SIA - Grill & Seafood Bar", "Obsidian Gym Bali", "Finns Rec Club", "Finns Recreation Club"} <= set(got["bali"])
    assert "臺北鼎泰豐" in got["taiwan"] and "Din Tai Fung" in got["taiwan"]
    assert "Central Parking" not in got["us"] and "The Night Hotel" not in got["us"] and "O'Brien's Pub" in got["us"]


def test_company_supplier_and_stuffed_names():
    assert clean.junk_reason("Boba King Llc", "BrightQuery") == "junk_company"
    assert clean.junk_reason("The Georgian Motel Corporation", "BrightQuery") == "junk_company"
    assert clean.junk_reason("Caffe Bon, Llc", "meta") is None             # a café's page with its legal name stays
    assert clean.junk_reason("Grand Central Holdings", "Foursquare") == "junk_company"
    assert clean.junk_reason("Hotel Motel Trades Council", "meta") == "junk_company"
    assert clean.junk_reason("Tachungho Bubble Tea Supplier 大中和國際", "meta") == "junk_supplier"
    assert clean.junk_reason("The Coffee Company", "BrightQuery") is None
    assert clean.destuff("Analog Uluwatu | Cafe | Bar | Bistro") == "Analog Uluwatu"
    assert clean.destuff("rizy ｜爵士酒吧、JAZZ BAR ｜") == "rizy"
    assert clean.destuff("Outpost Club | Coliving") == "Outpost Club | Coliving"   # one separator: a real name


def test_us_landmark_trim_keeps_trails(tmp_path):
    park = ["sports_and_recreation", "park"]
    rows = [r(1, "Central Park", 40.7712, -73.9742, "park", park), r(2, "Bear Mountain Trailhead", 41.31, -73.99, "park", park),
            r(3, "Joe's Pizza", 40.7547, -73.9870), r(4, "Empire State Building", 40.7484, -73.9857, "historic_site",
                                                          ["cultural_and_historic", "historic_site"])]
    path = str(tmp_path / "us.sqlite")
    res = fixture.build_region(rows, "us", path)
    db = apsw.Connection(path, flags=apsw.SQLITE_OPEN_READONLY)
    names = {n for (n,) in db.execute("SELECT name FROM place")}
    db.close()
    assert names == {"Bear Mountain Trailhead", "Joe's Pizza"}
    assert res["clean"]["removed_trim_us_landmark"] == 2
    # Bali keeps its parks and beaches
    res = fixture.build_region([r(1, "Taman Ayun", -8.54, 115.17, "park", park)], "bali", str(tmp_path / "b.sqlite"))
    assert res["clean"]["rows"] == 1


def test_misplaced_named_copy(tmp_path):
    """'Single Fin Uluwatu' pinned in Kuta, 14 km from Single Fin, where the addresses say Uluwatu."""
    m = 1 / 111_320
    bar = ["food_and_drink", "bar"]
    rows = [r(1, "Single Fin", -8.8148, 115.0890, "bar", bar, addr="Jl. Labuansait, Uluwatu"),
            r(2, "Single Fin Uluwatu", -8.7172, 115.1689, "bar", bar, addr="Jalan Pantai Kuta 80361")]
    # neighbours: around Single Fin the addresses say Uluwatu, around the Kuta pin they say Kuta
    rows += [r(10 + i, f"Warung Uluwatu Neighbour {i}", -8.8148 + i * 50 * m, 115.0890, addr=f"Jl. Raya Uluwatu {i}") for i in range(6)]
    rows += [r(20 + i, f"Warung Kuta Neighbour {i}", -8.7172 + i * 50 * m, 115.1689, addr=f"Jl. Pantai Kuta {i}") for i in range(6)]
    # a branch really in Seminyak: its neighbours' addresses say Seminyak
    rows += [r(3, "Revolver Espresso", -8.6478, 115.1385, "cafe", FOOD, addr="Jl. Raya Canggu"),
             r(4, "Revolver Espresso Seminyak", -8.6913, 115.1683, "cafe", FOOD, addr="Jl. Kayu Aya")]
    rows += [r(30 + i, f"Shop Seminyak {i}", -8.6913 + i * 50 * m, 115.1683, "cafe", FOOD, addr=f"Jl. Seminyak {i}") for i in range(6)]
    res = fixture.build_region(rows, "bali", str(tmp_path / "b.sqlite"))
    db = apsw.Connection(str(tmp_path / "b.sqlite"), flags=apsw.SQLITE_OPEN_READONLY)
    names = {n for (n,) in db.execute("SELECT name FROM place")}
    db.close()
    assert "Single Fin" in names and "Single Fin Uluwatu" not in names
    assert "Revolver Espresso Seminyak" in names
    assert res["clean"]["removed_misplaced_copy"] == 1
