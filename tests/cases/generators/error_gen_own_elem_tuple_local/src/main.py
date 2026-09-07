# A generator frame slot holding an owning tuple that is REASSIGNED: the
# borrow-tuple frame write has no source form for the second binding.
from typing import Iterator
from tpy import Int32, Own


class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def make_pair(n: Int32) -> Own[tuple[Int32, Box]]:
    return (n, Box(n))


def gen_reassigned() -> Iterator[Int32]:
    t = make_pair(1)  # tpyc: error(/res\.btuple_source/)
    yield t[0]
    # The frame slot's owning tuple is rebound after a suspension.
    t = make_pair(2)
    yield t[1].val


def main() -> None:
    for v in gen_reassigned():
        print(v)


main()
