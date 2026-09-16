# A `str` literal pattern against a `char` subject stays rejected: `char` is a
# single C++ char, so the arm would emit `c == "a"` -- a pointer comparison,
# not the character test the source reads as. CPython (no `char` type) matches
# the one-character string; the rejection is the declared divergence recorded
# under docs/LANGUAGE_FEATURES.md "Control Flow" -> "Other" -> error
# diagnostics. Guards the sibling of the numeric family, where the cross-kind
# comparison IS admitted.
from tpy import char


def classify(c: char) -> str:
    match c:
        case "a":  # tpyc: error(/str literal pattern not valid for subject type 'char'/)
            return "a"
        case _:
            return "other"


def main() -> None:
    pass


main()
