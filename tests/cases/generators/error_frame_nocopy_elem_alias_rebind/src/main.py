# An alias of a @nocopy element of a tuple frame slot the body rebinds is
# rejected like any element alias (BUGS.md#resumable-alias-identity).
# Binding a copy is no workaround here: a `copy()` of a @nocopy element
# does not compile.
from typing import Iterator

from tpy import int32, nocopy


@nocopy
class U:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def g(c: bool) -> Iterator[int32]:
    t = (U(1), U(2))
    saved = t[1]  # tpyc: error(/binding 'saved' to an element inside 't' is not yet supported in a generator.*last reassignment of 't'$/)
    yield saved.x
    if c:
        t = (U(9), U(8))
    yield saved.x


def main() -> None:
    for v in g(True):
        print(v)


main()
