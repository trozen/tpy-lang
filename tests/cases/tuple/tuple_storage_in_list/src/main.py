# A list literal whose elements are non-value tuples (a record member) stores
# each via tuple_to_storage<S>(S{...}) -- the borrow-vs-storage tuple form
# split. The elements are fresh ctor rvalues, so value capture is the only
# possible semantics (no aliasing dimension); this guards the construction +
# storage-conversion emit, not a copy-vs-alias distinction. Reading a member
# back (items[0][1]) needs subscript-of-tuple-index routing, still un-migrated.
from tpy import Int32


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


def main() -> None:
    items: list[tuple[str, Point]] = [("a", Point(1, 2)), ("b", Point(3, 4))]
    print(len(items))
    empty: list[tuple[Int32, Point]] = []
    print(len(empty))


main()
