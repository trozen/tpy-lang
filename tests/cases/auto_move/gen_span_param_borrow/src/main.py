# A generator over a Span param views the argument's storage: the
# signature-derived view-param borrow fact must gate a later consume.
from typing import Iterator
from tpy import int32, Own, Span


def gen(s: Span[int32]) -> Iterator[int32]:
    for x in s:
        yield x


def drop(xs: Own[list[int32]]) -> int32:
    store: list[list[int32]] = []
    store.append(xs)
    return len(store)


def main():
    xs = [1, 2, 3]
    g = gen(xs)
    print(drop(xs))  # tpyc: warning(/copies/)
    for v in g:
        print(v)


main()
