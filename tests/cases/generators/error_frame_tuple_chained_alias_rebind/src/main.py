# An alias reached from a tuple frame slot through a record field and a list
# (`t[0].xs[1]`) is rejected when the body rebinds the slot after it, like a
# direct element alias (BUGS.md#resumable-alias-identity): the path crosses
# the tuple that holds the record by value, so the rebind replaces it.
from typing import Iterator

from tpy import int32


class A:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class H:
    xs: list[A]

    def __init__(self, i: int32) -> None:
        self.xs = [A(i), A(i + 1)]


def g(c: bool) -> Iterator[int32]:
    t = (H(1), 5)
    saved = t[0].xs[1]  # tpyc: error(/binding 'saved' to an element inside 't' is not yet supported in a generator.*last reassignment of 't'$/)
    yield saved.x
    if c:
        t = (H(10), 6)
    yield saved.x


def main() -> None:
    for v in g(True):
        print(v)


main()
