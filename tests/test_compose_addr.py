import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build"))
from write_index import ADDR_MAX, compose_addr  # noqa: E402


def test_street_then_town():
    assert compose_addr("Jl. Pantai Bingin No.8", "Pecatu", "BA", "bali") == "Jl. Pantai Bingin No.8, Pecatu"
    assert compose_addr(None, "Denpasar", None, "bali") == "Denpasar"
    assert compose_addr("Jl. Raya Canggu", None, None, "bali") == "Jl. Raya Canggu"
    assert compose_addr(None, None, None, "bali") is None


def test_us_town_carries_its_state():
    assert compose_addr("10350 Ellis Ave", "Inglewood", "CA", "us") == "10350 Ellis Ave, Inglewood, CA"
    assert compose_addr("10350 Ellis Ave", "Inglewood", "US-CA", "us") == "10350 Ellis Ave, Inglewood, CA"
    assert compose_addr("10350 Ellis Ave", "Inglewood", "California", "us") == "10350 Ellis Ave, Inglewood"
    assert compose_addr("Jl. X", "Pecatu", "BA", "bali") == "Jl. X, Pecatu"   # not outside the US


def test_town_already_in_the_street_address_is_not_repeated():
    assert compose_addr("91-1001 Kapolei Pkwy, Kapolei, HI 96707", "Kapolei", "HI", "us") == "91-1001 Kapolei Pkwy, Kapolei, HI 96707"
    assert compose_addr("Jl. Raya Uluwatu, Pecatu, Bali", "pecatu", None, "bali") == "Jl. Raya Uluwatu, Pecatu, Bali"


def test_taiwan_chinese_address_puts_the_city_first():
    assert compose_addr("信義路二段194號", "台北市", None, "taiwan") == "台北市信義路二段194號"
    assert compose_addr("台北市信義路二段194號", "台北市", None, "taiwan") == "台北市信義路二段194號"
    assert compose_addr("No. 25號, Section 2, Zhongzheng S Rd", "台南市", None, "taiwan") == "No. 25號, Section 2, Zhongzheng S Rd, 台南市"


def test_long_street_is_cut_so_the_town_stays():
    a = compose_addr(" ".join(["Wordy"] * 30), "Some Town", "NY", "us")
    assert len(a) <= ADDR_MAX and a.endswith(", Some Town, NY")
