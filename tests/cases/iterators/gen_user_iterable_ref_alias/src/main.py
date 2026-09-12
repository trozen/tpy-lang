# Same frame-aliasing requirement as gen_proto_param_ref_alias, reached without
# any protocol param: a user-defined iterable routes through the same universal
# __iter__/__next__ frame strategy, so its loop element must alias too. The bag
# owns the list, and the mutation is observed through the bag's own field.
from tpy import int32, Own
from typing import Iterator


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Bag:
    items: list[Point]

    def __init__(self, items: Own[list[Point]]) -> None:
        self.items = items

    def __iter__(self) -> Iterator[Point]:
        for p in self.items:
            yield p


def bump(bag: Bag) -> Iterator[int32]:
    for p in bag:
        p.x += 100
        yield p.x
        yield p.x


def main() -> None:
    bag = Bag([Point(1), Point(2)])
    for v in bump(bag):
        print(v)
    print("mutations reached the bag:", bag.items[0].x, bag.items[1].x)


main()
