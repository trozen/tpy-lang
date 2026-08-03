# An `Own[T]` frame local is the object T, constructed at the BINDING rather than
# at frame creation, so its slot must run the real constructor there. A bare field
# would default-construct with the frame and then assign -- which is not that
# constructor, and is deleted outright once T holds a non-default-constructible
# member. `Box` is one, so this shape failed to build while the same value bound
# by an ordinary `a = make()` compiled fine: the two spellings disagreed.
from typing import Iterator

from tpy import Int32, Own
from tplib import Box


class Item:
    boxed: Box[Int32]

    def __init__(self, b: Own[Box[Int32]]):
        self.boxed = b


class Fresh:
    def __enter__(self) -> Own[Item]:
        return Item(Box(5))

    def __exit__(self, et, ev, tb) -> None:
        pass


def gen() -> Iterator[Int32]:
    with Fresh() as a:
        yield a.boxed.get()
        a.boxed.set(6)
        yield a.boxed.get()
    yield a.boxed.get()


def main() -> None:
    for x in gen():
        print(x)


main()
