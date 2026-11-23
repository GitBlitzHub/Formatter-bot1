"""Локальный справочник UK outcode -> Royal Mail Post Town."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent / "data" / "uk_outcodes_posttown.json"

_FULL_PC_RE = re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?|GIR)\s*(\d[A-Z]{2})$")
_OUTCODE_RE = re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?|GIR)$")


@lru_cache(maxsize=1)
def _load_outcodes() -> dict[str, str | list[str]]:
    if not _DATA_PATH.exists():
        return {}
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


def norm_place(s: str) -> str:
    """Нормализация названия города/locality для сравнения без учёта регистра и точек."""
    s = (s or "").lower().replace(".", " ").replace(",", " ").replace("&", " and ")
    return re.sub(r"\s+", " ", s).strip()


def extract_outcode(postcode: str) -> str:
    """Извлекает outward code (например 'NG9', 'SW1A', 'GIR') из посткода."""
    clean = re.sub(r"\s+", "", (postcode or "").upper())
    if not clean:
        return ""
    m = _FULL_PC_RE.match(clean)
    if m:
        return m.group(1)
    m = _OUTCODE_RE.match(clean)
    if m:
        return m.group(1)
    return ""


def get_post_town_candidates(postcode: str) -> list[str]:
    """Возвращает список канонических Post Town для outcode (пустой, если неизвестен)."""
    outcode = extract_outcode(postcode)
    if not outcode:
        return []
    val = _load_outcodes().get(outcode)
    if not val:
        return []
    return [val] if isinstance(val, str) else list(val)


def _match_candidate(hint: str, candidates: list[str]) -> str:
    nh = norm_place(hint)
    if not nh:
        return ""
    for c in candidates:
        if norm_place(c) == nh:
            return c
    return ""


def _title_fallback(s: str) -> str:
    out = []
    for w in (s or "").split():
        if re.fullmatch(r"[A-Za-z][A-Za-z'’\-\.]*", w) and (w.isupper() or w.islower()):
            w = re.sub(r"(^|['’\-])([a-z])", lambda m: m.group(1) + m.group(2).upper(), w.lower())
        out.append(w)
    return " ".join(out)


def resolve_post_town(
    postcode: str,
    town_in: str = "",
    lr_town: str = "",
    pc_info: dict | None = None,
    osm_town: str = "",
) -> str:
    """Определяет официальный Royal Mail Post Town по outcode и подсказкам."""
    candidates = get_post_town_candidates(postcode)
    if len(candidates) == 1:
        return candidates[0]

    if len(candidates) > 1:
        pc_hints: list[str] = []
        if pc_info:
            for k in ("parish", "admin_district", "bua"):
                v = pc_info.get(k)
                if v:
                    pc_hints.append(str(v))
        for hint in (lr_town, town_in, *pc_hints, osm_town):
            hit = _match_candidate(hint, candidates)
            if hit:
                return hit
        return candidates[0]

    # Неизвестный outcode — fallback
    for hint in (lr_town, osm_town, town_in, (pc_info or {}).get("admin_district") or ""):
        if hint and hint.strip():
            return _title_fallback(hint.strip())
    return ""
