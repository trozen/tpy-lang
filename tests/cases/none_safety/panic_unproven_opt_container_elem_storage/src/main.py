# The null path of an element read off an unproven STORAGE `Optional[list]`
# field: `::tpy::deref_optional_check(h.d)` unwraps the whole optional and
# panics before the element is read.
from typing import Optional

from tpy import int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class H:
    d: Optional[list[P]]

    def __init__(self, full: bool) -> None:
        self.d = None
        if full:
            self.d = [P(3)]


def first_x(h: H) -> int32:
    return h.d[0].x  # tpyc: warning(/Potential None access/)


def main() -> None:
    print(first_x(H(True)))
    print(first_x(H(False)))


main()
