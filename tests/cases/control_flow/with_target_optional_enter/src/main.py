# An `__enter__` returning `T | None` answers alias-vs-own by itself: the
# pointer-repr Optional IS a pointer, with null doubling as None. It must keep
# that field form rather than being wrapped in a source-spelled slot, whose
# reads would deref one level short. Mutating through the narrowed target and
# reading the manager after proves the field still aliases.
from typing import Iterator

from tpy import int32


class Box:
    def __init__(self, n: int32):
        self.n = n


class Holder:
    box: Box

    def __init__(self, n: int32):
        self.box = Box(n)

    def __enter__(self) -> "Box | None":
        return self.box

    def __exit__(self, et, ev, tb) -> None:
        pass


def gen() -> Iterator[int32]:
    h = Holder(7)
    with h as m:
        pass
    yield 1
    if m is not None:
        m.n += 1
        yield m.n
    yield h.box.n


def main() -> None:
    for v in gen():
        print(v)


main()
