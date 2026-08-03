# A resumable frame iterating a user iterable whose __iter__ is ITSELF a frame.
# The consumer's iterator field is `frame_slot<iter_type_t<Bag>>`, which embeds
# Bag::__iter__'s return type by value -- so that generator's struct has to be
# emitted first. Bag.__iter__ has two yields, which is what keeps it off the
# simple-generator lambda (whose in-class `auto __iter__()` hid the ordering
# requirement).
from tpy import Int32, Own
from typing import Iterator


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Bag:
    items: list[Point]

    def __init__(self, items: Own[list[Point]]) -> None:
        self.items = items

    # Two yields -> a resumable frame, not the lambda peephole.
    def __iter__(self) -> Iterator[Point]:
        for p in self.items:
            yield p
            yield p


# The loop element must ALIAS the bag's element, not copy it: the mutation below
# is observed through the bag's own field after the loop.
def bump(bag: Bag) -> Iterator[Int32]:
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
