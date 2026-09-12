# The boundary beside the admitted `*args` frame PARAM: a varargs LOCAL. A
# slice of a pack preserves the varargs form, and frame-local storage for it is
# not lowered -- the reject must stay while the param rides.
from typing import Iterator

from tpy import int32


def gen(*xs: int32) -> Iterator[int32]:  # tpyc: error(/not yet supported/)
    tail = xs[1:3]
    yield len(tail)
    yield -1


def main() -> None:
    for v in gen(1, 2, 3, 4):
        print(v)


main()
