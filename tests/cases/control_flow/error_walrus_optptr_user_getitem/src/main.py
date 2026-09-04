# A pointer-Optional walrus over a subscript admits only a CONTAINER element,
# whose read is the stored `std::optional<T>` a lift can take the address of. A
# user `__getitem__` returns the optional BY VALUE, so there is nothing live to
# alias and the shape must keep rejecting.
from tpy import Int32


class Rec:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Bag:
    items: list[Rec]

    def __init__(self) -> None:
        self.items = [Rec(1)]

    def __getitem__(self, i: Int32) -> Rec | None:
        if i < len(self.items):
            return self.items[i]
        return None


def f(b: Bag) -> Int32:
    if (r := b[0]) is not None:  # tpyc: error(/expr\.walrus/)
        return r.n
    return 0


def main() -> None:
    print(f(Bag()))


main()
