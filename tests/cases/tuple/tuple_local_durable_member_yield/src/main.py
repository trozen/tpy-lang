# A tuple local with a durable reference member (a param), yielded by name,
# now ALIASES the member (pointer borrow form): mutating the yielded element
# after the boundary is visible in the caller's object, matching CPython's
# shared-reference semantics (no silent copy). Mutate-and-observe, not a
# read-only match, so a silent copy would diverge.
from typing import Iterator
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def gen(b: Box) -> Iterator[tuple[int32, Box]]:
    t = (1, b)
    yield t


def main() -> None:
    shared = Box(5)
    for pair in gen(shared):
        pair[1].val = 99
    print(shared.val)


main()
