# A generator-valued PROPERTY as a for-each iterable: the user-iterator route has
# no field-access receiver row, so `for v in b.items:` rejects.
from typing import Iterator
from tpy import int32


class Bag:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    @property
    def items(self) -> Iterator[int32]:
        i = 0
        while i < self.n:
            yield i
            i = i + 1


def main() -> None:
    b = Bag(2)
    for v in b.items:  # tpyc: error(/iter.user_iterator.field_access/)
        print(v)


main()
