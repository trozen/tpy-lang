# A call-returned reference-member tuple bound to a local and yielded: the
# returned tuple is already borrow form (std::tuple<..., Box*>), the local
# keeps the borrow, and the yield hands it out -- so a post-boundary mutation
# reaches the original object through the whole chain.
from typing import Iterator
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def make(b: Box) -> tuple[int32, Box]:
    return (1, b)


def gen(b: Box) -> Iterator[tuple[int32, Box]]:
    u = make(b)
    yield u


def main() -> None:
    shared = Box(5)
    for pair in gen(shared):
        pair[1].val = 99
    print(shared.val)


main()
