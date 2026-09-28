# The null path of an element read off an unproven pointer-repr
# `Optional[list]` receiver: `::tpy::deref_check(d)` panics before the element
# is read, so the null pointer is never dereferenced.
from typing import Optional

from tpy import int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def first_x(d: Optional[list[P]]) -> int32:
    return d[0].x  # tpyc: warning(/Potential None access/)


def main() -> None:
    print(first_x([P(3)]))
    print(first_x(None))


main()
