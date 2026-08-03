# INVERSE of the aliasing rule: an `__enter__` declared `-> Own[T]` hands back a
# FRESH value, so the target must OWN it -- aliasing would point at a dead
# temporary. The target is mutated between suspensions and read after the block
# to prove the storage both survives resume and is the same slot throughout.
from typing import Iterator

from tpy import Int32, Own


class Item:
    def __init__(self, v: Int32):
        self.v = v


class Fresh:
    def __enter__(self) -> Own[Item]:
        return Item(5)

    def __exit__(self, et, ev, tb) -> None:
        pass


def gen() -> Iterator[Int32]:
    with Fresh() as a:
        yield a.v
        a.v += 1
        yield a.v
    yield a.v


def main() -> None:
    for x in gen():
        print(x)


main()
