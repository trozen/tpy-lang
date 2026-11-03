# A field write through a conditional over two NAMES is no write into a new
# object (both arms exist, so sema lets it through); the lowering refuses it,
# a gap of its own -- BUGS.md#select-receiver-field-write-rejects.
from tpy import int32


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def f(a: Item, b: Item, c: bool) -> None:
    (a if c else b).n = 5  # tpyc: error(/assign.field_write_shape/)
    print(a.n, b.n)


f(Item(1), Item(2), True)
