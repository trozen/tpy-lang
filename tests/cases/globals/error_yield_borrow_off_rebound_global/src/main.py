# The rebound-global rule at a generator's YIELD: a view yielded off a module
# `str` that some function rebinds through `global` points into the buffer the
# rebind frees, since the frame survives suspension -- only the verb differs
# from the return. (A reference-typed global cannot reach this leg: its own
# `global` rebind is rejected first, and a borrow yield is checked after every
# body.)
from typing import Iterator

from tpy import StrView

S = "abc"


def rows() -> Iterator[StrView]:
    yield S  # tpyc: error(/Cannot yield a borrow of module variable 'S'.*Yield str or String/)


def reset() -> None:
    global S
    S = "xyz"


def main() -> None:
    for row in rows():
        print(len(row))
    reset()


main()
