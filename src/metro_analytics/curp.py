"""CURP (Clave Única de Registro de Población) construction and validation.

Implements the structure published by RENAPO:

    pos 1    first letter of the first surname
    pos 2    first internal vowel of the first surname
    pos 3    first letter of the second surname (X if none)
    pos 4    first letter of the given name
    pos 5-10 birth date YYMMDD
    pos 11   sex (H = male, M = female)
    pos 12-13 birth state code
    pos 14-16 first internal consonant of surname 1, surname 2, given name
    pos 17   homoclave: digit if born before 2000, letter otherwise
    pos 18   check digit
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

ALPHABET = "0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"
VOWELS = set("AEIOU")
CURP_RE = re.compile(r"^[A-Z][AEIOUX][A-Z]{2}[0-9]{6}[HM][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[0-9A-Z][0-9]$")

# Four-letter prefixes RENAPO replaces to avoid offensive words (2nd letter -> X).
INCONVENIENT = {
    "BACA", "BAKA", "BUEI", "BUEY", "CACA", "CACO", "CAGA", "CAGO", "CAKA", "CAKO", "COGE",
    "COGI", "COJA", "COJE", "COJI", "COJO", "COLA", "CULO", "FALO", "FETO", "GETA", "GUEI",
    "GUEY", "JETA", "JOTO", "KACA", "KACO", "KAGA", "KAGO", "KAKA", "KAKO", "KOGE", "KOGI",
    "KOJA", "KOJE", "KOJI", "KOJO", "KOLA", "KULO", "LILO", "LOCA", "LOCO", "LOKA", "LOKO",
    "MAME", "MAMO", "MEAR", "MEAS", "MEON", "MIAR", "MION", "MOCO", "MOKO", "MULA", "MULO",
    "NACA", "NACO", "PEDA", "PEDO", "PENE", "PIPI", "PITO", "POPO", "PUTA", "PUTO", "QULO",
    "RATA", "ROBA", "ROBE", "ROBO", "RUIN", "SENO", "TETA", "VACA", "VAGA", "VAGO", "VAKA",
    "VUEI", "VUEY", "WUEI", "WUEY",
}


def normalize(name: str) -> str:
    """Upper-case, strip accents (Ñ -> X as RENAPO does) and drop non-letters."""
    name = name.upper().replace("Ñ", "X")
    name = unicodedata.normalize("NFKD", name)
    return "".join(ch for ch in name if "A" <= ch <= "Z")


def _first_internal(word: str, consonant: bool) -> str:
    for ch in word[1:]:
        if (ch not in VOWELS) == consonant:
            return ch
    return "X"


def check_digit(curp17: str) -> str:
    total = sum(ALPHABET.index(ch) * (18 - i) for i, ch in enumerate(curp17[:17]))
    return str((10 - total % 10) % 10)


def is_valid(curp: str) -> bool:
    return bool(CURP_RE.match(curp)) and curp[17] == check_digit(curp)


def build(first_name: str, last_name: str, second_last_name: str | None, birth_date: date,
          sex: str, state_code: str, homoclave: str) -> str:
    p, m, n = normalize(last_name), normalize(second_last_name or ""), normalize(first_name)
    prefix = p[0] + _first_internal(p, consonant=False) + (m[0] if m else "X") + n[0]
    if prefix in INCONVENIENT:
        prefix = prefix[0] + "X" + prefix[2:]
    body = (
        prefix
        + birth_date.strftime("%y%m%d")
        + sex
        + state_code
        + _first_internal(p, True)
        + (_first_internal(m, True) if m else "X")
        + _first_internal(n, True)
        + homoclave
    )
    return body + check_digit(body)
