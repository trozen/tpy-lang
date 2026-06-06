# Inside a generator frame, a tuple local bound from a frame-held list
# element is a borrow-form frame field (std::tuple<..., T*>): the binding
# aliases the element across suspension, so mutation through it is visible
# on a later read of the list.
from typing import Iterator
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen() -> Iterator[Int32]:
    items: list[tuple[Int32, Box]] = [(1, Box(5))]
    t = items[0]
    t[1].val = 99
    yield items[0][1].val
    t[1].val = 7
    yield items[0][1].val


def main() -> None:
    for x in gen():
        print(x)


main()
