# A `[T: Default]` shadowed method param resolved through a subclass receiver:
# make_default() fills grown slots with T's zero value (the owned-container
# resize shape). Split from method_bound_shadow_subclass because make_default()
# with an inferred type argument is TPy-only (the CPython stub requires an
# explicit make_default[T]()), hence no_cpython.
from tpy import int32, Default, make_default


class Bag[T, N: int]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def add(self, value: T) -> None:
        self.items.append(value)

    def resize[T: Default](self, n: int32) -> None:
        while len(self.items) > n:
            self.items.pop()
        while len(self.items) < n:
            self.items.append(make_default())

    def get(self, i: int32) -> T:
        return self.items[i]


class IntBag(Bag[int32, 4]):
    pass


def main() -> None:
    b = IntBag()
    b.add(5)
    b.resize(3)
    print(b.get(0), b.get(1), b.get(2))
    b.resize(1)
    print(len(b.items))


main()
