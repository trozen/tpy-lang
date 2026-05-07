# Field-write of tuple[T | None, ...] outside the constructor needs the
# same pointer-form -> storage-form lift as field-init: `self.f = p` where
# p is a pointer-form param goes through tuple_to_storage.
from tpy import Int32


class T:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


class Holder:
    pair: tuple[T | None, T | None]
    def __init__(self) -> None:
        self.pair = (None, None)

    def update(self, p: tuple[T | None, T | None]) -> None:
        self.pair = p


def main() -> None:
    t1 = T(1)
    t2 = T(2)
    h = Holder()
    h.update((t1, t2))
    a, b = h.pair
    if a is not None:
        print(a.x)
    if b is not None:
        print(b.x)


main()
