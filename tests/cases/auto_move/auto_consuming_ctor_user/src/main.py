# User-defined Iterable[Own[T]] constructor consumes at last use.
# Borrowing when source is still needed, consuming when at last use.
from tpy import int32, Own
from typing import Iterable

class Bag:
    items: list[int32]
    def __init__(self, src: Iterable[Own[int32]]) -> None:
        self.items = list(src)

def main() -> None:
    # Borrowing (nums used after)
    nums: list[int32] = [10, 20, 30]
    b1 = Bag(nums)
    print(b1.items)
    print(len(nums))

    # Consuming (nums2 at last use)
    nums2: list[int32] = [40, 50]
    b2 = Bag(nums2)
    print(b2.items)

main()
