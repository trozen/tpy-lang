# The generic spelling admits an enum type arg, but an enum-MEMBER access at an
# `Own[T]` constructor slot has no argument row.
# TPy rejects `Keep(Color.RED)` today.
from enum import Enum
from tpy import Own


class Color(Enum):
    RED = 1
    BLUE = 2


class Keep[T]:
    first: T

    def __init__(self, first: Own[T]) -> None:
        self.first = first


def main() -> None:
    p = Keep(Color.RED)  # tpyc: error(/call.ctor_arg.own_enum/)
    print(p.first == Color.BLUE)


main()
