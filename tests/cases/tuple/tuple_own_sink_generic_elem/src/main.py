# A tuple literal appended into a `list[tuple[T, Int32]]` from a generic
# method. `list.append` takes `Own[tuple[...]]`, so the slot owns the tuple
# past the call and its elements must be built in STORAGE form -- a borrow-form
# lift there would store element addresses that are valid only through the
# call, and at a reference-type T it does not even type-check.
from tpy import Int32, Own, copy


class Item:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Tally[T]:
    _items: list[T]

    def __init__(self) -> None:
        self._items = []

    def add(self, x: Own[T]) -> None:
        self._items.append(x)

    def get(self, i: Int32) -> T:
        return self._items[i]

    def pairs(self) -> Own[list[tuple[T, Int32]]]:
        out: list[tuple[T, Int32]] = []
        i = 0
        for it in self._items:
            # Subject: the tuple literal at append's Own[tuple[T, Int32]] slot.
            out.append((copy(it), i))
            i += 1
        return out


def main() -> None:
    t: Tally[Item] = Tally()
    t.add(Item(1))
    t.add(Item(2))
    ps = t.pairs()
    # Mutating the source AFTER the append must not show through the stored
    # pair: the list owns an independent copy, it does not alias the tally.
    t.get(0).n = 99
    for it, i in ps:
        print(i, it.n)
    # Mutating the stored element persists in the list and stays out of the
    # tally -- the two sides are separate storage in both directions.
    for it2, _ in ps:
        it2.n = it2.n + 10
    for it3, i3 in ps:
        print(i3, it3.n)
    print(t.get(0).n)


main()
