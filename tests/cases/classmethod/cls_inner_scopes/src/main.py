# A `cls` bound by a nested function's parameter is an ordinary inner-scope
# shadow, not a rebind of the defining-class alias: the nested body sees its own
# `cls` while the enclosing classmethod still resolves `cls` to the class.
from typing import Final

from tpy import int32


class Totals:
    BASE: Final[int32] = 10

    @classmethod
    def plain_nested(cls) -> int32:
        def double(n: int32) -> int32:
            return n * 2

        return double(4) + cls.BASE

    @classmethod
    def shadowing_nested(cls) -> int32:
        def triple(cls: int32) -> int32:
            return cls * 3

        return triple(2) + cls.BASE


def main() -> None:
    print(Totals.plain_nested())
    print(Totals.shadowing_nested())


main()
