"""Детерминированное определение фамилии.

Позиция слова НЕ используется как признак. Фамилия выбирается по сумме
независимых «улик», главная из которых — номер UK-прав (DVLA):

    SSSSS D MM DD Y II 9 AA   (+ 2 цифры выпуска)
    │     │ │  │  │ └─ инициалы: имя, второе имя ("9" если нет)
    │     │ │  │  └─ последняя цифра года рождения
    │     │ │  └─ день рождения
    │     │ └─ месяц (+50 для женщин)
    │     └─ десятилетие рождения
    └─ первые 5 букв фамилии (добивается девятками)

Остальные улики: явная метка (Surname:/Last name:/«SMITH, John»),
выбор LLM, слово КАПСОМ среди обычных, словарь частых личных имён.

Гарантия: фамилия — один непересекающийся срез токенов; в first/middle
попадают только оставшиеся токены, поэтому фамилия туда попасть не может.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- DL ----

_DL_RE = re.compile(
    r"\b([A-Z9]{5})(\d)(\d{2})(\d{2})(\d)([A-Z9])([A-Z9])(\d)([A-Z]{2})(?:\s?(\d{2}))?\b",
    re.IGNORECASE,
)


@dataclass
class DLInfo:
    number: str            # как в тексте, с пробелом перед выпуском
    surname5: str          # 5 символов фамилии (с 9-ками)
    initial1: str          # инициал имени ("9" = нет)
    initial2: str          # инициал второго имени ("9" = нет)
    gender: str            # "M"/"F"
    dob: str               # дд.мм.гггг или "" если не сходится
    

def parse_dl(text: str) -> DLInfo | None:
    """Ищет номер прав в произвольном тексте."""
    for m in _DL_RE.finditer(text or ""):
        sur, dec, mm, dd, yr, i1, i2, _n, _chk, issue = m.groups()
        sur, i1, i2 = sur.upper(), i1.upper(), i2.upper()
        month = int(mm)
        gender = "F" if month > 50 else "M"
        month = month - 50 if month > 50 else month
        day = int(dd)
        # даты вида 6-61-18-7 -> 1967
        dob = ""
        if 1 <= month <= 12 and 1 <= day <= 31:
            year_last = int(yr)
            dec_i = int(dec)
            century = 2000 if dec_i in (0, 1, 2) else 1900
            dob = f"{day:02d}.{month:02d}.{century + dec_i * 10 + year_last}"
        number = (sur + dec + mm + dd + yr + i1 + i2 + _n + _chk.upper()
                  + (f" {issue}" if issue else ""))
        return DLInfo(number, sur, i1, i2, gender, dob)
    return None


def _dl_surname_key(text: str) -> str:
    """Как DVLA строит 5 букв: только буквы, MC -> MAC, добивка 9."""
    s = re.sub(r"[^A-Za-z]", "", text).upper()
    if s.startswith("MC"):
        s = "MAC" + s[2:]
    return (s + "99999")[:5]


def _dl_match_score(cand: str, sur5: str) -> int:
    k = _dl_surname_key(cand)
    if k == sur5:
        return 100
    same = sum(a == b for a, b in zip(k, sur5))
    if same >= 4:           # одна опечатка
        return 70
    return 0


# ------------------------------------------------------ словарь имён ----

_GIVEN = set("""
james john robert michael william david richard joseph thomas charles christopher daniel matthew anthony mark
donald steven paul andrew joshua kenneth kevin brian george timothy ronald edward jason jeffrey ryan jacob gary
nicholas eric jonathan stephen larry justin scott brandon benjamin samuel gregory alexander patrick frank raymond
jack dennis jerry tyler aaron henry adam douglas nathan peter zachary kyle walter harold jeremy ethan carl keith
roger gerald christian terry sean arthur austin noah lawrence jesse joe bryan billy bruce albert willie gabriel
alan juan logan wayne ralph roy eugene randy vincent russell elijah louis bobby philip johnny harry oliver
jake charlie freddie alfie archie oscar leo max theo finley joshua riley dylan callum connor liam luke harvey
reece jamie lewis rhys craig stuart neil colin graham martin simon ian barry derek malcolm trevor clive
mary patricia jennifer linda elizabeth barbara susan jessica sarah karen lisa nancy betty margaret sandra ashley
kimberly emily donna michelle carol amanda dorothy melissa deborah stephanie rebecca sharon laura cynthia kathleen
amy angela shirley anna brenda pamela emma nicole helen samantha katherine christine debra rachel carolyn janet
catherine maria heather diane ruth julie olivia joyce virginia victoria kelly lauren christina joan evelyn judith
megan andrea cheryl hannah jacqueline martha gloria teresa ann sara madison frances kathryn janice jean abigail
alice julia judy sophia grace denise amber doris marilyn danielle beverly isabella theresa diana natalie brittany
charlotte marie kayla alexis lori ellie amelia isla ava mia poppy daisy lily rosie holly jessica chloe lucy
georgia millie evie phoebe imogen freya molly sophie zoe tracy gemma kerry joanne claire karen gillian leanne
sam ben tom dan nick matt chris jim bob bill dave steve mike tim tony andy alex joe jon pete rob ed ted
jane jo kate katie kathy beth liz lizzie sue susie sally hayley kirsty nikki vicky jenny jen becky
mohammed muhammad ahmed ali omar hassan hussain ibrahim yusuf aisha fatima priya raj amit sanjay anita
""".split())


# ------------------------------------------------------- токенизация ----

_TITLES = {
    "mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "dame", "lord",
    "lady", "mister", "doctor", "professor", "mz",
}
_PARTICLES = {
    "van", "von", "der", "den", "de", "del", "della", "di", "da", "du", "la",
    "le", "los", "las", "el", "al", "bin", "ibn", "ap", "st", "mac", "mc",
    "ten", "ter", "op", "het", "dos", "das",
}


def tokenize_name(text: str) -> list[str]:
    """Разбивает ФИО на слова, титулы отбрасывает. Дефис/апостроф сохраняет."""
    out = []
    for w in re.split(r"[\s,;]+", text or ""):
        w = w.strip()
        if not w:
            continue
        if w.strip(".").lower() in _TITLES:
            continue
        out.append(w)
    return out


# --------------------------------------------------------- результат ----

@dataclass
class NameResult:
    first: str = ""
    middle: str = ""
    last: str = ""
    gender: str = "U"
    confidence: str = "high"     # high | medium | low
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _norm(w: str) -> str:
    return re.sub(r"[^a-z]", "", w.lower())


def _spans(tokens: list[str]):
    """Допустимые срезы-фамилии: префикс или суффикс (не середина).

    Это НЕ позиционная эвристика: оба края проверяются одинаково,
    выбор делают улики. Середина исключена — имена не «обрамляют» фамилию.
    """
    n = len(tokens)
    seen = set()
    for k in range(1, min(n, 4) + 1):
        for start in (0, n - k):
            if (start, k) in seen:
                continue
            seen.add((start, k))
            if n - k < 1 and n > 1:       # нужно хотя бы одно имя
                continue
            yield start, k


def resolve_name(
    tokens: list[str],
    *,
    dl: DLInfo | None = None,
    llm_last: str = "",
    explicit: str = "",          # "label" | "comma" | ""
    llm_first: str = "",
) -> NameResult:
    """Выбирает фамилию среди токенов по уликам, а не по позиции."""
    res = NameResult()
    if not tokens:
        return res
    if len(tokens) == 1:
        res.first = ""
        res.last = tokens[0]
        res.confidence = "low"
        res.warnings.append("В тексте только одно слово ФИО — имя не найдено")
        return res

    llm_last_n = _norm(llm_last)
    llm_first_n = _norm(llm_first)
    upper_flags = [t.isupper() and len(t) > 1 for t in tokens]
    some_not_upper = not all(upper_flags)

    best = None
    scored = []
    for start, k in _spans(tokens):
        sur_tokens = tokens[start:start + k]
        rest = tokens[:start] + tokens[start + k:]
        sur = " ".join(sur_tokens)
        score = 0
        why = []

        if dl:
            s = _dl_match_score(sur, dl.surname5)
            if s:
                score += s
                why.append("совпала с номером прав" if s == 100 else "почти совпала с номером прав")
            # инициалы из прав
            if rest:
                i1 = _norm(rest[0])[:1].upper()
                i2 = _norm(rest[1])[:1].upper() if len(rest) > 1 else "9"
                if dl.initial1 != "9" and i1 == dl.initial1:
                    score += 20
                    why.append("инициал имени = инициал в правах")
                if (i2 or "9") == dl.initial2:
                    score += 10

        if explicit and llm_last_n and _norm(sur) == llm_last_n:
            score += 70
            why.append("явно помечена в тексте" if explicit == "label" else "формат «ФАМИЛИЯ, Имя»")

        if llm_last_n and _norm(sur) == llm_last_n:
            score += 15
            why.append("выбор LLM")

        if llm_first_n and rest and _norm(rest[0]) == llm_first_n:
            score += 5

        if some_not_upper and all(upper_flags[start:start + k]) and not any(
            upper_flags[i] for i in range(len(tokens)) if not (start <= i < start + k)
        ):
            score += 12
            why.append("единственное слово КАПСОМ")

        # словарь личных имён: токен из словаря — скорее имя
        rest_given = sum(_norm(t) in _GIVEN for t in rest[:2])
        if rest and _norm(rest[0]) in _GIVEN:
            score += 14
            why.append("остаток начинается с типичного личного имени")
        score += 2 * rest_given
        if all(_norm(t) in _GIVEN for t in sur_tokens):
            score -= 12           # «фамилия» целиком из личных имён — подозрительно

        # частицы (Van, De...) должны входить в фамилию, а не в имя
        if rest and _norm(rest[-1]) in _PARTICLES and k < 3:
            score -= 8
        if sur_tokens and _norm(sur_tokens[0]) in _PARTICLES:
            score += 4
        scored.append((score, start, k, sur, rest, why))

    scored.sort(key=lambda x: -x[0])
    best = scored[0]
    score, start, k, sur, rest, why = best
    margin = score - scored[1][0] if len(scored) > 1 else score

    res.last = sur
    res.first = rest[0] if rest else ""
    res.middle = " ".join(rest[1:])
    res.reasons = why

    joined = " ".join(why)
    strong = bool(
        (dl and _dl_match_score(sur, dl.surname5) == 100)
        or "явно помечена" in joined
        or "формат «ФАМИЛИЯ" in joined
    )
    if strong:
        res.confidence = "high"
    elif margin >= 15 and score >= 25:
        res.confidence = "medium"
    else:
        res.confidence = "low"
        res.warnings.append(
            "Не удалось уверенно отделить фамилию (нет номера прав/меток). "
            "Проверь вручную: фамилия = «%s»" % sur
        )

    if dl:
        res.gender = dl.gender
        if _dl_match_score(sur, dl.surname5) < 70:
            res.warnings.append(
                "Фамилия «%s» не совпадает с номером прав (%s…) — "
                "проверь фамилию или номер" % (sur, dl.surname5.rstrip("9"))
            )
    return res
