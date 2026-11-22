"""Генерация номера UK DL, если его нет во входных данных."""
from __future__ import annotations

import re

ALPHA23 = "ABCDEFGHJKLMNPRSTUVWXYZ"  # 23 буквы (без I, O, Q)


def ebcdic_code(ch: str) -> int:
    """Возвращает код символа A-Z или 0-9 в таблице EBCDIC."""
    if 'A' <= ch <= 'I':
        return 193 + (ord(ch) - ord('A'))
    elif 'J' <= ch <= 'R':
        return 209 + (ord(ch) - ord('J'))
    elif 'S' <= ch <= 'Z':
        return 226 + (ord(ch) - ord('S'))
    elif '0' <= ch <= '9':
        return 240 + (ord(ch) - ord('0'))
    raise ValueError(f"Недопустимый символ: {ch}")


def compute_check_letters(prefix14: str) -> str:
    """Вычисляет 2 контрольные буквы по 14-символьному префиксу."""
    prefix14 = prefix14.strip().upper()
    if len(prefix14) != 14:
        raise ValueError("prefix14 должен быть длиной 14")
    s = sum((15 - i) * ebcdic_code(c) for i, c in enumerate(prefix14))
    v = (-s) % 523
    return ALPHA23[v % 23] + ALPHA23[v // 23]


def _surname5(surname: str) -> str:
    s = re.sub(r"[^A-Za-z]", "", surname or "").upper()
    if s.startswith("MC"):
        s = "MAC" + s[2:]
    return (s + "99999")[:5]


def _initial(name: str) -> str:
    m = re.search(r"[A-Za-z]", name or "")
    return m.group(0).upper() if m else "9"


def generate_dl(surname: str, first: str, middle: str, dob: str, gender: str) -> str:
    """dob = дд.мм.гггг, gender = M/F. Возвращает 16 символов или "" если данных мало."""
    m = re.fullmatch(r"\s*(\d{1,2})\D(\d{1,2})\D(\d{4})\s*", dob or "")
    if not m or gender not in ("M", "F") or not _surname5(surname).strip("9"):
        return ""
    day, month, year = int(m[1]), int(m[2]), int(m[3])
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return ""
    if gender == "F":
        month += 50
    first_tok = _initial(first)
    mid_tok = _initial(middle)
    prefix = (f"{_surname5(surname)}{str(year)[2]}{month:02d}{day:02d}{str(year)[3]}"
              f"{first_tok}{mid_tok}9")
    return prefix + compute_check_letters(prefix)
