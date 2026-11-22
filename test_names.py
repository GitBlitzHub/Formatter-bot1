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
