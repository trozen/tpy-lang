from typing import Iterator, Iterable, Self, overload
from tpy import Int32, Own
class Bag:
    items: list[Int32]
    def __init__(self) -> None:
        self.items = [1, 2]
    @overload
    def __iter__(self) -> Iterator[Int32]:
        for x in self.items:
            yield x
    @overload
    def __iter__(self: Own[Self]) -> Iterator[Own[Int32]]:
        for x in self.items:
            yield x
def main() -> None:
    b = Bag()
    xs: list[Int32] = []
    xs.extend(b)
    print(len(xs))
main()
