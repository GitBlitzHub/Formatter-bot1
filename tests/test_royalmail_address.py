import asyncio
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("LLM_API_KEY", "test-key")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import address  # noqa: E402
from normalizer import build_block_details  # noqa: E402
from posttowns import resolve_post_town  # noqa: E402
from schemas import Person  # noqa: E402


def _mock_network(monkeypatch, *, pc_info_map=None, osm_map=None, lr_rows_map=None):
    """Мокает все сетевые вызовы в address.py для полностью офлайн-тестирования."""
    pc_info_map = pc_info_map or {}
    osm_map = osm_map or {}
    lr_rows_map = lr_rows_map or {}

    async def fake_lookup_postcode(client, pc):
        return pc_info_map.get(address.format_postcode(pc))

    async def fake_lr_by_postcode(client, pc):
        return lr_rows_map.get(address.format_postcode(pc), [])

    async def fake_lr_by_street(client, street, town, number):
        return []

    async def fake_nominatim(client, params):
        return []

    async def fake_osm_check(client, lat, lon, number, street, pc, town):
        return osm_map.get(address.format_postcode(pc), (street, True, pc, ""))

    async def fake_terminated(client, pc):
        return False

    monkeypatch.setattr(address, "IDEAL_KEY", "")
    monkeypatch.setattr(address, "lookup_postcode", fake_lookup_postcode)
    monkeypatch.setattr(address, "_lr_by_postcode", fake_lr_by_postcode)
    monkeypatch.setattr(address, "_lr_by_street", fake_lr_by_street)
    monkeypatch.setattr(address, "_nominatim", fake_nominatim)
    monkeypatch.setattr(address, "_osm_check", fake_osm_check)
    monkeypatch.setattr(address, "_terminated", fake_terminated)


# 1. Три эталонных адреса Royal Mail
@pytest.mark.parametrize(
    "raw, flat, number, street, locality, town, postcode, pc_info, expected",
    [
        (
            "31 Lonsdale Drive, Toton, NG9 6LS",
            "", "31", "Lonsdale Drive", "", "Toton", "NG9 6LS",
            {"postcode": "NG9 6LS", "latitude": 52.912, "longitude": -1.246, "admin_district": "Broxtowe"},
            "31 Lonsdale Drive Toton, Nottingham, NG9 6LS",
        ),
        (
            "4 West Avenue, Hullbridge, SS5 6JU",
            "", "4", "West Avenue", "", "Hullbridge", "SS5 6JU",
            {"postcode": "SS5 6JU", "latitude": 51.625, "longitude": 0.612, "admin_district": "Rochford", "parish": "Hullbridge"},
            "4 West Avenue Hullbridge, Hockley, SS5 6JU",
        ),
        (
            "132 Ormskirk Road, Rainford, WA11 8HS",
            "", "132", "Ormskirk Road", "", "Rainford", "WA11 8HS",
            {"postcode": "WA11 8HS", "latitude": 53.504, "longitude": -2.785, "admin_district": "St. Helens", "parish": "Rainford"},
            "132 Ormskirk Road Rainford, St. Helens, WA11 8HS",
        ),
    ],
)
def test_reference_addresses_royal_mail(
    monkeypatch, raw, flat, number, street, locality, town, postcode, pc_info, expected
):
    _mock_network(
        monkeypatch,
        pc_info_map={postcode: pc_info},
        osm_map={postcode: (street, True, postcode, "")},
    )
    res = asyncio.run(
        address.verify_address(
            raw, flat=flat, number=number, street=street,
            locality=locality, town=town, postcode=postcode,
        )
    )
    assert res.status == "CONFIRMED"
    assert res.formatted == expected


# 2. Стандартные адреса, где town == post_town (без дублей и лишнего suburb)
@pytest.mark.parametrize(
    "raw, number, street, town, postcode, pc_info, osm_town, expected",
    [
        (
            "35 Serpentine Road, Kendal, LA9 4PE",
            "35", "Serpentine Road", "Kendal", "LA9 4PE",
            {"postcode": "LA9 4PE", "latitude": 54.328, "longitude": -2.749, "admin_district": "Westmorland and Furness"},
            "Kendal",
            "35 Serpentine Road, Kendal, LA9 4PE",
        ),
        (
            "10 Downing Street, London, SW1A 2AA",
            "10", "Downing Street", "London", "SW1A 2AA",
            {"postcode": "SW1A 2AA", "latitude": 51.503, "longitude": -0.127, "admin_district": "Westminster"},
            "Westminster",
            "10 Downing Street, London, SW1A 2AA",
        ),
    ],
)
def test_standard_address_town_equals_post_town(
    monkeypatch, raw, number, street, town, postcode, pc_info, osm_town, expected
):
    _mock_network(
        monkeypatch,
        pc_info_map={postcode: pc_info},
        osm_map={postcode: (street, True, postcode, osm_town)},
    )
    res = asyncio.run(
        address.verify_address(raw, number=number, street=street, town=town, postcode=postcode)
    )
    assert res.status == "CONFIRMED"
    assert res.formatted == expected


# 3. Адрес с квартирой (Flat) и locality
def test_address_with_flat_and_locality(monkeypatch):
    pc = "NG9 6LS"
    _mock_network(
        monkeypatch,
        pc_info_map={pc: {"postcode": pc, "latitude": 52.912, "longitude": -1.246, "admin_district": "Broxtowe"}},
        osm_map={pc: ("Lonsdale Drive", True, pc, "")},
    )
    res = asyncio.run(
        address.verify_address(
            "Flat 2, 31 Lonsdale Drive, Toton, NG9 6LS",
            flat="Flat 2", number="31", street="Lonsdale Drive",
            locality="Toton", town="", postcode=pc,
        )
    )
    assert res.status == "CONFIRMED"
    assert res.formatted == "Flat 2, 31 Lonsdale Drive Toton, Nottingham, NG9 6LS"


# 4. Дедупликация locality == post_town
def test_locality_deduplication_when_same_as_post_town(monkeypatch):
    pc = "NG9 6LS"
    _mock_network(
        monkeypatch,
        pc_info_map={pc: {"postcode": pc, "latitude": 52.912, "longitude": -1.246, "admin_district": "Broxtowe"}},
        osm_map={pc: ("Lonsdale Drive", True, pc, "")},
    )
    res = asyncio.run(
        address.verify_address(
            "31 Lonsdale Drive, Nottingham, NG9 6LS",
            number="31", street="Lonsdale Drive",
            locality="Nottingham", town="Nottingham", postcode=pc,
        )
    )
    assert res.status == "CONFIRMED"
    assert res.formatted == "31 Lonsdale Drive, Nottingham, NG9 6LS"


# 5. Флаг ADDRESS_TOWN_UPPERCASE=1
def test_town_uppercase_env_flag(monkeypatch):
    pc = "NG9 6LS"
    monkeypatch.setenv("ADDRESS_TOWN_UPPERCASE", "1")
    _mock_network(
        monkeypatch,
        pc_info_map={pc: {"postcode": pc, "latitude": 52.912, "longitude": -1.246, "admin_district": "Broxtowe"}},
        osm_map={pc: ("Lonsdale Drive", True, pc, "")},
    )
    res = asyncio.run(
        address.verify_address(
            "31 Lonsdale Drive, Toton, NG9 6LS",
            number="31", street="Lonsdale Drive",
            locality="Toton", town="", postcode=pc,
        )
    )
    assert res.formatted == "31 Lonsdale Drive Toton, NOTTINGHAM, NG9 6LS"


# 6. Таймаут / None от HM Land Registry -> быстрый фолбэк с точным Post Town
def test_lr_timeout_falls_back_gracefully_with_post_town(monkeypatch):
    pc = "SS5 6JU"

    async def slow_or_failing_get(client, url, **kw):
        raise address.httpx.ReadTimeout("LR timeout")

    monkeypatch.setattr(address, "_LR_CACHE", {})
    monkeypatch.setattr(address, "_LR_DOWN_UNTIL", 0.0)
    monkeypatch.setattr(address, "IDEAL_KEY", "")

    async def fake_lookup_postcode(client, p):
        return {"postcode": pc, "latitude": 51.625, "longitude": 0.612, "admin_district": "Rochford"}

    async def fake_osm_check(client, lat, lon, number, street, p, town):
        return ("West Avenue", True, pc, "Rochford")

    monkeypatch.setattr(address, "lookup_postcode", fake_lookup_postcode)
    monkeypatch.setattr(address, "_osm_check", fake_osm_check)

    # Мокаем client.get в _lr_query через AsyncClient.get, чтобы проверить обработку таймаута в _lr_query
    monkeypatch.setattr(address.httpx.AsyncClient, "get", slow_or_failing_get)

    res = asyncio.run(
        address.verify_address(
            "4 West Avenue, Hullbridge, SS5 6JU",
            number="4", street="West Avenue", town="Hullbridge", postcode=pc,
        )
    )
    assert res.status == "CONFIRMED"
    assert res.formatted == "4 West Avenue Hullbridge, Hockley, SS5 6JU"


# 7. Multi-town outcode (WA3: Warrington / Newton-le-Willows / Leigh)
def test_multi_town_outcode_resolution():
    assert resolve_post_town("WA3 4AA", town_in="Newton-le-Willows") == "Newton-le-Willows"
    assert resolve_post_town("WA3 4AA", lr_town="LEIGH") == "Leigh"
    assert resolve_post_town("WA3 4AA", pc_info={"parish": "Warrington"}) == "Warrington"
    assert resolve_post_town("WA3 4AA") == "Warrington"


# 8. Интеграция normalizer.build_block_details с addr_locality
def test_normalizer_build_block_with_addr_locality(monkeypatch):
    pc = "SS5 6JU"
    _mock_network(
        monkeypatch,
        pc_info_map={pc: {"postcode": pc, "latitude": 51.625, "longitude": 0.612, "admin_district": "Rochford", "parish": "Hullbridge"}},
        osm_map={pc: ("West Avenue", True, pc, "")},
    )
    p = Person(
        title="Mr",
        gender="M",
        name_tokens=["John", "Smith"],
        surname_guess="Smith",
        dob="01.02.1980",
        address="4 West Avenue, Hullbridge, SS5 6JU",
        addr_number="4",
        addr_street="West Avenue",
        addr_locality="Hullbridge",
        addr_town="Hockley",
        addr_postcode="SS5 6JU",
    )
    block, gender, dob, display_name = asyncio.run(build_block_details(p))
    assert "ADRESS: 4 West Avenue Hullbridge, Hockley, SS5 6JU" in block
    assert gender == "M"
    assert dob == "01.02.1980"
    assert display_name == "Mr John Smith"


def test_edge_cases_and_outcode_coverage(monkeypatch):
    # Обязательные стандартные и эталонные outcode из задания
    assert resolve_post_town("NG9 6LS") == "Nottingham"
    assert resolve_post_town("SS5 6JU") == "Hockley"
    assert resolve_post_town("WA11 8HS") == "St. Helens"
    assert resolve_post_town("LA9 4PE") == "Kendal"
    assert resolve_post_town("SW1A 2AA") == "London"
    assert resolve_post_town("EC4Y 1AA") == "London"
    assert resolve_post_town("CF37 1LQ") == "Pontypridd"

    # Дедупликация: street уже заканчивается на locality
    assert address._join("", "31", "Lonsdale Drive Toton", "Nottingham", "NG9 6LS", locality="Toton") == (
        "31 Lonsdale Drive Toton, Nottingham, NG9 6LS"
    )
    # Название дома без номера (number пустой, flat задан)
    assert address._join("Rose Cottage", "", "High Street", "St. Helens", "WA11 8HS", locality="Rainford") == (
        "Rose Cottage, High Street Rainford, St. Helens, WA11 8HS"
    )
    # Обратная совместимость Person(addr_locality=None)
    p = Person(addr_locality=None)
    assert p.addr_locality == ""

