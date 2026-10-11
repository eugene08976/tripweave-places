"""Traveler set v2: which Overture categories we keep, and the trim list (spec section 1.3 step 1)."""

SET_NAME = "traveler-v2"

# Top-level taxonomy (taxonomy.hierarchy[1]) kept whole.
TRAVEL_L1 = (
    "food_and_drink", "lodging", "arts_and_entertainment", "cultural_and_historic", "sports_and_recreation",
    "travel_and_transportation", "geographic_entities", "shopping",
)
# Single basic_category values kept from other branches.
TRAVEL_EXTRA = (
    "wellness_service", "atm", "bank_or_credit_union", "hospital", "emergency_or_urgent_care_facility",
    "emergency_department", "primary_care_or_general_clinic", "public_plaza", "public_restroom", "embassy",
    "police_station", "laundry_service", "college_university", "library", "community_center", "public_fountain",
)
# taxonomy.primary values kept from other branches. They also survive the trim below (post offices).
TRAVEL_PRIMARY = ("coworking_space", "pharmacy", "post_office", "tourist_information_center", "visitor_center")

# basic_category values dropped even though their branch is kept: OpenStreetMap covers these better, and in the US
# they are about 960k rows.
TRIM = (
    # vehicles and home
    "automotive_service", "vehicle_service", "auto_dealer", "vehicle_dealer", "vehicle_parts_store",
    "hardware_home_and_garden_store", "shipping_or_delivery_service",
    # parking and tolls
    "parking", "parking_garage", "parking_lot", "park_and_ride", "toll_station",
    # transit
    "airport", "train_station", "public_transit_facility_or_service", "air_transport_facility_or_service",
    "rail_facility_or_service", "ground_transport_facility_or_service", "ferry_service", "taxi_or_ride_share_service",
)

# United States only: landmark categories. OpenStreetMap maps US parks, museums, monuments and beaches well, and
# Overture's US rows for them are mostly fan pages pinned away from the place (15 of 16 in the 2026-10-10 benchmark
# sample; six "Empire State Building" pins around Midtown, "Central Park" pins at City Hall). About 330k rows.
# Trails and trailheads filed under these categories stay (TRAIL_RE).
US_LANDMARK_TRIM = (
    "park", "museum", "historic_site", "monument", "landmark", "memorial", "public_plaza", "bridge", "national_park",
    "beach",
)
TRAIL_RE = r"\btrail(head)?s?\b"

# A trimmed row whose name says it rents vehicles stays (scooter and car rentals filed as dealers or services).
RENTAL_RE = r"\b(rental|rent|hire|sewa|scooter|motorbike)\b"

# A row with no category at all stays only when its name carries one of these words.
NO_CATEGORY_WORDS_RE = r"\b(beach|temple|pura|cafe|warung|hotel|villa)\b"


def _in(values) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def traveler_sql() -> str:
    return (f"(tax_hierarchy[1] IN {_in(TRAVEL_L1)} OR basic_category IN {_in(TRAVEL_EXTRA)} "
            f"OR tax_primary IN {_in(TRAVEL_PRIMARY)})")


def trimmed_sql() -> str:
    """True for a row the trim list removes (after its exceptions)."""
    return (f"(basic_category IN {_in(TRIM)} AND coalesce(tax_primary, '') NOT IN {_in(TRAVEL_PRIMARY)} "
            f"AND NOT regexp_matches(lower(name), '{RENTAL_RE}'))")


def us_landmark_sql() -> str:
    """True for a US row the landmark trim removes (trails and trailheads stay)."""
    return (f"(basic_category IN {_in(US_LANDMARK_TRIM)} "
            f"AND NOT regexp_matches(strip_accents(lower(name)), '{TRAIL_RE}'))")


def no_category_sql() -> str:
    return "(basic_category IS NULL AND tax_primary IS NULL AND tax_hierarchy IS NULL)"


def no_category_kept_sql() -> str:
    return f"regexp_matches(strip_accents(lower(name)), '{NO_CATEGORY_WORDS_RE}')"
