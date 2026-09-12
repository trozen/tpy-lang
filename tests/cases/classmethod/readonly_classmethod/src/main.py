# @readonly on a classmethod: there is no receiver to make const, so the method
# still emits as a plain (non-const) static member -- the decorator is accepted
# for symmetry with @staticmethod rather than changing the signature.
from typing import Final

from tpy import int32, readonly


class Limits:
    BASE: Final[int32] = 4

    @readonly
    @classmethod
    def base(cls) -> int32:
        return cls.BASE

    @readonly
    @classmethod
    def doubled(cls) -> int32:
        return cls.base() * 2


def main() -> None:
    print(Limits.base())
    print(Limits.doubled())


main()
