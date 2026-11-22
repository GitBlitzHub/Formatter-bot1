import re
from datetime import datetime

from schemas import Person

# ---------- титулы ----------

_TITLE_MAP = {
    "mr": "Mr", "mister": "Mr",
    "mrs": "Mrs",
    "ms": "Ms", "mz": "Ms",
    "miss": "Miss",
    "dr": "Dr", "doctor": "Dr",
    "prof": "Prof", "professor": "Prof",
    "sir": "Sir", "dame": "Dame",
    "lord": "Lord", "lady": "Lady",
    "mx": "Mx",
}

_GENDER_TITLE = {"M": "Mr", "F": "Ms"}


def _clean_title(raw: str) -> str:
    t = (raw or "").strip().strip(".").lower()
    return _TITLE_MAP.get(t, "")


# ---------- регистр ----------

_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'\-\.]*$")


def _smart_title(text: str) -> str:
    """Title Case, не ломая цифры, посткоды и скобки."""
    out = []
    for w in (text or "").split():
        core = w.strip("()")
        if _WORD_RE.match(core) and (core.isupper() or core.islower()):
            w = w.replace(core, core.title())
        out.append(w)
    return " ".join(out)


# ---------- дата ----------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}


def _fix_year(y: int) -> int:
    if y < 100:
        return 2000 + y if y <= 30 else 1900 + y
    return y


def _fmt(d: int, m: int, y: int) -> str | None:
    try:
        return datetime(y, m, d).strftime("%d.%m.%Y")
    except ValueError:
        return None


def normalize_dob(raw: str) -> str:
    if not raw:
        return ""

    s = str(raw).strip()
    s = re.sub(r"(\d{1,2})(st|nd|rd|th)\b", r"\1", s, flags=re.I)
    s = s.replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip()

    # 1) день + месяц-словом + год
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{2,4})", s)
    if m:
        mon = _MONTHS.get(m.group(2).lower())
        if mon:
            r = _fmt(int(m.group(1)), mon, _fix_year(int(m.group(3))))
            if r:
                return r

    # 2) месяц-словом + день + год
    m = re.fullmatch(r"([A-Za-z]+)\.?\s+(\d{1,2})\s+(\d{2,4})", s)
    if m:
        mon = _MONTHS.get(m.group(1).lower())
        if mon:
            r = _fmt(int(m.group(2)), mon, _fix_year(int(m.group(3))))
            if r:
                return r

    # 3) полностью цифрами
    m = re.fullmatch(r"(\d{1,4})[./\-\s](\d{1,2})[./\-\s](\d{1,4})", s)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))

        # ISO: yyyy-mm-dd
        if len(m.group(1)) == 4:
            r = _fmt(c, b, a)
            if r:
                return r

        day, mon, year = a, b, _fix_year(c)

        if mon > 12 and day <= 12:
            day, mon = mon, day

        r = _fmt(day, mon, year)
        if r:
            return r

        r = _fmt(a, b, year) or _fmt(b, a, year)
        if r:
            return r

    # 4) слитно: ддммгггг
    m = re.fullmatch(r"(\d{2})(\d{2})(\d{4})", s)
    if m:
        r = _fmt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if r:
            return r

    return s


# ---------- адрес ----------

_UK_POSTCODE = re.compile(
    r"\b(?:GIR\s?0AA|(?:[A-Z]{1,2}\d{1,2}[A-Z]?)\s?\d[A-Z]{2})\b",
    re.IGNORECASE,
)
_COUNTRY_TAIL = re.compile(
    r"[\s,]*\b(united kingdom|uk|great britain|england|scotland|wales|northern ireland)\b\.?[\s,]*$",
    re.IGNORECASE,
)


def normalize_address(raw: str) -> str:
    if not raw:
        return ""

    s = re.sub(r"\s+", " ", str(raw).strip())
    s = _COUNTRY_TAIL.sub("", s)
    s = re.sub(r"[\s,]+$", "", s)
    s = re.sub(r"^[\s,]+", "", s)

    postcode = ""
    m = _UK_POSTCODE.search(s)
    if m:
        postcode = m.group(0).upper().replace("  ", " ")
        s = (s[: m.start()] + " " + s[m.end():]).strip()

    parts = [_smart_title(p) for p in re.split(r"[,\n]+", s) if p.strip(" ,")]
    if postcode:
        parts.append(postcode)

    return ", ".join(parts)


# ---------- сборка блока ----------

def build_block(p: Person, address_override: str | None = None) -> str:
    title = _clean_title(p.title)
    if not title and p.gender in _GENDER_TITLE:
        title = _GENDER_TITLE[p.gender]

    name_parts = [title, _smart_title(p.first_name), _smart_title(p.middle_names)]
    first_name = " ".join(x for x in name_parts if x).strip()

    last_name = _smart_title(p.last_name).upper() if p.last_name else ""
    dob = normalize_dob(p.dob)
    dl = re.sub(r"\s+", " ", (p.dl_number or "").strip()).upper()

    if address_override is not None:
        addr = address_override
    else:
        addr = normalize_address(p.address)

    return (
        f"FIRST NAME: {first_name}\n"
        f"LAST NAME: {last_name}\n"
        f"DOB: {dob}\n"
        f"NUMBER DL: {dl}\n"
        f"ADRESS: {addr}"
    )