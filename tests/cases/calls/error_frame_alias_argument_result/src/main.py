# A generator local holding a method result that points at an ARGUMENT is
# refused: the frame's alias slot takes its const from the call's receiver,
# which a result lent from an argument does not share --
# BUGS.md#frame-alias-of-argument-result. A result that follows its receiver
# (`c.__getitem__(0)`) is held (calls/getitem_call_parity).
from typing import Iterator
from tpy import int32


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Bag:
    def __init__(self) -> None:
        pass

    def pick(self, o: Item) -> Item:
        return o


def gen(c: Bag, a: Item) -> Iterator[int32]:
    x = c.pick(a)  # tpyc: error(/res.alias_bind/)
    yield 1
    x.n = 5
    yield a.n


for v in gen(Bag(), Item(1)):
    print(v)
