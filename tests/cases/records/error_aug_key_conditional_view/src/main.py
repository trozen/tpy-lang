# An augmented key that is a view chosen inside a conditional cannot be
# evaluated once; binding the viewed value to a name first can.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, Assignment.)
from tpy import StrView


def label(s: str) -> str:
    return " " + s + " "


def view(s: str) -> StrView:
    return s.strip()


def main(flag: bool) -> None:
    d = {"y": 1, "n": 2}
    d[view(label("y")) if flag else view(label("n"))] += 1  # tpyc: error(/the subscript key here is a view chosen inside a conditional expression and cannot be evaluated once; bind the value it views to a name first/)
    print(d)


main(True)
