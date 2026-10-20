# An augmented key viewing a ternary of a new value and existing storage cannot
# be evaluated once without copying the element; binding it to a name first can.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, Assignment.)
from tpy import StrView


def label() -> str:
    return " y "


def view(s: str) -> StrView:
    return s.strip()


def main(flag: bool) -> None:
    names = [" n "]
    d = {"y": 1, "n": 2}
    d[view(label() if flag else names[0])] += 1  # tpyc: error(/a value in the subscript key here is chosen inside a conditional expression between a new value and existing storage/)
    print(d)


main(True)
