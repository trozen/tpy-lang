# Regression: a user-defined generic record `Container[T]` wrapping
# `set[Forward]` as a field exercises the `validate_record_type_args` recursion
# path of the wider flag-forwarding fix. Without flag forwarding through
# validate_record_type_args, the hashable check would fire on Point before
# Point's __hash__/__eq__ were registered, false-rejecting Point.
from tpy import Own, Int32, UInt64


class Container[T]:
    item: T

    def __init__(self, item: Own[T]) -> None:
        self.item = item


class Holder:
    c: Container[set["Point"]]

    def __init__(self) -> None:
        self.c = Container(set())


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def __hash__(self) -> UInt64:
        return UInt64(self.x)

    def __eq__(self, other: "Point") -> bool:
        return self.x == other.x


def main() -> None:
    h = Holder()
    h.c.item.add(Point(1))
    h.c.item.add(Point(2))
    h.c.item.add(Point(1))
    print(len(h.c.item))


main()
