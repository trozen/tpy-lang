# A generator local holding a getter's reference result across a yield is
# refused when the receiver is a temporary: the result would point into the
# object the statement destroys (the subscript twin `make_bag()[0]` the same)
# -- BUGS.md#frame-alias-of-temp-receiver-result.
from typing import Iterator
from tpy import int32, Own


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Bag:
    items: list[Item]

    def __init__(self) -> None:
        self.items = [Item(10)]

    def __getitem__(self, i: int32) -> Item:
        return self.items[i]


def make_bag() -> Own[Bag]:
    return Bag()


def gen() -> Iterator[int32]:
    x = make_bag().__getitem__(0)  # tpyc: warning(/temporary receiver/) error(/res.alias_bind/)
    yield x.n


for v in gen():
    print(v)
