# Returning a view of a tuple literal's OWNED element rejects: the tuple
# temporary holds its own copy of `s`, which dies at the end of the return
# statement -- the same rule as `return first1(s + "x")` at a str param.
from tpy import StrView


def first(t: tuple[str, int]) -> StrView:
    return t[0]


def head(s: str) -> StrView:
    return first((s, 1))  # tpyc: error(/Cannot return StrView referencing a local or temporary/)


def main() -> None:
    print(head("abcdefghijklmnopqrstuvwxyz0123"))


main()
