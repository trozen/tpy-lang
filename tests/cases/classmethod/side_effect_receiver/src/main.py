# Calling a classmethod through an instance expression still evaluates that
# expression -- its side effects happen and are then discarded, as in CPython.
from typing import Self

from tpy import Int32, Own

built: Int32 = 0


class Widget:
    def __init__(self, n: Int32):
        self.n = n

    @classmethod
    def blank(cls) -> Own[Self]:
        return cls(0)


def source() -> Own[Widget]:
    global built
    built += 1
    return Widget(5)


def main() -> None:
    w = source().blank()
    print(w.n, built)


main()
