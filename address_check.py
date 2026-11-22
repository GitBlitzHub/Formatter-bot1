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
_find_cache: dict[str, list[str] | None] = {}   # ключ: POSTCODE -> список адресов
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
    log.info("postcodes.io %s -> %s", key, res)
    async with _lock:
        _pc_cache[key] = res
    return res


# ---------------- уровень 3: getAddress.io ----------------

class _Limited(Exception):
    """Исчерпан бесплатный лимит."""


async def _ga_find(postcode: str) -> list[str] | None:
    """Все адреса по посткоду через /find. None — API лёг/лимит."""
    if not config.GETADDRESS_API_KEY:
        return None

    key = postcode.replace(" ", "").upper()
    async with _lock:
        if key in _find_cache:
            return _find_cache[key]

    # пробуем и без пробела, и с пробелом — у getAddress разные сборки
    for pc in {key, postcode.strip().upper()}:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                r = await c.get(
                    f"{_GETADDRESS}/find/{pc}",
                    params={"api-key": config.GETADDRESS_API_KEY, "expand": "true"},
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
            async with _lock:
                _find_cache[key] = []
            return []
        if r.status_code != 200:
            log.warning("getAddress.io вернул %s на %s", r.status_code, pc)
            continue

        try:
            body = r.json()
        except Exception:
            log.warning("getAddress.io: не JSON: %s", r.text[:200])
            continue

        if isinstance(body, dict) and body.get("Message"):
            log.warning("getAddress.io сообщение: %s", body.get("Message"))
            return None

        addrs = body.get("addresses") or []

        # addresses могут быть строками или объектами (при expand=true)
        flat: list[str] = []
        for a in addrs:
            if isinstance(a, str):
                flat.append(a)
            elif isinstance(a, dict):
                flat.append(a.get("formatted_address") or " ".join(
                    str(a.get(k, "")).strip()
                    for k in ("line_1", "line_2", "line_3", "line_4", "postcode")
                ).strip())

        log.info("getAddress /find %s -> %d адресов", pc, len(flat))
        async with _lock:
            _find_cache[key] = flat
        return flat

    return None


def _house_in(addresses: list[str], house: str) -> bool:
    pat = re.compile(rf"(?:^|\s){re.escape(house)}\b", re.IGNORECASE)
    return any(pat.search(a or "") for a in addresses)


def _street_in(addresses: list[str], street: str) -> bool:
    want = _norm(street)
    return any(want in _norm(a) for a in addresses)


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

    try:
        addrs = await _ga_find(pc)
    except _Limited:
        return None

    if addrs is None:                      # getAddress лёг
        return True if pc_ok is True else None
    if not addrs:                          # посткод есть, но адресов нет
        return False

    house = _house(address)
    street = _street(address)

    if house and _house_in(addrs, house):
        return True
    if street and _street_in(addrs, street):
        return True
    if house or street:
        return False

    return True if pc_ok is True else None