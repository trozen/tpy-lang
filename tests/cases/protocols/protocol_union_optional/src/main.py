# Nullable protocol union: `is not None` guard + isinstance dispatch,
# else branch for None, and constexpr guard interaction with if/else.
from typing import Sized, Sequence

def process(items: Sized | Sequence[int] | None = None) -> int:
    if items is not None:
        if isinstance(items, Sequence):
            return items[0]
        elif isinstance(items, Sized):
            return len(items)
    return -1

def with_else(items: Sized | Sequence[int] | None = None) -> int:
    if items is not None:
        if isinstance(items, Sequence):
            return items[0]
        elif isinstance(items, Sized):
            return len(items)
    else:
        return -99
    return -1

class Holder:
    count: int

    def __init__(self, items: Sized | Sequence[int] | None = None) -> None:
        self.count = 0
        if items is not None:
            if isinstance(items, Sized):
                self.count = len(items)
            else:
                self.count = -1

def main() -> None:
    nums: list[int] = [10, 20, 30]

    # process: with arg, explicit None, omitted
    print(process(nums))
    print(process(None))
    print(process())

    # with_else: else branch fires for None / omitted
    print(with_else(nums))
    print(with_else(None))
    print(with_else())

    # Constructor with protocol union + None
    h1 = Holder(nums)
    print(h1.count)
    h2 = Holder(None)
    print(h2.count)
    h3 = Holder()
    print(h3.count)

main()
