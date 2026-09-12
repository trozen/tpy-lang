# The boundary beside the admitted view frame PARAM: a `Span[T]` LOCAL. The
# param is a moved-in view field, but frame-local storage for a view is not
# lowered -- the reject must stay while the param rides.
from typing import Iterator

from tpy import int32, Span


def gen(xs: list[int32]) -> Iterator[int32]:  # tpyc: error(/not yet supported/)
    sp: Span[int32] = xs
    yield sp[0]
    yield sp[1]


def main() -> None:
    xs = [1, 2, 3]
    for v in gen(xs):
        print(v)


main()
