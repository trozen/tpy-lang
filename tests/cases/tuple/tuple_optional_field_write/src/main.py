# Field-write of tuple[T | None, ...] outside the constructor needs the
# same pointer-form -> storage-form lift as field-init: `self.f = p` where
# p is a pointer-form param goes through tuple_to_storage.
from tpy import int32


class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


class Holder:
    pair: tuple[T | None, T | None]
    def __init__(self) -> None:
        self.pair = (None, None)

    def update(self, p: tuple[T | None, T | None]) -> None:
        self.pair = p  # tpyc: warning(/copies/) warning(/copies/)

    def copy_from_subscript(self, items: list[tuple[T | None, T | None]]) -> None:
        # Source is a subscript -- already storage-form, so the field-write
        # is a direct copy (no redundant tuple_to_storage wrap).
        self.pair = items[0]  # tpyc: warning(/copies/) warning(/copies/)

    def copy_from_field(self, other: 'Holder') -> None:
        # Source is a field -- already storage-form, direct copy.
        self.pair = other.pair  # tpyc: warning(/copies/) warning(/copies/)


g_anchor = T(99)
g_pair: tuple[T | None, T | None] = (g_anchor, None)


class GlobalCopier:
    pair: tuple[T | None, T | None]
    def __init__(self) -> None:
        # Source is a value global -- already storage-form, direct copy.
        self.pair = g_pair  # tpyc: warning(/copies/) warning(/copies/)


class SubscriptCtor:
    pair: tuple[T | None, T | None]
    def __init__(self, items: list[tuple[T | None, T | None]]) -> None:
        # Constructor MIL path: subscript source is already storage-form,
        # the field-init should be a direct copy (no redundant wrap).
        self.pair = items[0]  # tpyc: warning(/copies/) warning(/copies/)


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

    items: list[tuple[T | None, T | None]] = [(t1, None)]
    h.copy_from_subscript(items)
    a2, _ = h.pair
    if a2 is not None:
        print(a2.x)

    h2 = Holder()
    h2.copy_from_field(h)
    a3, _ = h2.pair
    if a3 is not None:
        print(a3.x)

    gc = GlobalCopier()
    a4, _ = gc.pair
    if a4 is not None:
        print(a4.x)

    sc = SubscriptCtor(items)
    a5, _ = sc.pair
    if a5 is not None:
        print(a5.x)


main()
