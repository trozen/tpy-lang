# User-defined Iterable[Own[T]] constructor consumes at last use.
# Borrowing when source is still needed, consuming when at last use.
from tpy import Int32, Own
from typing import Iterable

class Bag:
    items: list[Int32]
    def __init__(self, src: Iterable[Own[Int32]]) -> None:
        self.items = list(src)

def main() -> None:
    # Borrowing (nums used after)
    nums: list[Int32] = [10, 20, 30]
    b1 = Bag(nums)
    print(b1.items)
    print(len(nums))

    # Consuming (nums2 at last use)
    nums2: list[Int32] = [40, 50]
    b2 = Bag(nums2)
    print(b2.items)

main()
