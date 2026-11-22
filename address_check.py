import asyncio
import logging
import re

import httpx

import config

log = logging.getLogger("address")

_GETADDRESS = "https://api.getaddress.io"
_POSTCODES_IO = "https://api.postcodes.io/postcodes"
_TIMEOUT = 12.0

_UK_POSTCODE = re.compile(
    r"\b(?:GIR\s?0AA|(?:[A-Z]{1,2}\d{1,2}[A-Z]?)\s?\d[A-Z]{2})\b",
    re.IGNORECASE,
)

_pc_cache: dict[str, bool | None] = {}
_house_cache: dict[str, bool | None] = {}    # ключ: POSTCODE|HOUSE
_street_cache: dict[str, bool | None] = {}   # ключ: POSTCODE
_lock = asyncio.Lock()


# ---------------- утилиты ----------------

def extract_postcode(address: str) -> str:
    if not address:
        return ""
    m = _UK_POSTCODE.search(address)
    return m.group(0).upper().replace("  ", " ").strip() if m else ""


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _first_segment(address: str) -> str:
    return (address or "").split(",")[0].strip()


def _house(address: str) -> str:
    m = re.match(r"^(\d+[A-Za-z]?)\b", _first_segment(address))
    return m.group(1) if m else ""


def _street(address: str) -> str:
    s = re.sub(r"^\d+[A-Za-z]?\s*", "", _first_segment(address))
    s = re.sub(r"[^\w\s'\-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------- уровень 1: посткод ----------------

async def _verify_postcode(postcode: str) -> bool | None:
    if not postcode:
        return False
    key = postcode.replace(" ", "").upper()
    async with _lock:
        if key in _pc_cache:
            return _pc_cache[key]

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{_POSTCODES_IO}/{key}")
    except Exception as e:
        log.warning("postcodes.io: %s", e)
        return None

    res = True if r.status_code == 200 else (False if r.status_code == 404 else None)
    async with _lock:
        _pc_cache[key] = res
    return res


# ---------------- уровень 3: getAddress.io ----------------

class _Limited(Exception):
    """Исчерпан бесплатный лимит."""


async def _ga_get(path: str) -> list[str] | None:
    """GET к getAddress.io. Список адресов, None — API лёг."""
    if not config.GETADDRESS_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(
                f"{_GETADDRESS}{path}",
                params={"api-key": config.GETADDRESS_API_KEY},
            )
    except Exception as e:
        log.warning("getAddress.io: %s", e)
        return None

    if r.status_code == 429:
        log.warning("getAddress.io: лимит исчерпан")
        raise _Limited()
    if r.status_code == 401:
        log.warning("getAddress.io: неверный GETADDRESS_API_KEY")
        return None
    if r.status_code == 404:
        return []
    if r.status_code != 200:
        log.warning("getAddress.io вернул %s", r.status_code)
        return None

    try:
        return r.json().get("addresses") or []
    except Exception:
        return None


async def _check_house(postcode: str, house: str) -> bool | None:
    """Точечная проверка дома через /get/{postcode}/{house}. None — не смогли."""
    key = f"{postcode.replace(' ', '').upper()}|{house.upper()}"
    async with _lock:
        if key in _house_cache:
            return _house_cache[key]

    addrs = await _ga_get(f"/get/{postcode}/{house}")
    if addrs is None:
        return None

    res = len(addrs) > 0
    async with _lock:
        _house_cache[key] = res
    log.info("getAddress /get %s/%s -> %s", postcode, house, res)
    return res


async def _check_street(postcode: str, street: str) -> bool | None:
    """Проверка улицы через /find/{postcode}. None — не смогли."""
    key = postcode.replace(" ", "").upper()
    async with _lock:
        if key in _street_cache:
            return _street_cache[key]

    addrs = await _ga_get(f"/find/{postcode}")
    if addrs is None:
        return None

    want = _norm(street)
    res = any(want in _norm(a) for a in addrs)
    async with _lock:
        _street_cache[key] = res
    log.info("getAddress /find %s street=%s -> %s", postcode, street, res)
    return res


# ---------------- главная ----------------

async def verify_full_address(address: str) -> bool | None:
    """
    True  — адрес подтверждён
    False — посткод вран, либо дом/улица не найдены
    None  — API лёг или кончился лимит, не наказываем
    """
    address = (address or "").strip()
    if not address:
        return False

    pc = extract_postcode(address)
    if not pc:
        return False

    pc_ok = await _verify_postcode(pc)
    if pc_ok is False:
        return False

    if not config.GETADDRESS_API_KEY:
        return True if pc_ok is True else None

    house = _house(address)
    street = _street(address)

    try:
        if house:
            res = await _check_house(pc, house)
            if res is not None:
                return res
        if street:
            res = await _check_street(pc, street)
            if res is not None:
                return res
    except _Limited:
        return None

    return True if pc_ok is True else None