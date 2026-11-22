import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from dlgen import compute_check_letters, generate_dl, ebcdic_code


def test_ebcdic():
    assert (ebcdic_code("A"), ebcdic_code("J"), ebcdic_code("S"), ebcdic_code("0")) == (193, 209, 226, 240)


def test_generate_female():
    d = generate_dl("Deakin", "Judith", "Ann", "18.11.1967", "F")
    assert d[:14] == "DEAKI661187JA9" and len(d) == 16
    assert d[14:] == compute_check_letters(d[:14])


def test_generate_male_short_surname():
    d = generate_dl("Li", "John", "", "01.02.1980", "M")
    assert d[:14] == "LI999802010J99"


def test_not_enough_data():
    assert generate_dl("Smith", "John", "", "", "M") == ""
    assert generate_dl("Smith", "John", "", "01.02.1980", "U") == ""


def test_ni_numbers_not_dl():
    import asyncio
    from normalizer import build_block
    from schemas import Person
    for ni in ("PB690472A", "SE757564A", "JZ102318C", "JM621924A"):
        p = Person(name_tokens=["John", "Smith"], dob="01.02.1980", gender="M",
                   surname_guess="Smith", dl_number=ni)
        out = asyncio.run(build_block(p, f"John Smith {ni}"))
        assert f"NUMBER DL: {ni}" not in out, out
        assert "NUMBER DL: SMITH802010J" in out, out


def test_real_dl_kept():
    import asyncio
    from normalizer import build_block
    from schemas import Person
    p = Person(name_tokens=["Judith", "Ann", "Deakin"], dob="18.11.1967", gender="F",
               surname_guess="Deakin", dl_number="deaki661187ja9gy  39")
    assert "NUMBER DL: DEAKI661187JA9GY 39" in asyncio.run(build_block(p, ""))
