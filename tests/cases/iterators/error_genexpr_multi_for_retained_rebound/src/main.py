# A kept genexpr may not see a capture its inner clause iterates rebound before
# the last pull (LANGUAGE_FEATURES.md, Generators); CPython runs it.
from typing import Iterable, Iterator
from tpy import int32


def relay(it: Iterable[int32]) -> Iterator[int32]:
    for v in it:
        yield v + 100


def main() -> None:
    xs: list[int32] = [1, 2]
    b_list: list[int32] = [10, 20]
    g = relay(a + b for a in xs for b in b_list)  # tpyc: error(/.b_list. is iterated by this generator expression, but it is kept past the statement and can be rebound between pulls; bind it to a local first/)
    b_list = [5]
    for v in g:
        print(v)


main()
