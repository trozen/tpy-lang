# A cycle member: it calls into `modb`, which calls back into it. Its
# generators delegate only to same-module callees.
from typing import Iterator
from tpy import int32
import modb


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def steps(self) -> Iterator[int32]:
        yield self.n
        yield self.n + 1


def local_walk() -> Iterator[int32]:
    yield 1
    yield 2


# free-function delegator, callee in this same (cycle-member) module
def free_delegator() -> Iterator[int32]:
    yield 0
    for x in local_walk():  # tpyc: ok
        yield x


# method callee, same module -- the receiver's owner type is a cycle-member type
def method_delegator(s: Src) -> Iterator[int32]:
    yield 20
    for x in s.steps():  # tpyc: ok
        yield x


# the ordinary call that closes the import cycle
def ping(n: int32) -> int32:
    if n <= 0:
        return 0
    return modb.pong(n - 1)
