from datetime import date

from metro_analytics import curp


def test_known_valid_curp():
    # Public RENAPO example
    assert curp.check_digit("HEGG560427MVZRRL0") == "4"
    assert curp.is_valid("HEGG560427MVZRRL04")


def test_tampering_breaks_check_digit():
    assert not curp.is_valid("HEGG560427MVZRRL05")
    assert not curp.is_valid("HEGG560428MVZRRL04")


def test_build_structure():
    c = curp.build("Gloria", "Hernández", "García", date(1956, 4, 27), "M", "VZ", "0")
    assert c == "HEGG560427MVZRRL04"


def test_build_handles_accents_enye_and_missing_second_surname():
    c = curp.build("Íñigo", "Muñoz", None, date(2001, 2, 3), "H", "DF", "A")
    assert c.startswith("MUXI010203HDF")
    assert curp.is_valid(c)


def test_inconvenient_words_are_masked():
    c = curp.build("Ignacio", "Bueno", "Estrada", date(1990, 1, 1), "H", "DF", "1")
    assert c[:4] == "BXEI"  # RENAPO list: BUEI -> BXEI
    assert curp.is_valid(c)


def test_sql_and_python_check_digit_agree(db, tables):
    sample = tables["operator"].curp.head(200).tolist()
    values = ",".join(f"('{c}')" for c in sample)
    res = db.query(f"SELECT c, metro.curp_check_digit(c) AS d FROM (VALUES {values}) v(c)")
    assert (res.c.str[17] == res.d.astype(str)).all()
