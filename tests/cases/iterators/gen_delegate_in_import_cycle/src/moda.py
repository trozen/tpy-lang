# A cycle member: it calls into `modb`, which calls back into it. Its
# generators delegate only to same-module callees.
from typing import Iterator
from tpy import Int32
import modb


class Src:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def steps(self) -> Iterator[Int32]:
        yield self.n
        yield self.n + 1


def local_walk() -> Iterator[Int32]:
    yield 1
    yield 2


# free-function delegator, callee in this same (cycle-member) module
def free_delegator() -> Iterator[Int32]:
    yield 0
    for x in local_walk():  # tpyc: ok
        yield x


# method callee, same module -- the receiver's owner type is a cycle-member type
def method_delegator(s: Src) -> Iterator[Int32]:
    yield 20
    for x in s.steps():  # tpyc: ok
        yield x


# the ordinary call that closes the import cycle
def ping(n: Int32) -> Int32:
    if n <= 0:
        return 0
    return modb.pong(n - 1)
