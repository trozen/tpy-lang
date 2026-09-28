# In a generator body, an element READ through a user type calls its
# __getitem__, which counts like any call against a generator held into the
# next pass: here a mutating one grows the list the held generator walks.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, Generators.)
from typing import Iterator

from tpy import int32, readonly


class Bag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    @readonly(False)
    def __getitem__(self, i: int32) -> int32:
        self.items.append(i)
        return i


def walk(b: Bag) -> Iterator[int32]:
    try:
        for x in b.items:
            yield x
    finally:
        print("close", len(b.items), b.items[0])


def outer(b: Bag, n: int32) -> Iterator[int32]:
    for i in range(n):
        g = walk(b)  # tpyc: error(/cannot keep 'g' open across passes of this loop: the loop calls '__getitem__', which may write 'b'/)
        for v in g:
            yield v
            break
        # The subject: Bag.__getitem__ grows b.items under g's loop.
        yield b[i]


print(list(outer(Bag(), 3)))
