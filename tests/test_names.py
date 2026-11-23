import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from names import parse_dl, resolve_name, tokenize_name

DEAKIN = "DEAKI661187JA9GY 39"


def r(text, **kw):
    return resolve_name(tokenize_name(text), **kw)


def test_dl_parse():
    d = parse_dl("DL: " + DEAKIN)
    assert d.surname5 == "DEAKI" and d.gender == "F" and d.dob == "18.11.1967"
    assert (d.initial1, d.initial2) == ("J", "A")


def test_surname_last_with_dl():
    x = r("Judith Ann Deakin", dl=parse_dl(DEAKIN))
    assert (x.first, x.middle, x.last) == ("Judith", "Ann", "Deakin")


def test_surname_first_with_dl():
    x = r("Deakin Judith Ann", dl=parse_dl(DEAKIN))
    assert (x.first, x.middle, x.last) == ("Judith", "Ann", "Deakin")


def test_dl_beats_wrong_llm():
    # LLM ошиблась и назвала фамилией "Judith"
    x = r("Deakin Judith Ann", dl=parse_dl(DEAKIN), llm_last="Judith")
    assert x.last == "Deakin" and x.first == "Judith"


def test_ambiguous_names_resolved_by_dl():
    # Оба слова - личные имена/фамилии: Grant James, DL говорит GRANT
    dl = parse_dl("GRANT801010J99AB 01")
    for text in ("James Grant", "Grant James"):
        x = r(text, dl=dl)
        assert (x.first, x.last) == ("James", "Grant"), text


def test_comma_format():
    x = r("SMITH, John Edward", llm_last="SMITH", explicit="comma")
    assert (x.first, x.middle, x.last) == ("John", "Edward", "SMITH")


def test_caps_surname_first_no_dl():
    x = r("SMITH John Edward", llm_last="SMITH")
    assert (x.first, x.middle, x.last) == ("John", "Edward", "SMITH")


def test_compound_surname_at_start():
    x = r("Van Der Berg Michael", llm_last="Van Der Berg")
    assert (x.first, x.last) == ("Michael", "Van Der Berg")


def test_compound_surname_at_end():
    x = r("Michael Van Der Berg", llm_last="Van Der Berg")
    assert (x.first, x.last) == ("Michael", "Van Der Berg")


def test_mc_dl():
    x = r("Fiona McDonald", dl=parse_dl("MACDO701015F99AA 01"))
    assert x.last == "McDonald"


def test_label_overrides_given_name_dictionary():
    # Фамилия James: явная метка
    x = r("Peter James", llm_last="James", explicit="label")
    assert (x.first, x.last) == ("Peter", "James")


def test_low_confidence_warns():
    x = r("Lee Grant")           # ничего не помогает
    assert x.confidence == "low" and x.warnings


def test_surname_never_in_first():
    for text in ("Deakin Judith Ann", "Judith Ann Deakin", "Ann Deakin Judith"):
        x = r(text, dl=parse_dl(DEAKIN))
        assert "Deakin" not in (x.first + " " + x.middle)


def test_build_block_preserves_last_name_with_trailing_initials(monkeypatch):
    import asyncio
    import normalizer
    from address import AddressResult
    from schemas import Person

    async def fake_verify_address(*args, **kwargs):
        return AddressResult(
            status="CONFIRMED",
            formatted="22 Maritime Street, Pontypridd, CF37 1LQ",
        )

    monkeypatch.setattr(normalizer, "verify_address", fake_verify_address)

    for marker in ("label", ""):
        p = Person(
            title="Mr",
            gender="M",
            name_tokens=["Jason", "P", "T"],
            surname_guess="HARMAN",
            surname_marker=marker,
            first_name="Jason",
            middle_names="P T",
            last_name="HARMAN",
            dob="02.11.1985",
            address="22 Maritime Street, Pontypridd, CF37 1LQ",
        )
        out = asyncio.run(normalizer.build_block(p, ""))
        assert "FIRST NAME: Mr Jason P T\n" in out, out
        assert "LAST NAME: HARMAN\n" in out, out
        assert "NUMBER DL: HARMA811025JP9" in out, out
        assert "T9999" not in out, out


def test_resolve_name_trailing_initials_with_explicit_last():
    cases = [
        (["Jason", "P", "T", "HARMAN"], "HARMAN", "label", "Jason", "P T", "HARMAN"),
        (["Jason", "P", "T", "HARMAN"], "HARMAN", "", "Jason", "P T", "HARMAN"),
        (["John", "A", "B", "SMITH"], "SMITH", "label", "John", "A B", "SMITH"),
        (["John", "A", "B", "SMITH"], "SMITH", "", "John", "A B", "SMITH"),
    ]
    for tokens, llm_last, explicit, exp_first, exp_middle, exp_last in cases:
        x = resolve_name(tokens, llm_last=llm_last, explicit=explicit)
        assert (x.first, x.middle, x.last) == (exp_first, exp_middle, exp_last)


def test_resolve_name_penalizes_single_letter_surname_when_longer_tokens_exist():
    x = resolve_name(["Jason", "P", "T"])
    assert x.last != "T"


def test_person_tokens_label_prefers_last_name_and_skips_duplicates():
    from normalizer import _contains_tokens, _person_tokens
    from schemas import Person

    p = Person(
        name_tokens=["Jason", "P", "T"],
        surname_guess="T",
        surname_marker="label",
        last_name="HARMAN",
    )
    assert _person_tokens(p) == ["Jason", "P", "T", "HARMAN"]

    # Уже присутствует в name_tokens — не дублируется
    p_dup = Person(
        name_tokens=["Jason", "P", "T", "HARMAN"],
        surname_guess="HARMAN",
        surname_marker="label",
        last_name="HARMAN",
    )
    assert _person_tokens(p_dup) == ["Jason", "P", "T", "HARMAN"]

    # Пустые нормализованные токены (только пунктуация) не ломают _contains_tokens
    assert _contains_tokens(["Jason", "HARMAN"], ["---"]) is False
