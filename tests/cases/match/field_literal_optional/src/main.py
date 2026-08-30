# Literal sub-patterns on Optional fields, plain and `as`-bound. The emitted
# comparison reads the field's storage form, where an empty slot compares
# false -- CPython's answer for a None field -- so nullability must not reach
# the literal's kind check as an unrecognized wrapper.
from typing import Optional


class P:
    x: Optional[int]
    name: Optional[str]

    def __init__(self, x: Optional[int], name: Optional[str]) -> None:
        self.x = x
        self.name = name


def describe(p: P) -> str:
    match p:
        case P(x=3):  # tpyc: ok
            return "three"
        case P(name="hi"):  # tpyc: ok
            return "hi"
        # The `as` spelling routes the same field type through the same check.
        case P(x=7 as v):  # tpyc: ok
            return "seven" if v is not None else "unreachable"
        case _:
            return "other"


def main() -> None:
    print(describe(P(3, None)))
    print(describe(P(4, "hi")))
    print(describe(P(7, None)))
    print(describe(P(None, None)))


main()
