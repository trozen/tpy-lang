# Owning tuple local in a generator, mutated then yielded -- the slot owns
# the Box, mutation is visible across the suspension.
from typing import Iterator
from tpy import int32, Own


class Box:
    def __init__(self, val: int32):
        self.val = val


def make_pair(n: int32) -> Own[tuple[int32, Box]]:
    return (n, Box(n))


def gen() -> Iterator[int32]:
    t = make_pair(9)
    t[1].val = 50
    yield t[0]
    yield t[1].val


def main():
    for v in gen():
        print(v)


main()
