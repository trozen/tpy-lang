# A `with` target whose field is a VALUE type that nonetheless carries a pointer
# into the manager: a borrow-form tuple, rendered `std::tuple<Item*, Int32>`. It is
# not an alias, not a pointer-repr Optional and not a view spelling, so a
# predicate enumerating those three misses it -- and an owned manager then dies at
# the end of its case block while the field still points into it. Read after a
# suspension the `with` body does not contain, with the element mutated so a lost
# alias shows up as a wrong value rather than only as garbage.
#
# `Span[T]` over the manager's own storage is the same cell and is fixed by the
# same rule; it has no case here because the `lib/cpy` Span stub mis-bounds
# `take_ptr(...).span(n)` (see BUGS.md), so the shape cannot be cpy-compared yet.
from typing import Iterator

from tpy import Int32


class Item:
    def __init__(self, v: Int32):
        self.v = v


class Pair:
    item: Item
    tag: Int32

    def __init__(self, v: Int32):
        self.item = Item(v)
        self.tag = v

    def __enter__(self) -> tuple[Item, Int32]:
        return (self.item, self.tag)

    def __exit__(self, et, ev, tb) -> None:
        pass


def gen() -> Iterator[Int32]:
    pr = Pair(7)
    with pr as p:
        pass
    yield 1
    item, tag = p
    item.v += 1
    yield item.v
    yield tag
    # Observed through the manager's own handle: a copied element
    # would leave this at 7.
    yield pr.item.v


def main() -> None:
    for v in gen():
        print(v)


main()
