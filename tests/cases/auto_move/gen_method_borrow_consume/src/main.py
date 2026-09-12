# A live generator METHOD object stores its params by reference; the
# method's frame-borrow facts must gate a later consume of the argument.
from typing import Iterator
from tpy import int32, Own


class Walker:
    def walk(self, xs: list[int32]) -> Iterator[int32]:
        for x in xs:
            yield x


def drop(xs: Own[list[int32]]) -> int32:
    store: list[list[int32]] = []
    store.append(xs)
    return len(store)


def main():
    w = Walker()
    xs = [1, 2, 3]
    g = w.walk(xs)
    print(drop(xs))  # tpyc: warning(/copies/)
    for v in g:
        print(v)


main()
