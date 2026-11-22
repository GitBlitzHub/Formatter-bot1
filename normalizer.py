import html
import re
from datetime import datetime

from address import verify_address
from dlgen import generate_dl
from names import parse_dl, resolve_name, tokenize_name
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
    """Аккуратно приводит к Title Case, не ломая цифры и посткоды."""
    out = []
    for w in (text or "").split():
        if _WORD_RE.match(w) and (w.isupper() or w.islower()):
            out.append(w.title())
        else:
            out.append(w)
    return " ".join(out)


def _name_case(text: str) -> str:
    """Регистр для имён: Mc/Mac не трогаем, O'Brien, Smith-Jones, van der."""
    out = []
    for w in (text or "").split():
        if w.isupper() or w.islower():
            w = re.sub(r"(^|['’\-])([a-z])", lambda m: m.group(1) + m.group(2).upper(), w.lower())
            if w.lower().startswith("mc") and len(w) > 2:
                w = "Mc" + w[2:3].upper() + w[3:]
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

        # если второе число не может быть месяцем -> это mm/dd
        if mon > 12 and day <= 12:
            day, mon = mon, day

        r = _fmt(day, mon, year)
        if r:
            return r

        # fallback: вдруг это mm/dd/yyyy
        r = _fmt(a, b, year) or _fmt(b, a, year)
        if r:
            return r

    # 4) слитно: ддммгггг
    m = re.fullmatch(r"(\d{2})(\d{2})(\d{4})", s)
    if m:
        r = _fmt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if r:
            return r

    # не распознали — отдаём как есть, чтобы было видно
    return s


# ---------- сборка блока ----------

def _dl_clean(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip()).upper()


def _person_tokens(p: Person) -> list[str]:
    toks = [t for t in p.name_tokens if t and t.strip()]
    if not toks:   # запасной путь: LLM не дала токены
        toks = " ".join(x for x in (p.first_name, p.middle_names, p.last_name) if x).split()
    return tokenize_name(" ".join(toks))


async def build_block(p: Person, raw_text: str = "") -> str:
    """Собирает итоговый блок. Фамилию определяет names.resolve_name (не LLM)."""
    dl_text = p.dl_number or ""
    dl = parse_dl(dl_text) or parse_dl(raw_text)
    dl_str = _dl_clean(dl_text) or (dl.number if dl else "")

    tokens = _person_tokens(p)
    nm = resolve_name(
        tokens, dl=dl, llm_last=p.surname_guess or p.last_name,
        explicit=p.surname_marker, llm_first=p.first_name,
    )

    gender = nm.gender if nm.gender != "U" else p.gender
    title = _clean_title(p.title)
    if not title and gender in _GENDER_TITLE:
        title = _GENDER_TITLE[gender]

    first_name = " ".join(x for x in (title, _name_case(nm.first), _name_case(nm.middle)) if x)
    last_name = _name_case(nm.last).upper()

    dob = normalize_dob(p.dob)
    warnings = list(nm.warnings)

    if not dl_str:
        dl_str = generate_dl(nm.last, nm.first, nm.middle, dob, gender)
        if dl_str:
            warnings.append("DL не был указан — номер сгенерирован автоматически")
    if dl and dl.dob and dob and dl.dob != dob:
        warnings.append(f"DOB {dob} не совпадает с датой из номера прав ({dl.dob})")
    if dl and dl.dob and not dob:
        warnings.append(f"DOB не указана; по номеру прав это {dl.dob} (не подставлено автоматически)")

    # адрес
    ar = await verify_address(
        p.address, flat=p.addr_flat, number=p.addr_number, street=p.addr_street,
        town=p.addr_town, postcode=p.addr_postcode,
    )
    if ar.status == "NO_ADDRESS":
        addr_line = ""
    elif ar.status == "NOT_FOUND":
        addr_line = "❌ АДРЕС НЕ НАЙДЕН"
    else:
        addr_line = ar.formatted

    lines = [
        f"FIRST NAME: {first_name}",
        f"LAST NAME: {last_name}",
        f"DOB: {dob}",
        f"NUMBER DL: {dl_str}",
        f"ADRESS: {addr_line}",
    ]
    lines = [html.escape(x) for x in lines]

    badge = {
        "VERIFIED": "✅ Адрес подтверждён по базе Royal Mail (PAF)",
        "CONFIRMED": "✅ Адрес найден (посткод, улица и дом подтверждены по OSM)",
        "STREET_OK": "🟡 Посткод и улица реальны, дом подтвердить не удалось",
        "NOT_FOUND": "❌ Такого адреса нет / не удалось восстановить",
    }.get(ar.status)
    extra = []
    if badge:
        extra.append(badge)
    extra += [f"• {n}" for n in ar.notes]
    if ar.status == "NOT_FOUND" and p.address:
        extra.append(f"• Введено: {p.address}")
    extra += [f"⚠️ {w}" for w in warnings]
    if nm.confidence == "high" and nm.reasons:
        pass
    if extra:
        lines.append("")
        lines.append("<i>" + "\n".join(html.escape(x) for x in extra) + "</i>")
    return "\n".join(lines)
