# A live generator object stores its non-value param by reference: a
# consume of xs while g is un-exhausted must copy (with warning), not
# move -- a move would make g iterate a moved-from (empty) container.
from typing import Iterator
from tpy import int32, Own


def gen(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
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
