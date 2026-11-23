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
