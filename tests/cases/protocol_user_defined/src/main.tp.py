from typing import Protocol
from tpy import Int32

class Measurable(Protocol):
    def __len__(self) -> Int32: ...

def count(items: Measurable) -> Int32:
    return len(items)

def main() -> None:
    nums: list[Int32] = [1, 2, 3]
    print(count(nums))

main()
