# Owning tuple local in a generator, mutated then yielded -- the slot owns
# the Box, mutation is visible across the suspension.
from typing import Iterator
from tpy import Int32, Own


class Box:
    def __init__(self, val: Int32):
        self.val = val


def make_pair(n: Int32) -> Own[tuple[Int32, Box]]:
    return (n, Box(n))


def gen() -> Iterator[Int32]:
    t = make_pair(9)
    t[1].val = 50
    yield t[0]
    yield t[1].val


def main():
    for v in gen():
        print(v)


main()
