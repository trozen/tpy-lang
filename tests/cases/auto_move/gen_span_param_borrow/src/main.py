# A generator over a Span param views the argument's storage: the
# signature-derived view-param borrow fact must gate a later consume.
from typing import Iterator
from tpy import Int32, Own, Span


def gen(s: Span[Int32]) -> Iterator[Int32]:
    for x in s:
        yield x


def drop(xs: Own[list[Int32]]) -> Int32:
    store: list[list[Int32]] = []
    store.append(xs)
    return len(store)


def main():
    xs = [1, 2, 3]
    g = gen(xs)
    print(drop(xs))  # tpyc: warning(/copies/)
    for v in g:
        print(v)


main()
