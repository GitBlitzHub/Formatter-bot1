"""Проверка и восстановление британского адреса, вывод в формате Royal Mail.

Источники (по убыванию надёжности; всё бесплатно, кроме п.1):
 1. Ideal Postcodes (если задан IDEAL_POSTCODES_API_KEY) — это данные PAF
    (официальная база Royal Mail): подтверждает КАЖДЫЙ дом и даёт точный
    формат (строки адреса + POST TOWN + посткод).
 2. Бесплатно: HM Land Registry (открытые данные: реальные адреса E&W),
    затем postcodes.io (посткод существует?) + OpenStreetMap
    (улица/номер дома рядом с посткодом). OSM неполон по номерам домов,
    поэтому «дом не найден в OSM» ≠ «дома нет» — такой результат помечается
    как «не удалось подтвердить», а не как «не существует».

Статусы:
  VERIFIED   — адрес подтверждён по PAF
  CONFIRMED  — посткод, улица и номер дома найдены в OSM
  STREET_OK  — посткод и улица реальны, номер дома подтвердить нечем
  NOT_FOUND  — такого адреса нет (посткод/улица не существуют и не восстановлены)
  NO_ADDRESS — адреса нет во вводе
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field

import httpx

from posttowns import norm_place, resolve_post_town

log = logging.getLogger("address")

IDEAL_KEY = os.getenv("IDEAL_POSTCODES_API_KEY", "").strip()
TOWN_UPPER = os.getenv("ADDRESS_TOWN_UPPERCASE", "0").strip() in {"1", "true", "yes"}
UA = "tg-formatter-bot/2.0 (address validation)"
_TIMEOUT = httpx.Timeout(15.0)

_POSTCODE_RE = re.compile(
    r"\b(GIR\s?0AA|[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2})\b", re.IGNORECASE
)
_STREET_WORDS = ("road|street|lane|avenue|drive|close|court|gardens|place|crescent|square|terrace|grove|"
                 "park|way|walk|hill|row|mews|rise|view|green|parade|rd|st|ave|ln|dr|ct|cl|gdns")
_COUNTRY_TAIL = re.compile(
    r"[\s,]*\b(united kingdom|uk|u\.k\.|great britain|gb|england|scotland|wales|northern ireland)\b\.?[\s,]*$",
    re.IGNORECASE,
)


@dataclass
class AddressResult:
    status: str                 # VERIFIED | CONFIRMED | STREET_OK | NOT_FOUND | NO_ADDRESS
    formatted: str = ""
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------ утилиты ----

def format_postcode(pc: str) -> str:
    pc = re.sub(r"\s+", "", pc or "").upper()
    return f"{pc[:-3]} {pc[-3:]}" if len(pc) >= 5 else pc


def _title(s: str) -> str:
    out = []
    for w in (s or "").split():
        if re.fullmatch(r"[A-Za-z][A-Za-z'’\-\.]*", w) and (w.isupper() or w.islower()):
            w = re.sub(r"(^|['’\-])([a-z])", lambda m: m.group(1) + m.group(2).upper(), w.lower())
            if w.lower().startswith("mc") and len(w) > 2:
                w = "Mc" + w[2:].capitalize()
        out.append(w)
    return " ".join(out)


def _sim(a: str, b: str) -> float:
    a = re.sub(r"[^a-z0-9 ]", "", a.lower()).strip()
    b = re.sub(r"[^a-z0-9 ]", "", b.lower()).strip()
    return difflib.SequenceMatcher(None, a, b).ratio()


_ABBR = {
    "rd": "road", "st": "street", "ave": "avenue", "av": "avenue", "ln": "lane",
    "dr": "drive", "ct": "court", "cl": "close", "gdns": "gardens", "pl": "place",
    "cres": "crescent", "sq": "square", "ter": "terrace", "tce": "terrace",
    "gr": "grove", "pk": "park", "hwy": "highway", "pde": "parade",
}


def _expand(street: str) -> str:
    words = street.replace(".", "").split()
    return " ".join(_ABBR.get(w.lower(), w) for w in words)


def _ssim(a: str, b: str) -> float:
    """Сходство названий улиц: общий тип (Road/Street) не должен завышать оценку."""
    wa, wb = _expand(a).lower().split(), _expand(b).lower().split()
    if len(wa) > 1 and len(wb) > 1 and wa[-1] == wb[-1]:
        wa, wb = wa[:-1], wb[:-1]
    return _sim(" ".join(wa), " ".join(wb))


def _haversine(lat1, lon1, lat2, lon2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _clean_part(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip(" ,")).strip()


def _join(flat: str, number: str, street: str, town: str, pc: str, locality: str = "") -> str:
    flat = _clean_part(flat)
    number = _clean_part(number)
    street = _clean_part(street)
    locality = _clean_part(locality)
    town = _clean_part(town)
    pc = _clean_part(pc)

    if locality and town and norm_place(locality) == norm_place(town):
        locality = ""
    if locality and street:
        ns, nl = norm_place(street), norm_place(locality)
        if ns == nl or ns.endswith(" " + nl):
            locality = ""

    town_upper = TOWN_UPPER or os.getenv("ADDRESS_TOWN_UPPERCASE", "0").strip().lower() in {"1", "true", "yes"}
    town_fmt = town.upper() if town_upper else town
    line1 = " ".join(x for x in (number, street, locality) if x)
    parts = [p for p in (flat, line1, town_fmt, pc) if p]
    return ", ".join(parts)


def _resolve_locality_and_town(
    pc: str,
    town_in: str,
    locality_in: str,
    lr_town: str = "",
    lr_locality: str = "",
    pc_info: dict | None = None,
    osm_town: str = "",
) -> tuple[str, str]:
    post_town = resolve_post_town(
        pc, town_in=town_in, lr_town=lr_town, pc_info=pc_info, osm_town=osm_town,
    )
    loc = _clean_part(locality_in)
    if not loc and town_in:
        if norm_place(town_in) != norm_place(post_town):
            admin_dist = (pc_info or {}).get("admin_district") or ""
            parish = (pc_info or {}).get("parish") or ""
            bua = (pc_info or {}).get("bua") or ""
            if (
                admin_dist
                and norm_place(town_in) == norm_place(admin_dist)
                and norm_place(town_in) not in {norm_place(parish), norm_place(bua)}
            ):
                loc = ""
            else:
                loc = _clean_part(town_in)
    if not loc and lr_locality and norm_place(lr_locality) != norm_place(post_town):
        loc = _title(_clean_part(lr_locality))
    if loc and post_town and norm_place(loc) == norm_place(post_town):
        loc = ""
    return loc, post_town


# -------------------------------------------------------------- HTTP ----

_nom_lock = asyncio.Lock()
_nom_last = 0.0


async def _get(client: httpx.AsyncClient, url: str, **kw):
    for attempt in range(3):
        try:
            r = await client.get(url, **kw)
            if r.status_code in (429, 502, 503, 504):
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return r
        except (httpx.TimeoutException, httpx.TransportError) as e:
            log.warning("HTTP %s: %s", url, e)
            await asyncio.sleep(1 + attempt)
    return None


async def _nominatim(client, params: dict) -> list[dict]:
    """Nominatim: не чаще 1 запроса/сек (правило сервиса)."""
    global _nom_last
    async with _nom_lock:
        wait = 1.1 - (time.monotonic() - _nom_last)
        if wait > 0:
            await asyncio.sleep(wait)
        r = await _get(
            client, "https://nominatim.openstreetmap.org/search",
            params={**params, "format": "jsonv2", "addressdetails": 1,
                    "countrycodes": "gb", "limit": 10},
        )
        _nom_last = time.monotonic()
    if r is not None and r.status_code == 200:
        return r.json()
    return []


async def _overpass(client, query: str) -> list[dict]:
    for url in ("https://overpass-api.de/api/interpreter",
                "https://overpass.kumi.systems/api/interpreter"):
        r = await _get(client, url, params={"data": query})
        if r is not None and r.status_code == 200:
            try:
                return r.json().get("elements", [])
            except ValueError:
                continue
    return []


async def lookup_postcode(client, pc: str) -> dict | None:
    r = await _get(client, f"https://api.postcodes.io/postcodes/{pc.replace(' ', '')}")
    if r is not None and r.status_code == 200:
        return r.json().get("result")
    return None


async def _terminated(client, pc: str) -> bool:
    r = await _get(client, f"https://api.postcodes.io/terminated_postcodes/{pc.replace(' ', '')}")
    return r is not None and r.status_code == 200


# ----------------------------------------------- 1. PAF (Ideal Postcodes) ----

async def _paf_lookup(client, pc: str, flat: str, number: str, street: str):
    """Возвращает (строка_адреса | None, [заметки]) или None если сервис недоступен."""
    r = await _get(client, f"https://api.ideal-postcodes.co.uk/v1/postcodes/{pc.replace(' ', '')}",
                   params={"api_key": IDEAL_KEY})
    if r is None or r.status_code not in (200, 404):
        return None
    if r.status_code == 404:
        return "", ["Посткод не найден в PAF"]
    rows = r.json().get("result", [])
    if not rows:
        return "", ["Посткод не найден в PAF"]

    num = number.upper().replace(" ", "")
    best, best_s = None, 0.0
    for a in rows:
        s = 0.0
        a_num = (a.get("building_number") or "").upper()
        a_name = (a.get("building_name") or "")
        a_thor = a.get("thoroughfare") or a.get("dependant_thoroughfare") or ""
        if num:
            if a_num == num:
                s += 2
            elif num in (a_name or "").upper().split():
                s += 1.5
        if street:
            s += _sim(_expand(street), a_thor)
        if flat:
            sub = (a.get("sub_building_name") or "") + " " + a_name
            s += 1.5 * _sim(flat, sub) if sub.strip() else 0
        if s > best_s:
            best, best_s = a, s
    if not best:
        return "", ["В PAF нет такого дома по этому посткоду"]

    ok_num = (not num) or (best.get("building_number", "").upper() == num
                           or num in (best.get("building_name") or "").upper().split())
    ok_street = (not street) or _ssim(street, best.get("thoroughfare") or "") >= 0.8
    if not (ok_num and ok_street):
        return "", [f"В PAF по посткоду {pc} нет дома «{number} {street}»".replace("  ", " ")]

    lines = [best.get(k) for k in ("line_1", "line_2", "line_3") if best.get(k)]
    town = best.get("post_town", "")
    town = town.upper() if TOWN_UPPER else _title(town)
    return ", ".join(lines + [town, best["postcode"]]), []


# ------------------- 2a. бесплатно: HM Land Registry (открытые данные) ----
# Реальные адреса всех проданных с 1995 г. объектов Англии и Уэльса
# (paon = номер/название дома, saon = квартира, улица, город, посткод).
# Это НЕ PAF, но покрывает большинство жилых адресов. Шотландия/С.Ирландия
# и ни разу не продававшиеся объекты отсутствуют — отсутствие не доказывает «нет дома».

_LR_URL = "https://landregistry.data.gov.uk/landregistry/query"
_LR_TIMEOUT = httpx.Timeout(4.0)
_LR_CACHE: dict[str, list[dict]] = {}
_LR_DOWN_UNTIL = 0.0
_LR_COOLDOWN_SEC = 60.0


def _sq(v: str) -> str:
    return v.replace("\\", "").replace('"', "")


async def _lr_query(client, where: str, key: str) -> list[dict]:
    global _LR_DOWN_UNTIL
    if key in _LR_CACHE:
        return _LR_CACHE[key]
    if time.monotonic() < _LR_DOWN_UNTIL:
        return []
    q = ("PREFIX lrcommon: <http://landregistry.data.gov.uk/def/common/>\n"
         "SELECT DISTINCT ?paon ?saon ?street ?town ?locality ?pc WHERE {\n" + where +
         "\n OPTIONAL{?a lrcommon:paon ?paon} OPTIONAL{?a lrcommon:saon ?saon}"
         "\n ?a lrcommon:street ?street . OPTIONAL{?a lrcommon:town ?town}"
         "\n OPTIONAL{?a lrcommon:locality ?locality}"
         "\n ?a lrcommon:postcode ?pc } LIMIT 400")
    try:
        r = await client.get(
            _LR_URL,
            params={"query": q},
            headers={"Accept": "application/sparql-results+json"},
            timeout=_LR_TIMEOUT,
        )
    except (httpx.TimeoutException, httpx.TransportError) as e:
        log.warning("HTTP %s: %s", _LR_URL, e)
        _LR_DOWN_UNTIL = time.monotonic() + _LR_COOLDOWN_SEC
        return []
    rows: list[dict] = []
    if r is not None and r.status_code == 200:
        try:
            for b in r.json()["results"]["bindings"]:
                rows.append({k: b[k]["value"] for k in b})
        except (ValueError, KeyError):
            rows = []
        _LR_CACHE[key] = rows
    elif r is None or r.status_code >= 500:
        _LR_DOWN_UNTIL = time.monotonic() + _LR_COOLDOWN_SEC
    return rows


async def _lr_by_postcode(client, pc: str) -> list[dict]:
    return await _lr_query(client, f'?a lrcommon:postcode "{_sq(pc)}" .', "pc:" + pc)


async def _lr_by_street(client, street: str, town: str, number: str) -> list[dict]:
    w = f'?a lrcommon:street "{_sq(street.upper())}" . '
    if town:
        w += f'?a lrcommon:town "{_sq(town.upper())}" . '
    if number:
        w += f'?a lrcommon:paon "{_sq(number.upper())}" . '
    return await _lr_query(client, w, f"st:{street}|{town}|{number}")


def _lr_match(rows: list[dict], flat: str, number: str, street: str):
    """-> (street_name | None, row_with_house | None, other_numbers[])"""
    streets = {r["street"] for r in rows if "street" in r}
    best = max(streets, key=lambda x: _ssim(_expand(street), x), default=None) if street else None
    if street and (not best or _ssim(_expand(street), best) < 0.8):
        return None, None, []
    n = number.upper().replace(" ", "")
    same = [r for r in rows if best is None or r.get("street") == best]
    hit = None
    if n:
        for r in same:
            if r.get("paon", "").upper().replace(" ", "") == n:
                if flat and r.get("saon") and _sim(flat, r["saon"]) < 0.6:
                    hit = hit or None
                    continue
                hit = r
                break
    others = sorted({r.get("paon", "") for r in same if r.get("paon")})[:12]
    return best, hit, others


async def _lr_verify(client, pc, flat, number, street, town_in, notes, locality_in="", pc_info=None):
    rows = await _lr_by_postcode(client, pc)
    if not rows or not street:
        return None
    best, hit, others = _lr_match(rows, flat, number, street)
    if best is None:
        return None
    lr_town = _title(next((r.get("town", "") for r in rows if r.get("street") == best and r.get("town")), ""))
    lr_locality = _title(next((r.get("locality", "") for r in rows if r.get("street") == best and r.get("locality")), ""))
    loc, town = _resolve_locality_and_town(
        pc, town_in, locality_in, lr_town=lr_town, lr_locality=lr_locality, pc_info=pc_info,
    )
    name = _title(best)
    if _expand(street).lower() != name.lower():
        notes.append(f"Улица исправлена: «{street}» → «{name}»")
    if hit:
        notes.append("Дом найден в открытом реестре HM Land Registry")
        return AddressResult("CONFIRMED", _join(flat, number, name, town, pc, locality=loc), notes)
    if not number:
        return AddressResult("STREET_OK", _join(flat, "", name, town, pc, locality=loc), notes)
    # дом с таким номером на этой улице под ЭТИМ посткодом не найден: ищем под другим
    alt = await _lr_by_street(client, best, town_in, number)
    alt = [r for r in alt if r.get("pc") and r["pc"] != pc]
    if alt:
        npc = alt[0]["pc"]
        notes.append(f"Дом {number} на {name} числится под посткодом {npc}, а не {pc} — посткод исправлен")
        return AddressResult("CONFIRMED", _join(flat, number, name, town, npc, locality=loc), notes)
    notes.append(f"Посткод и улица реальны, но дома {number} в реестре нет "
                 f"(на этой улице под {pc} есть: {', '.join(others)}). Возможна ошибка в номере "
                 "либо дом не продавался с 1995 г.")
    return AddressResult("STREET_OK", _join(flat, number, name, town, pc, locality=loc), notes)


# ------------------------------------------ 2b. бесплатно: OSM + postcodes.io ----

def _near(r: dict, lat: float, lon: float, meters: float) -> bool:
    try:
        return _haversine(lat, lon, float(r["lat"]), float(r["lon"])) <= meters
    except (KeyError, ValueError):
        return False


_OVERPASS = (
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)


async def _nearby_roads(client, lat: float, lon: float) -> set[str]:
    """Названия улиц вокруг точки через Nominatim reverse (сетка 3x3, ~350 м)."""
    global _nom_last
    out: set[str] = set()
    dlat, dlon = 0.0032, 0.0055
    for i, j in [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)]:
        async with _nom_lock:
            wait = 1.1 - (time.monotonic() - _nom_last)
            if wait > 0:
                await asyncio.sleep(wait)
            r = await _get(client, "https://nominatim.openstreetmap.org/reverse",
                           params={"lat": lat + i * dlat, "lon": lon + j * dlon, "zoom": 17,
                                   "format": "jsonv2", "addressdetails": 1})
            _nom_last = time.monotonic()
        if r is not None and r.status_code == 200:
            road = r.json().get("address", {}).get("road")
            if road:
                out.add(road)
    return out


async def _osm_check(client, lat: float, lon: float, number: str, street: str, pc: str, town: str):
    """-> (имя_улицы | None, дом_найден, посткод_дома, город_дома)

    1) Nominatim по «номер улица, посткод» — подтверждает дом.
    2) Nominatim по улице в рамке вокруг посткода — подтверждает улицу.
    3) Overpass (если доступен, короткий таймаут) — список улиц рядом для
       исправления опечатки в названии.
    """
    want = _expand(street)
    n = number.upper().replace(" ", "")

    # 1. дом
    if n:
        for r in await _nominatim(client, {"q": f"{number} {street}, {pc}"}):
            a = r.get("address", {})
            if (a.get("house_number", "").upper().replace(" ", "") == n
                    and _ssim(want, a.get("road", "")) >= 0.85
                    and _near(r, lat, lon, 2500)):
                return (a.get("road"), True, a.get("postcode", ""),
                        a.get("town") or a.get("city") or a.get("village") or "")

    # 2. улица в рамке ±~3 км
    dx = 0.045
    vb = f"{lon - dx},{lat + dx / 1.7},{lon + dx},{lat - dx / 1.7}"
    for q in (f"{street}", f"{street}, {town}" if town else ""):
        if not q:
            continue
        for r in await _nominatim(client, {"q": q, "viewbox": vb, "bounded": 1}):
            a = r.get("address", {})
            road = a.get("road") or r.get("name", "")
            if road and _ssim(want, road) >= 0.9 and _near(r, lat, lon, 3500):
                return road, False, "", a.get("town") or a.get("city") or a.get("village") or ""

    # 3. Overpass: улицы и дома рядом (опечатка в названии, неполный Nominatim)
    qy = (f'[out:json][timeout:12];(way["highway"]["name"](around:1000,{lat},{lon});'
          f'nwr["addr:housenumber"]["addr:street"](around:1000,{lat},{lon}););out tags 600;')
    tags = []
    for url in _OVERPASS:
        try:
            r = await asyncio.wait_for(_get(client, url, params={"data": qy}), 7)
        except asyncio.TimeoutError:
            continue
        if r is not None and r.status_code == 200:
            try:
                tags = [e.get("tags", {}) for e in r.json().get("elements", [])]
                break
            except ValueError:
                continue
    names = {t["name"] for t in tags if "name" in t and "addr:housenumber" not in t}
    names |= {t["addr:street"] for t in tags if "addr:street" in t}
    if not names:                       # Overpass недоступен — нащупываем улицы через reverse
        names = await _nearby_roads(client, lat, lon)
    best = max(names, key=lambda x: _ssim(want, x), default=None)
    if best and _ssim(want, best) >= 0.8:
        house = pc_h = town_h = ""
        for t in tags:
            if (n and t.get("addr:housenumber", "").upper().replace(" ", "") == n
                    and t.get("addr:street") == best):
                house = True
                pc_h = t.get("addr:postcode", "")
                town_h = t.get("addr:city", "")
                break
        return best, bool(house), pc_h, town_h
    return None, False, "", ""


async def _free_lookup(client, pc: str, pc_info: dict | None, flat, number, street, town_in, notes, locality_in=""):
    """Свободная проверка. Возвращает AddressResult."""
    lat = lon = None
    if pc_info:
        lat, lon = pc_info.get("latitude"), pc_info.get("longitude")

    # город: сначала проверяем локальный справочник outcode -> Post Town
    nom_town = ""
    if not resolve_post_town(pc, town_in=town_in, pc_info=pc_info) and lat is not None:
        res = await _nominatim(client, {"q": pc})
        for r in res:
            a = r.get("address", {})
            if a.get("postcode", "").replace(" ", "").upper().startswith(pc.replace(" ", "")[:-1].upper()):
                nom_town = a.get("town") or a.get("city") or a.get("village") or a.get("hamlet") or ""
                if nom_town:
                    break

    if not street:
        # улицы нет — подтвердить нечего, кроме посткода
        notes.append("Улица не указана — проверен только посткод")
        loc, town = _resolve_locality_and_town(pc, town_in, locality_in, pc_info=pc_info, osm_town=nom_town)
        out = _join(flat, number, "", town, pc, locality=loc)
        return AddressResult("STREET_OK", out, notes)

    if lat is None:
        return AddressResult("NOT_FOUND", "", notes + ["Нет координат посткода для проверки улицы"])

    found, house, house_pc, house_town = await _osm_check(client, lat, lon, number, street, pc, town_in)
    if not found:
        return AddressResult(
            "NOT_FOUND", "",
            notes + [f"Улица «{street}» не найдена рядом с посткодом {pc}. "
                     "Либо ошибка в улице/посткоде, либо такого адреса нет."])

    if _ssim(street, found) < 0.97 or _expand(street).lower() != found.lower():
        notes.append(f"Улица исправлена: «{street}» → «{found}»")
    loc, town = _resolve_locality_and_town(
        pc, town_in, locality_in, pc_info=pc_info, osm_town=house_town or nom_town,
    )
    if house:
        if house_pc and format_postcode(house_pc) != pc:
            notes.append(f"В OSM этот дом числится под посткодом {format_postcode(house_pc)}")
        out = _join(flat, number, found, town, pc, locality=loc)
        return AddressResult("CONFIRMED", out, notes)

    if number:
        notes.append("Номер дома не удалось подтвердить (в бесплатных данных OSM есть не все дома)")
    out = _join(flat, number, found, town, pc, locality=loc)
    return AddressResult("STREET_OK", out, notes)


# ------------------------------------- восстановление неверного посткода ----

def _pc_variants(pc: str) -> list[str]:
    """Типичные опечатки: O↔0, I↔1, S↔5, B↔8, Z↔2 (в инкоде — цифра первая)."""
    raw = re.sub(r"\s+", "", pc).upper()
    if len(raw) < 5:
        return []
    out, swap = set(), {"O": "0", "0": "O", "I": "1", "1": "I", "S": "5", "5": "S",
                        "B": "8", "8": "B", "Z": "2", "2": "Z", "L": "1", "Q": "0"}
    for i, ch in enumerate(raw):
        if ch in swap:
            v = raw[:i] + swap[ch] + raw[i + 1:]
            out.add(format_postcode(v))
    return list(out)


async def _restore_postcode(client, pc, number, street, town):
    """Ищем настоящий посткод, если введённый не существует."""
    for v in _pc_variants(pc):
        info = await lookup_postcode(client, v)
        if info:
            return format_postcode(info["postcode"]), info, f"Посткод исправлен (опечатка): {pc} → {format_postcode(info['postcode'])}"
    if street and number:
        rows = await _lr_by_street(client, street, town, number) or \
            (await _lr_by_street(client, street, "", number) if town else [])
        pcs = {r["pc"] for r in rows if r.get("pc")}
        if len(pcs) == 1:
            p = pcs.pop()
            info = await lookup_postcode(client, p)
            if info:
                return p, info, f"Посткод {pc or '(не указан)'} неверен/отсутствует; дом {number} на {_title(street)} найден в реестре под {p}"
    if street:
        q = ", ".join(x for x in (f"{number} {street}".strip(), town) if x)
        for r in await _nominatim(client, {"q": q}):
            a = r.get("address", {})
            p = a.get("postcode")
            if p and _ssim(street, a.get("road", "")) >= 0.85:
                p = format_postcode(p)
                info = await lookup_postcode(client, p)
                if info:
                    return p, info, f"Посткод {pc or '(не указан)'} не найден/неверен; по улице и городу найден {p}"
    return "", None, ""


# ------------------------------------------------------------ главное ----

async def verify_address(
    raw: str, *, flat: str = "", number: str = "", street: str = "",
    town: str = "", postcode: str = "", locality: str = "",
) -> AddressResult:
    raw = re.sub(r"\s+", " ", (raw or "").strip())
    if not raw and not (street or postcode):
        return AddressResult("NO_ADDRESS")

    raw = _COUNTRY_TAIL.sub("", raw)
    # посткод из явного поля или из сырой строки
    pc_text = postcode or (_POSTCODE_RE.search(raw).group(0) if _POSTCODE_RE.search(raw) else "")
    pc = format_postcode(pc_text) if pc_text else ""

    if not street and raw:      # LLM не разобрала — берём «как есть», чистим
        rest = _POSTCODE_RE.sub("", raw)
        parts = [p.strip() for p in re.split(r"[,\n]+", rest) if p.strip()]
        if parts:
            m = re.match(r"^(\d+[A-Za-z]?)\s+(.+)$", parts[0])
            if m and not number:
                number, street = m.group(1), m.group(2)
            else:
                street = parts[0]
            if len(parts) > 1 and not town:
                town = parts[-1]
            elif len(parts) == 1:      # «12A Fleet Street London» без запятых
                mm = re.match(rf"^(.*?\b(?:{_STREET_WORDS}))\b\s*(.*)$", street, re.I)
                if mm:
                    street, town = mm.group(1), town or mm.group(2)

    number, street = number.strip(), _title(_expand(street.strip())) if street else ""
    flat, town = _title(flat.strip()), _title(town.strip())
    locality = _title(locality.strip())
    notes: list[str] = []

    async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": UA}) as client:
        info = await lookup_postcode(client, pc) if pc else None

        if pc and not info:
            if await _terminated(client, pc):
                notes.append(f"Посткод {pc} закрыт (больше не используется)")
            new_pc, new_info, why = await _restore_postcode(client, pc, number, street, town)
            if not new_pc:
                return AddressResult(
                    "NOT_FOUND", "",
                    notes + [f"Посткода {pc} не существует, и восстановить адрес по остальным данным не удалось"])
            pc, info = new_pc, new_info
            notes.append(why)
        elif not pc:
            new_pc, new_info, why = await _restore_postcode(client, "", number, street, town)
            if not new_pc:
                return AddressResult(
                    "NOT_FOUND", "",
                    ["Посткод не указан и по улице/городу его определить не удалось"])
            pc, info = new_pc, new_info
            notes.append(why)

        # PAF — приоритет
        if IDEAL_KEY:
            r = await _paf_lookup(client, pc, flat, number, street)
            if r is not None:
                addr, n = r
                if addr:
                    return AddressResult("VERIFIED", addr, notes)
                # PAF ответил «нет» → пробуем соседние посткоды из восстановления
                return AddressResult("NOT_FOUND", "", notes + n)
            notes.append("PAF-сервис недоступен — использована бесплатная проверка")

        res = await _lr_verify(client, pc, flat, number, street, town, list(notes), locality_in=locality, pc_info=info)
        if res is not None:
            return res
        return await _free_lookup(client, pc, info, flat, number, street, town, notes, locality_in=locality)
