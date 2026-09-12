from typing import Iterator, Iterable, Self
from tpy import int32, Own, dispatch
class Bag:
    items: list[int32]
    def __init__(self) -> None:
        self.items = [1, 2]
    @dispatch
    def __iter__(self) -> Iterator[int32]:
        for x in self.items:
            yield x
    @dispatch
    def __iter__(self: Own[Self]) -> Iterator[Own[int32]]:
        for x in self.items:
            yield x
def main() -> None:
    b = Bag()
    xs: list[int32] = []
    xs.extend(b)
    print(len(xs))
main()
