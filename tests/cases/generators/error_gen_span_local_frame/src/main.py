# The boundary beside the admitted view frame PARAM: a `Span[T]` LOCAL. The
# param is a moved-in view field, but frame-local storage for a view is not
# lowered -- the reject must stay while the param rides.
from typing import Iterator

from tpy import Int32, Span


def gen(xs: list[Int32]) -> Iterator[Int32]:  # tpyc: error(/not yet supported/)
    sp: Span[Int32] = xs
    yield sp[0]
    yield sp[1]


def main() -> None:
    xs = [1, 2, 3]
    for v in gen(xs):
        print(v)


main()
