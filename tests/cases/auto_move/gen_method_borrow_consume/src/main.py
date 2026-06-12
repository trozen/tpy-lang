# A live generator METHOD object stores its params by reference; the
# method's frame-borrow facts must gate a later consume of the argument.
from typing import Iterator
from tpy import Int32, Own


class Walker:
    def walk(self, xs: list[Int32]) -> Iterator[Int32]:
        for x in xs:
            yield x


def drop(xs: Own[list[Int32]]) -> Int32:
    store: list[list[Int32]] = []
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
